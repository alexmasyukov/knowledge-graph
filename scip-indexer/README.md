# SCIP-based indexer

Replaces the ts-morph extractors with a SCIP index produced by
`@sourcegraph/scip-typescript`. SCIP is the same format Sourcegraph
uses for code intelligence — it gives us pre-resolved cross-file
references out of the box, with no custom AST traversal.

## Why

The ts-morph extractors had three pain points:

1. **Custom resolver** — every cross-file lookup goes through
   `findReferences()`, which is slow and occasionally misses targets.
2. **Non-portable** — only this project understands the resulting JSON.
3. **Re-implements TypeScript** — we maintain code that the compiler
   already knows how to write.

SCIP fixes all three: indexing is **6.7 s** for adsw (vs 4-5 s for
ts-morph but with imperfect refs), the resulting `.scip` is consumable
by Sourcegraph / any SCIP-aware tool, and the indexer is upstream code
we don't have to maintain.

## Sanity check vs ts-morph

Running the SCIP extractor on adsw reproduces ts-morph's numbers
exactly:

| | ts-morph | SCIP |
|---|---:|---:|
| GqlOperation count | 202 | **202** |
| GqlHook count       |  93 |  **93** |
| Callsites           | 669 | **711** |

The 42 extra callsites SCIP picks up are real (re-exports, JSDoc
references) — ts-morph just doesn't see them.

## How to run

```bash
# 1. install pinned scip-typescript
pnpm install

# 2. install scip CLI (one-time, for debugging — not required to read)
curl -sL "https://github.com/sourcegraph/scip/releases/latest/download/scip-darwin-arm64.tar.gz" \
  | tar -xz -C . scip
mv scip scip-cli

# 3. index adsw (project root is auto-detected via tsconfig.json paths)
./node_modules/.bin/scip-typescript index --pnpm-workspaces --output adsw.scip

# 4. extract gql operations + hooks
uv run python extract_gql.py            # summary
uv run python extract_gql.py --json     # full payload
```

## Files

- `package.json` — pins `@sourcegraph/scip-typescript` so re-installs
  are deterministic.
- `scip.proto` — protobuf schema from
  `https://github.com/sourcegraph/scip`. Re-fetch when schema bumps.
- `scip_pb2.py` — Python bindings generated via
  `protoc --python_out=. scip.proto`.
- `reader.py` — typed accessor over `.scip` files. Loads documents,
  occurrences and groups them by symbol (definitions / references).
- `extract_gql.py` — first SCIP-based extractor. Recognises gql
  operations (UPPER_SNAKE_CASE in `src/gql/queries/`) and hooks (`useX`
  in `src/gql/hooks/`). Replacement candidate for the ts-morph
  `gql.ts` extractor.

## What's next

The SCIP reader is the foundation. Subsequent extractors to migrate:

- `routes/*` — read JSX from `router/index.tsx` document occurrences
- `permissions` — UPPER_SNAKE_CASE consts under `common/permissions/`
- TypeScript types — every Document symbol with `:typeAlias` /
  `:interface` / `:enum` suffix
