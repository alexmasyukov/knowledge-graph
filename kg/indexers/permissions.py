"""Permissions extractor.

Parses `src/common/permissions/index.ts` with tree-sitter-typescript,
walks the nested `PERMISSIONS = { ... }` object literal, and emits one
Permission node per leaf entry — where a leaf is an array of role-code
string literals.

  Permission   (project, key, file, line, roles)

`key` is a dot-path that strips the `PERMISSIONS` root, mirroring what
the routes extractor produces from `roles={PERMISSIONS.x.y.z}` JSX
attributes — so both producers MERGE onto the same Permission nodes.

Edges:
  Permission -[:GRANTS]-> Role
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import tree_sitter

from .base import Extractor, IndexerContext, IngestResult
from .tree_sitter_util import parse, text, walk

PERMISSIONS_PATH: Final = "src/common/permissions/index.ts"


def _peel_cast(node: tree_sitter.Node) -> tree_sitter.Node:
    """`{...} as PagePermissions` arrives as an `as_expression` —
    return the wrapped expression so callers see the literal object."""
    while node.type in {"as_expression", "satisfies_expression", "parenthesized_expression"}:
        if node.named_child_count == 0:
            break
        node = node.named_children[0]
    return node


def _find_permissions_object(root: tree_sitter.Node, src: bytes) -> tree_sitter.Node | None:
    for vd in walk(root):
        if vd.type != "variable_declarator":
            continue
        name_node = vd.child_by_field_name("name")
        if name_node is None or text(name_node, src) != "PERMISSIONS":
            continue
        value = vd.child_by_field_name("value")
        if value is None:
            continue
        peeled = _peel_cast(value)
        if peeled.type == "object":
            return peeled
    return None


def _key_text(pair: tree_sitter.Node, src: bytes) -> str:
    """Return a property key as plain text (strip quotes)."""
    k = pair.child_by_field_name("key")
    if k is None:
        return ""
    raw = text(k, src)
    return raw.strip("'\"`")


def _array_string_values(arr: tree_sitter.Node, src: bytes) -> list[str]:
    """Read an `array` of string literals into a list of strings."""
    out: list[str] = []
    if arr.type != "array":
        return out
    for el in arr.named_children:
        if el.type == "string":
            if el.named_child_count:
                out.append(text(el.named_children[0], src))
            else:
                out.append(text(el, src).strip("'\"`"))
    return out


def _walk_obj(
    obj: tree_sitter.Node,
    src: bytes,
    path: tuple[str, ...],
    file_rel: str,
    out: list[dict],
) -> None:
    for pair in obj.named_children:
        if pair.type != "pair":
            continue
        key = _key_text(pair, src)
        value = pair.child_by_field_name("value")
        if value is None:
            continue
        value = _peel_cast(value)
        sub_path = (*path, key)
        if value.type == "object":
            _walk_obj(value, src, sub_path, file_rel, out)
            continue
        if value.type == "array":
            line = pair.start_point[0] + 1
            roles = _array_string_values(value, src)
            out.append({
                "key": ".".join(sub_path),
                "roles": roles,
                "file": file_rel,
                "line": line,
            })


class PermissionsExtractor:
    NAME = "permissions"

    def run(self, ctx: IndexerContext) -> IngestResult:
        path = ctx.project.code_root / PERMISSIONS_PATH
        if not path.exists():
            return IngestResult(stats={"permissions": 0, "skipped": 1})

        tree, src = parse("typescript", path)
        obj = _find_permissions_object(tree.root_node, src)
        if obj is None:
            return IngestResult(stats={"permissions": 0, "skipped": 1})

        leaves: list[dict] = []
        _walk_obj(obj, src, (), PERMISSIONS_PATH, leaves)

        # Permission nodes (merge on key). Carry roles + file/line for richer info.
        perm_nodes = [
            {
                "project": ctx.project.name,
                "key": leaf["key"],
                "roles": leaf["roles"],
                "file": leaf["file"],
                "line": leaf["line"],
            }
            for leaf in leaves
        ]

        # Role nodes — deduped across all leaves.
        role_codes = sorted({r for leaf in leaves for r in leaf["roles"]})
        role_nodes = [{"project": ctx.project.name, "code": r} for r in role_codes]

        edges: list[dict] = []
        for leaf in leaves:
            for role in leaf["roles"]:
                edges.append({
                    "source": {"label": "Permission", "key": {"project": ctx.project.name, "key": leaf["key"]}},
                    "type": "GRANTS",
                    "target": {"label": "Role", "key": {"project": ctx.project.name, "code": role}},
                })

        return IngestResult(
            nodes={
                "Permission": perm_nodes,
                "Role": role_nodes,
            },
            edges=edges,
            stats={
                "permissions": len(perm_nodes),
                "roles": len(role_nodes),
                "edges": len(edges),
            },
        )


EXTRACTOR: Extractor = PermissionsExtractor()  # type: ignore[assignment]
