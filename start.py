#!/usr/bin/env python3
"""Interactive control panel for knowledge-graph (SCIP + Memgraph + tree-sitter).

Just run it any way you like — the script re-execs itself with the
project's `.venv/bin/python` if it was launched outside the venv, so
`python3 start.py`, `./start.py` and `uv run python start.py` all work.

If `.venv` doesn't exist, run `uv sync` once and try again.

Manages four side-processes (Memgraph + Memgraph Lab in Docker, the
FastAPI core API, the optional file watcher) via PID files so they
survive between menu sessions.
"""

from __future__ import annotations

# ── auto-activate the project venv before importing anything else ──
import os
import subprocess
import sys
from pathlib import Path


def _ensure_venv_python() -> None:
    """Re-exec ourselves under `.venv/bin/python` if we aren't already.
    Boots `uv sync` once when the venv is missing — first-run friendly."""
    repo = Path(__file__).resolve().parent
    venv_py = repo / ".venv" / "bin" / "python"
    if not venv_py.exists():
        sys.stderr.write(
            "[start.py] .venv missing — running `uv sync` to create it…\n"
        )
        try:
            subprocess.run(["uv", "sync"], cwd=repo, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError) as e:
            sys.stderr.write(
                f"[start.py] uv sync failed: {e}\n"
                "  install uv from https://docs.astral.sh/uv/ and retry.\n"
            )
            raise SystemExit(2) from e
    if Path(sys.executable).resolve() == venv_py.resolve():
        return
    os.execv(str(venv_py), [str(venv_py), str(Path(__file__).resolve()), *sys.argv[1:]])


_ensure_venv_python()

import questionary

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
