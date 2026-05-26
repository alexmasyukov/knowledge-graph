"""Reindex endpoint — runs the full pipeline for a configured project."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, Query

from ..indexers.ingest import reindex as run_reindex
from ..schema import ReindexResponse
from ..settings import settings

router = APIRouter()


@router.post("/reindex", response_model=ReindexResponse)
async def reindex(project: str = Query(...)) -> dict:
    cfg = settings.project(project)
    if cfg is None:
        raise HTTPException(404, f"project not configured: {project!r}")
    # The pipeline shells out to scip-typescript and runs heavy
    # Cypher MERGE batches — keep it off the event loop.
    return await asyncio.to_thread(run_reindex, cfg)
