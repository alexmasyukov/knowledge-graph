"""User-facing actions invoked from the main loop."""

from __future__ import annotations

import subprocess
import time
import webbrowser

import httpx
import questionary

from .env import CORE_URL, NEO4J_BROWSER, PROJECTS
from .services import CORE, INDEXER, neo4j_down, neo4j_state, neo4j_up
from .ui import MENU_STYLE, console


def start_all() -> None:
    with console.status("[cyan]Starting Neo4j…", spinner="dots"):
        if neo4j_state() not in ("running", "starting"):
            neo4j_up()
        else:
            for _ in range(60):
                if neo4j_state() == "running":
                    break
                time.sleep(1)
    console.print("[green]✓[/green] Neo4j ready")

    with console.status("[cyan]Starting indexer…", spinner="dots"):
        INDEXER.start()
        ok = INDEXER.wait_healthy(15)
    console.print(
        f"[{'green' if ok else 'red'}]{'✓' if ok else '✗'}[/] indexer {'ready' if ok else 'failed'} — see {INDEXER.log_file}"
    )

    with console.status("[cyan]Starting core API…", spinner="dots"):
        CORE.start()
        ok = CORE.wait_healthy(15)
    console.print(
        f"[{'green' if ok else 'red'}]{'✓' if ok else '✗'}[/] core {'ready' if ok else 'failed'} — see {CORE.log_file}"
    )


def stop_all() -> None:
    with console.status("[cyan]Stopping core…"):
        CORE.stop()
    with console.status("[cyan]Stopping indexer…"):
        INDEXER.stop()
    with console.status("[cyan]Stopping Neo4j…"):
        neo4j_down()
    console.print("[green]✓[/green] all stopped")


def restart_all() -> None:
    stop_all()
    time.sleep(0.5)
    start_all()


def reindex() -> None:
    if not PROJECTS:
        console.print("[red]No projects configured in .env (PROJECT_<NAME>=…)[/red]")
        return
    if len(PROJECTS) == 1:
        target = PROJECTS[0]
    else:
        target = questionary.select(
            "Which project to reindex?",
            choices=[*PROJECTS, "(all)"],
            style=MENU_STYLE,
        ).ask()
        if target is None:
            return
        if target == "(all)":
            target = None

    params = {"project": target} if target else None
    with console.status(f"[cyan]Reindexing {target or 'all projects'}…"):
        try:
            r = httpx.post(f"{CORE_URL}/reindex", params=params, timeout=300.0)
            r.raise_for_status()
            console.print_json(r.text)
        except Exception as e:
            console.print(f"[red]reindex failed:[/red] {e}")


def tail_logs() -> None:
    choice = questionary.select(
        "Tail which log?",
        choices=["indexer", "core", "back"],
        style=MENU_STYLE,
    ).ask()
    if not choice or choice == "back":
        return
    log = INDEXER.log_file if choice == "indexer" else CORE.log_file
    if not log.exists():
        console.print(f"[yellow]no log yet:[/yellow] {log}")
        return
    console.print(f"[dim]tail -f {log} — Ctrl+C to return[/dim]")
    try:
        subprocess.run(["tail", "-n", "50", "-f", str(log)])
    except KeyboardInterrupt:
        pass


def open_browser() -> None:
    webbrowser.open(NEO4J_BROWSER)
    console.print(f"[green]opened[/green] {NEO4J_BROWSER}")


def open_api_docs() -> None:
    webbrowser.open(f"{CORE_URL}/docs")
    console.print(f"[green]opened[/green] {CORE_URL}/docs")
