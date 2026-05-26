"""Routes + Components + Guards + Permissions extractor.

Reads `src/router/index.tsx` with tree-sitter-tsx, walks the route
array, and emits a normalized graph slice:

  Route   (path, index, depth, file, line)            keyed by (project, path, depth, line)
  Component (name, file, line, exported)              keyed by (project, file, name)
  Guard   (name)
  Permission (key)                                    keys are dot-paths like 'education.schedule.read'

Edges:
  Route   -[:CHILD_OF]->     Route
  Route   -[:RENDERS]->      Component
  Route   -[:GUARDED_BY]->   Guard
  Route   -[:REQUIRES]->     Permission
  Component -[:CALLS_HOOK]-> GqlHook
  Component -[:USES_OPERATION]-> GqlOperation

Notes:

  * Tree-sitter gives us the JSX tree; SCIP gives us the actual file
    path each lazily-imported component resolves to (via the import
    path of `React.lazy(() => import('@pages/...').then((m) => m.X))`).
  * Template-literal `path` values like `:id/${PageMode.EDIT}` are
    resolved by parsing `router/types.ts` for the PageMode enum first.
    Unknown member expressions degrade to the lowercased property name
    so route paths still survive even if an enum import goes missing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Optional

import tree_sitter

from ..writers.graph import NODE_KEYS  # noqa: F401  — keep label registry in sync conceptually
from .base import Extractor, IndexerContext, IngestResult
from .ts_resolver import load_resolver
from .tree_sitter_util import find_pair, parse, text, walk

ROUTER_PATH: Final = "src/router/index.tsx"
ENUM_PATH: Final = "src/router/types.ts"

# Tag names that aren't real components — they're layouts/wrappers.
_REACT_ROUTER_NATIVE = {"Navigate", "Outlet"}
_WRAPPER_NAMES = {"DashboardLayout", "Suspense"}
_GUARD_NAMES = {
    "AuthGuard",
    "RouterGuard",
    "RoleBasedGuard",
    "RoleBasedDisableGuard",
}


def _is_layout_or_wrapper(name: str) -> bool:
    if name in _REACT_ROUTER_NATIVE:
        return True
    if name in _WRAPPER_NAMES:
        return True
    return name.startswith("Lazy")


def _is_guard(name: str) -> bool:
    return name in _GUARD_NAMES


# ---------------------------------------------------------------------------
# Enum parsing (PageMode etc.) — used to resolve template-literal route paths
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EnumValues:
    """{ enum_name: { member_name: value_or_lowercase_name } }."""
    values: dict[str, dict[str, str]] = field(default_factory=dict)

    def resolve_member(self, enum_name: str, member: str) -> str:
        members = self.values.get(enum_name) or {}
        return members.get(member, member.lower())


def parse_enums(code_root: Path) -> EnumValues:
    """Parse top-level string enums in router/types.ts.

    Tree-sitter parses an enum_declaration with a body of
    property_identifier = literal pairs.
    """
    out = EnumValues()
    path = code_root / ENUM_PATH
    if not path.exists():
        return out
    tree, src = parse("typescript", path)
    for node in walk(tree.root_node):
        if node.type != "enum_declaration":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        enum_name = text(name_node, src)
        body = node.child_by_field_name("body")
        if body is None:
            continue
        members: dict[str, str] = {}
        for assign in body.named_children:
            if assign.type not in {"property_identifier", "enum_assignment"}:
                continue
            if assign.type == "property_identifier":
                # Auto-numbered enum member — fall back to lowercase name.
                m_name = text(assign, src)
                members[m_name] = m_name.lower()
                continue
            m_name_node = assign.child_by_field_name("name")
            m_val_node = assign.child_by_field_name("value")
            if m_name_node is None:
                continue
            m_name = text(m_name_node, src)
            value = m_name.lower()
            if m_val_node is not None and m_val_node.type == "string":
                # Strip the surrounding quotes — string_fragment is the
                # first named child.
                if m_val_node.named_child_count:
                    value = text(m_val_node.named_children[0], src)
            members[m_name] = value
        out.values[enum_name] = members
    return out


# ---------------------------------------------------------------------------
# Lazy-imported components (router-local name → file)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class LazyComponent:
    local_name: str
    file: str            # repo-package-relative, matches scip Document.relative_path
    line: int
    exported_name: str


def collect_lazy_components(
    code_root: Path,
    src: bytes,
    root: tree_sitter.Node,
) -> dict[str, LazyComponent]:
    """Map every `const X = React.lazy(() => import('mod').then((m) => m.Foo))`
    to a LazyComponent record. The `file` field is the resolved path of
    the imported module, relative to the package root."""
    resolver = load_resolver(code_root / "tsconfig.json")
    out: dict[str, LazyComponent] = {}
    for decl in walk(root):
        if decl.type != "lexical_declaration":
            continue
        for vd in decl.named_children:
            if vd.type != "variable_declarator":
                continue
            name_node = vd.child_by_field_name("name")
            value_node = vd.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            local_name = text(name_node, src)
            module_path, exported = _extract_lazy(value_node, src)
            if module_path is None:
                continue
            real = resolver.resolve_to_package_relative(module_path, code_root)
            if real is None:
                continue
            out[local_name] = LazyComponent(
                local_name=local_name,
                file=real,
                line=name_node.start_point[0] + 1,
                exported_name=exported or local_name,
            )
    return out


_IMPORT_STRING_RX = re.compile(r"""['"]([^'"]+)['"]""")


def _extract_lazy(value_node: tree_sitter.Node, src: bytes) -> tuple[Optional[str], Optional[str]]:
    """Inspect a variable_declarator value. If it looks like
    `React.lazy(() => import('mod').then((m) => ({ default: m.X })))`
    return (module_path, exported_name). Otherwise (None, None)."""
    raw = text(value_node, src)
    if "React.lazy" not in raw and "lazy(" not in raw:
        return None, None
    m = re.search(r"import\(\s*['\"]([^'\"]+)['\"]\s*\)", raw)
    if not m:
        return None, None
    module_path = m.group(1)
    # exported name: `m.<name>` after the .then default
    em = re.search(r"default:\s*m\.([A-Za-z_]\w*)", raw)
    return module_path, em.group(1) if em else None


# ---------------------------------------------------------------------------
# JSX analysis: collect guards, components, permissions
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class JsxAnalysis:
    guards: list[str] = field(default_factory=list)
    permissions: list[str] = field(default_factory=list)
    components: list[str] = field(default_factory=list)


def _strip_parens(node: tree_sitter.Node) -> tree_sitter.Node:
    while node.type == "parenthesized_expression" and node.named_child_count:
        node = node.named_children[0]
    return node


def _jsx_tag_name(opening: tree_sitter.Node, src: bytes) -> str:
    name = opening.child_by_field_name("name")
    return text(name, src) if name is not None else ""


def _dot_path_from_member(expr: tree_sitter.Node, src: bytes) -> str | None:
    """Walk a `PERMISSIONS.a.b.c` chain. Returns 'a.b.c' (root stripped)
    when the root is PERMISSIONS, otherwise the full dot-path."""
    parts: list[str] = []
    cur = expr
    while cur is not None and cur.type == "member_expression":
        prop = cur.child_by_field_name("property")
        if prop is None:
            break
        parts.append(text(prop, src))
        cur = cur.child_by_field_name("object")
    if cur is not None and cur.type == "identifier":
        root = text(cur, src)
        if root == "PERMISSIONS":
            return ".".join(reversed(parts))
        return ".".join([root, *reversed(parts)])
    return ".".join(reversed(parts)) or None


def _collect_guard_attrs(opening: tree_sitter.Node, src: bytes, acc: JsxAnalysis) -> None:
    """Read `roles={PERMISSIONS.x.y.z}` from a guard's opening element."""
    for attr in opening.named_children:
        if attr.type != "jsx_attribute":
            continue
        if not attr.named_children:
            continue
        name_node = attr.named_children[0]
        if name_node.type != "property_identifier" or text(name_node, src) != "roles":
            continue
        val_node = attr.named_children[1] if attr.named_child_count > 1 else None
        if val_node is None or val_node.type != "jsx_expression":
            continue
        for inner in val_node.named_children:
            if inner.type == "member_expression":
                dp = _dot_path_from_member(inner, src)
                if dp:
                    acc.permissions.append(dp)


