"""Command-line entrypoint for the lab starter."""

from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from multi_agent_research_lab.core.config import get_settings
from multi_agent_research_lab.core.errors import StudentTodoError
from multi_agent_research_lab.core.schemas import AgentName, AgentResult, ResearchQuery
from multi_agent_research_lab.core.state import ResearchState
from multi_agent_research_lab.evaluation.benchmark import run_benchmark, total_cost_usd
from multi_agent_research_lab.evaluation.report import render_markdown_report
from multi_agent_research_lab.graph.workflow import MultiAgentWorkflow
from multi_agent_research_lab.observability.logging import configure_logging
from multi_agent_research_lab.observability.tracing import workflow_trace
from multi_agent_research_lab.services.llm_client import LLMClient

app = typer.Typer(help="Multi-Agent Research Lab starter CLI")
console = Console()


def _init() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)


def _parse_query(query: str) -> ResearchQuery:
    try:
        return ResearchQuery(query=query)
    except ValidationError as exc:
        console.print(
            Panel.fit(
                f"Invalid query: {exc.errors()[0]['msg']}",
                title="Input Error",
                style="red",
            )
        )
        raise typer.Exit(code=1) from exc


BASELINE_SYSTEM_PROMPT = """Bạn là một research assistant đơn lẻ.
Bạn phải tự làm toàn bộ: tìm ý, phân tích, và viết câu trả lời cuối cùng.
Bạn KHÔNG có công cụ search, chỉ dựa vào kiến thức sẵn có.
Viết câu trả lời có cấu trúc, nêu rõ chỗ nào bạn không chắc chắn."""


def run_baseline(query: str) -> ResearchState:
    """Single-agent baseline: một LLM call làm hết mọi việc."""

    state = ResearchState(request=ResearchQuery(query=query))
    with workflow_trace("single_agent_baseline", query) as span:
        try:
            response = LLMClient().complete(
                BASELINE_SYSTEM_PROMPT,
                f"CÂU HỎI:\n{query}\n\nĐỐI TƯỢNG ĐỌC: {state.request.audience}",
            )
        except Exception as exc:  # noqa: BLE001
            state.errors.append(f"baseline failed: {exc}")
            state.add_trace_event("baseline_error", {"error": str(exc)})
            return state

        state.final_answer = response.content
        state.agent_results.append(
            AgentResult(
                agent=AgentName.WRITER,
                content=response.content,
                metadata={
                    "mode": "single_agent_baseline",
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "cost_usd": response.cost_usd,
                },
            )
        )
        state.record_route("baseline")
        state.add_trace_event("baseline_done", {"answer_chars": len(response.content)})
        span["attributes"]["answer_chars"] = len(response.content)
    return state


@app.command()
def baseline(
    query: Annotated[str, typer.Option("--query", "-q", help="Research query")],
) -> None:
    """Run a real single-agent baseline (one LLM call does everything)."""

    _init()
    request = _parse_query(query)
    state = run_baseline(request.query)
    if state.errors:
        console.print(Panel.fit("\n".join(state.errors), title="Baseline Error", style="red"))
        raise typer.Exit(code=1)
    console.print(Panel.fit(state.final_answer or "(rỗng)", title="Single-Agent Baseline"))
    cost = total_cost_usd(state)
    console.print(f"[dim]cost≈${cost:.6f} | agents={len(state.agent_results)}[/dim]")


def run_multi_agent(query: str) -> ResearchState:
    """Multi-agent workflow: supervisor điều phối researcher/analyst/writer/critic."""

    state = ResearchState(request=ResearchQuery(query=query))
    return MultiAgentWorkflow().run(state)


@app.command("multi-agent")
def multi_agent(
    query: Annotated[str, typer.Option("--query", "-q", help="Research query")],
    show_json: Annotated[
        bool, typer.Option("--json", help="In toàn bộ state dạng JSON thay vì bản tóm tắt")
    ] = False,
) -> None:
    """Run the multi-agent workflow."""

    _init()
    state = ResearchState(request=_parse_query(query))
    workflow = MultiAgentWorkflow()
    try:
        result = workflow.run(state)
    except StudentTodoError as exc:
        console.print(Panel.fit(str(exc), title="Expected TODO", style="yellow"))
        raise typer.Exit(code=2) from exc

    if show_json:
        console.print(result.model_dump_json(indent=2))
        return

    console.print(Panel.fit(result.final_answer or "(rỗng)", title="Multi-Agent Answer"))

    table = Table(title="Trace", show_lines=False)
    table.add_column("#", justify="right")
    table.add_column("Event")
    table.add_column("Payload", overflow="fold")
    for i, event in enumerate(result.trace, start=1):
        table.add_row(str(i), event["name"], str(event["payload"]))
    console.print(table)

    console.print(
        f"[dim]routes={' > '.join(result.route_history)} | "
        f"iterations={result.iteration} | sources={len(result.sources)} | "
        f"cost≈${total_cost_usd(result):.6f} | errors={len(result.errors)}[/dim]"
    )
    if result.errors:
        console.print(Panel.fit("\n".join(result.errors), title="Errors", style="yellow"))


@app.command()
def benchmark(
    query: Annotated[str, typer.Option("--query", "-q", help="Research query")],
    output: Annotated[
        str, typer.Option("--output", "-o", help="Đường dẫn file markdown report")
    ] = "reports/benchmark_report.md",
) -> None:
    """Chạy cả baseline lẫn multi-agent trên cùng query và xuất báo cáo so sánh."""

    _init()
    request = _parse_query(query)

    console.print("[bold]1/2[/bold] Đang chạy single-agent baseline...")
    baseline_state, baseline_metrics = run_benchmark(
        "single-agent", request.query, run_baseline
    )

    console.print("[bold]2/2[/bold] Đang chạy multi-agent workflow...")
    multi_state, multi_metrics = run_benchmark("multi-agent", request.query, run_multi_agent)

    trace_url = next(
        (e["payload"]["url"] for e in multi_state.trace if e["name"] == "langfuse_trace"),
        None,
    )
    report = render_markdown_report(
        [baseline_metrics, multi_metrics], query=request.query, trace_url=trace_url
    )
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")

    console.print(report)
    console.print(f"[green]Đã ghi report vào {path}[/green]")
    console.print(
        f"[dim]baseline sources={len(baseline_state.sources)} | "
        f"multi sources={len(multi_state.sources)} "
        f"routes={' > '.join(multi_state.route_history)}[/dim]"
    )


if __name__ == "__main__":
    app()
