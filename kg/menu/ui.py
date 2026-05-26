"""Status panel rendering + questionary styling."""

from __future__ import annotations

from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .env import CORE_URL, INDEXER_URL, NEO4J_BROWSER, PROJECTS
from .services import CORE, INDEXER, Service, neo4j_state

console = Console()

MENU_STYLE = Style(
    [
        ("qmark", "fg:#22aaff bold"),
        ("question", "bold"),
        ("answer", "fg:#22aaff bold"),
        ("pointer", "fg:#22aaff bold"),
        ("highlighted", "fg:#22aaff bold"),
        ("selected", "fg:#22aaff"),
        ("instruction", "fg:#888888"),
    ]
)


def _dot(state: str) -> Text:
    color = {
        "running": "green",
        "healthy": "green",
        "starting": "yellow",
        "blocked": "magenta",
        "stopped": "red",
        "unhealthy": "red",
    }.get(state, "red")
    return Text("●", style=color)


def _svc_state(svc: Service) -> tuple[str, str]:
    """Returns (state, hint). State 'blocked' means the port is held by
    a foreign process — meaning the menu won't be able to start us."""
    if svc.alive() and svc.healthy():
        return "running", f"pid={svc.pid()}"
    if svc.alive():
        return "starting", f"pid={svc.pid()}"
    blocker = svc.port_blocker()
    if blocker is not None:
        return "blocked", f"port held by pid {blocker}"
    return "stopped", "pid=—"


def render_status() -> Panel:
    neo = neo4j_state()
    idx_state, idx_hint = _svc_state(INDEXER)
    core_state, core_hint = _svc_state(CORE)

    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(justify="left", style="bold")
    table.add_column(justify="left")
    table.add_column(justify="left", style="dim")
    table.add_column(justify="left", style="dim")

    table.add_row("Neo4j", _dot(neo) + Text(f" {neo:<9}"), NEO4J_BROWSER, "browser")
    table.add_row("Indexer", _dot(idx_state) + Text(f" {idx_state:<9}"), INDEXER_URL, idx_hint)
    table.add_row("Core API", _dot(core_state) + Text(f" {core_state:<9}"), CORE_URL, core_hint)
    table.add_row()
    table.add_row("Projects", Text(", ".join(PROJECTS) or "— none —", style="cyan"))

    return Panel(table, title="[bold]knowledge-graph[/bold]", border_style="cyan", padding=(1, 2))


def show_status() -> None:
    console.clear()
    console.print(render_status())
