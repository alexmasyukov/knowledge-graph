"""User-facing actions invoked from the main loop."""

from __future__ import annotations

import subprocess
import time
import webbrowser

import httpx
import questionary

from .env import CORE_DOCS_URL, CORE_URL, CORE_VIZ_URL, MEMGRAPH_LAB_URL, PROJECTS, ROOT
from .services import (
    CORE,
    WATCHER,
    PortBusy,
    memgraph_down,
    memgraph_state,
    memgraph_up,
    watcher_for,
)
from .ui import MENU_STYLE, console


# ── lifecycle ─────────────────────────────────────────────────────


def start_all() -> None:
    with console.status("[cyan]Starting Memgraph…", spinner="dots"):
        if memgraph_state() not in ("running", "healthy", "starting"):
            memgraph_up()
        else:
            for _ in range(60):
                if memgraph_state() in ("running", "healthy"):
                    break
                time.sleep(1)
    console.print("[green]✓[/green] Memgraph ready")
    _start_svc(CORE, "core")
    _autostart_watcher()


def _autostart_watcher() -> None:
    """Single-project mode: auto-start the watcher for the only project
    in .env. Multi-project setups won't fly through here — for those use
    the explicit `Start watcher` menu item."""
    if WATCHER.alive():
        return
    if len(PROJECTS) != 1:
        return
    project = PROJECTS[0]
    svc = watcher_for(project)
    with console.status(f"[cyan]Starting watcher for {project}…", spinner="dots"):
        svc.start()
    if svc.alive():
        console.print(f"[green]✓[/green] watcher up ({project}) — pid {svc.pid()}")
    else:
        console.print(f"[red]✗[/red] watcher failed to start — see {svc.log_file}")


def _start_svc(svc, label: str, timeout: float = 15.0) -> None:
    try:
        with console.status(f"[cyan]Starting {label}…", spinner="dots"):
            svc.start()
            ok = svc.wait_healthy(timeout)
    except PortBusy as e:
        console.print(
            f"[red]✗[/red] {label}: port {e.port} is held by another process (PID {e.blocker_pid}).\n"
            f"  Run [bold]kill {e.blocker_pid}[/bold] and try again, or use Restart all."
        )
        return
    console.print(
        f"[{'green' if ok else 'red'}]{'✓' if ok else '✗'}[/] "
        f"{label} {'ready' if ok else 'failed'} — see {svc.log_file}"
    )


def stop_all() -> None:
    with console.status("[cyan]Stopping watcher…"):
        WATCHER.stop()
    with console.status("[cyan]Stopping core…"):
        CORE.stop()
    with console.status("[cyan]Stopping Memgraph…"):
        memgraph_down()
    console.print("[green]✓[/green] all stopped")


def restart_all() -> None:
    stop_all()
    time.sleep(0.5)
    start_all()


def start_watcher() -> None:
    if WATCHER.alive():
        console.print(f"[dim]watcher already running (pid {WATCHER.pid()})[/dim]")
        return
    target = _pick_project()
    if target is None:
        return
    svc = watcher_for(target)
    with console.status(f"[cyan]Starting watcher for {target}…", spinner="dots"):
        try:
            svc.start()
        except PortBusy:
            pass  # watcher has no port
    if svc.alive():
        console.print(f"[green]✓[/green] watcher up — pid {svc.pid()}, log {svc.log_file}")
    else:
        console.print(f"[red]✗[/red] watcher failed to start — see {svc.log_file}")


def stop_watcher() -> None:
    if not WATCHER.alive():
        console.print("[dim]watcher was not running[/dim]")
        return
    with console.status("[cyan]Stopping watcher…"):
        WATCHER.stop()
    console.print("[green]✓[/green] watcher stopped")


# ── reindex / sanity / tests ──────────────────────────────────────


def _pick_project() -> str | None:
    if not PROJECTS:
        console.print("[red]No projects configured in .env (PROJECT_<NAME>=…)[/red]")
        return None
    if len(PROJECTS) == 1:
        return PROJECTS[0]
    return questionary.select(
        "Which project?",
        choices=PROJECTS,
        style=MENU_STYLE,
    ).ask()


def reindex() -> None:
    target = _pick_project()
    if target is None:
        return
    since = questionary.text(
        "since git-ref? (empty = full reindex)",
        default="",
        style=MENU_STYLE,
        qmark="›",
    ).ask()
    params: dict[str, str] = {"project": target}
    if since:
        params["since"] = since
    with console.status(f"[cyan]Reindexing {target}…", spinner="dots"):
        try:
            r = httpx.post(f"{CORE_URL}/reindex", params=params, timeout=600.0)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            console.print(f"[red]reindex failed:[/red] {e}")
            return
    if not data.get("extractors"):
        console.print(f"[dim]no changes since {since!r} — skipped[/dim]")
        return
    console.print(
        f"[green]ok={data.get('ok')}[/green] scip={data['scip_duration_ms']}ms "
        f"total={data['total_duration_ms']}ms"
    )
    for ext in data["extractors"]:
        counts = ", ".join(f"{k}={v}" for k, v in (ext.get("nodes") or {}).items())
        err = ext.get("error")
        tag = f" [red]ERR:[/red] {err}" if err else ""
        console.print(
            f"  {ext['name']:13s} {ext.get('duration_ms', 0):5d}ms "
            f"edges={ext.get('edges', 0):4d}  {counts}{tag}"
        )


def sanity() -> None:
    target = _pick_project()
    if target is None:
        return
    with console.status(f"[cyan]Running probes for {target}…", spinner="dots"):
        try:
            r = httpx.get(f"{CORE_URL}/sanity", params={"project": target}, timeout=30.0)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            console.print(f"[red]sanity failed:[/red] {e}")
            return
    summary = data.get("summary") or {}
    total = summary.get("total_issues", 0)
    console.print(f"[bold]{total} issue(s)[/bold] (project={target})")
    for k, v in sorted(summary.items()):
        if k == "total_issues" or v == 0:
            continue
        console.print(f"  {k}: [bold]{v}[/bold]")


def run_tests() -> None:
    console.print("[dim]uv run pytest tests/ -q[/dim]")
    subprocess.run(["uv", "run", "pytest", "tests/", "-q"], cwd=ROOT, check=False)


# ── browsers / logs ───────────────────────────────────────────────


def tail_logs() -> None:
    choice = questionary.select(
        "Tail which log?",
        choices=["core", "watcher", "back"],
        style=MENU_STYLE,
    ).ask()
    if not choice or choice == "back":
        return
    log = CORE.log_file if choice == "core" else WATCHER.log_file
    if not log.exists():
        console.print(f"[yellow]no log yet:[/yellow] {log}")
        return
    console.print(f"[dim]tail -f {log} — Ctrl+C to return[/dim]")
    try:
        subprocess.run(["tail", "-n", "50", "-f", str(log)])
    except KeyboardInterrupt:
        pass


def open_lab() -> None:
    webbrowser.open(MEMGRAPH_LAB_URL)
    console.print(f"[green]opened[/green] {MEMGRAPH_LAB_URL}")


def open_api_docs() -> None:
    webbrowser.open(CORE_DOCS_URL)
    console.print(f"[green]opened[/green] {CORE_DOCS_URL}")


def open_viz() -> None:
    webbrowser.open(CORE_VIZ_URL)
    console.print(f"[green]opened[/green] {CORE_VIZ_URL}")