def analyze_jsx(node: tree_sitter.Node, src: bytes, acc: JsxAnalysis) -> None:
    node = _strip_parens(node)
    kind = node.type
    if kind == "jsx_self_closing_element":
        name = _jsx_tag_name(node, src)
        if _is_guard(name):
            acc.guards.append(name)
            _collect_guard_attrs(node, src, acc)
        elif name and not _is_layout_or_wrapper(name):
            acc.components.append(name)
        return
    if kind == "jsx_element":
        opening = node.named_children[0] if node.named_child_count else None
        if opening is not None and opening.type == "jsx_opening_element":
            name = _jsx_tag_name(opening, src)
            if _is_guard(name):
                acc.guards.append(name)
                _collect_guard_attrs(opening, src, acc)
            elif name and not _is_layout_or_wrapper(name):
                acc.components.append(name)
        # children may include nested jsx_elements/expressions
        for child in node.named_children[1:-1]:
            analyze_jsx(child, src, acc)
        return
    if kind == "jsx_fragment":
        for child in node.named_children:
            analyze_jsx(child, src, acc)
        return
    if kind == "jsx_expression":
        for child in node.named_children:
            analyze_jsx(child, src, acc)
        return
    # Anything else: walk through (parens already stripped above).
    for child in node.named_children:
        analyze_jsx(child, src, acc)


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def _string_value(node: tree_sitter.Node, src: bytes) -> str | None:
    if node.type == "string":
        if node.named_child_count:
            return text(node.named_children[0], src)
        return text(node, src).strip("'\"`")
    return None


