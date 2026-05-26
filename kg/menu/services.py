"""Local services managed by the control panel: Neo4j (Docker) + two
long-running processes (indexer, core API) tracked via PID files."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import httpx

from .env import CORE_URL, ENV, INDEXER_URL, LOG_DIR, NEO4J_BROWSER, ROOT, RUN_DIR


class Service:
    """Local subprocess service tracked by PID file. Survives between
    menu sessions because we start it in a new process group."""

    def __init__(
        self,
        name: str,
        *,
        cmd: list[str],
        cwd: Path,
        url: str,
        health_path: str = "/health",
    ):
        self.name = name
        self.cmd = cmd
        self.cwd = cwd
        self.url = url
        self.health_path = health_path
        self.pid_file = RUN_DIR / f"{name}.pid"
        self.log_file = LOG_DIR / f"{name}.log"

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

    def start(self) -> None:
        if self.alive():
            return
        # The subprocess keeps the file descriptor open for its lifetime —
        # we deliberately don't use a context manager here.
        log_fh = open(self.log_file, "ab", buffering=0)  # noqa: SIM115
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
    cmd=[
        "uv",
        "run",
        "uvicorn",
        "kg.server:app",
        "--host",
        ENV.get("API_HOST", "127.0.0.1"),
        "--port",
        ENV.get("API_PORT", "7400"),
    ],
    cwd=ROOT,
    url=CORE_URL,
)


# ── Neo4j (Docker) ───────────────────────────────────────────────────


def neo4j_state() -> str:
    """Returns 'running', 'starting', 'stopped' or 'unhealthy'."""
    try:
        r = subprocess.run(
            [
                "docker",
                "inspect",
                "-f",
                "{{.State.Status}}|{{if .State.Health}}{{.State.Health.Status}}{{end}}",
                "kg-neo4j",
            ],
            capture_output=True,
            text=True,
            timeout=3,
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
        return health
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
