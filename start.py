#!/usr/bin/env -S uv run python
"""Interactive control panel for knowledge-graph (SCIP + Memgraph + tree-sitter).

Manages four side-processes (Memgraph + Memgraph Lab in Docker, the
FastAPI core API, the optional file watcher) via PID files so they
survive between menu sessions.

Run either via uv (recommended — picks up the project venv automatically):

    uv run python start.py

…or, after `chmod +x start.py`, just:

    ./start.py
"""

from __future__ import annotations

try:
    import questionary
except ModuleNotFoundError:
    import sys
    sys.stderr.write(
        "knowledge-graph deps aren't on this interpreter.\n"
        "  → run with `uv run python start.py` (or `./start.py` after `chmod +x`)\n"
        "  → or `uv sync` if you've just cloned the repo.\n"
    )
    raise SystemExit(2)

from kg.menu.actions import (
    open_api_docs,
    open_lab,
    open_viz,
    reindex,
    restart_all,
    run_tests,
    sanity,
    start_all,
    start_watcher,
    stop_all,
    stop_watcher,
    tail_logs,
)
from kg.menu.ui import MENU_STYLE, console, show_status

ACTIONS = [
    ("Start all", start_all),
    ("Stop all", stop_all),
    ("Restart all", restart_all),
    ("Reindex project", reindex),
    ("Sanity probes", sanity),
    ("Run tests", run_tests),
    ("Start watcher", start_watcher),
    ("Stop watcher", stop_watcher),
    ("Tail logs", tail_logs),
    ("Open Memgraph Lab", open_lab),
    ("Open API docs (Swagger)", open_api_docs),
    ("Open graph viewer", open_viz),
    ("Refresh status", lambda: None),
    ("Quit", None),
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