def _resolve_member_value(node: tree_sitter.Node, src: bytes, enums: EnumValues) -> str:
    """PageMode.EDIT → 'edit'. Falls back to lowercased property name."""
    obj = node.child_by_field_name("object")
    prop = node.child_by_field_name("property")
    if prop is None:
        return ""
    enum_name = text(obj, src) if obj is not None else ""
    member = text(prop, src)
    return enums.resolve_member(enum_name, member)


def _resolve_path(node: tree_sitter.Node, src: bytes, enums: EnumValues) -> str | None:
    """Turn a path-property value into the literal path segment."""
    if node.type == "string":
        return _string_value(node, src)
    if node.type == "template_string":
        out: list[str] = []
        for piece in node.named_children:
            if piece.type == "string_fragment":
                out.append(text(piece, src))
            elif piece.type == "template_substitution":
                inner = piece.named_children[0] if piece.named_child_count else None
                if inner is None:
                    out.append("")
                elif inner.type == "member_expression":
                    out.append(_resolve_member_value(inner, src, enums))
                elif inner.type == "identifier":
                    out.append(text(inner, src).lower())
                else:
                    out.append("${" + text(inner, src) + "}")
        return "".join(out)
    if node.type == "member_expression":
        return _resolve_member_value(node, src, enums)
    return None


def _bool_value(node: tree_sitter.Node, src: bytes) -> bool:
    return node.type == "true" or text(node, src) == "true"


def _join_path(parent: str | None, segment: str | None, *, index: bool) -> str:
    if not parent or parent == "":
        if segment is None:
            return "/"
        return segment if segment.startswith("/") else "/" + segment
    if index or segment is None:
        return parent
    if segment.startswith("/"):
        return segment
    p = parent.rstrip("/")
    s = segment.lstrip("/")
    return f"{p}/{s}"


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class RouteRecord:
    path: str
    index: bool
    depth: int
    parent_path: str | None
    file: str
    line: int
    components: list[str]
    guards: list[str]
    permissions: list[str]


