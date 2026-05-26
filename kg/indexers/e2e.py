"""End-to-end testing graph slice.

Two source classes feed this extractor:

  1. **adsw sources** under `src/**/*.tsx` — every JSX `data-testid` attribute,
     parsed with tree-sitter-tsx. Literal values map to TestId nodes,
     template-literal values whose static prefix is non-empty become
     TestId nodes with `pattern=true` and the prefix as `value`.

  2. **playwright specs** under `playwright/tests/*.spec.ts` (or `e2e/tests/`
     when the project follows the old layout). Each `test(...)` /
     `test.describe(...)` call becomes an E2eSpec node. Locator and
     PageObject extraction stays best-effort: adsw uses pytest-Selenium
     for the bulk of its e2e, so .page.ts / .locators.ts files are rare.

Edges:
  E2eSpec    -[:DECLARED_IN]-> File
  TestId     -[:OWNED_BY]->    File          (adsw source location)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

import tree_sitter

from .base import Extractor, IndexerContext, IngestResult
from .tree_sitter_util import parse, text, walk

# Spec discovery — search both Playwright layouts so we don't have to
# special-case projects.
_SPEC_DIRS: Final = ("playwright/tests", "e2e/tests")
_TEST_KIND_RX = re.compile(r"^test(?:\.(?:describe|only|skip))?$")


def _split_template_testid(t: tree_sitter.Node, src: bytes) -> tuple[str, bool, str]:
    """For a `template_string`, return (prefix, is_pattern, full_text).

    Pattern logic mirrors the one used on master:
      - the prefix is everything up to the first ${...} substitution
      - is_pattern is True iff the template contains a substitution
      - full_text is the raw template (kept for diagnostics)
    """
    pieces: list[str] = []
    is_pattern = False
    for piece in t.named_children:
        if piece.type == "string_fragment":
            pieces.append(text(piece, src))
        elif piece.type == "template_substitution":
            is_pattern = True
            break
    return "".join(pieces), is_pattern, text(t, src)


def _string_literal(n: tree_sitter.Node, src: bytes) -> str | None:
    """`string` node → its inner text (drops the surrounding quotes)."""
    if n.type != "string":
        return None
    if n.named_child_count:
        return text(n.named_children[0], src)
    return text(n, src).strip("'\"`")


def _testid_value_node(attr_value: tree_sitter.Node, src: bytes) -> tuple[str, bool] | None:
    """Resolve a JSX `data-testid` attribute value into (value, pattern).
    Returns None when the value is non-static (object access, function
    call, etc) — we drop those to avoid false positives like `{period.testId}`."""
    if attr_value.type == "string":
        lit = _string_literal(attr_value, src) or ""
        return (lit, False) if lit else None
    if attr_value.type == "jsx_expression":
        if not attr_value.named_child_count:
            return None
        inner = attr_value.named_children[0]
        if inner.type == "string":
            lit = _string_literal(inner, src) or ""
            return (lit, False) if lit else None
        if inner.type == "template_string":
            prefix, is_pattern, _full = _split_template_testid(inner, src)
            if is_pattern and not prefix:
                return None  # empty-prefix template matches anything; drop
            return (prefix, is_pattern)
    return None


def _scan_adsw_testids(code_root: Path) -> list[dict]:
    """Walk `<code_root>/src/**/*.tsx` and collect every JSX
    data-testid attribute that resolves to a static value."""
    out: list[dict] = []
    src_dir = code_root / "src"
    if not src_dir.is_dir():
        return out
    for path in src_dir.rglob("*.tsx"):
        if "node_modules" in path.parts or "__tests__" in path.parts:
            continue
        try:
            tree, src = parse("tsx", path)
        except Exception:
            continue
        rel = str(path.relative_to(code_root))
        for node in walk(tree.root_node):
            if node.type != "jsx_attribute":
                continue
            if not node.named_child_count:
                continue
            name_node = node.named_children[0]
            if name_node.type != "property_identifier":
                continue
            if text(name_node, src) != "data-testid":
                continue
            if node.named_child_count < 2:
                continue
            resolved = _testid_value_node(node.named_children[1], src)
            if resolved is None:
                continue
            value, pattern = resolved
            out.append({
                "value": value,
                "pattern": pattern,
                "file": rel,
                "line": name_node.start_point[0] + 1,
            })
    return out


def _scan_specs(repo_root: Path) -> list[dict]:
    """Walk Playwright spec files. Returns one row per test() call."""
    out: list[dict] = []
    for sub in _SPEC_DIRS:
        spec_dir = repo_root / sub
        if not spec_dir.is_dir():
            continue
        for path in sorted(spec_dir.rglob("*.spec.ts")):
            try:
                tree, src = parse("typescript", path)
            except Exception:
                continue
            rel = str(path.relative_to(repo_root))
            for node in walk(tree.root_node):
                if node.type != "call_expression":
                    continue
                callee = node.child_by_field_name("function")
                if callee is None:
                    continue
                callee_text = text(callee, src)
                if not _TEST_KIND_RX.match(callee_text):
                    continue
                args = node.child_by_field_name("arguments")
                if args is None or args.named_child_count == 0:
                    continue
                first_arg = args.named_children[0]
                title = _string_literal(first_arg, src)
                if title is None and first_arg.type == "template_string":
                    title = _split_template_testid(first_arg, src)[2].strip("`")
                if not title:
                    continue
                out.append({
                    "name": title,
                    "file": rel,
                    "line": node.start_point[0] + 1,
                    "kind": callee_text,
                })
    return out


class E2eExtractor:
    NAME = "e2e"

    def run(self, ctx: IndexerContext) -> IngestResult:
        # 1. data-testid sources
        adsw_rows = _scan_adsw_testids(ctx.project.code_root)
        testid_nodes: dict[tuple[str, bool], dict] = {}
        files_seen: set[str] = set()
        testid_edges: list[dict] = []
        for r in adsw_rows:
            key = (r["value"], r["pattern"])
            files_seen.add(r["file"])
            testid_nodes.setdefault(key, {
                "project": ctx.project.name,
                "value": r["value"],
                "pattern": r["pattern"],
                "first_file": r["file"],
                "first_line": r["line"],
            })
            testid_edges.append({
                "source": {
                    "label": "TestId",
                    "key": {"project": ctx.project.name, "value": r["value"], "pattern": r["pattern"]},
                },
                "type": "OWNED_BY",
                "target": {"label": "File", "key": {"project": ctx.project.name, "path": r["file"]}},
                "props": {"line": r["line"]},
            })

        # 2. specs
        spec_rows = _scan_specs(ctx.project.repo_root)
        spec_nodes: list[dict] = []
        spec_edges: list[dict] = []
        for r in spec_rows:
            files_seen.add(r["file"])
            spec_nodes.append({
                "project": ctx.project.name,
                "name": r["name"],
                "file": r["file"],
                "line": r["line"],
                "kind": r["kind"],
            })
            spec_edges.append({
                "source": {
                    "label": "E2eSpec",
                    "key": {"project": ctx.project.name, "file": r["file"], "line": r["line"]},
                },
                "type": "DECLARED_IN",
                "target": {"label": "File", "key": {"project": ctx.project.name, "path": r["file"]}},
            })

        file_nodes = [{"project": ctx.project.name, "path": p} for p in sorted(files_seen)]

        return IngestResult(
            nodes={
                "TestId": list(testid_nodes.values()),
                "E2eSpec": spec_nodes,
                "File": file_nodes,
            },
            edges=testid_edges + spec_edges,
            stats={
                "testids": len(testid_nodes),
                "testid_occurrences": len(adsw_rows),
                "specs": len(spec_nodes),
                "files": len(file_nodes),
            },
        )


EXTRACTOR: Extractor = E2eExtractor()  # type: ignore[assignment]
