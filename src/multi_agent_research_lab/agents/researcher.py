"""Researcher agent skeleton."""

import logging

from multi_agent_research_lab.agents.base import BaseAgent
from multi_agent_research_lab.core.schemas import AgentName, AgentResult
from multi_agent_research_lab.core.state import ResearchState
from multi_agent_research_lab.observability.tracing import trace_span
from multi_agent_research_lab.services.llm_client import LLMClient
from multi_agent_research_lab.services.search_client import SearchClient

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """Bạn là Researcher trong một hệ multi-agent.
Nhiệm vụ DUY NHẤT của bạn là tóm tắt bằng chứng từ các nguồn được cung cấp.
Quy tắc bắt buộc:
- Chỉ dùng thông tin có trong SOURCES. Không bịa thêm.
- Mỗi ý phải gắn citation dạng [S1], [S2]... theo đúng số thứ tự nguồn.
- Nếu các nguồn mâu thuẫn, nêu rõ mâu thuẫn đó.
- Nếu nguồn không đủ để trả lời, nói thẳng phần nào còn thiếu.
- KHÔNG viết kết luận cuối cùng, đó là việc của Writer.
Xuất ra bullet notes ngắn gọn, tối đa 400 từ."""


class ResearcherAgent(BaseAgent):
    """Collects sources and creates concise research notes."""

    name = "researcher"

    def __init__(self, llm: LLMClient | None = None, search: SearchClient | None = None) -> None:
        self._llm = llm or LLMClient()
        self._search = search or SearchClient()

    def run(self, state: ResearchState) -> ResearchState:
        """Populate `state.sources` and `state.research_notes`."""

        query = state.request.query
        with trace_span("researcher", {"query": query}) as span:
            try:
                sources = self._search.search(query, max_results=state.request.max_sources)
            except Exception as exc:  # noqa: BLE001 - lỗi search không được giết workflow
                logger.exception("researcher search failed")
                state.errors.append(f"researcher.search failed: {exc}")
                state.add_trace_event("researcher_error", {"stage": "search", "error": str(exc)})
                return state

            state.sources = sources
            sources_block = "\n\n".join(
                f"[S{i}] {doc.title}\nURL: {doc.url or 'n/a'}\n{doc.snippet}"
                for i, doc in enumerate(sources, start=1)
            )
            user_prompt = (
                f"CÂU HỎI NGHIÊN CỨU:\n{query}\n\n"
                f"ĐỐI TƯỢNG ĐỌC: {state.request.audience}\n\n"
                f"SOURCES:\n{sources_block or '(không có nguồn nào)'}"
            )

            try:
                response = self._llm.complete(SYSTEM_PROMPT, user_prompt)
            except Exception as exc:  # noqa: BLE001
                logger.exception("researcher llm failed")
                state.errors.append(f"researcher.llm failed: {exc}")
                state.add_trace_event("researcher_error", {"stage": "llm", "error": str(exc)})
                return state

            state.research_notes = response.content
            state.agent_results.append(
                AgentResult(
                    agent=AgentName.RESEARCHER,
                    content=response.content,
                    metadata={
                        "source_count": len(sources),
                        "input_tokens": response.input_tokens,
                        "output_tokens": response.output_tokens,
                        "cost_usd": response.cost_usd,
                    },
                )
            )
            state.add_trace_event(
                "researcher_done",
                {"source_count": len(sources), "notes_chars": len(response.content)},
            )
            span["attributes"]["source_count"] = len(sources)
            logger.info("researcher collected %d sources", len(sources))
        return state
