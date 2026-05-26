# Pro-stack rewrite — work log

Branch: `experiment/pro-stack` (in this repo and in `arenadata-adsw`).
Master is untouched and keeps the working ts-morph + Neo4j stack.

## Goal

Replace the legacy stack with a professional foundation:

1. **scip-typescript** instead of custom ts-morph extractors — point of
   truth for cross-file references is the upstream indexer Sourcegraph
   maintains.
2. **Memgraph** instead of Neo4j Community — drop-in Bolt-compatible
   replacement, faster, no licence ceiling.
3. **Sourcegraph self-hosted** for code search/refs UI (optional layer
   on top — still pending, see below).
4. **Incremental indexing** by file diff (watchdog/git diff) — pending.
5. **Tree-sitter** for SCSS / Markdown / e2e / JSX trees / TypeScript
   declarations instead of regex.

Non-negotiable: keep the same HTTP contract that the MCP wrapper
(`arenadata-mcp/tools/kg.py`) already speaks.

## Status: Stage 2 complete

Every extractor and endpoint from the master stack has been ported to
the new foundation. `experiment/pro-stack` is feature-complete for
the MCP contract and adds two new capabilities (`/sanity` probes,
`/types/*`).

### Numbers on adsw (latest reindex)

| Extractor | Nodes | Edges | Time |
|---|---|---|---|
| gql         | 202 GqlOperation, 93 GqlHook, 268 File | 431 | 159 ms |
| routes      | 101 Route, 61 Component, 2 Guard, 30 Permission | 452 | ~95 ms |
| permissions | 135 Permission, 7 Role               | 265 | 33 ms |
| pages       | 56 Page, 95 Component                 | 95  | 28 ms |
| e2e         | 187 TestId, 3 E2eSpec, 60 File        | 199 | 246 ms |
| scss        | 22 ScssModule, 67 ScssClass, 43 File  | 111 | 95 ms |
| docs        | 3 Doc                                 | 0   | 3 ms |
| types       | 114 Type, 290 consumer File           | 1370| 519 ms |
| **scip-typescript** | — | — | **5.2 s** |
| **total** | | | **~6.5 s** |

## Commits on this branch

```
75f66e1 feat(api): /routes/{list,resolve,by-component} endpoints
7d46018 feat(routes): tree-sitter-tsx routes extractor + tsconfig path resolver
0af8a53 docs: WORK_LOG for the pro-stack rewrite
5cfbb9b feat(api): /gql endpoints + kind/gql_name capture in extractor
5dd2e3f feat(pipeline): SCIP→extractor→Memgraph runs end-to-end with gql
d03ed47 feat(rewrite): scrap legacy, scaffold Memgraph + SCIP foundation
adddfac feat(scip): stage 1 — SCIP indexer + gql/hooks extractor
9f12409 feat(permissions): extractor + /permissions/{list,info} endpoints
6765cc2 feat(pages): filesystem extractor + /pages/{list,get} endpoints
7a3aa39 feat(e2e): tree-sitter testid scanner + /e2e/{specs,testid,coverage,uncovered}
6a236be feat(scss): tree-sitter-scss modules extractor + /scss/{list,class}
d358367 feat(docs): tree-sitter-markdown extractor + /docs/{list,search,get}
d08819e feat(types): tree-sitter typescript types extractor + /types/{list,get,search}
```

## Done

- `scip-indexer/` — pinned `@sourcegraph/scip-typescript@0.4.0`,
  `scip_pb2.py` generated from `scip.proto`, smoke-test parity with
  the legacy ts-morph extractor.
- `docker-compose.yml` — Memgraph 3.6 + Memgraph Lab. No Neo4j.
- `kg/settings.py` — MEMGRAPH_URI/USER/PASSWORD + PROJECT_<NAME>.
- `kg/db.py` — driver factory, idempotent `init_schema()` that reads
  `SHOW INDEX INFO` / `SHOW CONSTRAINT INFO` to skip already-present
  objects.
- `kg/writers/graph.py` — batched MERGE for nodes (via `NODE_KEYS`)
  and grouped edge writes per `(source_label, type, target_label)`.
- `kg/indexers/tree_sitter_util.py` — shared tree-sitter helpers.
- `kg/indexers/ts_resolver.py` — tsconfig path-alias resolver (with a
  JSONC stripper that respects string literals so `@core/*` aliases
  don't get eaten).
- `kg/indexers/scip_loader.py`, `kg/indexers/scip_runner.py`,
  `kg/indexers/base.py`, `kg/indexers/ingest.py` — pipeline plumbing.
- **Extractors**: `gql`, `routes`, `permissions`, `pages`, `e2e`,
  `scss`, `docs`, `types_extractor` (Type-label collision avoided in
  the module name).
- **API routers**: `gql`, `routes`, `permissions`, `pages`, `e2e`,
  `scss`, `docs`, `types`, `sanity`, `viz`, plus `health` and
  `reindex`.
- `/viz/` cytoscape page wired against Memgraph (`id(n)` not
  `elementId(n)`).
- `/sanity?project=X` runs 10 probes — duplicate names, orphans,
  template leakage, unused permissions, hook callsite outliers, etc.

## To do

### Sourcegraph self-hosted
Pending. Would host the existing `.scip` files for a richer code-search
UI; doesn't block the MCP contract.

### Incremental indexing
Pending. Currently every `/reindex` runs scip-typescript from scratch.
Next steps:
- watchdog (or git-diff-based trigger) to invalidate per-file slices
- per-file extractor inputs so we can re-run on just the changed paths
- partial graph wipes scoped to the touched file set

### Tests
- `tests/conftest.py` with a Memgraph fixture (separate DB or full
  wipe between tests)
- per-extractor tests using a small committed `.scip` fixture from a
  minimal sample workspace
- API tests against a seeded Memgraph

### Known gaps
- 33 of 202 gql operations carry no `kind`/`gql_name` — they use the
  keyword-less `gql\`{ ... }\`` form. Probably default to `"query"`
  when they sit under `src/gql/queries/`.
- Memgraph 3 has no composite indexes; we use per-property indexes
  for now and revisit if perf degrades.

## How to run what's here

```bash
# 1. Memgraph
docker compose up -d memgraph

# 2. Core API
uv run uvicorn kg.server:app --host 127.0.0.1 --port 7400

# 3. Full reindex
curl -X POST 'http://127.0.0.1:7400/reindex?project=adsw'

# 4. Live endpoint sample
curl 'http://127.0.0.1:7400/routes/resolve?project=adsw&path=/services/education/booking'

# 5. Data quality probes
curl 'http://127.0.0.1:7400/sanity?project=adsw'

# 6. Graph viewer
open http://127.0.0.1:7400/viz/
```
