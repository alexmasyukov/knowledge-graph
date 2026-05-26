from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx
import typer

from .settings import settings


app = typer.Typer(no_args_is_help=True, help="knowledge-graph control CLI")

ROOT = Path(__file__).resolve().parent.parent


def _sh(cmd: list[str], cwd: Path | None = None, check: bool = True) -> int:
    proc = subprocess.run(cmd, cwd=cwd or ROOT, check=check)
    return proc.returncode


@app.command()
def up() -> None:
    """docker compose up -d (Neo4j)."""
    _sh(["docker", "compose", "up", "-d"])
    typer.echo("Waiting for Neo4j to become healthy…")
    for _ in range(60):
        try:
            httpx.get("http://localhost:7474", timeout=1.0)
            typer.echo("Neo4j ready: http://localhost:7474")
            return
        except Exception:
            time.sleep(1)
    typer.echo("Neo4j did not become ready in 60s", err=True)
    raise typer.Exit(1)


@app.command()
def down() -> None:
    """docker compose down."""
    _sh(["docker", "compose", "down"])


@app.command()
def indexer() -> None:
    """Run the ts-morph indexer (Fastify :7401) in foreground."""
    _sh(["pnpm", "start"], cwd=ROOT / "indexer")


@app.command()
def api() -> None:
    """Run the FastAPI core (:7400) in foreground."""
    import uvicorn

    uvicorn.run(
        "kg.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


@app.command()
def status() -> None:
    """Hit /health on the core API."""
    try:
        r = httpx.get(f"http://{settings.api_host}:{settings.api_port}/health", timeout=3.0)
        typer.echo(r.text)
    except Exception as e:
        typer.echo(f"core down: {e}", err=True)
        raise typer.Exit(1)


@app.command()
def reindex(project: str | None = None) -> None:
    """Trigger reindex via the core API."""
    params = {"project": project} if project else None
    r = httpx.post(
        f"http://{settings.api_host}:{settings.api_port}/reindex",
        params=params,
        timeout=120.0,
    )
    typer.echo(r.text)


@app.command()
def install() -> None:
    """Install indexer node deps + python deps via uv."""
    _sh(["pnpm", "install"], cwd=ROOT / "indexer")
    _sh(["uv", "sync"])


if __name__ == "__main__":
    app()
