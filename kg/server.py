"""FastAPI entry point. Register routers from kg.api here as they
become available."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import docs, e2e, gql, health, pages, permissions, reindex, routes, scss
from .db import close_driver, init_schema
from .settings import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_schema()
    yield
    close_driver()


app = FastAPI(
    title="knowledge-graph",
    version="0.1.0",
    description="Code intelligence over Memgraph + SCIP",
    lifespan=lifespan,
)

app.include_router(health.router, tags=["meta"])
app.include_router(reindex.router, tags=["ingest"])
app.include_router(gql.router)
app.include_router(routes.router)
app.include_router(permissions.router)
app.include_router(pages.router)
app.include_router(e2e.router)
app.include_router(scss.router)
app.include_router(docs.router)


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