def walk_routes(
    arr: tree_sitter.Node,
    src: bytes,
    enums: EnumValues,
    rel_file: str,
    parent_path: str | None,
    depth: int,
    out: list[RouteRecord],
) -> None:
    for obj in arr.named_children:
        if obj.type != "object":
            continue
        line = obj.start_point[0] + 1
        path_val = find_pair(obj, "path", src)
        seg = _resolve_path(path_val, src, enums) if path_val is not None else None
        idx_val = find_pair(obj, "index", src)
        idx = _bool_value(idx_val, src) if idx_val is not None else False
        full = _join_path(parent_path, seg, index=idx)

        analysis = JsxAnalysis()
        el_val = find_pair(obj, "element", src)
        if el_val is not None:
            analyze_jsx(el_val, src, analysis)

        out.append(RouteRecord(
            path=full,
            index=idx,
            depth=depth,
            parent_path=parent_path,
            file=rel_file,
            line=line,
            components=analysis.components,
            guards=sorted(set(analysis.guards)),
            permissions=sorted(set(analysis.permissions)),
        ))

        children_val = find_pair(obj, "children", src)
        if children_val is not None and children_val.type == "array":
            walk_routes(children_val, src, enums, rel_file, full, depth + 1, out)


def find_routes_array(root: tree_sitter.Node, src: bytes) -> tree_sitter.Node | None:
    """Find `const routes = [...]` at top level. The router exports a
    component that calls useRoutes(routes); we want the array, not the
    call."""
    for node in walk(root):
        if node.type != "variable_declarator":
            continue
        name_node = node.child_by_field_name("name")
        if name_node is None or text(name_node, src) != "routes":
            continue
        value = node.child_by_field_name("value")
        if value is not None and value.type == "array":
            return value
    return None


# ---------------------------------------------------------------------------
# Extractor entrypoint
# ---------------------------------------------------------------------------

