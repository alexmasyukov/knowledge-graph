# Architecture Decision Records

Short notes about non-obvious choices. Each one answers a single
question: "why did we pick X and not the alternatives that, at first
glance, look equivalent?"

ADRs are immutable. When a decision is reversed, write a new ADR that
says so and link the old one — don't edit history.

## Index

- [0001 — Neo4j as the graph store](0001-neo4j-as-the-graph-store.md)
- [0002 — ts-morph instead of SCIP or tree-sitter](0002-ts-morph-instead-of-scip-or-tree-sitter.md)
- [0003 — Node sidecar over HTTP, not stdio](0003-node-sidecar-over-http.md)
- [0004 — REST, not GraphQL, for the core API](0004-rest-not-graphql.md)
- [0005 — One repo, multiple projects](0005-multi-project-single-repo.md)
- [0006 — Two-step pipeline: gql before everything else](0006-gql-extractor-runs-first.md)
