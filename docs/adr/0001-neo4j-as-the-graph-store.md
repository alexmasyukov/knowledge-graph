# ADR-0001: Neo4j as the graph store

**Status:** accepted
**Date:** 2026-05-26

## Context

We need to store a graph of project entities (routes, components, gql
operations, hooks, permissions, e2e specs, testids, …) and answer
transitive questions over it: "which e2e specs depend on this testid?",
"which components call hooks that wrap this mutation?". Counts are
small (low thousands of nodes per project) but path queries go three
to five hops deep.

## Options considered

- **Postgres with `nodes`/`edges` tables and recursive CTEs.** Works for
  small graphs. The query language is verbose for path-style questions,
  and there's no graph-aware visual debugger.
- **DuckDB / SQLite.** Same shape as Postgres but no service to run.
  Same query-ergonomics problem.
- **Memgraph / Kùzu.** Cypher dialects, similar maturity story to Neo4j
  for our scale but smaller ecosystems.
- **Neo4j Community.** Mature, free for our use, official Python driver,
  and ships with a browser-based graph visualiser on :7474 out of the
  box.

## Decision

Neo4j Community in a single Docker container.

## Why

The browser-based graph explorer at :7474 was the deciding factor. We
get a working visual UI for the graph for free — clicking a node to
expand its neighbours is how most "is this graph useful?" debugging
happens in practice. Standing up an equivalent Web UI on Postgres
would have meant building it ourselves.

APOC plugins ship in the same image (`NEO4J_PLUGINS: ["apoc"]`), so
auxiliary operations (dump/restore, bulk imports) are one call away.

## Consequences

- Operators need Docker running. (Already a project assumption; we use
  Docker for the indexer too in production-like setups.)
- All persistence is in `./neo4j_data/` — gitignored, survives
  `docker compose down`.
- Queries are in Cypher, not SQL. Team needed to learn ~10 keywords
  (MATCH, MERGE, WITH, UNWIND, OPTIONAL MATCH); cost paid back the
  first time a five-hop transitive query fit in two lines.
- Single-instance, no HA. Acceptable: this is a developer-machine tool,
  not a production service.
