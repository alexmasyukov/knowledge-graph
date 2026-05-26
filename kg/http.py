"""Module-level shared httpx client for talking to the ts-morph indexer."""

from __future__ import annotations

import httpx

from .settings import settings

_client: httpx.AsyncClient | None = None


def client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(base_url=settings.indexer_url, timeout=300.0)
    return _client


async def close() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
