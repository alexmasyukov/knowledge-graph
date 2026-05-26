"""TypeScript types extractor.

Picks up top-level type declarations in `src/types/**/*.ts`:
  - `interface X {...}`           → kind=interface
  - `type X = ...`                → kind=type_alias
  - `enum X {...}`                → kind=enum (also captures members)
  - `class X {...}`               → kind=class

For each declaration we emit a Type node and, optionally, EnumMember
nodes (one per enum member). Consumers come from SCIP — any non-
definition occurrence of the matching symbol is recorded as a
`File -[:USES_TYPE]-> Type` edge so we can answer "where is this
type used".

Note: scip-typescript leaves `SymbolInformation.kind` unset (all 0),
so we lean on tree-sitter for the kind classification; SCIP only
gives us the cross-file consumer list.
"""

from __future__ import annotations

import re
from typing import Final

import tree_sitter

from .base import Extractor, IndexerContext, IngestResult
from .scip_loader import parse_symbol_path, short_symbol
from .tree_sitter_util import parse, text, walk

TYPES_ROOT: Final = "src/types"

_DECL_KIND = {
    "interface_declaration": "interface",
    "type_alias_declaration": "type_alias",
    "enum_declaration": "enum",
    "class_declaration": "class",
}


def _is_top_level(node: tree_sitter.Node) -> bool:
    """True if the declaration's parent is the program or an
    export_statement at program level."""
    parent = node.parent
    if parent is None:
        return False
    if parent.type == "program":
        return True
    if parent.type == "export_statement" and parent.parent is not None and parent.parent.type == "program":
        return True
    return False


def _decl_name(node: tree_sitter.Node, src: bytes) -> str:
    name_node = node.child_by_field_name("name")
    return text(name_node, src) if name_node is not None else ""


def _enum_members(enum_node: tree_sitter.Node, src: bytes) -> list[dict]:
    body = enum_node.child_by_field_name("body")
    if body is None:
        return []
    members: list[dict] = []
    for child in body.named_children:
        if child.type == "property_identifier":
            members.append({"name": text(child, src), "value": text(child, src).lower()})
        elif child.type == "enum_assignment":
            n = child.child_by_field_name("name")
            v = child.child_by_field_name("value")
            if n is None:
                continue
            mname = text(n, src)
            mvalue = mname.lower()
            if v is not None and v.type == "string" and v.named_child_count:
                mvalue = text(v.named_children[0], src)
            members.append({"name": mname, "value": mvalue})
    return members


def _scan_types_file(path, code_root) -> list[dict]:
    """Return a list of Type rows discovered in this file."""
    try:
        tree, src = parse("typescript", path)
    except Exception:
        return []
    rel = str(path.relative_to(code_root))
    rows: list[dict] = []
    for node in walk(tree.root_node):
        kind = _DECL_KIND.get(node.type)
        if kind is None:
            continue
        if not _is_top_level(node):
            continue
        name = _decl_name(node, src)
        if not name:
            continue
        row = {
            "name": name,
            "file": rel,
            "line": node.start_point[0] + 1,
            "kind": kind,
        }
        if kind == "enum":
            row["members"] = _enum_members(node, src)
        rows.append(row)
    return rows


_SCIP_TYPE_RX = re.compile(r"^(?P<dir>(?:[^`]+/)?)`(?P<file>[^`]+)`/(?P<name>[A-Z][A-Za-z0-9_]*)#$")


def _scip_symbol_for(name: str, file: str, scip) -> str | None:
    """Find the SCIP symbol whose short form is `<file>/<Name>#`. We
    have to scan because the index isn't keyed by short form, but the
    per-file symbol count is small enough for this to be cheap."""
    for info in scip.local_definitions():
        short = short_symbol(info.symbol)
        m = _SCIP_TYPE_RX.match(short)
        if m is None:
            continue
        if m["name"] == name and (m["dir"] + m["file"]) == file:
            return info.symbol
    return None


class TypesExtractor:
    NAME = "types"

    def run(self, ctx: IndexerContext) -> IngestResult:
        types_dir = ctx.project.code_root / TYPES_ROOT
        if not types_dir.is_dir():
            return IngestResult(stats={"types": 0, "skipped": 1})

        rows: list[dict] = []
        for path in sorted(types_dir.rglob("*.ts")):
            if path.suffix == ".ts" and not path.name.endswith(".d.ts"):
                rows.extend(_scan_types_file(path, ctx.project.code_root))
            elif path.name.endswith(".d.ts"):
                # .d.ts declares ambient types — index them too.
                rows.extend(_scan_types_file(path, ctx.project.code_root))

        # Build symbol lookup once.
        symbol_lookup: dict[tuple[str, str], str] = {}
        for info in ctx.scip.local_definitions():
            short = short_symbol(info.symbol)
            m = _SCIP_TYPE_RX.match(short)
            if m is None:
                continue
            symbol_lookup[(m["dir"] + m["file"], m["name"])] = info.symbol

        # Now build node + edge payloads.
        type_nodes: list[dict] = []
        edges: list[dict] = []
        consumer_files: set[str] = set()
        enum_members_total = 0

        for row in rows:
            sym = symbol_lookup.get((row["file"], row["name"]))
            type_nodes.append({
                "project": ctx.project.name,
                "name": row["name"],
                "file": row["file"],
                "line": row["line"],
                "kind": row["kind"],
                "symbol": sym,
                "members": [m["name"] for m in row.get("members", [])],
            })
            enum_members_total += len(row.get("members", []))

            # Consumers via SCIP refs.
            if sym is None:
                continue
            info = ctx.scip.symbols.get(sym)
            if info is None:
                continue
            for occ in info.references:
                if occ.is_definition:
                    continue
                consumer_files.add(occ.file)
                edges.append({
                    "source": {"label": "File", "key": {"project": ctx.project.name, "path": occ.file}},
                    "type": "USES_TYPE",
                    "target": {
                        "label": "Type",
                        "key": {"project": ctx.project.name, "name": row["name"]},
                    },
                    "props": {"line": occ.line},
                })

        file_nodes = [{"project": ctx.project.name, "path": p} for p in sorted(consumer_files)]

        return IngestResult(
            nodes={
                "Type": type_nodes,
                "File": file_nodes,
            },
            edges=edges,
            stats={
                "types": len(type_nodes),
                "enum_members": enum_members_total,
                "consumer_files": len(file_nodes),
                "edges": len(edges),
            },
        )


EXTRACTOR: Extractor = TypesExtractor()  # type: ignore[assignment]
