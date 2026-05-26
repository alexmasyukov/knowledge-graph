"""E2E + testid extractor.

Scans the Playwright e2e directory (e2e/tests/*.spec.ts and
e2e/pages/*.{page,locators}.ts) plus adsw component sources for
data-testid usage, then bridges the two via testid string values.

Schema:
    (:E2eSpec     {project, name, file, line})
    (:PageObject  {project, name, file})
    (:TestIdLoc   {project, name, value, file, line, parametric})
    (:TestId      {project, value})

    (E2eSpec)-[:USES_POM]->(PageObject)
    (PageObject)-[:DEFINES_LOC]->(TestIdLoc)
    (TestIdLoc)-[:RESOLVES_TO]->(TestId)
    (File)-[:HAS_TESTID]->(TestId)
    (Component)-[:DEFINES_TESTID]->(TestId)   # if testid is inside that component's file
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from ..db import session
from ..settings import settings


# ── regexes ───────────────────────────────────────────────────────────

_TEST_CALL_RX = re.compile(
    r"""(?P<indent>^[ \t]*)(?P<kind>test|test\.describe|test\.only|test\.skip)\s*\(\s*['"`](?P<name>[^'"`]+)['"`]""",
    re.MULTILINE,
)
_IMPORT_FROM_PAGES_RX = re.compile(
    r"""from\s+['"](?P<spec>\.\./pages/[^'"]+|\.\./\.\./pages/[^'"]+)['"]""",
)
_IMPORT_NAMES_RX = re.compile(r"import\s*(?:type\s*)?(?:\{([^}]+)\}|(\*\s+as\s+\w+)|(\w+))\s*from")
_LOC_CONST_RX = re.compile(
    r"export\s+const\s+(?P<name>\w+)\s*(?::\s*[^=]+)?=\s*"
    r"(?P<body>"
    r"(?:\([^)]*\)\s*=>\s*)?"
    r"(?:"
        r"`(?:\\.|[^`])*`"           # backticks
        r"|'(?:\\.|[^'])*'"          # single quotes
        r"|\"(?:\\.|[^\"])*\""       # double quotes
    r")"
    r")",
    re.DOTALL,
)
_TESTID_LITERAL_RX = re.compile(r"""data-testid=['"]([^'"]+)['"]""")
_TESTID_TEMPLATE_RX = re.compile(r"""data-testid=`([^`]+)`""")
_TESTID_JSX_PROP_RX = re.compile(r"""data-testid=\{\s*[`'"]([^`'"]+)[`'"]""")  # <X data-testid={`foo-${x}`} />
_TESTID_JSX_LITERAL_RX = re.compile(r"""(?:data-testid|testid)=['"]([^'"]+)['"]""")  # JSX attribute literal

# We also catch testid template strings inside literal selectors used in locator files:
#   `[data-testid='sidebar-${x}']` or `[data-testid="foo-${x}"]`
_TESTID_IN_SELECTOR_RX = re.compile(r"""\[data-testid=['"]([^'"]+)['"]\]""")


# ── helpers ───────────────────────────────────────────────────────────

def _project_root(project: str) -> Path | None:
    cfg = next((p for p in settings.projects if p.name == project), None)
    if cfg is None:
        return None
    # repo root is two levels above packages/adsw
    return cfg.root.parent.parent if cfg.root.name == "adsw" else cfg.root.parent


def _imported_pom_classes(spec_text: str) -> list[str]:
    pom_names: list[str] = []
    for m in re.finditer(
        r"""import\s*(?:type\s*)?\{([^}]+)\}\s*from\s*['"](?:\.\./)+pages/[^'"]+\.page['"]""",
        spec_text,
    ):
        for raw in m.group(1).split(","):
            n = raw.split(" as ")[0].strip()
            # drop 'type', generics, etc.
            n = re.sub(r"^type\s+", "", n)
            if n and n[0].isupper():
                pom_names.append(n)
    return pom_names


def _imported_locator_modules(pom_text: str) -> list[str]:
    """Returns relative paths of *.locators imports inside a POM file."""
    out: list[str] = []
    for m in re.finditer(
        r"""from\s+['"](?P<spec>(?:\./|\.\./)+[^'"]*\.locators)['"]""",
        pom_text,
    ):
        out.append(m.group("spec"))
    return out


# ── collectors ────────────────────────────────────────────────────────

def _collect_specs(e2e_root: Path, project_root: Path) -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []
    spec_dir = e2e_root / "tests"
    if not spec_dir.is_dir():
        return specs

    for p in sorted(spec_dir.rglob("*.spec.ts")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(p.relative_to(project_root))
        used_poms = _imported_pom_classes(text)
        for m in _TEST_CALL_RX.finditer(text):
            specs.append(
                {
                    "name": m.group("name"),
                    "file": rel,
                    "line": text.count("\n", 0, m.start()) + 1,
                    "kind": m.group("kind"),
                    "poms": used_poms,
                }
            )
    return specs


def _collect_page_objects(e2e_root: Path, project_root: Path) -> list[dict[str, Any]]:
    pages: list[dict[str, Any]] = []
    pages_dir = e2e_root / "pages"
    if not pages_dir.is_dir():
        return pages

    for p in sorted(pages_dir.glob("*.page.ts")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(p.relative_to(project_root))
        classes = re.findall(r"export\s+class\s+(\w+Page)\b", text)
        loc_imports = _imported_locator_modules(text)
        for cls in classes:
            pages.append({"name": cls, "file": rel, "locator_imports": loc_imports})
    return pages


def _resolve_locator_file(pom_file: Path, ref: str) -> Path | None:
    """Resolve relative import like './booking.locators' against the POM file's dir."""
    candidate = (pom_file.parent / (ref + ".ts")).resolve()
    return candidate if candidate.is_file() else None


def _collect_locators(e2e_root: Path, project_root: Path) -> list[dict[str, Any]]:
    locators: list[dict[str, Any]] = []
    pages_dir = e2e_root / "pages"
    if not pages_dir.is_dir():
        return locators

    for p in sorted(pages_dir.glob("*.locators.ts")):
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(p.relative_to(project_root))

        for m in _LOC_CONST_RX.finditer(text):
            name = m.group("name")
            body = m.group("body")
            line = text.count("\n", 0, m.start()) + 1
            value, parametric = _extract_locator_value(body)
            testids = _extract_testid_targets(body)
            locators.append(
                {
                    "module": p.stem.replace(".locators", ""),
                    "name": name,
                    "value": value,
                    "parametric": parametric,
                    "testids": testids,
                    "file": rel,
                    "line": line,
                }
            )
    return locators


def _extract_locator_value(body: str) -> tuple[str, bool]:
    """Return (representative string, is_parametric). Strips outer quote and
    treats arrow-function bodies as parametric."""
    parametric = body.lstrip().startswith("(")
    # Strip outer template/quote pair
    m = re.search(r"['\"`]([^'\"`]+)['\"`]", body)
    if not m:
        return ("", parametric)
    return (m.group(1), parametric)


def _extract_testid_targets(body: str) -> list[str]:
    """Extract every data-testid value mentioned inside a locator definition."""
    found: set[str] = set()
    for rx in (_TESTID_IN_SELECTOR_RX, _TESTID_LITERAL_RX, _TESTID_TEMPLATE_RX):
        for m in rx.finditer(body):
            v = m.group(1)
            # for parametric "sidebar-${x}" -> store as is; can be matched on prefix later
            found.add(v)
    return sorted(found)


def _collect_adsw_testids(adsw_src: Path, project_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not adsw_src.is_dir():
        return out

    for p in adsw_src.rglob("*.tsx"):
        if "node_modules" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(p.relative_to(project_root))
        for rx in (_TESTID_JSX_LITERAL_RX, _TESTID_JSX_PROP_RX, _TESTID_TEMPLATE_RX):
            for m in rx.finditer(text):
                v = m.group(1)
                line = text.count("\n", 0, m.start()) + 1
                out.append({"value": v, "file": rel, "line": line})
    # also .ts files (helpers that emit testid)
    for p in adsw_src.rglob("*.ts"):
        if "node_modules" in p.parts:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        rel = str(p.relative_to(project_root))
        for rx in (_TESTID_JSX_PROP_RX, _TESTID_TEMPLATE_RX, _TESTID_LITERAL_RX):
            for m in rx.finditer(text):
                v = m.group(1)
                line = text.count("\n", 0, m.start()) + 1
                out.append({"value": v, "file": rel, "line": line})
    return out


# ── writer ────────────────────────────────────────────────────────────

def write_e2e(project: str, payload: dict[str, Any]) -> dict[str, int]:
    specs = payload["specs"]
    page_objects = payload["page_objects"]
    locators = payload["locators"]
    testids = payload["testids"]

    # Build POM name → file mapping
    pom_by_name = {po["name"]: po for po in page_objects}
    pom_by_module = {}
    for po in page_objects:
        mod = Path(po["file"]).stem.replace(".page", "")
        pom_by_module.setdefault(mod, []).append(po["name"])

    # Unique testid values
    testid_values = sorted({t["value"] for t in testids} | {t for loc in locators for t in loc["testids"]})

    with session() as s:
        s.run(
            """
            MATCH (n {project: $project})
            WHERE n:E2eSpec OR n:PageObject OR n:TestIdLoc OR n:TestId
            DETACH DELETE n
            """,
            project=project,
        )

        # PageObjects
        if page_objects:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $items AS po
                CREATE (o:PageObject {project: $project, name: po.name, file: po.file})
                MERGE (o)-[:IN_PROJECT]->(p)
                """,
                project=project,
                items=page_objects,
            )

        # Specs
        if specs:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $items AS sp
                CREATE (s:E2eSpec {
                    project: $project,
                    name:    sp.name,
                    file:    sp.file,
                    line:    sp.line,
                    kind:    sp.kind
                })
                MERGE (s)-[:IN_PROJECT]->(p)
                """,
                project=project,
                items=specs,
            )
            spec_pom = [
                {"name": sp["name"], "file": sp["file"], "line": sp["line"], "pom": p}
                for sp in specs
                for p in sp["poms"]
            ]
            if spec_pom:
                s.run(
                    """
                    UNWIND $items AS i
                    MATCH (sp:E2eSpec   {project: $project, name: i.name, file: i.file, line: i.line})
                    MATCH (po:PageObject {project: $project, name: i.pom})
                    MERGE (sp)-[:USES_POM]->(po)
                    """,
                    project=project,
                    items=spec_pom,
                )

        # TestId values
        if testid_values:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $vals AS v
                MERGE (t:TestId {project: $project, value: v})
                MERGE (t)-[:IN_PROJECT]->(p)
                """,
                project=project,
                vals=testid_values,
            )

        # Locators
        if locators:
            s.run(
                """
                MATCH (p:Project {name: $project})
                UNWIND $items AS loc
                CREATE (l:TestIdLoc {
                    project:    $project,
                    name:       loc.name,
                    module:     loc.module,
                    value:      loc.value,
                    parametric: loc.parametric,
                    file:       loc.file,
                    line:       loc.line
                })
                MERGE (l)-[:IN_PROJECT]->(p)
                """,
                project=project,
                items=locators,
            )

            # Locator → PageObject (by module name)
            loc_pom = []
            for loc in locators:
                for pom_name in pom_by_module.get(loc["module"], []):
                    loc_pom.append({"loc_name": loc["name"], "loc_file": loc["file"], "pom": pom_name})
            if loc_pom:
                s.run(
                    """
                    UNWIND $items AS i
                    MATCH (l:TestIdLoc   {project: $project, name: i.loc_name, file: i.loc_file})
                    MATCH (po:PageObject {project: $project, name: i.pom})
                    MERGE (po)-[:DEFINES_LOC]->(l)
                    """,
                    project=project,
                    items=loc_pom,
                )

            # Locator → TestId
            loc_testid = [
                {"loc_name": loc["name"], "loc_file": loc["file"], "value": v}
                for loc in locators
                for v in loc["testids"]
            ]
            if loc_testid:
                s.run(
                    """
                    UNWIND $items AS i
                    MATCH (l:TestIdLoc {project: $project, name: i.loc_name, file: i.loc_file})
                    MATCH (t:TestId    {project: $project, value: i.value})
                    MERGE (l)-[:RESOLVES_TO]->(t)
                    """,
                    project=project,
                    items=loc_testid,
                )

        # File → TestId (from adsw component files)
        if testids:
            s.run(
                """
                UNWIND $items AS i
                MERGE (f:File   {project: $project, path: i.file})
                MERGE (t:TestId {project: $project, value: i.value})
                MERGE (f)-[:HAS_TESTID {line: i.line}]->(t)
                """,
                project=project,
                items=testids,
            )

    return {
        "specs": len(specs),
        "page_objects": len(page_objects),
        "locators": len(locators),
        "testids_unique": len(testid_values),
        "testid_occurrences": len(testids),
    }


async def run_for_project(project: str) -> dict[str, Any]:
    cfg = next((p for p in settings.projects if p.name == project), None)
    if cfg is None:
        return {"project": project, "skipped": "no settings"}
    project_root = cfg.root.parent.parent
    e2e_root = project_root / "e2e"
    adsw_src = cfg.root / "src"

    specs = _collect_specs(e2e_root, project_root)
    pages = _collect_page_objects(e2e_root, project_root)
    locators = _collect_locators(e2e_root, project_root)
    testids = _collect_adsw_testids(adsw_src, project_root)

    payload = {
        "specs": specs,
        "page_objects": pages,
        "locators": locators,
        "testids": testids,
    }
    counts = write_e2e(project, payload)
    return {"project": project, "written": counts}
