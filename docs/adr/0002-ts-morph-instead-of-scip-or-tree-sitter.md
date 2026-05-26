# ADR-0002: ts-morph instead of SCIP or tree-sitter

**Status:** accepted
**Date:** 2026-05-26

## Context

The indexer needs to answer type-aware questions about a TypeScript
codebase: "where is this symbol referenced?", "what file does this
lazy import resolve to?", "is this Identifier the same `useBooking`
as the one imported from `@gql/hooks/...`?" Plain text search (grep,
ripgrep, regex) gives false positives on comments, strings and
same-name unrelated symbols.

## Options considered

- **tree-sitter** (with `tree-sitter-typescript`). Fast incremental
  parser, excellent for syntax highlighting and structural search. No
  type system, no scope resolution — we'd have to build our own scope
  analyser to figure out which `foo` is which.
- **SCIP** (Sourcegraph code intelligence protocol). Pre-built indexer
  (`scip-typescript`) emits a `.scip` protobuf. Read it with a library,
  query as needed. Excellent precision. Downside: another build step,
  the index is stale until you rebuild it, and consuming the protobuf
  is its own learning curve.
- **TypeScript Compiler API directly.** Maximum power but verbose;
  every traversal becomes a 30-line incantation.
- **ts-morph.** A thin, ergonomic wrapper over the TypeScript Compiler
  API. Same precision as the compiler, ~3x less code per traversal.

## Decision

ts-morph, running inside a small Fastify sidecar on `:7401`. The
TypeScript `Program` stays in memory between requests; the Python
core POSTs an extract request and gets JSON back.

## Why

- **Type-aware analysis comes for free.** `findReferences()` returns
  the same set of nodes that "Find All References" gives in VS Code —
  scope-correct, no false positives from same-name unrelated symbols.
- **Module resolution is built in.** `ts.resolveModuleName()` honours
  `tsconfig.json` paths, baseUrl, package exports. We removed a
  hand-rolled "guess where `@pages/foo` lives" function once we
  realised ts-morph could just do it.
- **No build step.** Unlike SCIP, the index is the running TypeScript
  Program — always current.

## Consequences

- The Fastify sidecar must be running for any extractor that needs the
  AST (gql, routes, permissions). Two other extractors (pages, scss,
  e2e) are pure Python — they walk the filesystem and don't need a TS
  process.
- Initial Program load takes a few seconds on a thousand-file project;
  this is amortised because the sidecar stays up between reindexes.
- Memory cost: ~500MB resident for adsw. Acceptable on a dev machine.
