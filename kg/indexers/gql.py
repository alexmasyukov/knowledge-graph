"""GraphQL operations + hook wrappers extractor.

Identifies adsw's gql convention symbols out of the SCIP index:

  GqlOperation: src/gql/queries/**/<FILE>.ts  →  exported const in
                UPPER_SNAKE_CASE.
                Example: GET_EDUCATION_BOOKINGS in
                src/gql/queries/booking.ts.

  GqlHook:      src/gql/hooks/**/<FILE>.ts    →  exported const named
                useX (camelCase, starts with `use` followed by an
                uppercase letter).
                Example: useBookings in
                src/gql/hooks/education/bookings/useBookings.ts.

Cross-file callsites come for free from the SCIP index. A hook that
imports a gql operation produces a SCIP reference to the operation's
symbol; we record that as a (GqlHook)-[:WRAPS]->(GqlOperation) edge.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Final

from .base import Extractor, IndexerContext, IngestResult
from .scip_loader import SymbolInfo, parse_symbol_path, short_symbol

# Prefix of the package-relative file path that gates each kind.
# scip-typescript emits paths relative to the package root, not the
# repo root — so this matches the path stored as `<dir>/`file.ts`/...`
# in the SCIP symbol.
QUERIES_PREFIX: Final = "src/gql/queries/"
HOOKS_PREFIX:   Final = "src/gql/hooks/"

_UPPER_SNAKE_RX = re.compile(r"^[A-Z][A-Z0-9_]*$")
_HOOK_RX        = re.compile(r"^use[A-Z]\w*$")


def _is_operation(file_: str, name: str) -> bool:
    return file_.startswith(QUERIES_PREFIX) and bool(_UPPER_SNAKE_RX.match(name))


def _is_hook(file_: str, name: str) -> bool:
    return file_.startswith(HOOKS_PREFIX) and bool(_HOOK_RX.match(name))


def _repo_relative(project_code_root: str, package_relative: str) -> str:
    """Convert the SCIP definition's path (which is repo-relative) into
    the form we store. We accept it as-is — scip-typescript already
    emits repo-relative paths in `Document.relative_path`."""
    return package_relative


class GqlExtractor:
    NAME = "gql"

    def run(self, ctx: IndexerContext) -> IngestResult:
        operations: list[dict] = []
        hooks: list[dict] = []
        files_seen: set[str] = set()
        # symbol_id → (label, key_props) so we can wire references later.
        symbol_to_node: dict[str, tuple[str, dict]] = {}

        for info in ctx.scip.local_definitions():
            short = short_symbol(info.symbol)
            parsed = parse_symbol_path(short)
            if not parsed:
                continue
            pkg_file, name = parsed
            d = info.definitions[0]
            row = {
                "project": ctx.project.name,
                "symbol": info.symbol,
                "name": name,
                "file": d.file,        # repo-relative
                "line": d.line,
                "column": d.column,
                "callsites": len(info.references),
            }
            if _is_operation(pkg_file, name):
                operations.append(row)
                symbol_to_node[info.symbol] = ("GqlOperation", row)
                files_seen.add(d.file)
            elif _is_hook(pkg_file, name):
                hooks.append(row)
                symbol_to_node[info.symbol] = ("GqlHook", row)
                files_seen.add(d.file)

        # Edges:
        #   Hook -[:WRAPS]-> Operation   when a hook file references the op
        #   File -[:USES_OPERATION]-> Operation  for every non-hook ref
        #   File -[:CALLS_HOOK]-> Hook   for every reference to a hook
        edges: list[dict] = []
        # Map file → set of (symbol, role) we touched, used to build
        # File-level edges without re-iterating.
        for sym_id, (label, _def_row) in symbol_to_node.items():
            info: SymbolInfo | None = ctx.scip.symbols.get(sym_id)
            if info is None:
                continue
            ref_files: dict[str, list[int]] = defaultdict(list)
            for ref in info.references:
                ref_files[ref.file].append(ref.line)

            for ref_file, lines in ref_files.items():
                # Build a File node (always, idempotent MERGE later)
                files_seen.add(ref_file)
                edge_type = "USES_OPERATION" if label == "GqlOperation" else "CALLS_HOOK"
                edges.append({
                    "source": {"label": "File",  "key": {"project": ctx.project.name, "path": ref_file}},
                    "type":   edge_type,
                    "target": {"label": label,   "key": {"project": ctx.project.name, "symbol": sym_id}},
                    "props":  {"lines": lines, "count": len(lines)},
                })

        # Hook->Operation edges: detect by walking hook *definition* files'
        # references — if a hook file references an operation symbol,
        # call it a wrap.
        ops_by_symbol = {sym: row for sym, (label, row) in symbol_to_node.items() if label == "GqlOperation"}
        hooks_by_symbol = {sym: row for sym, (label, row) in symbol_to_node.items() if label == "GqlHook"}
        op_files = {row["file"] for row in ops_by_symbol.values()}
        for hook_sym, hook_row in hooks_by_symbol.items():
            hook_file = hook_row["file"]
            doc = ctx.scip.documents.get(hook_file)
            if doc is None:
                continue
            wrapped: set[str] = set()
            for occ in doc.occurrences:
                if occ.symbol_roles & 1:  # definition → skip
                    continue
                if occ.symbol in ops_by_symbol:
                    wrapped.add(occ.symbol)
            for op_sym in wrapped:
                edges.append({
                    "source": {"label": "GqlHook",      "key": {"project": ctx.project.name, "symbol": hook_sym}},
                    "type":   "WRAPS",
                    "target": {"label": "GqlOperation", "key": {"project": ctx.project.name, "symbol": op_sym}},
                })

        # Add File nodes for every path we touched (deduped).
        files = [
            {"project": ctx.project.name, "path": path}
            for path in sorted(files_seen)
        ]

        return IngestResult(
            nodes={
                "GqlOperation": operations,
                "GqlHook":      hooks,
                "File":         files,
            },
            edges=edges,
            stats={
                "operations": len(operations),
                "hooks":      len(hooks),
                "files":      len(files),
                "edges":      len(edges),
                "operation_files": len(op_files),
            },
        )


# Module-level singleton — matches the Extractor protocol indirectly via
# duck typing. Importers can either do `GqlExtractor()` or use this.
EXTRACTOR: Extractor = GqlExtractor()  # type: ignore[assignment]
