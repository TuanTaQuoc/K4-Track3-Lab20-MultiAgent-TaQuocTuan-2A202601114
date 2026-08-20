"""Benchmark report rendering."""

from datetime import datetime

from multi_agent_research_lab.core.schemas import BenchmarkMetrics

AUTHOR = "Tạ Quốc Tuấn"
STUDENT_ID = "2A202601114"


def _delta_line(label: str, base: float | None, other: float | None, unit: str = "") -> str | None:
    """Mô tả chênh lệch giữa hai run, bỏ qua khi thiếu số liệu."""

    if base is None or other is None:
        return None
    if base == 0:
        return f"- **{label}**: baseline {base}{unit} → multi-agent {other}{unit}"
    ratio = other / base
    direction = "cao hơn" if ratio > 1 else "thấp hơn"
    return (
        f"- **{label}**: {base:.4g}{unit} → {other:.4g}{unit} "
        f"(multi-agent {direction} {abs(ratio):.2f}x)"
    )


def render_markdown_report(
    metrics: list[BenchmarkMetrics],
    query: str | None = None,
    trace_url: str | None = None,
    screenshot_path: str | None = "screenshots/langfuse.png",
    run_timestamp: datetime | None = None,
) -> str:
    """Render benchmark metrics thành markdown report kèm phân tích chênh lệch."""

    lines = [
        "# Benchmark Report",
        "",
        f"**Học viên:** {AUTHOR} — **MSSV:** {STUDENT_ID}  ",
        f"**Ngày chạy:** {(run_timestamp or datetime.now()).strftime('%Y-%m-%d %H:%M')}",
        "",
    ]
    if query:
        lines += [f"**Query:** `{query}`", ""]

    lines += [
        "## Kết quả",
        "",
        "| Run | Latency (s) | Cost (USD) | Quality | Citation cov. | Failure rate | Notes |",
        "|---|---:|---:|---:|---:|---:|---|",
    ]
    for item in metrics:
        cost = "" if item.estimated_cost_usd is None else f"{item.estimated_cost_usd:.4f}"
        quality = "" if item.quality_score is None else f"{item.quality_score:.1f}"
        citation = "" if item.citation_coverage is None else f"{item.citation_coverage:.0%}"
        failure = "" if item.failure_rate is None else f"{item.failure_rate:.0%}"
        lines.append(
            f"| {item.run_name} | {item.latency_seconds:.2f} | {cost} | {quality} "
            f"| {citation} | {failure} | {item.notes} |"
        )

    # Phân tích chênh lệch khi có đúng 2 run để so sánh.
    if len(metrics) == 2:
        base, other = metrics[0], metrics[1]
        lines += ["", "## Chênh lệch", ""]
        deltas = [
            _delta_line("Latency", base.latency_seconds, other.latency_seconds, "s"),
            _delta_line("Cost", base.estimated_cost_usd, other.estimated_cost_usd, " USD"),
            _delta_line("Quality (proxy)", base.quality_score, other.quality_score),
        ]
        lines += [d for d in deltas if d]
        if base.citation_coverage is None and other.citation_coverage is not None:
            lines.append(
                f"- **Citation coverage**: baseline không đo được (không có nguồn) → "
                f"multi-agent {other.citation_coverage:.0%}"
            )

    if trace_url or screenshot_path:
        lines += ["", "## Trace evidence", ""]
        if screenshot_path:
            lines += [f"![Langfuse trace multi-agent end-to-end]({screenshot_path})", ""]
        if trace_url:
            lines += [
                f"Langfuse trace: {trace_url}",
                "",
                "> Link trace là private trong project Langfuse của tác giả; "
                "screenshot ở trên là bằng chứng chính.",
            ]

    lines += [
        "",
        "## Ghi chú về `quality_score`",
        "",
        "Điểm này do `heuristic_quality_score()` tính tự động và chỉ đo **cấu trúc** "
        "(có citation, có heading, có thừa nhận giới hạn, chạy không lỗi). "
        "Nó **không** kiểm chứng tính đúng đắn của nội dung. "
        "Đánh giá nội dung vẫn cần peer review theo `docs/peer_review_rubric.md`.",
        "",
        "Cần nói thẳng một điểm yếu của chỉ số này: Writer bị prompt **bắt buộc** phải có "
        "mục `## Limitations`, trong khi scorer lại **cộng điểm** cho câu trả lời chứa chữ "
        '"limitation". Đây là vòng tròn logic — hệ thống được chấm điểm vì làm đúng lệnh vừa '
        "được ra, nên điểm 10.0 của multi-agent gần như được đảm bảo trước khi chạy. "
        "Chỉ số này thiên vị multi-agent **theo thiết kế**.",
        "",
        "## Failure mode gặp phải và cách fix",
        "",
        "### Vòng lặp vô hạn giữa Supervisor và worker",
        "",
        "**Triệu chứng.** Supervisor route sang một worker; worker thất bại im lặng và không "
        "ghi được gì vào state; supervisor thấy field vẫn rỗng nên route lại đúng worker đó. "
        "Vòng lặp chạy mãi và đốt token cho tới khi bị kill.",
        "",
        "**Nguyên nhân gốc.** Routing dựa trên *\"field nào còn rỗng\"* là điều kiện về "
        "**kết quả**, không phải về **nỗ lực đã bỏ ra**. Nếu worker không bao giờ điền được "
        "field thì điều kiện không bao giờ đổi, và graph không có lý do gì để dừng.",
        "",
        "**Cách fix — hai tầng chặn độc lập.** Tầng nghiệp vụ nằm trong "
        "`SupervisorAgent.decide()`: `iteration >= max_iterations` thì trả `done`, cộng thêm "
        "`len(errors) >= 3` để dừng sớm khi lỗi lặp lại thay vì đợi hết quota. Tầng hạ tầng "
        "nằm trong `MultiAgentWorkflow.run()`: `recursion_limit = max_iterations * 2 + 5` "
        "truyền vào `compiled.invoke()`. Tầng thứ hai tồn tại vì tầng thứ nhất là *code do "
        "tôi viết và có thể có bug* — nếu `decide()` sai logic thì LangGraph vẫn cắt được "
        "vòng lặp. Ngoài ra khi buộc phải dừng mà chưa có `final_answer`, supervisor tự ghi "
        "một câu trả lời fallback nêu rõ lý do dừng thay vì trả `None`, để hệ thống degrade "
        "có kiểm soát chứ không im lặng trả về khoảng trống.",
        "",
        "**Kiểm chứng.** `test_max_iterations_guardrail_stops_the_loop` và "
        "`test_repeated_errors_trigger_early_stop` trong "
        "[`tests/test_supervisor_routing.py`](../tests/test_supervisor_routing.py).",
        "",
        "### Quan sát thật kèm theo: nguồn thu thập nhưng không được trích dẫn",
        "",
        "Ở một lần chạy trước đó, `CriticAgent` báo `citation_coverage=0.8`: Researcher thu "
        "được 5 nguồn nhưng Writer chỉ trích dẫn 4, nguồn `[S3]` bị bỏ qua hoàn toàn. Câu trả "
        "lời vẫn trông đầy đủ và có citation, nên nếu chỉ đọc bằng mắt thì không phát hiện ra. "
        "Đây đúng là loại lỗi mà Critic được xây để bắt: kiểm tra bằng regex so khớp index "
        "citation với `len(sources)`, xác định và chi phí bằng 0, thay vì dùng LLM-judge vốn "
        "có thể tự hallucinate khi chấm điểm.",
        "",
        "Phân tích đầy đủ 6 failure mode (bao gồm hallucinated citation, search provider chết, "
        "và tracing làm chết workflow) xem tại "
        "[`docs/failure_modes.md`](../docs/failure_modes.md).",
    ]
    return "\n".join(lines) + "\n"
