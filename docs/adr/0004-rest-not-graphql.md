# ADR-0004: REST, not GraphQL, for the core API

**Status:** accepted
**Date:** 2026-05-26

## Context

The core API exposes the graph to two consumers: the MCP wrapper (Claude
Code / Cursor) and a future Web UI. GraphQL-on-graph databases is a
natural-sounding combination, especially given that the data is, well, a
graph.

## Options considered

- **GraphQL.** Single endpoint, client picks the shape of the response.
  Strong typing via the schema. Trade-off: server-side resolver
  complexity, N+1 query risks, and a learning surface most contributors
  don't yet need.
- **REST + JSON with Pydantic models.** Each endpoint answers one
  question, response shape declared as a Pydantic model and surfaced in
  OpenAPI for free.

## Decision

REST + FastAPI + Pydantic. Endpoints are named after the question they
answer: `/routes/resolve`, `/gql/find-callsites`, `/permissions/info/{key}`.

## Why

- **There is exactly one client per endpoint shape.** The MCP wrapper
  doesn't pick fields — it formats the whole response. GraphQL's "the
  client picks the shape" is wasted flexibility here.
- **Pydantic gives us validation, OpenAPI schema and Swagger docs for
  free.** Same expressiveness as a GraphQL schema for the questions we
  actually ask.
- **Cypher is already our query language.** Each endpoint is a small
  Cypher query plus a Pydantic envelope. Adding a GraphQL layer on top
  means two query languages serving the same data.

## Consequences

- Adding a new question = new endpoint. Each one is ~30 lines (Cypher
  query + Pydantic response model). Acceptable churn.
- The API contract is the FastAPI/OpenAPI spec at `/docs`. The MCP
  wrapper hand-implements clients against it; eventually we can codegen
  these.
