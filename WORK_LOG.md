# Pro-stack rewrite — work log

Branch: `experiment/pro-stack` (in this repo and in `arenadata-adsw`).
Master is untouched and keeps the working ts-morph + Neo4j stack.

## Goal

Replace the legacy stack with a professional foundation:

1. **scip-typescript** instead of ts-morph custom extractors — point of
   truth for cross-file references is the upstream indexer Sourcegraph
   maintains.
2. **Memgraph** instead of Neo4j Community — drop-in Bolt-compatible
   replacement, faster, no licence ceiling.
3. **Sourcegraph self-hosted** for code search/refs UI (optional layer
   on top).
4. **Incremental indexing** by file diff (watchdog/git diff).
5. **Tree-sitter** for SCSS / Markdown / e2e parsing instead of regex.

Plus a non-negotiable: keep the same HTTP contract that the MCP wrapper
(`arenadata-mcp/tools/kg.py`) already speaks, so the chat side keeps
working unchanged.

## Done

### Foundation (commits `adddfac`, `d03ed47`, `b0c4ab8`, `5cfbb9b`)

- `scip-indexer/` — pinned `@sourcegraph/scip-typescript@0.4.0`,
  generated `scip_pb2.py` from `scip.proto`, `reader.py` groups
  occurrences into `SymbolInfo` (defs + refs), `extract_gql.py` is a
  standalone smoke test.
- `docker-compose.yml` — only Memgraph 3.6 + Memgraph Lab on
  `http://localhost:3000`. No Neo4j.
- `pyproject.toml` — dropped typer/questionary/watchdog. Added
  `tree-sitter`, `tree-sitter-language-pack`, `pytest`, `pytest-asyncio`.
- `kg/settings.py` — MEMGRAPH_URI/USER/PASSWORD + PROJECT_<NAME>
  entries. Memgraph runs without auth by default.
- `kg/db.py` — driver factory, idempotent `init_schema()` that reads
  `SHOW INDEX INFO` / `SHOW CONSTRAINT INFO` to skip already-present
  objects (instead of swallowing exceptions).
- `kg/schema.py` — Pydantic response contracts. Source of truth for
  every API URL — matches what the MCP wrapper currently sends/expects.
- `kg/writers/graph.py` — batched MERGE for nodes (via `NODE_KEYS`
  registry) and edges (grouped per `(source_label, type, target_label)`
  triple, parameterised relationship type isn't allowed in Memgraph
  Cypher).
- `kg/indexers/scip_runner.py` — runs `scip-typescript index` against
  a project (auto-detects pnpm workspaces) and returns a `ScipRunResult`.
- `kg/indexers/scip_loader.py` — production-grade SCIP reader. Same
  semantics as `scip-indexer/reader.py` but exposed as the main path.
- `kg/indexers/base.py` — `Extractor` Protocol, `IndexerContext`,
  `IngestResult`.
- `kg/indexers/ingest.py` — orchestrator: wipe → SCIP → every extractor
  → batched writes → `attach_to_project`.
- `kg/api/health.py`, `kg/api/reindex.py`, `kg/server.py`.

### First extractor + API (commits `b0c4ab8`, `5cfbb9b`)

- `kg/indexers/gql.py` — extracts GqlOperation + GqlHook + File nodes
  and WRAPS / CALLS_HOOK / USES_OPERATION edges directly from SCIP
  refs. Adds an lru-cached regex pass over each operation file to
  capture `kind` and `gql_name` from the tagged-template declaration.
- `kg/api/gql.py` — `GET /gql/operations`, `GET /gql/hooks/{name}`,
  `GET /gql/callsites` with the same URL paths the MCP wrapper already
  calls. `hook_info` returns `definitions[]` so a duplicated hook name
  (the legacy/new useCourses case) shows both files.

### Numbers on adsw

- scip-typescript: **5.7 s** for 941 documents
- gql extractor: **0.13 s**, output → **202 GqlOperation, 93 GqlHook,
  268 File, 431 edges**
- 169/202 operations carry `kind` + `gql_name`; the remaining 33 are
  Apollo's keyword-less `gql\`{ ... }\`` form (still valid queries)
- `/gql/operations?project=adsw&kind=mutation&name=booking` → returns
  the two real mutations with their gql names

## To do

Sticking to the same contracts as the live MCP wrapper. Each item is
an extractor + an API module + ideally a test.

### Extractors

