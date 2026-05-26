#!/usr/bin/env python3
"""Interactive control panel for knowledge-graph.

Manages three services (Neo4j in Docker, ts-morph indexer, FastAPI core)
via PID files so they survive between menu sessions.

    uv run python start.py
"""
from __future__ import annotations

import questionary

from kg.menu.actions import (
    open_api_docs,
    open_browser,
    reindex,
    restart_all,
    start_all,
    stop_all,
    tail_logs,
)
from kg.menu.ui import MENU_STYLE, console, show_status


ACTIONS = [
    ("Start all",                  start_all),
    ("Stop all",                   stop_all),
    ("Restart all",                restart_all),
    ("Reindex project",            reindex),
    ("Tail logs",                  tail_logs),
    ("Open Neo4j Browser",         open_browser),
    ("Open API docs (Swagger)",    open_api_docs),
    ("Refresh status",             lambda: None),
    ("Quit",                       None),
]


def main() -> None:
    while True:
        show_status()
        answer = questionary.select(
            "What now?",
            choices=[a[0] for a in ACTIONS],
            style=MENU_STYLE,
            qmark="›",
            use_shortcuts=False,
        ).ask()
        if answer is None or answer == "Quit":
            console.print("[dim]bye — services keep running in background[/dim]")
            return
        for label, fn in ACTIONS:
            if label == answer:
                if fn is not None:
                    try:
                        fn()
                    except KeyboardInterrupt:
                        console.print("[yellow]interrupted[/yellow]")
                    except Exception as e:
                        console.print(f"[red]error:[/red] {e}")
                    if label != "Refresh status":
                        questionary.press_any_key_to_continue(style=MENU_STYLE).ask()
                break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]bye[/dim]")
