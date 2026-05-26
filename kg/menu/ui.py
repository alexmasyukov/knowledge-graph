"""Status panel rendering + questionary styling."""

from __future__ import annotations

from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .env import CORE_URL, INDEXER_URL, NEO4J_BROWSER, PROJECTS
from .services import CORE, INDEXER, neo4j_state

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
        "stopped": "red",
        "unhealthy": "red",
    }.get(state, "red")
    return Text("●", style=color)


def render_status() -> Panel:
    neo = neo4j_state()
    idx_alive = INDEXER.alive()
    idx_state = "running" if (idx_alive and INDEXER.healthy()) else ("starting" if idx_alive else "stopped")
    core_alive = CORE.alive()
    core_state = "running" if (core_alive and CORE.healthy()) else ("starting" if core_alive else "stopped")

    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(justify="left", style="bold")
    table.add_column(justify="left")
    table.add_column(justify="left", style="dim")
    table.add_column(justify="left", style="dim")

    table.add_row("Neo4j", _dot(neo) + Text(f" {neo:<9}"), NEO4J_BROWSER, "browser")
    table.add_row(
        "Indexer", _dot(idx_state) + Text(f" {idx_state:<9}"), INDEXER_URL, f"pid={INDEXER.pid() or '—'}"
    )
    table.add_row(
        "Core API", _dot(core_state) + Text(f" {core_state:<9}"), CORE_URL, f"pid={CORE.pid() or '—'}"
    )
    table.add_row()
    table.add_row("Projects", Text(", ".join(PROJECTS) or "— none —", style="cyan"))

    return Panel(table, title="[bold]knowledge-graph[/bold]", border_style="cyan", padding=(1, 2))


def show_status() -> None:
    console.clear()
    console.print(render_status())
