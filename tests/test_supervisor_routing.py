"""Unit tests cho routing policy của SupervisorAgent.

Thay thế skeleton guard test cũ (chỉ kiểm tra `StudentTodoError` còn tồn tại).
Test ở đây không gọi LLM: routing là deterministic nên test được offline.
"""

from multi_agent_research_lab.agents import SupervisorAgent
from multi_agent_research_lab.agents.supervisor import DONE
from multi_agent_research_lab.core.config import Settings
from multi_agent_research_lab.core.schemas import ResearchQuery
from multi_agent_research_lab.core.state import ResearchState


def _state(**kwargs: object) -> ResearchState:
    return ResearchState(request=ResearchQuery(query="Explain multi-agent systems"), **kwargs)


def test_routes_to_researcher_when_state_is_empty() -> None:
    assert SupervisorAgent().decide(_state()) == "researcher"


def test_routes_to_analyst_after_research() -> None:
    state = _state(research_notes="notes")
    assert SupervisorAgent().decide(state) == "analyst"


def test_routes_to_writer_after_analysis() -> None:
    state = _state(research_notes="notes", analysis_notes="analysis")
    assert SupervisorAgent().decide(state) == "writer"


def test_routes_to_done_when_answer_exists() -> None:
    state = _state(research_notes="n", analysis_notes="a", final_answer="answer")
    assert SupervisorAgent().decide(state) == DONE


def test_max_iterations_guardrail_stops_the_loop() -> None:
    settings = Settings(MAX_ITERATIONS=3)
    state = _state(iteration=3)
    assert SupervisorAgent(settings).decide(state) == DONE


def test_repeated_errors_trigger_early_stop() -> None:
    state = _state(errors=["e1", "e2", "e3"])
    assert SupervisorAgent().decide(state) == DONE


def test_run_records_route_and_trace_event() -> None:
    state = _state()
    result = SupervisorAgent().run(state)
    assert result.route_history == ["researcher"]
    assert result.iteration == 1
    assert result.trace[-1]["name"] == "supervisor_route"
    assert result.trace[-1]["payload"]["next"] == "researcher"


def test_run_writes_fallback_answer_when_giving_up() -> None:
    settings = Settings(MAX_ITERATIONS=2)
    state = _state(iteration=2)
    result = SupervisorAgent(settings).run(state)
    assert result.route_history[-1] == DONE
    assert result.final_answer is not None
    assert result.errors, "phải ghi lại lý do dừng để debug được"
