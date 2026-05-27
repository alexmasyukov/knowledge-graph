"""<PageDataProvider dataLoaderHook={X}> binds X as the data dependency of
its enclosing Component, no matter how deeply nested the element sits.

Example (adsw, src/pages/partner/distroApplications/DistroApplication.tsx):

    export function DistroApplication() {
      return (
        <PageDataProvider dataLoaderHook={useFormData}>
          <ItemPage><Form /></ItemPage>
        </PageDataProvider>
      )
    }

`DistroApplication` itself doesn't call any gql hook directly, but the data
flow through PageDataProvider means it *depends on* useFormData (which in
turn calls usePartnerDistroApplications which wraps GET_PARTNER_…).

This convention turns that into explicit graph edges:

  Component  -[:LOADS_DATA_VIA {convention: 'page_data_provider'}]->  LocalHook
  LocalHook  -[:CALLS_HOOK]->                                          GqlHook

…so a viz query like
    MATCH (c:Component {name: 'DistroApplication'})-[*1..3]->(target)
shows the full data stack rather than dead-ending at the component.

Algorithm:
  1. Iterate every Component file already in the graph.
  2. Parse with tree-sitter-tsx, walk the whole AST (depth-independent).
  3. Match jsx_element / jsx_self_closing_element with tag PageDataProvider.
  4. Find the dataLoaderHook attribute, take its identifier value.
  5. Resolve identifier → its definition file via SCIP (the identifier's
     occurrence at the matched (row, col) carries a symbol → definitions[0]).
  6. Emit LocalHook(file, name) + edges. Reuse the existing File→GqlHook
     edges to also draw LocalHook→GqlHook (file-level approximation: any
     gql hook called anywhere in the LocalHook's file).
"""

from __future__ import annotations

from typing import Final

import tree_sitter

from ...db import session
from ..base import IndexerContext, IngestResult
from ..tree_sitter_util import parse, text, walk

NAME_TAG: Final = "PageDataProvider"
ATTR_NAME: Final = "dataLoaderHook"


def _find_identifier_in_jsx_expression(value_node: tree_sitter.Node) -> tree_sitter.Node | None:
    """`{useFormData}` → identifier node. `{() => useFormData()}` → first
    identifier inside. Returns None for non-static forms like {obj.field}."""
    if value_node.type != "jsx_expression" or not value_node.named_child_count:
        return None
    inner = value_node.named_children[0]
    if inner.type == "identifier":
        return inner
    # Arrow function fallback: look for the first identifier inside.
    if inner.type == "arrow_function":
        body = inner.child_by_field_name("body")
        if body is not None:
            for n in walk(body):
                if n.type == "identifier":
                    return n
    return None


def _scip_def_for_occurrence(scip, file: str, row: int, col: int) -> tuple[str, int] | None:
    """Locate the SCIP occurrence at (row, col) in `file` and return its
    definition's (file, line). row/col are 0-indexed (tree-sitter style)."""
    doc = scip.documents.get(file)
    if doc is None:
        return None
    target_symbol = None
    for occ in doc.occurrences:
        if occ.range[0] == row and occ.range[1] == col:
            target_symbol = occ.symbol
            break
    if target_symbol is None:
        return None
    info = scip.symbols.get(target_symbol)
    if info is None or not info.definitions:
        return None
    d = info.definitions[0]
    return d.file, d.line


def _opening_element(node: tree_sitter.Node) -> tree_sitter.Node | None:
    """For jsx_element returns the opening tag; for jsx_self_closing_element
    returns the node itself."""
    if node.type == "jsx_self_closing_element":
        return node
    if node.type != "jsx_element":
        return None
    if not node.named_child_count:
        return None
    first = node.named_children[0]
    return first if first.type == "jsx_opening_element" else None


