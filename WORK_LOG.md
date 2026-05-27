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
3. **Sourcegraph self-hosted** for code search/refs UI (optional layer).
4. **Incremental indexing** by file diff (watchdog / git-diff `since=`).
5. **Tree-sitter** for SCSS / Markdown / e2e / JSX / TS declarations.

Plus, on top of the structural layer:

6. **Convention rules** — pluggable semantic overlay encoding project-
   wide patterns the structural layer can't see (JSX wrappers, data
   providers, etc).

Non-negotiable: keep the same HTTP contract that the MCP wrapper
(`arenadata-mcp/tools/kg.py`) already speaks.

## Status — Stage 3 (semantic conventions)

Stage 1 (SCIP + Memgraph foundation) and Stage 2 (all 8 extractors +
endpoints + MCP wrapper) are complete. Stage 3 adds the convention
framework; first rule (`page_data_provider`) shipped.

### Numbers on adsw (latest reindex)

| Stage | Name | Nodes | Edges | Time |
|---|---|---|---|---|
| extractor | gql         | 202 GqlOperation, 93 GqlHook, 268 File | 431 | 163 ms |
| extractor | routes      | 101 Route, 61 Component, 2 Guard, 30 Permission | 452 | ~95 ms |
| extractor | permissions | 135 Permission, 7 Role | 265 | 31 ms |
| extractor | pages       | 56 Page, 95 Component | 95 | 25 ms |
| extractor | e2e         | 187 TestId, 3 E2eSpec, 60 File | 199 | 226 ms |
| extractor | scss        | 22 ScssModule, 67 ScssClass, 43 File | 111 | 80 ms |
| extractor | docs        | 4 Doc | 0 | 4 ms |
| extractor | types       | 114 Type, 290 consumer File | 1370 | 512 ms |
| convention | **page_data_provider** | **28 PageDataProviderHook** | **73** | **73 ms** |
| **scip-typescript** | — | — | — | **5.2 s** |
| **total** | | | | **~6.5 s** |

## Done — current scope

### Foundation + extractors (Stage 1–2)
- `scip-indexer/` — pinned `@sourcegraph/scip-typescript@0.4.0`
- `docker-compose.yml` — Memgraph 3.6 + Lab + optional Sourcegraph profile
- `kg/settings.py`, `kg/db.py`, `kg/writers/graph.py`, `kg/indexers/*`
- 8 structural extractors: gql, routes, permissions, pages, e2e, scss,
  docs, types
- 25 HTTP endpoints covering the MCP wrapper contract
- `/sanity` — 10 data-quality probes
- `/viz/` — cytoscape graph viewer (see below)
- `kg/watcher.py` + `since=<ref>` short-circuit on `/reindex`
- 11 pytest tests (~50ms suite)

### Conventions (Stage 3, in progress)
- `kg/indexers/conventions/` — module layout for semantic rules
- `page_data_provider` — first convention. `<PageDataProvider dataLoaderHook={X}>`
  binds X as the data dependency of its enclosing Component. Tree-sitter
  walks the full JSX AST (depth-independent); identifier X resolved
  to its definition file via SCIP. Emits:
  `Component -[:LOADS_DATA_VIA]-> PageDataProviderHook -[:CALLS_HOOK]-> GqlHook`

### Viz polish
- CodeMirror Cypher editor with line numbers / dark theme
- Per-label filter chips with on/off toggle
- Resizable toolbar (drag handle)
- Floating draggable detail panel (props + outgoing/incoming with
  click-to-navigate), width-resizable from left edge, position +
  width persisted in `localStorage`
- "Save PNG ⬇" — 2× canvas export
- `no-store` Cache-Control on `/viz/` to avoid stale-HTML confusion

