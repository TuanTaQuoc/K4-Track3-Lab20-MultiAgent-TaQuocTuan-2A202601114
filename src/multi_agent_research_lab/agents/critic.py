"""Optional critic agent skeleton for bonus work."""

import logging
import re

from multi_agent_research_lab.agents.base import BaseAgent
from multi_agent_research_lab.core.schemas import AgentName, AgentResult
from multi_agent_research_lab.core.state import ResearchState
from multi_agent_research_lab.observability.tracing import trace_span

logger = logging.getLogger(__name__)

CITATION_PATTERN = re.compile(r"\[S(\d+)\]")


class CriticAgent(BaseAgent):
    """Optional fact-checking and safety-review agent.

    Dùng kiểm tra deterministic (regex + so khớp index nguồn) thay vì gọi LLM:
    rẻ hơn, không tự nó hallucinate, và cho ra số đo dùng được cho benchmark.
    """

    name = "critic"

    def run(self, state: ResearchState) -> ResearchState:
        """Validate final answer and append findings."""

        with trace_span("critic", {"has_answer": bool(state.final_answer)}) as span:
            if not state.final_answer:
                state.errors.append("critic: không có final_answer để kiểm tra")
                state.add_trace_event("critic_skipped", {"reason": "missing final_answer"})
                return state

            cited = {int(n) for n in CITATION_PATTERN.findall(state.final_answer)}
            total_sources = len(state.sources)
            valid_range = set(range(1, total_sources + 1))

            # Citation trỏ tới nguồn không tồn tại => dấu hiệu hallucination.
            dangling = sorted(cited - valid_range)
            unused = sorted(valid_range - cited)
            coverage = len(cited & valid_range) / total_sources if total_sources else 0.0
            mock_used = sum(
                1
                for i, doc in enumerate(state.sources, start=1)
                if i in cited and doc.metadata.get("provider") == "mock"
            )

            findings: list[str] = []
            if dangling:
                findings.append(
                    f"Citation trỏ tới nguồn không tồn tại: {dangling} "
                    f"(chỉ có {total_sources} nguồn)"
                )
            if not cited:
                findings.append("Câu trả lời không có citation nào")
            if unused:
                findings.append(f"Nguồn thu thập nhưng không dùng: {unused}")
            if mock_used:
                findings.append(f"{mock_used} nguồn mock chưa kiểm chứng được trích dẫn")

            verdict = "PASS" if not dangling and cited else "NEEDS_REVIEW"
            content = f"{verdict}\n" + (
                "\n".join(f"- {f}" for f in findings) if findings else "- Không phát hiện vấn đề"
            )

            state.agent_results.append(
                AgentResult(
                    agent=AgentName.CRITIC,
                    content=content,
                    metadata={
                        "verdict": verdict,
                        "citation_coverage": coverage,
                        "dangling_citations": dangling,
                        "unused_sources": unused,
                        "mock_sources_cited": mock_used,
                        "cost_usd": 0.0,
                    },
                )
            )
            if dangling:
                state.errors.append(f"critic: dangling citations {dangling}")
            state.add_trace_event(
                "critic_done", {"verdict": verdict, "citation_coverage": coverage}
            )
            span["attributes"]["verdict"] = verdict
            logger.info("critic verdict=%s coverage=%.2f", verdict, coverage)
        return state
