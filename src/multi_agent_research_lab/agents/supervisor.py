"""Supervisor / router skeleton."""

import logging

from multi_agent_research_lab.agents.base import BaseAgent
from multi_agent_research_lab.core.config import Settings, get_settings
from multi_agent_research_lab.core.state import ResearchState
from multi_agent_research_lab.observability.tracing import trace_span

logger = logging.getLogger(__name__)

DONE = "done"


class SupervisorAgent(BaseAgent):
    """Decides which worker should run next and when to stop.

    Routing policy là deterministic (không gọi LLM) vì trạng thái thiếu-đủ của
    `ResearchState` đã đủ để quyết định. Điều này giữ router rẻ, nhanh, và test được.
    """

    name = "supervisor"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    def decide(self, state: ResearchState) -> str:
        """Chọn route kế tiếp dựa trên field nào còn thiếu trong state."""

        # Guardrail 1: chặn vòng lặp vô hạn.
        if state.iteration >= self._settings.max_iterations:
            return DONE

        # Guardrail 2: quá nhiều lỗi thì dừng sớm thay vì retry mãi.
        if len(state.errors) >= 3:
            return DONE

        # Routing chính: đi theo pipeline research -> analysis -> writing.
        if not state.research_notes:
            return "researcher"
        if not state.analysis_notes:
            return "analyst"
        if not state.final_answer:
            return "writer"
        return DONE

    def run(self, state: ResearchState) -> ResearchState:
        """Ghi route kế tiếp vào `state.route_history` và trace event."""

        with trace_span(
            "supervisor",
            {"iteration": state.iteration, "errors": len(state.errors)},
        ) as span:
            route = self.decide(state)

            # Fallback: hết iteration mà chưa có câu trả lời -> ghi rõ lý do dừng.
            if route == DONE and not state.final_answer:
                reason = (
                    f"Dừng sau {state.iteration} iteration mà chưa có final_answer "
                    f"(max_iterations={self._settings.max_iterations}, errors={len(state.errors)})."
                )
                state.errors.append(reason)
                state.final_answer = (
                    "Không tạo được câu trả lời hoàn chỉnh trong giới hạn cho phép. "
                    f"Lý do: {reason}"
                )

            state.record_route(route)
            state.add_trace_event(
                "supervisor_route",
                {
                    "next": route,
                    "iteration": state.iteration,
                    "has_research": bool(state.research_notes),
                    "has_analysis": bool(state.analysis_notes),
                    "has_answer": bool(state.final_answer),
                },
            )
            span["attributes"]["route"] = route
            logger.info("supervisor route=%s iteration=%d", route, state.iteration)
        return state
