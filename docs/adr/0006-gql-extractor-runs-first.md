# ADR-0006: The gql extractor runs first

**Status:** accepted
**Date:** 2026-05-26

## Context

We have seven extractors that populate the graph: `gql`, `routes`,
`permissions`, `pages`, `docs`, `e2e`, `scss`. Several of them produce
edges that depend on nodes another extractor created — most visibly,
`routes` wants to wire `Component -[:CALLS_HOOK]-> GqlHook` and
`Component -[:USES_OPERATION]-> GqlOperation`, which only exist after
`gql` ran.

## Options considered

- **Topological sort.** Each extractor declares its dependencies, the
  orchestrator computes the run order. Future-proof but premature —
  with seven extractors the order is obvious by inspection.
- **Two passes.** Run everything once for nodes, then again for edges.
  Doubles the reindex time on the ts-morph side.
- **Fixed order in a list.** `extractors.ALL = [gql, routes, …]`. The
  list itself is the contract.

## Decision

Fixed order. `gql` is index 0. The remaining six are largely
independent and listed after.

## Why

- **There is exactly one ordering constraint** (gql before routes). A
  full dependency framework is overkill.
- **The constraint is visible at the top of `kg/extractors/__init__.py`.**
  A contributor adding a new extractor sees the list and the docstring
  explaining ordering on the same screen.

## Consequences

- If someone adds a second ordering constraint, they should add a brief
  comment in `ALL = [...]` saying so. If three or more constraints
  accumulate, revisit this ADR.
- Extractors that need to read nodes another extractor created (like
  `routes`, which reads `GqlHook.name` and `GqlOperation.symbol`)
  query Neo4j at the start of `run()` — they do not pass data through
  some shared structure. This keeps each extractor a self-contained
  unit.
