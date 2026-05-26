# knowledge-graph

Code intelligence over Arenadata frontend projects (`adsw`, `network`)
built on **scip-typescript**, **tree-sitter** and **Memgraph**. Serves
a REST API consumed by the `arenadata-docs` MCP server (chat side) and
a built-in cytoscape graph viewer.

## Why

Open an unfamiliar page like `/services/education/booking`. Without a
graph you'd usually:

1. Grep `router/index.tsx` for the path
2. Open the lazy-imported component (`Bookings`)
3. Find the hook it calls (`useBookings`)
4. Open the hook, find the GraphQL operation (`GET_EDUCATION_BOOKINGS`)
5. Pull up its schema definition
6. Find the e2e tests that exercise it
7. Check what permission key guards the route

Seven files, seven greps. With the graph it's one tool call:

```
routes_resolve /services/education/booking
→ guards:      RouterGuard
→ permissions: education.booking.read  (ADMIN, EDUCATION_HEAD, EDUCATION_MANAGER)
→ components:  Bookings @ src/pages/education/booking/Bookings.tsx:43
→ hooks:       useBookings
→ operations:  [query] GET_EDUCATION_BOOKINGS  (GetEducationBookings)
```

The cross-file resolution is **type-aware** — backed by SCIP, the same
indexer format Sourcegraph uses for its TypeScript code intelligence.
Not regex over text, real `Find References`.

## Stack

| Component | Where | Port |
|---|---|---|
| Memgraph 3.6 + Lab | Docker | 7687 (Bolt) / 7444 (HTTP) / 3000 (Lab) |
| FastAPI core | local (uv) | 7400 |
| scip-typescript indexer | Node binary | n/a (subprocess) |
| MCP wrapper | `arenadata-mcp` repo | external |

The MCP wrapper is what chat models call — it lives in another repo and
speaks HTTP to the URLs documented below. Keep those URL shapes stable
across rewrites; everything else is internal.

## Quick start

Requires: Python 3.12+, Node 20+, pnpm, Docker, [uv](https://docs.astral.sh/uv/).

```bash
# 1. Pin the SCIP TypeScript indexer
cd scip-indexer && pnpm install && cd ..

# 2. Bring up Memgraph
docker compose up -d memgraph

# 3. Configure projects (one PROJECT_<NAME> entry per indexed package)
cp .env.example .env
$EDITOR .env

# 4. Run the API
uv run uvicorn kg.server:app --host 127.0.0.1 --port 7400

# 5. Index a project
curl -X POST 'http://127.0.0.1:7400/reindex?project=adsw'
```

## Pipeline

```
        scip-typescript          tree-sitter
            │                       │
            ▼                       ▼
       .scip file        ┌──────────────────────┐
            │            │ routes / permissions │
            ▼            │ pages / e2e / scss   │
     ┌──────────────┐    │ docs / types        │
     │ ScipIndex    │◄───┘                      │
     │ extractors   │  ─►   Memgraph (Bolt)
     └──────────────┘
            │
            ▼
       FastAPI ──► MCP wrapper ──► chat tools
            │
            └──────► /viz (cytoscape) ──► browser
```

Each extractor is a `kg/indexers/<name>.py` module that exposes an
`EXTRACTOR` singleton conforming to the `Extractor` Protocol.

## What's indexed

| Extractor | Source | Nodes / edges (adsw) |
|---|---|---|
| `gql` | SCIP refs in `src/gql/**` | 202 GqlOperation, 93 GqlHook |
| `routes` | tree-sitter on `src/router/index.tsx` | 101 Route, 61 Component, 2 Guard, 30 Permission |
| `permissions` | tree-sitter on `src/common/permissions/index.ts` | 135 Permission, 7 Role |
| `pages` | filesystem walk of `src/pages/<domain>/<entity>/` | 56 Page, 95 Component |
| `e2e` | tree-sitter on `src/**/*.tsx` + `playwright/tests/` | 187 TestId, 3 E2eSpec |
| `scss` | tree-sitter on `*.module.scss` | 22 ScssModule, 67 ScssClass |
| `docs` | tree-sitter on README/CLAUDE/mcp/docs `.md` | per repo |
| `types` | tree-sitter on `src/types/**/*.ts` + SCIP refs | 114 Type, 1370 USES_TYPE |

## HTTP API

```
POST  /reindex?project=<name>
GET   /health

GET   /gql/operations[?kind&name&limit]
GET   /gql/hooks/{name}?project
GET   /gql/callsites?project&target

GET   /routes/list[?prefix&limit]
GET   /routes/resolve?project&path
GET   /routes/by-component?project&name

GET   /permissions/list[?prefix&role]
GET   /permissions/info/{key}?project

GET   /pages/list[?domain]
GET   /pages/get?project&domain&entity

GET   /e2e/specs[?name]
GET   /e2e/testid/{value}?project
GET   /e2e/coverage?project
GET   /e2e/uncovered[?group_by_file]

GET   /scss/list[?class_name]
GET   /scss/class/{name}?project

GET   /docs/list?project
GET   /docs/search?project&q
GET   /docs/get/{name}?project

GET   /types/list[?kind&name]
GET   /types/get/{name}?project
GET   /types/search?project&q

GET   /sanity?project           — data-quality probes

GET   /viz/                     — cytoscape graph viewer
GET   /viz/graph?cypher=…       — JSON elements payload
```

## Viz

`http://127.0.0.1:7400/viz/` opens a cytoscape page wired to the
`/viz/graph?cypher=...` endpoint. Memgraph syntax (`id(n)` not
`elementId(n)`), read-only queries only.

A quick demo query:

```
MATCH (r:Route {project:'adsw', path:'/services/education/booking'})
OPTIONAL MATCH (r)-[*1..2]-(n)
RETURN r, n
```

## Sanity probes

`GET /sanity?project=<name>` runs every probe in `kg/api/sanity.py`
and returns a single report. Useful right after `/reindex` to spot
structural anomalies — duplicate hook names, orphan components,
template substrings that leaked into stored values, unused permissions.
Probes don't enforce anything; they surface unknowns.

## Tests

`uv run pytest tests/` — 11 tests, ~50 ms suite. Pure-parse extractors
run against a synthetic tmp_path workspace; API tests seed Memgraph
directly via Cypher and use a dedicated `kgtest` project name so the
live adsw graph stays untouched.

## Live mode (watcher)

```bash
uv run python -m kg.watcher adsw
```

Watches `<project.code_root>/src/` and triggers a debounced full
reindex on TS/TSX/SCSS/MD edits. SCIP runs from scratch every time
(scip-typescript has no per-file mode), but the API stays available
between runs.

A faster `since=` short-circuit also exists:

```bash
curl -X POST 'http://127.0.0.1:7400/reindex?project=adsw&since=HEAD'
```

— returns immediately when git reports no changes under the project's
code_root since the given ref.

## Optional: Sourcegraph self-hosted

```bash
docker compose --profile sourcegraph up -d sourcegraph
# Open http://localhost:7080, create admin, add a code host.

# Then upload the .scip on every reindex:
SRC_ACCESS_TOKEN=<token> scripts/upload-scip.sh adsw
```

Gives you a richer code-search UI backed by the same SCIP index the
graph uses internally. Not required for the MCP contract.

## Branch layout

- `master` — the previous ts-morph + Neo4j stack (still serves the
  live MCP wrapper if a rollback is ever needed).
- `experiment/pro-stack` — current development. SCIP + tree-sitter +
  Memgraph. Same HTTP contract.
