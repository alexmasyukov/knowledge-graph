from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import gql, health, reindex, routes_api, docs, permissions_api, pages_api, e2e_api, scss_api
from .db import close_driver, init_schema
from .http import close as close_http
from .logger import setup as setup_logging
from .settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_schema()
    yield
    await close_http()
    close_driver()


app = FastAPI(
    title="knowledge-graph",
    version="0.0.1",
    description="Code intelligence knowledge graph",
    lifespan=lifespan,
)

app.include_router(health.router, tags=["meta"])
app.include_router(reindex.router, tags=["ingest"])
app.include_router(gql.router)
app.include_router(routes_api.router)
app.include_router(docs.router)
app.include_router(permissions_api.router)
app.include_router(pages_api.router)
app.include_router(e2e_api.router)
app.include_router(scss_api.router)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "kg.server:app",
        host=settings.api_host,
        port=settings.api_port,
        reload=False,
    )


if __name__ == "__main__":
    main()
