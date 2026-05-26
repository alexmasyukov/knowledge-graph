#!/usr/bin/env python3
"""Interactive control panel for knowledge-graph.

Manages three services (Neo4j in Docker, ts-morph indexer, FastAPI core)
via PID files so they survive between menu sessions.

    uv run python start.py
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
import webbrowser
from pathlib import Path

import httpx
import questionary
from questionary import Style
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

ROOT = Path(__file__).resolve().parent
RUN_DIR = ROOT / ".run"
LOG_DIR = ROOT / ".logs"
RUN_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# Lazy-loaded after env is read
def _load_env() -> dict:
    env_path = ROOT / ".env"
    out: dict[str, str] = dict(os.environ)
    if env_path.exists():
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out.setdefault(k.strip(), v.strip())
    return out


ENV = _load_env()
INDEXER_URL = ENV.get("INDEXER_URL", "http://127.0.0.1:7401")
CORE_URL = f"http://{ENV.get('API_HOST', '127.0.0.1')}:{ENV.get('API_PORT', '7400')}"
NEO4J_BROWSER = "http://localhost:7474"
PROJECTS = sorted({k[len("PROJECT_"):].lower() for k in ENV if k.startswith("PROJECT_") and ENV[k].strip()})

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


# ──────────────────────────────────────────────────────────────────────────
# Service primitives
# ──────────────────────────────────────────────────────────────────────────

class Service:
    """Local subprocess service tracked by PID file."""

    def __init__(self, name: str, *, cmd: list[str], cwd: Path, url: str, health_path: str = "/health"):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.url = url
        self.health_path = health_path
        self.pid_file = RUN_DIR / f"{name}.pid"
        self.log_file = LOG_DIR / f"{name}.log"

    # — process state —

    def pid(self) -> int | None:
        if not self.pid_file.exists():
            return None
        try:
            return int(self.pid_file.read_text().strip())
        except ValueError:
            return None

    def alive(self) -> bool:
        pid = self.pid()
        if not pid:
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            self.pid_file.unlink(missing_ok=True)
            return False

    def healthy(self) -> bool:
        try:
            r = httpx.get(f"{self.url}{self.health_path}", timeout=1.0)
            return r.status_code == 200
        except Exception:
            return False

    # — actions —

    def start(self) -> None:
        if self.alive():
            return
        log_fh = open(self.log_file, "ab", buffering=0)
        proc = subprocess.Popen(
            self.cmd,
            cwd=self.cwd,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=ENV,
        )
        self.pid_file.write_text(str(proc.pid))

    def stop(self) -> None:
        pid = self.pid()
        if not pid:
            return
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        for _ in range(30):
            time.sleep(0.1)
            if not self.alive():
                break
        else:
            try:
                os.killpg(os.getpgid(pid), signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.pid_file.unlink(missing_ok=True)

    def wait_healthy(self, timeout: float = 30.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.healthy():
                return True
            time.sleep(0.5)
        return False


INDEXER = Service(
    "indexer",
    cmd=["pnpm", "start"],
    cwd=ROOT / "indexer",
    url=INDEXER_URL,
)

CORE = Service(
    "core",
    cmd=["uv", "run", "uvicorn", "kg.server:app", "--host", ENV.get("API_HOST", "127.0.0.1"), "--port", ENV.get("API_PORT", "7400")],
    cwd=ROOT,
    url=CORE_URL,
)


# ──────────────────────────────────────────────────────────────────────────
# Docker (Neo4j)
# ──────────────────────────────────────────────────────────────────────────

def neo4j_state() -> str:
    """Returns 'running', 'starting', 'stopped' or 'unhealthy'."""
    try:
        r = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}", "kg-neo4j"],
            capture_output=True, text=True, timeout=3,
        )
        if r.returncode != 0:
            return "stopped"
        status, _, health = r.stdout.strip().partition("|")
        if status != "running":
            return status
        if health in ("", "healthy"):
            try:
                httpx.get(NEO4J_BROWSER, timeout=1.0)
                return "running"
            except Exception:
                return "starting"
        return health  # starting / unhealthy
    except Exception:
        return "stopped"


def neo4j_up() -> None:
    subprocess.run(["docker", "compose", "up", "-d"], cwd=ROOT, check=True)
    for _ in range(60):
        if neo4j_state() == "running":
            return
        time.sleep(1)


def neo4j_down() -> None:
    subprocess.run(["docker", "compose", "down"], cwd=ROOT, check=True)


# ──────────────────────────────────────────────────────────────────────────
# UI
# ──────────────────────────────────────────────────────────────────────────

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

    table.add_row("Neo4j",    _dot(neo) + Text(f" {neo:<9}"), NEO4J_BROWSER,        "browser")
    table.add_row("Indexer",  _dot(idx_state) + Text(f" {idx_state:<9}"), INDEXER_URL, f"pid={INDEXER.pid() or '—'}")
    table.add_row("Core API", _dot(core_state) + Text(f" {core_state:<9}"), CORE_URL,  f"pid={CORE.pid() or '—'}")
    table.add_row()
    table.add_row("Projects", Text(", ".join(PROJECTS) or "— none —", style="cyan"))

    return Panel(table, title="[bold]knowledge-graph[/bold]", border_style="cyan", padding=(1, 2))


def show_status() -> None:
    console.clear()
    console.print(render_status())


# ──────────────────────────────────────────────────────────────────────────
# Actions
# ──────────────────────────────────────────────────────────────────────────

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
    console.print(f"[{'green' if ok else 'red'}]{'✓' if ok else '✗'}[/] indexer {'ready' if ok else 'failed'} — see {INDEXER.log_file}")

    with console.status("[cyan]Starting core API…", spinner="dots"):
        CORE.start()
        ok = CORE.wait_healthy(15)
    console.print(f"[{'green' if ok else 'red'}]{'✓' if ok else '✗'}[/] core {'ready' if ok else 'failed'} — see {CORE.log_file}")


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
            choices=PROJECTS + ["(all)"],
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


# ──────────────────────────────────────────────────────────────────────────
# Main loop
# ──────────────────────────────────────────────────────────────────────────

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
                    if label not in ("Refresh status",):
                        questionary.press_any_key_to_continue(style=MENU_STYLE).ask()
                break


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        console.print("\n[dim]bye[/dim]")