class PageDataProviderConvention:
    NAME = "page_data_provider"

    def run(self, ctx: IndexerContext) -> IngestResult:
        # 1. Snapshot Components from the live graph — conventions run after
        # extractors, so the Component label is already populated.
        with session() as s:
            comp_rows = s.run(
                "MATCH (c:Component {project: $p}) RETURN c.file AS file, c.name AS name",
                p=ctx.project.name,
            ).data()
            gql_rows = s.run(
                """
                MATCH (f:File {project: $p})-[:CALLS_HOOK]->(h:GqlHook)
                RETURN f.path AS file, collect(DISTINCT h.symbol) AS hooks
                """,
                p=ctx.project.name,
            ).data()
        comps_by_file: dict[str, list[str]] = {}
        for r in comp_rows:
            comps_by_file.setdefault(r["file"], []).append(r["name"])
        file_to_gql_hooks: dict[str, list[str]] = {r["file"]: list(r["hooks"] or []) for r in gql_rows}

        local_hooks: dict[tuple[str, str], dict] = {}
        edges: list[dict] = []
        matches = 0

        for rel_path in sorted(comps_by_file):
            if not rel_path.endswith(".tsx"):
                continue
            abs_path = ctx.project.code_root / rel_path
            if not abs_path.exists():
                continue
            try:
                tree, src = parse("tsx", abs_path)
            except Exception:
                continue

            for node in walk(tree.root_node):
                if node.type not in {"jsx_element", "jsx_self_closing_element"}:
                    continue
                opening = _opening_element(node)
                if opening is None:
                    continue
                tag = opening.child_by_field_name("name")
                if tag is None or text(tag, src) != NAME_TAG:
                    continue

                # Look for dataLoaderHook attribute. JSX attributes live as
                # named_children of the opening element; the attr name is
                # the first named child of the jsx_attribute.
                for attr in opening.named_children:
                    if attr.type != "jsx_attribute" or attr.named_child_count < 2:
                        continue
                    attr_name_node = attr.named_children[0]
                    if attr_name_node.type != "property_identifier":
                        continue
                    if text(attr_name_node, src) != ATTR_NAME:
                        continue
                    ident = _find_identifier_in_jsx_expression(attr.named_children[1])
                    if ident is None:
                        continue
                    hook_name = text(ident, src)

                    # Resolve via SCIP.
                    resolved = _scip_def_for_occurrence(
                        ctx.scip, rel_path, ident.start_point[0], ident.start_point[1]
                    )
                    if resolved is None:
                        continue
                    hook_file, hook_line = resolved

                    key = (hook_file, hook_name)
                    local_hooks.setdefault(key, {
                        "project": ctx.project.name,
                        "file": hook_file,
                        "name": hook_name,
                        "line": hook_line,
                    })

                    # Component → LocalHook (one per Component in this file).
                    for comp_name in comps_by_file.get(rel_path, []):
                        edges.append({
                            "source": {
                                "label": "Component",
                                "key": {"project": ctx.project.name, "file": rel_path, "name": comp_name},
                            },
                            "type": "LOADS_DATA_VIA",
                            "target": {
                                "label": "LocalHook",
                                "key": {"project": ctx.project.name, "file": hook_file, "name": hook_name},
                            },
                            "props": {"convention": self.NAME},
                        })

                    # LocalHook → GqlHook (file-level approximation: every gql
                    # hook that anything in this file calls). Good enough for
                    # the common case "one local hook per file".
                    for gql_sym in file_to_gql_hooks.get(hook_file, []):
                        edges.append({
                            "source": {
                                "label": "LocalHook",
                                "key": {"project": ctx.project.name, "file": hook_file, "name": hook_name},
                            },
                            "type": "CALLS_HOOK",
                            "target": {
                                "label": "GqlHook",
                                "key": {"project": ctx.project.name, "symbol": gql_sym},
                            },
                            "props": {"via_file_inference": True},
                        })

                    matches += 1
                    break  # one dataLoaderHook per PageDataProvider

        return IngestResult(
            nodes={"LocalHook": list(local_hooks.values())},
            edges=edges,
            stats={
                "matches": matches,
                "local_hooks": len(local_hooks),
                "edges": len(edges),
            },
        )


CONVENTION = PageDataProviderConvention()
