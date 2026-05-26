from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api import docs, e2e, errors, gql, health, pages, permissions, reindex, routes, scss, viz
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
errors.install(app)

app.include_router(health.router, tags=["meta"])
app.include_router(reindex.router, tags=["ingest"])
app.include_router(gql.router)
app.include_router(routes.router)
app.include_router(docs.router)
app.include_router(permissions.router)
app.include_router(pages.router)
app.include_router(e2e.router)
app.include_router(scss.router)
app.include_router(viz.router)


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
