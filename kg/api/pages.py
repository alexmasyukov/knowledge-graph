"""Pages endpoints (Phase 3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session
from ._models import (
    DomainsListResponse,
    PageGetResponse,
    PagesListResponse,
)

router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("/list", response_model=PagesListResponse)
async def list_pages(
    project: str,
    domain: str | None = Query(default=None, description="filter by domain"),
) -> dict:
    cypher = """
        MATCH (pg:Page {project: $project})
        WHERE ($domain IS NULL OR pg.domain = $domain)
        OPTIONAL MATCH (co:Component)-[:BELONGS_TO]->(pg)
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(co)
        WITH pg, collect(DISTINCT co.name) AS components, collect(DISTINCT r.path) AS routes
        RETURN pg.domain AS domain, pg.entity AS entity, pg.dir AS dir,
               pg.files AS files, pg.subdirs AS subdirs,
               components, routes
        ORDER BY pg.domain, pg.entity
    """
    with session() as s:
        rows = s.run(cypher, project=project, domain=domain).data()
    return {"project": project, "count": len(rows), "pages": rows}


@router.get("/get", response_model=PageGetResponse)
async def get_page(project: str, domain: str, entity: str = "") -> dict:
    # Hooks and operations defined *anywhere* under the page directory
    # are part of the page — even if they live in Form/hooks/ or
    # VnspForm/components/. Without this, pages_get for education/booking
    # only sees GET_EDUCATION_BOOKINGS (used by Bookings.tsx) and misses
    # the 3 booking mutations defined in Form/hooks/useBookingMutation.ts
    # and the VNSP mutation. We pick those up via path-prefix on pg.dir.
    cypher = """
        MATCH (pg:Page {project: $project, domain: $domain, entity: $entity})
        WITH pg, pg.dir + '/' AS dir_prefix
        OPTIONAL MATCH (co:Component)-[:BELONGS_TO]->(pg)
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(co)

        // Hooks come from two places — wrappers called by page components,
        // and any hook defined under the page directory (Form/hooks/, etc).
        OPTIONAL MATCH (co)-[:CALLS_HOOK]->(h1:GqlHook)
        OPTIONAL MATCH (h2:GqlHook {project: $project})
          WHERE h2.file STARTS WITH dir_prefix
        OPTIONAL MATCH (h1)-[:WRAPS]->(opH1:GqlOperation)
        OPTIONAL MATCH (h2)-[:WRAPS]->(opH2:GqlOperation)

        // Direct gql callsites — useMutation(OP) without a wrapper.
        OPTIONAL MATCH (co)-[:USES_OPERATION]->(o1:GqlOperation)
        OPTIONAL MATCH (src_f:File {project: $project})-[:USES_OPERATION]->(o2:GqlOperation)
          WHERE src_f.path STARTS WITH dir_prefix
        OPTIONAL MATCH (src_c:Component {project: $project})-[:USES_OPERATION]->(o3:GqlOperation)
          WHERE src_c.file STARTS WITH dir_prefix

        WITH pg,
             collect(DISTINCT {name: co.name, file: co.file, line: co.line}) AS components,
             collect(DISTINCT r.path) AS routes,
             collect(DISTINCT h1.name) + collect(DISTINCT h2.name) AS hooks_raw,
             collect(DISTINCT {symbol: o1.symbol, kind: o1.kind, gql_name: o1.gql_name}) +
             collect(DISTINCT {symbol: o2.symbol, kind: o2.kind, gql_name: o2.gql_name}) +
             collect(DISTINCT {symbol: o3.symbol, kind: o3.kind, gql_name: o3.gql_name}) AS ops_direct_raw,
             collect(DISTINCT {symbol: opH1.symbol, kind: opH1.kind, gql_name: opH1.gql_name}) +
             collect(DISTINCT {symbol: opH2.symbol, kind: opH2.kind, gql_name: opH2.gql_name}) AS ops_via_hooks_raw
        RETURN
          {domain: pg.domain, entity: pg.entity, dir: pg.dir, files: pg.files, subdirs: pg.subdirs} AS page,
          [c IN components WHERE c.name IS NOT NULL] AS components,
          [r IN routes WHERE r IS NOT NULL] AS routes,
          [h IN hooks_raw WHERE h IS NOT NULL] AS hooks,
          [o IN ops_direct_raw WHERE o.symbol IS NOT NULL] AS ops_direct,
          [o IN ops_via_hooks_raw WHERE o.symbol IS NOT NULL] AS ops_via_hooks
    """
    with session() as s:
        rec = s.run(cypher, project=project, domain=domain, entity=entity).single()
    if not rec or rec["page"] is None:
        raise HTTPException(404, f"page not found: {domain}/{entity} (project={project})")
    return {"project": project, **rec.data()}


@router.get("/domains", response_model=DomainsListResponse)
async def list_domains(project: str) -> dict:
    cypher = """
        MATCH (d:Domain {project: $project})
        OPTIONAL MATCH (pg:Page)-[:IN_DOMAIN]->(d)
        RETURN d.name AS name, count(DISTINCT pg) AS pages
        ORDER BY d.name
    """
    with session() as s:
        rows = s.run(cypher, project=project).data()
    return {"project": project, "count": len(rows), "domains": rows}
