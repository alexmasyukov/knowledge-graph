"""Unit tests for extractors that don't need SCIP — they read the
filesystem with tree-sitter only. We exercise their `run()` against a
synthetic workspace and check the shape of the output."""

from __future__ import annotations

from kg.indexers.base import IndexerContext
from kg.indexers.docs import EXTRACTOR as DOCS
from kg.indexers.pages import EXTRACTOR as PAGES
from kg.indexers.permissions import EXTRACTOR as PERMISSIONS
from kg.indexers.scss import EXTRACTOR as SCSS
from kg.indexers.types_extractor import EXTRACTOR as TYPES


class _NullScip:
    documents: dict = {}
    symbols: dict = {}

    def local_definitions(self):
        return []


def _ctx(sample_project) -> IndexerContext:
    return IndexerContext(project=sample_project, scip=_NullScip())  # type: ignore[arg-type]


def test_permissions_extractor(sample_project):
    res = PERMISSIONS.run(_ctx(sample_project))
    perms = {p["key"]: p for p in res.nodes["Permission"]}
    assert "home.read" in perms
    assert "billing.invoice.read" in perms
    assert "billing.invoice.update" in perms
    assert perms["home.read"]["roles"] == ["ADMIN", "USER"]
    # Role nodes deduped
    role_codes = {r["code"] for r in res.nodes["Role"]}
    assert role_codes == {"ADMIN", "USER"}
    # GRANTS edges: 2 + 1 + 1 = 4 (well, 2 for home, 1 for billing.invoice.read+update each)
    grants = [e for e in res.edges if e["type"] == "GRANTS"]
    assert len(grants) == 4


def test_pages_extractor_links_components(sample_project):
    res = PAGES.run(_ctx(sample_project))
    pages = {(p["domain"], p["entity"]): p for p in res.nodes["Page"]}
    assert ("home", "Home.tsx") not in pages   # 'home/Home.tsx' is a FILE, not entity
    # Layout is src/pages/<domain>/<entity>/; in our sample,
    # 'home' and 'billing' are domains, but they only contain TSX files
    # directly (no entity subdir). So pages count = 0 for those — they
    # need a subdir to qualify.
    assert len(res.nodes["Page"]) == 0


def test_scss_extractor_top_level_only(sample_project):
    res = SCSS.run(_ctx(sample_project))
    modules = {m["file"]: m for m in res.nodes["ScssModule"]}
    assert "src/components/Button/Button.module.scss" in modules
    classes = set(modules["src/components/Button/Button.module.scss"]["classes"])
    # `nested` is inside `.root` — should be excluded; top-level only.
    assert classes == {"root", "primary"}
    # Consumer was found by the import regex.
    consumes = [
        e for e in res.edges
        if e["type"] == "CONSUMES"
        and e["target"]["key"]["file"] == "src/components/Button/Button.module.scss"
    ]
    assert any(
        e["source"]["key"]["path"] == "src/components/Button/Button.tsx"
        for e in consumes
    )


def test_types_extractor_kinds(sample_project):
    res = TYPES.run(_ctx(sample_project))
    by_name = {t["name"]: t for t in res.nodes["Type"]}
    assert by_name["UserRoleCode"]["kind"] == "type_alias"
    assert by_name["UserPolicyType"]["kind"] == "enum"
    assert by_name["UserPolicyType"]["members"] == ["TERM", "EMAIL"]
    assert by_name["User"]["kind"] == "interface"


def test_docs_extractor_handles_missing_dirs(sample_project):
    # sample_project has no markdown — extractor should return empty without errors.
    res = DOCS.run(_ctx(sample_project))
    assert res.nodes.get("Doc", []) == []
