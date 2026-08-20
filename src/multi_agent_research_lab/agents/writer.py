"""Writer agent skeleton."""

import logging

from multi_agent_research_lab.agents.base import BaseAgent
from multi_agent_research_lab.core.schemas import AgentName, AgentResult
from multi_agent_research_lab.core.state import ResearchState
from multi_agent_research_lab.observability.tracing import trace_span
from multi_agent_research_lab.services.llm_client import LLMClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Bạn là Writer trong một hệ multi-agent.
Bạn nhận research notes và analysis notes, và viết câu trả lời cuối cùng.
Quy tắc bắt buộc:
- Chỉ dùng thông tin từ notes được cung cấp. Không thêm kiến thức ngoài.
- Giữ citation [S1], [S2]... ở các câu có bằng chứng.
- Kết thúc bằng mục "## Sources" liệt kê đầy đủ các nguồn theo số.
- Nếu Analyst đã flag weak evidence, phải nêu giới hạn đó trong một mục "## Limitations".
  Không được giấu điểm yếu để câu trả lời trông thuyết phục hơn thực tế.
- Viết mạch lạc, có heading, phù hợp đối tượng đọc được nêu."""


class WriterAgent(BaseAgent):
    """Produces final answer from research and analysis notes."""

    name = "writer"

    def __init__(self, llm: LLMClient | None = None) -> None:
        self._llm = llm or LLMClient()

    def run(self, state: ResearchState) -> ResearchState:
        """Populate `state.final_answer`."""

        with trace_span("writer", {"has_analysis": bool(state.analysis_notes)}) as span:
            if not state.research_notes:
                state.errors.append("writer: thiếu research_notes, không thể viết câu trả lời")
                state.add_trace_event("writer_skipped", {"reason": "missing research_notes"})
                return state

            sources_block = "\n".join(
                f"[S{i}] {doc.title} - {doc.url or 'không có URL'}"
                for i, doc in enumerate(state.sources, start=1)
            )
            user_prompt = (
                f"CÂU HỎI GỐC:\n{state.request.query}\n\n"
                f"ĐỐI TƯỢNG ĐỌC: {state.request.audience}\n\n"
                f"RESEARCH NOTES:\n{state.research_notes}\n\n"
                f"ANALYSIS NOTES:\n{state.analysis_notes or '(không có phân tích)'}\n\n"
                f"DANH SÁCH NGUỒN:\n{sources_block or '(không có nguồn)'}"
            )

            try:
                response = self._llm.complete(SYSTEM_PROMPT, user_prompt)
            except Exception as exc:  # noqa: BLE001
                logger.exception("writer llm failed")
                state.errors.append(f"writer.llm failed: {exc}")
                state.add_trace_event("writer_error", {"error": str(exc)})
                return state

            state.final_answer = response.content
            state.agent_results.append(
                AgentResult(
                    agent=AgentName.WRITER,
                    content=response.content,
                    metadata={
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cost_usd": response.cost_usd,
                    },
                )
            )
            state.add_trace_event("writer_done", {"answer_chars": len(response.content)})
            span["attributes"]["answer_chars"] = len(response.content)
            logger.info("writer produced %d chars", len(response.content))
        return state