| extractor | replaces | notes |
|---|---|---|
| `routes` | ts-morph routes/* | needs JSX tree walking for `router/index.tsx`. Use SCIP for component imports + tree-sitter for the `<Route>` tree. Must resolve `:id/${PageMode.EDIT}` like the old TS code did. |
| `permissions` | ts-morph permissions | scan `src/common/permissions/index.ts` for nested object literal. Roles + routes that reference each key. |
| `pages` | filesystem-only extractor | walk `src/pages/<domain>/<entity>/` and emit Page nodes. BELONGS_TO via file path (the earlier name-vs-name bug). |
| `e2e` | regex over `.spec.ts`/`.locators.ts`/`.page.ts` | rewrite with tree-sitter-typescript. Preserve template-vs-literal testid distinction we landed on master. |
| `scss` | regex over `.module.scss` | tree-sitter-scss (or postcss subprocess). Pick up `.classes` declarations and consumers. |
| `docs` | regex over markdown | tree-sitter-markdown for headings/title; we need both `CLAUDE/` and root README.md/CLAUDE.md. |
| `types` | nothing on master | new: SCIP gives us TypeAlias / Interface / Enum symbols via the `:typeAlias` / `:interface` / `:enum` suffix in scip-typescript. Use it for `types_*` MCP tools. |
| `sanity` | n/a | not an extractor — a post-ingest probe. See `arenadata-adsw/mcp/data-quality-probes.md`. |

### API endpoints to bring across

URL paths the MCP wrapper calls (`arenadata-mcp/tools/kg.py`):

- `GET /routes/list?project&prefix&limit`
- `GET /routes/resolve?project&path`
- `GET /routes/by-component?project&name`
- `GET /permissions/list?project&prefix&role`
- `GET /permissions/info/{key}?project`
- `GET /pages/list?project&domain`
- `GET /pages/get?project&domain&entity`
- `GET /docs/list?project`
- `GET /docs/search?project&q`
- `GET /docs/get/{name}?project`
- `GET /e2e/specs?project&name&limit`
- `GET /e2e/testid/{value}?project`
- `GET /e2e/coverage?project`
- `GET /e2e/uncovered?project&limit&group_by_file`
- `GET /scss/list?project&class_name`
- `GET /scss/class/{name}?project`

### Sanity / viz / tests / docs

- `/sanity?project=X` — probes for duplicate names per label, orphan
  nodes, templated values, outliers. Plan is in `mcp/data-quality-probes.md`.
- Port `/viz/` and `/viz/graph?cypher=…` from master (cytoscape page +
  endpoint) — rewrite the IN-clause to use `id()` not `elementId()`
  for Memgraph.
- Tests: `tests/conftest.py` with a Memgraph fixture (separate DB or
  full wipe between tests), then per-extractor and per-endpoint tests
  using a small committed `.scip` fixture from a minimal sample
  workspace.
- README — replace the stale top-level one with the new pipeline
  diagram, install steps, and operating commands.

### Open questions / known gaps

- 33 anonymous gql ops have no `kind` — should we default them to
  `"query"` if they live under `src/gql/queries/` and look like
  read-only? Or leave them untyped?
- SCIP for the routes tree is going to under-deliver — `<Route path=…>`
  values aren't symbols, they're string literals. Plan: tree-sitter-tsx
  over `router/index.tsx`, then resolve component references via SCIP.
- Memgraph 3 doesn't support composite indexes. We use per-property
  indexes; revisit if perf becomes an issue.

## How to run what's here

```bash
# 1. Memgraph
docker compose up -d memgraph

# 2. Core API
uv run uvicorn kg.server:app --host 127.0.0.1 --port 7400

# 3. Full reindex
curl -X POST 'http://127.0.0.1:7400/reindex?project=adsw'

# 4. Try the live endpoint
curl 'http://127.0.0.1:7400/gql/operations?project=adsw&kind=mutation&name=booking'
```

## Commits on this branch

```
5cfbb9b  feat(api): /gql endpoints + kind/gql_name capture in extractor
b0c4ab8  feat(pipeline): SCIP→extractor→Memgraph runs end-to-end with gql
d03ed47  feat(rewrite): scrap legacy, scaffold Memgraph + SCIP foundation
adddfac  feat(scip): stage 1 — SCIP indexer + gql/hooks extractor
2d9bfe2  feat(viz): cytoscape-based graph viewer + relaxed local password
fe2273e  fix(gql): hook_info returns all definitions when a name is duplicated
2accbbc  feat(routes): routes_resolve picks up gql usage from the page subtree
4dafe7d  feat(pages): pages_get now collects hooks/ops from the whole page subtree
742cb13  fix(e2e): pattern coverage walks through literal TestIds, not loc.value
2d566a3  feat(e2e): /e2e/uncovered grouped by source file
ed858ba  fix(routes): resolve template-literal route paths through enum members
7e7afa7  feat(e2e): testid_info now lists locators covering a pattern
ddb6b66  fix(pages): match Component to Page by file path, not name
02ff79c  fix(e2e): resolve template-literal testids via pattern prefix match
```
