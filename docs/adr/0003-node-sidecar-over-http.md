# ADR-0003: Node sidecar over HTTP, not stdio JSON-RPC

**Status:** accepted
**Date:** 2026-05-26

## Context

ts-morph runs in Node. The orchestrator that writes to Neo4j runs in
Python. Two languages, two processes, one needs to ask the other for
work.

## Options considered

- **Stdio JSON-RPC.** Spawn the Node process from Python, talk over
  stdin/stdout with newline-delimited JSON. Standard pattern (used by
  Language Server Protocol, Jupyter kernels, etc.).
- **gRPC.** Strict schemas, codegen for both languages. Heavy for a
  one-binary setup.
- **HTTP + JSON.** Fastify on a port, Python `httpx.AsyncClient` against
  it. Trivial to debug — you can `curl` the indexer directly.

## Decision

Fastify on `localhost:7401`, JSON request/response. Python connects via
a shared `httpx.AsyncClient` defined in `kg/http.py`.

## Why

- **Debuggable from the shell.** `curl -X POST localhost:7401/extract/gql
  -d '{"project":"adsw"}'` is how you reproduce a bug in 5 seconds.
  Stdio JSON-RPC requires a Python harness even for ad-hoc inspection.
- **Hot reload of either side is independent.** Restarting the indexer
  doesn't kill the core; restarting the core doesn't kill the indexer
  (which is the expensive one — it has the TS Program in memory).
- **One less invariant in the supervisor.** With stdio, the Python
  process owns the Node process's lifecycle; if Python crashes, Node
  goes too. With HTTP, the menu's PID-file supervision treats both
  services symmetrically.

## Consequences

- Two ports to remember (`:7401` indexer, `:7400` core). Both bind to
  `127.0.0.1` by default — never reachable from outside.
- No type-level guarantee that request/response shapes match. We
  mitigate with Pydantic on the Python side; the Node side declares
  Fastify route generics inline.
