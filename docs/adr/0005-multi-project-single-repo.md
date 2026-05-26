# ADR-0005: One repo, multiple projects (tagged by `project`)

**Status:** accepted
**Date:** 2026-05-26

## Context

We have two source codebases to index — `adsw` and `network` — and
might add more. Both follow similar conventions (React + TS + Apollo,
`pages/<domain>/<entity>/`-shaped layout). Should each codebase have
its own Neo4j database? Its own instance? Or share one graph keyed by
project name?

## Options considered

- **Per-project Neo4j database.** Cleanest separation; deleting a
  project's data is a `DROP DATABASE`. Trade-off: Neo4j Community
  doesn't support multi-database in the way we'd want, you'd be
  running multiple containers.
- **Per-project Neo4j instance.** Strongest isolation, expensive in
  resources (each Neo4j wants ~1 GB heap).
- **One graph, every node tagged `{project: 'adsw'}`.** Same approach
  as the RAG sister project (chunks tagged by `project_name` in
  Postgres + Qdrant). Single Cypher cluster, queries always include
  `{project: $project}` in the MATCH clause.

## Decision

One Neo4j database, one container, every node and edge carries a
`project` property. `wipe_labels(project, labels)` deletes only that
project's slice when reindexing.

## Why

- **Resource cost is paid once.** A second project doesn't double the
  RAM footprint.
- **Cross-project queries are trivial.** "Show me every domain across
  both adsw and network" is one MATCH without a UNION across databases.
- **Same pattern as RAG.** Users already know `PROJECT_<NAME>=…` from
  the .env of the sister tool; no new mental model.

## Consequences

- Every Cypher query must filter by `{project: $project}` — forgetting
  it returns a mix. We've made `project` part of every node-identity
  key (UNIQUE constraints) so accidental cross-contamination would
  raise a constraint violation.
- Backups are coarser. There's no per-project dump; you back up the
  whole instance.
