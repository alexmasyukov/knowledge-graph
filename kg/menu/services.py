"""Local services managed by the control panel: Neo4j (Docker) + two
long-running processes (indexer, core API) tracked via PID files."""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

from .env import CORE_URL, ENV, INDEXER_URL, LOG_DIR, NEO4J_BROWSER, ROOT, RUN_DIR


def _port_owner(port: int) -> int | None:
    """Return PID of the process listening on the given local port, or None."""
    try:
        out = subprocess.run(
            ["lsof", "-ti", f":{port}"],
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
    except Exception:
        return None
    if not out:
        return None
    # lsof may return several PIDs (parent + child); take the first.
    return int(out.splitlines()[0])


def _port_free(port: int) -> bool:
    """True if no socket is listening on (127.0.0.1, port)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        try:
            s.connect(("127.0.0.1", port))
            return False
        except (ConnectionRefusedError, OSError):
            return True


class PortBusy(RuntimeError):
    """Raised when a service's port is held by a foreign process."""

    def __init__(self, service: str, port: int, blocker_pid: int):
        self.service = service
        self.port = port
        self.blocker_pid = blocker_pid
        super().__init__(
            f"{service}: port {port} is busy (held by PID {blocker_pid}). "
            f"Kill it with `kill {blocker_pid}` (or `kill -9 {blocker_pid}`) and retry."
        )


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

    @property
    def port(self) -> int:
        return urlparse(self.url).port or 0

    def port_blocker(self) -> int | None:
        """If our port is taken by a process we don't own, return its PID.
        Returns None when the port is free or already owned by us."""
        my_pid = self.pid()
        owner = _port_owner(self.port)
        if owner is None:
            return None
        if my_pid and owner == my_pid:
            return None
        return owner

    def start(self) -> None:
        """Start the service. Raises PortBusy if the port is held by a
        foreign process so the menu can surface a useful message
        instead of a silent crash."""
        if self.alive() and not _port_free(self.port):
            return  # already running and listening
        blocker = self.port_blocker()
        if blocker is not None:
            raise PortBusy(self.name, self.port, blocker)
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
