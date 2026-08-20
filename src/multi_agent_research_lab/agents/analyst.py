"""Analyst agent skeleton."""

import logging

from multi_agent_research_lab.agents.base import BaseAgent
from multi_agent_research_lab.core.schemas import AgentName, AgentResult
from multi_agent_research_lab.core.state import ResearchState
from multi_agent_research_lab.observability.tracing import trace_span
from multi_agent_research_lab.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Bạn là Analyst trong một hệ multi-agent.
Đầu vào là research notes đã có citation. Nhiệm vụ của bạn KHÔNG phải tìm thêm thông tin,
mà là phân tích những gì đã có. Xuất ra đúng 3 mục:

## Key claims
Liệt kê các luận điểm chính, mỗi luận điểm giữ nguyên citation [S1], [S2]...

## Conflicting viewpoints
Các điểm mà nguồn mâu thuẫn hoặc chưa thống nhất. Ghi "Không phát hiện mâu thuẫn" nếu không có.

## Weak evidence
Các claim thiếu nguồn, nguồn yếu, nguồn mock, hoặc suy luận vượt quá bằng chứng.
Đây là mục quan trọng nhất - phải trung thực, không được bỏ trống cho đẹp.

Tối đa 400 từ. KHÔNG viết câu trả lời cuối cùng."""


class AnalystAgent(BaseAgent):
    """Turns research notes into structured insights."""

    name = "analyst"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()

    def run(self, state: ResearchState) -> ResearchState:
        """Populate `state.analysis_notes`."""

        with trace_span("analyst", {"has_research": bool(state.research_notes)}) as span:
            if not state.research_notes:
                # Validation: không có input thì báo lỗi thay vì gọi LLM vô nghĩa.
                state.errors.append("analyst: thiếu research_notes, bỏ qua bước phân tích")
                state.add_trace_event("analyst_skipped", {"reason": "missing research_notes"})
                return state

            mock_count = sum(
                1 for doc in state.sources if doc.metadata.get("provider") == "mock"
            )
            user_prompt = (
                f"CÂU HỎI GỐC:\n{state.request.query}\n\n"
                f"SỐ NGUỒN: {len(state.sources)} "
                f"(trong đó {mock_count} là nguồn mock chưa kiểm chứng)\n\n"
                f"RESEARCH NOTES:\n{state.research_notes}"
            )

            try:
                response = self._llm.complete(SYSTEM_PROMPT, user_prompt)
            except Exception as exc:  # noqa: BLE001
                logger.exception("analyst llm failed")
                state.errors.append(f"analyst.llm failed: {exc}")
                state.add_trace_event("analyst_error", {"error": str(exc)})
                return state

            state.analysis_notes = response.content
            state.agent_results.append(
                AgentResult(
                    agent=AgentName.ANALYST,
                    content=response.content,
                    metadata={
                        "mock_source_count": mock_count,
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cost_usd": response.cost_usd,
                    },
                )
            )
            state.add_trace_event("analyst_done", {"analysis_chars": len(response.content)})
            span["attributes"]["analysis_chars"] = len(response.content)
            logger.info("analyst produced %d chars", len(response.content))
        return state
