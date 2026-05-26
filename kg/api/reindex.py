"""Reindex endpoint — runs the full pipeline for a configured project.

Supports a `since=<git-ref>` short-circuit: when set, we ask git for
the list of files changed between that ref and HEAD; if nothing under
the project's code_root changed we return immediately without burning
a SCIP run.
"""

from __future__ import annotations

import asyncio
import subprocess

from fastapi import APIRouter, HTTPException, Query

from ..indexers.ingest import reindex as run_reindex
from ..schema import ReindexResponse
from ..settings import ProjectConfig, settings

router = APIRouter()


def _changed_paths(project: ProjectConfig, since: str) -> list[str] | None:
    """Return repo-relative paths changed between `since` and the
    working tree (HEAD + uncommitted edits). Returns None on git error
    so the caller can fall back to a full reindex."""
    try:
        committed = subprocess.run(
            ["git", "-C", str(project.repo_root), "diff", "--name-only", since],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.splitlines()
        unstaged = subprocess.run(
            ["git", "-C", str(project.repo_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.splitlines()
    except (subprocess.SubprocessError, FileNotFoundError):
        return None
    paths = set(p.strip() for p in committed if p.strip())
    for line in unstaged:
        # `git status --porcelain` lines: " M path", "?? path", etc.
        if len(line) > 3:
            paths.add(line[3:].strip())
    return sorted(paths)


def _affects_project(paths: list[str], project: ProjectConfig) -> list[str]:
    try:
        rel_root = str(project.code_root.relative_to(project.repo_root))
    except ValueError:
        rel_root = ""
    prefix = rel_root + "/" if rel_root else ""
    return [p for p in paths if not prefix or p.startswith(prefix)]


@router.post("/reindex", response_model=ReindexResponse)
async def reindex(
    project: str = Query(...),
    since: str | None = Query(default=None, description="git ref — skip reindex if no relevant changes since"),
) -> dict:
    cfg = settings.project(project)
    if cfg is None:
        raise HTTPException(404, f"project not configured: {project!r}")

    if since:
        changed = _changed_paths(cfg, since)
        if changed is not None:
            relevant = _affects_project(changed, cfg)
            if not relevant:
                return {
                    "ok": True,
                    "project": project,
                    "scip_duration_ms": 0,
                    "total_duration_ms": 0,
                    "extractors": [],
                }

    # The pipeline shells out to scip-typescript and runs heavy Cypher
    # MERGE batches — keep it off the event loop.
    return await asyncio.to_thread(run_reindex, cfg)