class RoutesExtractor:
    NAME = "routes"

    def run(self, ctx: IndexerContext) -> IngestResult:
        router_file = ctx.project.code_root / ROUTER_PATH
        if not router_file.exists():
            return IngestResult(stats={"routes": 0, "skipped": 1})

        tree, src = parse("tsx", router_file)
        root = tree.root_node

        enums = parse_enums(ctx.project.code_root)
        lazies = collect_lazy_components(ctx.project.code_root, src, root)
        arr = find_routes_array(root, src)
        if arr is None:
            return IngestResult(stats={"routes": 0, "skipped": 1})

        records: list[RouteRecord] = []
        walk_routes(arr, src, enums, ROUTER_PATH, None, 0, records)

        # Build node lists.
        routes_nodes: list[dict] = []
        for r in records:
            routes_nodes.append({
                "project": ctx.project.name,
                "path": r.path,
                "index": r.index,
                "depth": r.depth,
                "file": r.file,
                "line": r.line,
                "parent_path": r.parent_path,
            })

        components_nodes: list[dict] = []
        component_keys_seen: set[tuple[str, str]] = set()
        for local_name, lc in lazies.items():
            key = (lc.file, lc.exported_name)
            if key in component_keys_seen:
                continue
            component_keys_seen.add(key)
            components_nodes.append({
                "project": ctx.project.name,
                "name": lc.exported_name,
                "file": lc.file,
                "line": lc.line,
                "exported": True,
                "router_local": local_name,
            })

        guard_names = sorted({g for r in records for g in r.guards})
        guards_nodes = [{"project": ctx.project.name, "name": g} for g in guard_names]

        perm_keys = sorted({p for r in records for p in r.permissions})
        permissions_nodes = [{"project": ctx.project.name, "key": k} for k in perm_keys]

        # File nodes for the router + each component file (keeps File
        # identity consistent with the gql extractor's expectations).
        files_seen: set[str] = {ROUTER_PATH}
        files_seen.update(lc.file for lc in lazies.values())
        files_nodes = [{"project": ctx.project.name, "path": p} for p in sorted(files_seen)]

        # Edges.
        edges: list[dict] = []

        def route_key(r: RouteRecord) -> dict:
            return {
                "project": ctx.project.name,
                "path": r.path,
                "depth": r.depth,
                "line": r.line,
            }

        # Route -[:CHILD_OF]-> Route. Match the parent by its (path, depth-1)
        # — depth-1 may have several entries (the index:true sibling at the
        # parent's path), but they all share the same parent slot so the
        # edge is unambiguous.
        parents_by_pd: dict[tuple[str, int], list[RouteRecord]] = {}
        for r in records:
            parents_by_pd.setdefault((r.path, r.depth), []).append(r)
        for r in records:
            if r.parent_path is None:
                continue
            for parent in parents_by_pd.get((r.parent_path, r.depth - 1), []):
                edges.append({
                    "source": {"label": "Route", "key": route_key(r)},
                    "type": "CHILD_OF",
                    "target": {"label": "Route", "key": route_key(parent)},
                })

        # Route -[:RENDERS]-> Component  (resolve JSX local name through
        # the lazy map; drop unmapped names — they're usually wrappers
        # we didn't classify).
        for r in records:
            for comp_local in r.components:
                lc = lazies.get(comp_local)
                if lc is None:
                    continue
                edges.append({
                    "source": {"label": "Route", "key": route_key(r)},
                    "type": "RENDERS",
                    "target": {
                        "label": "Component",
                        "key": {"project": ctx.project.name, "file": lc.file, "name": lc.exported_name},
                    },
                })

        # Route -[:GUARDED_BY]-> Guard
        for r in records:
            for g in r.guards:
                edges.append({
                    "source": {"label": "Route", "key": route_key(r)},
                    "type": "GUARDED_BY",
                    "target": {"label": "Guard", "key": {"project": ctx.project.name, "name": g}},
                })

        # Route -[:REQUIRES]-> Permission
        for r in records:
            for p in r.permissions:
                edges.append({
                    "source": {"label": "Route", "key": route_key(r)},
                    "type": "REQUIRES",
                    "target": {"label": "Permission", "key": {"project": ctx.project.name, "key": p}},
                })

        # Component -[:CALLS_HOOK]-> GqlHook
        # Component -[:USES_OPERATION]-> GqlOperation
        # Discovered via SCIP: any non-definition occurrence in a
        # component's file targeting a known gql hook/op symbol.
        gql_hook_syms: dict[str, str] = {}
        gql_op_syms: dict[str, str] = {}
        for info in ctx.scip.local_definitions():
            short = info.symbol  # use full symbol — that's what File→Gql edges use
            # We don't need to re-derive here; just record the symbol
            # for any document scan below.
            gql_hook_syms[short] = short
            gql_op_syms[short] = short
        # The above is too permissive — narrow with a path heuristic via
        # the existing scip documents. We'll filter by the file path
        # prefix of each symbol's *definition* document.
        hook_symbols: set[str] = set()
        op_symbols: set[str] = set()
        for info in ctx.scip.local_definitions():
            if not info.definitions:
                continue
            d = info.definitions[0]
            f = d.file
            if f.startswith("src/gql/hooks/"):
                hook_symbols.add(info.symbol)
            elif f.startswith("src/gql/queries/"):
                op_symbols.add(info.symbol)

        for lc in lazies.values():
            doc = ctx.scip.documents.get(lc.file)
            if doc is None:
                continue
            wraps_hooks: set[str] = set()
            wraps_ops: set[str] = set()
            for occ in doc.occurrences:
                if occ.symbol_roles & 1:
                    continue
                if occ.symbol in hook_symbols:
                    wraps_hooks.add(occ.symbol)
                elif occ.symbol in op_symbols:
                    wraps_ops.add(occ.symbol)
            for hsym in wraps_hooks:
                edges.append({
                    "source": {
                        "label": "Component",
                        "key": {"project": ctx.project.name, "file": lc.file, "name": lc.exported_name},
                    },
                    "type": "CALLS_HOOK",
                    "target": {"label": "GqlHook", "key": {"project": ctx.project.name, "symbol": hsym}},
                })
            for osym in wraps_ops:
                edges.append({
                    "source": {
                        "label": "Component",
                        "key": {"project": ctx.project.name, "file": lc.file, "name": lc.exported_name},
                    },
                    "type": "USES_OPERATION",
                    "target": {"label": "GqlOperation", "key": {"project": ctx.project.name, "symbol": osym}},
                })

        return IngestResult(
            nodes={
                "Route": routes_nodes,
                "Component": components_nodes,
                "Guard": guards_nodes,
                "Permission": permissions_nodes,
                "File": files_nodes,
            },
            edges=edges,
            stats={
                "routes": len(routes_nodes),
                "components": len(components_nodes),
                "guards": len(guards_nodes),
                "permissions": len(permissions_nodes),
                "edges": len(edges),
            },
        )


EXTRACTOR: Extractor = RoutesExtractor()  # type: ignore[assignment]
