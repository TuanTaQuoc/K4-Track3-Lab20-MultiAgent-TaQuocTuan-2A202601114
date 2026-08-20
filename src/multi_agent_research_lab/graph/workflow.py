"""LangGraph workflow skeleton."""

import logging
from typing import Any, TypedDict, cast

from langgraph.graph import END, StateGraph

from multi_agent_research_lab.agents.analyst import AnalystAgent
from multi_agent_research_lab.agents.critic import CriticAgent
from multi_agent_research_lab.agents.researcher import ResearcherAgent
from multi_agent_research_lab.agents.supervisor import DONE, SupervisorAgent
from multi_agent_research_lab.agents.writer import WriterAgent
from multi_agent_research_lab.core.config import Settings, get_settings
from multi_agent_research_lab.core.state import ResearchState
from multi_agent_research_lab.observability.tracing import workflow_trace

logger = logging.getLogger(__name__)


class GraphState(TypedDict):
    """Bọc `ResearchState` trong một key duy nhất.

    Các node chạy tuần tự nên không cần reducer; giữ nguyên object Pydantic giúp
    validation và trace của `ResearchState` không bị mất khi qua graph.
    """

    state: ResearchState


class MultiAgentWorkflow:
    """Builds and runs the multi-agent graph.

    Keep orchestration here; keep agent internals in `agents/`.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        supervisor: SupervisorAgent | None = None,
        researcher: ResearcherAgent | None = None,
        analyst: AnalystAgent | None = None,
        writer: WriterAgent | None = None,
        critic: CriticAgent | None = None,
        enable_critic: bool = True,
    ) -> None:
        self._settings = settings or get_settings()
        # Lazy: chỉ tạo agent khi thật sự chạy, để `build()` test được không cần API key.
        self._supervisor = supervisor
        self._researcher = researcher
        self._analyst = analyst
        self._writer = writer
        self._critic = critic
        self._enable_critic = enable_critic

    def _ensure_agents(self) -> None:
        self._supervisor = self._supervisor or SupervisorAgent(self._settings)
        self._researcher = self._researcher or ResearcherAgent()
        self._analyst = self._analyst or AnalystAgent()
        self._writer = self._writer or WriterAgent()
        if self._enable_critic:
            self._critic = self._critic or CriticAgent()

    def build(self) -> Any:
        """Create a LangGraph graph với supervisor làm router trung tâm."""

        self._ensure_agents()

        def supervisor_node(payload: GraphState) -> GraphState:
            return {"state": self._supervisor.run(payload["state"])}  # type: ignore[union-attr]

        def researcher_node(payload: GraphState) -> GraphState:
            return {"state": self._researcher.run(payload["state"])}  # type: ignore[union-attr]

        def analyst_node(payload: GraphState) -> GraphState:
            return {"state": self._analyst.run(payload["state"])}  # type: ignore[union-attr]

        def writer_node(payload: GraphState) -> GraphState:
            return {"state": self._writer.run(payload["state"])}  # type: ignore[union-attr]

        def critic_node(payload: GraphState) -> GraphState:
            return {"state": self._critic.run(payload["state"])}  # type: ignore[union-attr]

        def route_from_supervisor(payload: GraphState) -> str:
            """Đọc quyết định mà supervisor vừa ghi vào route_history."""

            history = payload["state"].route_history
            return history[-1] if history else DONE

        # LangGraph dùng overload rất rộng cho `add_node`/`add_conditional_edges`, mypy strict
        # không khớp được. Giữ builder ở kiểu Any thay vì rải `type: ignore` khắp nơi;
        # `GraphState` vẫn là schema thật của graph nên type safety trong node không mất.
        graph: Any = StateGraph(GraphState)
        graph.add_node("supervisor", supervisor_node)
        graph.add_node("researcher", researcher_node)
        graph.add_node("analyst", analyst_node)
        graph.add_node("writer", writer_node)

        graph.set_entry_point("supervisor")

        # Stop condition: supervisor trả "done" -> đi critic (nếu bật) rồi END.
        if self._enable_critic:
            graph.add_node("critic", critic_node)
            path_map = {
                "researcher": "researcher",
                "analyst": "analyst",
                "writer": "writer",
                DONE: "critic",
            }
            graph.add_edge("critic", END)
        else:
            path_map = {
                "researcher": "researcher",
                "analyst": "analyst",
                "writer": "writer",
                DONE: END,
            }

        graph.add_conditional_edges("supervisor", route_from_supervisor, path_map)

        # Mỗi worker xong thì trả quyền quyết định lại cho supervisor.
        for worker in ("researcher", "analyst", "writer"):
            graph.add_edge(worker, "supervisor")

        return graph

    def run(self, state: ResearchState) -> ResearchState:
        """Execute the graph and return final state."""

        compiled = self.build().compile()
        # recursion_limit là guardrail tầng graph, bổ sung cho max_iterations của supervisor.
        config = {"recursion_limit": self._settings.max_iterations * 2 + 5}

        final: ResearchState
        with workflow_trace("multi_agent_workflow", state.request.query) as span:
            try:
                result = compiled.invoke({"state": state}, config=config)
                final = cast(ResearchState, result["state"])
            except Exception as exc:  # noqa: BLE001 - trả state một phần thay vì crash CLI
                logger.exception("workflow failed")
                state.errors.append(f"workflow failed: {exc}")
                final = state
            span["attributes"]["iterations"] = final.iteration
            span["attributes"]["errors"] = len(final.errors)
            if span.get("trace_url"):
                final.add_trace_event("langfuse_trace", {"url": span["trace_url"]})

        logger.info(
            "workflow done iterations=%d routes=%s errors=%d",
            final.iteration,
            final.route_history,
            len(final.errors),
        )
        return final
