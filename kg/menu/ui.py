"""Status panel rendering + questionary styling."""

from __future__ import annotations

import questionary
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .env import CORE_DOCS_URL, CORE_VIZ_URL, MEMGRAPH_BOLT_URL, MEMGRAPH_LAB_URL, PROJECTS
from .services import CORE, WATCHER, Service, memgraph_lab_state, memgraph_state

console = Console()

MENU_STYLE = questionary.Style(
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
    """Returns (state, hint). 'blocked' = port held by foreign process."""
    if svc.alive() and svc.healthy():
        return "running", f"pid={svc.pid()}"
    if svc.alive():
        return "starting", f"pid={svc.pid()}"
    blocker = svc.port_blocker()
    if blocker is not None:
        return "blocked", f"port held by pid {blocker}"
    return "stopped", "pid=—"


def render_status() -> Panel:
    mg_state = memgraph_state()
    lab_state = memgraph_lab_state()
    core_state, core_hint = _svc_state(CORE)
    w_alive = WATCHER.alive()
    w_state, w_hint = ("running", f"pid={WATCHER.pid()}") if w_alive else ("stopped", "pid=—")

    table = Table.grid(padding=(0, 2), expand=False)
    table.add_column(justify="left", style="bold")
    table.add_column(justify="left")
    table.add_column(justify="left", style="dim")
    table.add_column(justify="left", style="dim")

    table.add_row("Memgraph", _dot(mg_state) + Text(f" {mg_state:<9}"), MEMGRAPH_BOLT_URL, "bolt")
    table.add_row("Memgraph Lab", _dot(lab_state) + Text(f" {lab_state:<9}"), MEMGRAPH_LAB_URL, "browser")
    table.add_row("Core API", _dot(core_state) + Text(f" {core_state:<9}"), CORE_DOCS_URL, core_hint)
    table.add_row("Watcher", _dot(w_state) + Text(f" {w_state:<9}"), "—", w_hint)
    table.add_row()
    table.add_row("Viz", Text(CORE_VIZ_URL, style="dim"))
    table.add_row("Projects", Text(", ".join(PROJECTS) or "— none —", style="cyan"))

    return Panel(table, title="[bold]knowledge-graph[/bold]", border_style="cyan", padding=(1, 2))


def show_status() -> None:
    console.clear()
    console.print(render_status())
