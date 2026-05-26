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
from functools import lru_cache
from pathlib import Path
from typing import Final

from .base import Extractor, IndexerContext, IngestResult
from .scip_loader import SymbolInfo, parse_symbol_path, short_symbol

# Matches `gql\`\n  query GetX(...) {...\``  — captures kind + gql name.
# Also handles `gql\` query GetX { ... \`` on one line. Multiline mode
# so `^` anchors to the start of each line inside the template.
_GQL_TAG_RX = re.compile(
    r"gql\s*`\s*(?P<kind>query|mutation|subscription|fragment)\s+(?P<name>\w+)",
    re.IGNORECASE,
)


@lru_cache(maxsize=2048)
def _gql_info_for_file(file_path: str) -> dict[str, tuple[str, str]]:
    """Map {ts_local_name → (kind, gql_name)} for every gql tagged
    template in a TS source file.

    A file like:
        export const GET_X = gql`query GetX { ... }`
        export const ADD_Y = gql`mutation AddY($i: ...) { ... }`
    yields {"GET_X": ("query", "GetX"), "ADD_Y": ("mutation", "AddY")}.
    """
    out: dict[str, tuple[str, str]] = {}
    try:
        text = Path(file_path).read_text(encoding="utf-8")
    except Exception:
        return out
    # Find every `<ws>const <NAME> = gql\`<kind> <gql_name>` pair.
    pattern = re.compile(
        r"(?:export\s+)?const\s+(?P<ts>[A-Z][A-Z0-9_]*)\s*=\s*gql\s*`\s*(?P<kind>query|mutation|subscription|fragment)\s+(?P<gql>\w+)",
        re.IGNORECASE,
    )
    for m in pattern.finditer(text):
        out[m["ts"]] = (m["kind"].lower(), m["gql"])
    return out

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
                # Pull kind + gql_name out of the source file so consumers
                # know whether this is a query/mutation/etc and what its
                # GraphQL-side identifier is. scip-typescript emits paths
                # relative to the indexed *package* (code_root), not the
                # workspace root.
                abs_path = ctx.project.code_root / d.file
                gql_info = _gql_info_for_file(str(abs_path)).get(name)
                if gql_info:
                    row["kind"], row["gql_name"] = gql_info
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
