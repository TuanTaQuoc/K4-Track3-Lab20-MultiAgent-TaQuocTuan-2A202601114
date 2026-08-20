# Design Template

## Problem

Xây dựng research assistant nhận một câu hỏi nghiên cứu mở (ví dụ: *"Research GraphRAG
state-of-the-art and write a 500-word summary"*) và trả về một bản tóm tắt có dẫn nguồn.

Task này gồm 3 công việc khác bản chất nhau:

1. **Thu thập bằng chứng** từ nguồn bên ngoài (cần tool search, cần lọc nguồn rác).
2. **Phân tích** bằng chứng: đâu là luận điểm chính, chỗ nào các nguồn mâu thuẫn, chỗ nào
   bằng chứng yếu.
3. **Viết** câu trả lời mạch lạc cho một đối tượng đọc cụ thể, giữ nguyên citation.

## Why multi-agent?

Single-agent baseline (`run_baseline` trong [cli.py](../src/multi_agent_research_lab/cli.py))
làm cả 3 việc trong một lần gọi LLM. Đo thực tế cho thấy 2 vấn đề:

- **Không có bằng chứng.** Baseline không gọi search nên `sources=0`, citation coverage
  không đo được. Nội dung hoàn toàn dựa vào knowledge cutoff của model — với chủ đề đang
  thay đổi nhanh như GraphRAG thì đây là rủi ro hallucination trực tiếp.
- **Không có bước tự phản biện.** Khi một prompt vừa phải tìm ý, vừa phân tích, vừa viết
  cho hay, mục tiêu "viết cho thuyết phục" lấn át mục tiêu "trung thực về điểm yếu".
  Tách Analyst ra thành agent riêng, với prompt cấm viết kết luận và bắt buộc điền mục
  *Weak evidence*, khiến việc thừa nhận giới hạn trở thành đầu ra bắt buộc chứ không phải
  tùy hứng.

Cái giá phải trả: chậm hơn ~1.7x và tốn hơn ~3x token (xem
[benchmark_report.md](../../reports/benchmark_report.md)). Đây là đánh đổi có chủ đích, không
phải multi-agent tốt hơn ở mọi mặt.

## Agent roles

| Agent | Responsibility | Input | Output | Failure mode |
|---|---|---|---|---|
| Supervisor | Quyết định worker kế tiếp và khi nào dừng | `ResearchState` (field nào còn thiếu) | route vào `route_history` | Route sai thứ tự → worker chạy khi thiếu input; loop vô hạn nếu worker im lặng thất bại |
| Researcher | Search + lọc nguồn + ghi notes có citation | `request.query`, `max_sources` | `sources`, `research_notes` | Search provider chết/rate-limit; nguồn rác lọt vào; bịa citation không có trong nguồn |
| Analyst | Trích key claims, đối chiếu mâu thuẫn, flag bằng chứng yếu | `research_notes`, `sources` | `analysis_notes` | Bỏ trống mục *Weak evidence* cho đẹp; phân tích vượt quá dữ liệu đã có |
| Writer | Tổng hợp câu trả lời cuối, giữ citation | `research_notes`, `analysis_notes`, `sources` | `final_answer` | Bỏ qua limitation mà Analyst đã flag; thêm kiến thức ngoài notes |
| Critic *(bonus)* | Kiểm tra citation bằng regex, không dùng LLM | `final_answer`, `sources` | `AgentResult` verdict + coverage | Chỉ bắt được lỗi cấu trúc (citation trỏ sai), không phát hiện được sai về nội dung |

## Shared state

`ResearchState` trong [core/state.py](../src/multi_agent_research_lab/core/state.py):

| Field | Lý do cần |
|---|---|
| `request` | Query gốc + `max_sources` + `audience`; Writer cần `audience` để chọn giọng văn |
| `iteration` | Đếm số lần supervisor quyết định — cơ sở cho guardrail max iterations |
| `route_history` | Vừa là log debug, vừa là **kênh truyền quyết định** từ supervisor sang conditional edge của LangGraph |
| `sources` | Bằng chứng thô; Critic cần nó để đối chiếu index citation `[S1]`, benchmark cần nó để tính coverage |
| `research_notes` | Handoff Researcher → Analyst; đồng thời là điều kiện routing |
| `analysis_notes` | Handoff Analyst → Writer; đồng thời là điều kiện routing |
| `final_answer` | Deliverable; sự tồn tại của nó là stop condition |
| `agent_results` | Token/cost per-agent → nguồn dữ liệu cho benchmark cost |
| `trace` | Chuỗi sự kiện có thứ tự để giải thích "agent nào làm gì" khi review |
| `errors` | Lỗi mềm được tích lũy thay vì raise, cho phép workflow degrade thay vì chết |

Nguyên tắc: **ba field `research_notes` / `analysis_notes` / `final_answer` vừa là dữ liệu
vừa là tín hiệu routing.** Supervisor không cần LLM vì chỉ cần đọc field nào còn `None`.

## Routing policy

```text
        ┌──────────────┐
   ┌───►│  supervisor  │───── done ────►┌────────┐
   │    └──────┬───────┘                │ critic │──► END
   │           │                        └────────┘
   │   researcher / analyst / writer
   │           │
   │    ┌──────▼───────┐
   └────┤   worker     │
        └──────────────┘
```

Quyết định trong `SupervisorAgent.decide()`:

| Điều kiện (xét theo thứ tự) | Route |
|---|---|
| `iteration >= max_iterations` | `done` |
| `len(errors) >= 3` | `done` |
| `research_notes` rỗng | `researcher` |
| `analysis_notes` rỗng | `analyst` |
| `final_answer` rỗng | `writer` |
| còn lại | `done` |

Supervisor ghi route vào `route_history`; `route_from_supervisor()` trong
[graph/workflow.py](../src/multi_agent_research_lab/graph/workflow.py) đọc phần tử cuối để
chọn nhánh. Mọi worker đều quay lại supervisor — không có worker nào tự quyết định người kế tiếp.

## Guardrails

- **Max iterations:** `MAX_ITERATIONS=6` (`Settings`), supervisor chặn ở `decide()`. Tầng thứ
  hai: `recursion_limit = max_iterations * 2 + 5` truyền vào `compiled.invoke()`, phòng khi
  logic supervisor có bug.
- **Timeout:** `TIMEOUT_SECONDS=60` truyền thẳng vào constructor `OpenAI(timeout=...)` nên
  áp cho mọi call, không agent nào tự đặt timeout riêng.
- **Retry:** `@retry(stop_after_attempt(3), wait_exponential(2→10s))` của `tenacity`, đặt ở
  `LLMClient.complete()` — một chỗ duy nhất, agents không cần biết.
- **Fallback:**
  - Tavily lỗi hoặc thiếu key → `SearchClient._mock_sources()` để workflow vẫn chạy offline,
    và nguồn mock bị đánh dấu `verified: False` để Analyst/Critic biết mà cảnh báo.
  - Langfuse lỗi → chỉ log debug, không làm chết workflow (tracing không được là điểm gãy).
  - Hết iteration mà chưa có câu trả lời → supervisor ghi `final_answer` giải thích lý do
    dừng thay vì trả `None`.
- **Validation:**
  - Pydantic ở biên: `ResearchQuery(query: min_length=5)`, `max_sources: 1..20`,
    `BenchmarkMetrics.quality_score: 0..10`.
  - Analyst/Writer tự kiểm tra input trước khi gọi LLM, thiếu thì ghi `errors` và return sớm.
  - Critic đối chiếu index citation với `len(sources)` để bắt citation trỏ tới nguồn không tồn tại.

## Benchmark plan

**Query:** `"Research GraphRAG state-of-the-art and write a 500-word summary"` — chọn chủ đề
mới sau knowledge cutoff để làm lộ đúng điểm khác biệt giữa có search và không search.

| Metric | Cách đo | Expected outcome |
|---|---|---|
| Latency | `perf_counter` quanh runner | Multi-agent chậm hơn ~2x (4 LLM call tuần tự + search) |
| Cost | Cộng `cost_usd` từ `agent_results`, giá tra bảng `PRICING_USD_PER_MTOK` | Multi-agent tốn ~3x vì notes được truyền lại qua từng chặng |
| Quality | `heuristic_quality_score()` 0–10 (proxy structural) + peer review theo rubric | Multi-agent cao hơn nhờ có citation, heading, mục Limitations |
| Citation coverage | `\[S(\d+)\]` trong `final_answer` ∩ range hợp lệ, chia `len(sources)` | Baseline `None` (không có nguồn); multi-agent kỳ vọng ≥ 0.8 |
| Failure rate | `1.0` nếu không có `final_answer`, ngược lại `len(errors)/len(agent_results)` | Cả hai kỳ vọng 0% ở happy path |

**Lưu ý về `quality_score`:** đây là proxy đo *cấu trúc* (có citation không, có heading không,
có thừa nhận giới hạn không), **không** đo tính đúng đắn nội dung. Điểm 10.0 của multi-agent
nghĩa là "trình bày đủ các thành phần cần có", không phải "nội dung chính xác tuyệt đối".
Đánh giá đúng/sai vẫn cần peer review theo
[peer_review_rubric.md](peer_review_rubric.md).
