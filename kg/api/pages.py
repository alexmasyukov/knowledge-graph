"""Pages read API.

Endpoints:
  GET /pages/list?project[&domain]   list pages, optionally per domain
  GET /pages/get?project&domain&entity  full picture for one page

Response shapes match the MCP wrapper.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from ..db import session

router = APIRouter(prefix="/pages", tags=["pages"])


@router.get("/list")
async def list_pages(
    project: str,
    domain: str | None = Query(default=None, description="filter to one domain (e.g. 'education')"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    where = ["p.project = $project"]
    if domain:
        where.append("p.domain = $domain")
    cypher = f"""
        MATCH (p:Page)
        WHERE {' AND '.join(where)}
        OPTIONAL MATCH (c:Component)-[:BELONGS_TO]->(p)
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(c)
        WITH p,
             collect(DISTINCT c.name) AS components,
             collect(DISTINCT r.path) AS routes
        RETURN p.domain AS domain, p.entity AS entity, p.dir AS dir,
               p.files AS files, p.subdirs AS subdirs,
               components, routes
        ORDER BY p.domain, p.entity
        LIMIT $limit
    """
    with session() as s:
        rows = s.run(cypher, project=project, domain=domain, limit=limit).data()

    pages = []
    for r in rows:
        pages.append({
            "domain": r["domain"],
            "entity": r["entity"],
            "dir": r["dir"],
            "files": list(r["files"] or []),
            "subdirs": list(r["subdirs"] or []),
            "components": sorted([x for x in (r["components"] or []) if x]),
            "routes": sorted([x for x in (r["routes"] or []) if x]),
        })
    return {"project": project, "count": len(pages), "pages": pages}


@router.get("/get")
async def get_page(project: str, domain: str, entity: str) -> dict:
    """Full picture for one page: components, routes that render them,
    every GqlHook + GqlOperation defined or referenced anywhere under
    the page directory."""
    cypher = """
        MATCH (p:Page {project: $project, domain: $domain, entity: $entity})
        OPTIONAL MATCH (c:Component)-[:BELONGS_TO]->(p)
        OPTIONAL MATCH (r:Route)-[:RENDERS]->(c)
        // Hooks called from any component under this page.
        OPTIONAL MATCH (c)-[:CALLS_HOOK]->(h:GqlHook)
        // Hooks defined under the page dir (covers Form/, components/, …).
        OPTIONAL MATCH (hPage:GqlHook {project: $project})
          WHERE hPage.file STARTS WITH p.dir
        // Operations: direct, via hooks, and any file under the page dir.
        OPTIONAL MATCH (c)-[:USES_OPERATION]->(opDirect:GqlOperation)
        OPTIONAL MATCH (h)-[:WRAPS]->(opViaHook:GqlOperation)
        OPTIONAL MATCH (hPage)-[:WRAPS]->(opPageHook:GqlOperation)
        OPTIONAL MATCH (fSub:File {project: $project})-[:USES_OPERATION]->(opPage:GqlOperation)
          WHERE fSub.path STARTS WITH p.dir
        RETURN
          p.dir AS dir, p.files AS files, p.subdirs AS subdirs,
          collect(DISTINCT {name: c.name, file: c.file, line: c.line}) AS components,
          collect(DISTINCT r.path) AS routes,
          collect(DISTINCT h.name) + collect(DISTINCT hPage.name) AS hooks,
          collect(DISTINCT {symbol: opDirect.symbol,    gql_name: opDirect.gql_name,    kind: opDirect.kind}) +
          collect(DISTINCT {symbol: opViaHook.symbol,   gql_name: opViaHook.gql_name,   kind: opViaHook.kind}) +
          collect(DISTINCT {symbol: opPageHook.symbol,  gql_name: opPageHook.gql_name,  kind: opPageHook.kind}) +
          collect(DISTINCT {symbol: opPage.symbol,      gql_name: opPage.gql_name,      kind: opPage.kind})
            AS operations
    """
    with session() as s:
        rec = s.run(cypher, project=project, domain=domain, entity=entity).single()
    if rec is None or rec["dir"] is None:
        raise HTTPException(404, f"page not found: {domain}/{entity} (project={project})")
    data = rec.data()

    def _dedupe_by(items, key):
        seen, out = set(), []
        for item in items or []:
            v = item.get(key) if isinstance(item, dict) else item
            if not v or v in seen:
                continue
            seen.add(v)
            out.append(item)
        return out

    return {
        "project": project,
        "domain": domain,
        "entity": entity,
        "dir": data["dir"],
        "files": list(data["files"] or []),
        "subdirs": list(data["subdirs"] or []),
        "components": _dedupe_by(data["components"], "name"),
        "routes": sorted({x for x in (data["routes"] or []) if x}),
        "hooks": sorted({x for x in (data["hooks"] or []) if x}),
        "operations": _dedupe_by(data["operations"], "symbol"),
    }