### MCP wrapper
- `arenadata-mcp/tools/kg.py` rewritten for the SCIP+Memgraph stack
- 25 tools live: `kg_health`, `kg_reindex` (with `since=`), `kg_sanity`,
  `gql_list_operations` / `gql_hook_info` / `gql_find_callsites`,
  `routes_list` / `routes_resolve` / `routes_find_by_component`,
  `permissions_list` / `permissions_info`,
  `pages_list` / `pages_get`,
  `e2e_list_specs` / `e2e_testid_info` / `e2e_coverage` / `e2e_uncovered`,
  `scss_list_modules` / `scss_class_usage`,
  `docs_list` / `docs_search` / `docs_get`,
  `kg_types_list` / `kg_types_get` / `kg_types_search`
- Enabled on adsw via `arenadata-mcp.project.json` (kg block)

### Control panel
- `start.py` — questionary menu over rich status panel. Manages
  Memgraph + Memgraph Lab (Docker), core API (uvicorn), watcher.
- Auto-execs under `.venv/bin/python` so plain `python3 start.py` works
- Watcher auto-starts in `Start all` for single-project setups

## Next up

### Conventions (sole remaining product work)
User hasn't handed us the next pattern definitions yet. When they do,
each goes as a separate module under `kg/indexers/conventions/`.
Likely candidates from the conversation:
- `item_page_form` — `<ItemPage><Form /></ItemPage>` JSX shell
- guard-with-roles propagation — runtime permission checks inside forms
- Network project's twin conventions (same patterns, different shells)

### JSX composition extractor

Big gap surfaced while testing viz: the graph knows Route→Component and
the gql chain, but not which sub-components live inside a Component's
JSX. Asking "everything on the page partner/distroApplications" doesn't
return `ItemPageLayout`, `KendoTable`, or any shared layout components.

Design note: `arenadata-adsw/mcp/jsx-composition-extractor.md`.
Short version — walk every `.tsx` under `src/`, find capitalized JSX
tags, resolve identifiers via SCIP, emit `Component -[:RENDERS]-> Component`
and create Component nodes for every PascalCase TSX file (not just the
ones in `src/pages/`). Expected growth on adsw: ~700 Component, ~2000
RENDERS edges.

### True per-file incremental indexing
Currently `/reindex?since=…` short-circuits when nothing changed.
When something does change, we still rerun the full SCIP + all
extractors. Plan:
1. Pass the changed-paths list from the watcher into reindex()
2. Per-extractor `affected_paths` predicate — skip if irrelevant
3. Scoped wipes (delete only the slice attributable to the changed files)

SCIP is still all-or-nothing per package (scip-typescript limitation),
so the SCIP run can't be split. But the extractor stage can — and
that's where most of the 1.3s lives for our biggest extractor (types).

### Sourcegraph self-hosted
Container is parked behind `docker compose --profile sourcegraph up`.
Not exercised yet. First-run guide in README; admin account + code
host setup still needed.

### Tests beyond the pure-parse extractors
- API-against-real-Memgraph for the gql/routes/types/sanity endpoints
- Convention regression: feed a tiny JSX snippet, assert the emitted
  edges are exact

### Sanity probe ideas surfaced during testing
- "two Components with same name in different files" (we have this)
- "Routes pointing at a Component that doesn't exist" (broken render)
- "PageDataProviderHook with no matching gql edges" (broken inference)

## How to run

```bash
# 1. Bring up everything via the menu
uv run python start.py   # then "Start all"
# (Memgraph + Lab in Docker, core API, watcher all come up)

# 2. Open the dashboards
open http://localhost:3000      # Memgraph Lab
open http://127.0.0.1:7400/viz/ # our viewer
open http://127.0.0.1:7400/docs # Swagger

# 3. Live endpoint sample
curl 'http://127.0.0.1:7400/routes/resolve?project=adsw&path=/services/education/booking'
curl 'http://127.0.0.1:7400/sanity?project=adsw'
```

## Commit log

`git log experiment/pro-stack --oneline ^master` for the full list
(~40 commits since the branch diverged).
