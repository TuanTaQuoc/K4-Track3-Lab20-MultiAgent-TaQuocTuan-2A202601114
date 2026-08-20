"""Benchmark skeleton for single-agent vs multi-agent."""

import logging
import re
from collections.abc import Callable
from time import perf_counter

from multi_agent_research_lab.core.schemas import BenchmarkMetrics
from multi_agent_research_lab.core.state import ResearchState

logger = logging.getLogger(__name__)

Runner = Callable[[str], ResearchState]

CITATION_PATTERN = re.compile(r"\[S(\d+)\]")


def total_cost_usd(state: ResearchState) -> float:
    """Cộng chi phí token của tất cả agent đã chạy."""

    return sum(
        float(result.metadata.get("cost_usd") or 0.0) for result in state.agent_results
    )


def total_tokens(state: ResearchState) -> tuple[int, int]:
    """Tổng input/output token của cả lần chạy."""

    input_tokens = sum(int(r.metadata.get("input_tokens") or 0) for r in state.agent_results)
    output_tokens = sum(int(r.metadata.get("output_tokens") or 0) for r in state.agent_results)
    return input_tokens, output_tokens


def citation_coverage(state: ResearchState) -> float | None:
    """Tỉ lệ nguồn thu thập được thực sự trích dẫn trong câu trả lời.

    Trả None khi không có nguồn nào (baseline không search) để không so sánh khập khiễng.
    """

    if not state.sources:
        return None
    if not state.final_answer:
        return 0.0
    cited = {int(n) for n in CITATION_PATTERN.findall(state.final_answer)}
    valid = cited & set(range(1, len(state.sources) + 1))
    return len(valid) / len(state.sources)


def failure_rate(state: ResearchState) -> float:
    """1.0 nếu lần chạy không cho ra câu trả lời dùng được, ngược lại tỉ lệ lỗi mềm."""

    if not state.final_answer:
        return 1.0
    if not state.errors:
        return 0.0
    # Có câu trả lời nhưng vẫn có lỗi mềm -> tính theo số lỗi trên số agent đã chạy.
    denominator = max(len(state.agent_results), 1)
    return min(len(state.errors) / denominator, 1.0)


def heuristic_quality_score(state: ResearchState) -> float:
    """Điểm chất lượng 0-10 tính tự động (proxy trước khi có peer review).

    Đây CHỈ là proxy structural, không thay thế rubric peer review của con người.
    """

    answer = state.final_answer or ""
    if not answer:
        return 0.0

    score = 0.0
    # Có câu trả lời thực chất (không phải fallback rỗng).
    score += 2.0 if len(answer) > 400 else 1.0
    # Có citation.
    coverage = citation_coverage(state)
    if coverage is not None:
        score += 3.0 * coverage
    # Có cấu trúc heading.
    score += 1.5 if "##" in answer else 0.0
    # Thừa nhận giới hạn thay vì tự tin thái quá.
    score += 1.5 if re.search(r"limitation|giới hạn|hạn chế", answer, re.I) else 0.0
    # Có liệt kê nguồn.
    score += 1.0 if re.search(r"source|nguồn", answer, re.I) else 0.0
    # Chạy sạch, không lỗi.
    score += 1.0 if not state.errors else 0.0
    return round(min(score, 10.0), 1)


def run_benchmark(
    run_name: str, query: str, runner: Runner
) -> tuple[ResearchState, BenchmarkMetrics]:
    """Chạy một runner và đo latency, cost, citation coverage, failure rate, quality."""

    started = perf_counter()
    try:
        state = runner(query)
    except Exception as exc:  # noqa: BLE001 - một run hỏng không được dừng cả benchmark
        logger.exception("benchmark run %s failed", run_name)
        latency = perf_counter() - started
        from multi_agent_research_lab.core.schemas import ResearchQuery

        state = ResearchState(request=ResearchQuery(query=query))
        state.errors.append(f"run failed: {exc}")
        return state, BenchmarkMetrics(
            run_name=run_name,
            latency_seconds=latency,
            failure_rate=1.0,
            quality_score=0.0,
            notes=f"Run thất bại: {exc}",
        )

    latency = perf_counter() - started
    in_tok, out_tok = total_tokens(state)
    notes = (
        f"agents={len(state.agent_results)}, routes={'>'.join(state.route_history) or 'n/a'}, "
        f"sources={len(state.sources)}, tokens={in_tok}in/{out_tok}out, errors={len(state.errors)}"
    )
    metrics = BenchmarkMetrics(
        run_name=run_name,
        latency_seconds=latency,
        estimated_cost_usd=total_cost_usd(state),
        quality_score=heuristic_quality_score(state),
        citation_coverage=citation_coverage(state),
        failure_rate=failure_rate(state),
        notes=notes,
    )
    logger.info("benchmark %s: %s", run_name, metrics.model_dump())
    return state, metrics
