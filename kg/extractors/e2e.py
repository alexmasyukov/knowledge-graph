"""E2E + testid extractor.

Scans the Playwright e2e directory (e2e/tests/*.spec.ts and
e2e/pages/*.{page,locators}.ts) plus app component sources for
data-testid usage, then bridges the two via testid string values.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..db import session, wipe_labels
from ..settings import settings

NAME = "e2e"
LABELS = ("E2eSpec", "PageObject", "TestIdLoc", "TestId")


_TEST_CALL_RX = re.compile(
    r"""(?P<indent>^[ \t]*)(?P<kind>test|test\.describe|test\.only|test\.skip)\s*\(\s*['"`](?P<name>[^'"`]+)['"`]""",
    re.MULTILINE,
)
_LOC_CONST_RX = re.compile(
    r"export\s+const\s+(?P<name>\w+)\s*(?::\s*[^=]+)?=\s*"
    r"(?P<body>"
    r"(?:\([^)]*\)\s*=>\s*)?"
    r"(?:"
    r"`(?:\\.|[^`])*`"
    r"|'(?:\\.|[^'])*'"
    r"|\"(?:\\.|[^\"])*\""
    r")"
    r")",
    re.DOTALL,
)
_TESTID_LITERAL_RX = re.compile(r"""data-testid=['"]([^'"]+)['"]""")
_TESTID_TEMPLATE_RX = re.compile(r"""data-testid=`([^`]+)`""")
_TESTID_JSX_PROP_RX = re.compile(r"""data-testid=\{\s*[`'"]([^`'"]+)[`'"]""")
_TESTID_JSX_LITERAL_RX = re.compile(r"""(?:data-testid|testid)=['"]([^'"]+)['"]""")
_TESTID_IN_SELECTOR_RX = re.compile(r"""\[data-testid=['"]([^'"]+)['"]\]""")


def _imported_pom_classes(spec_text: str) -> list[str]:
    pom_names: list[str] = []
    for m in re.finditer(
        r"""import\s*(?:type\s*)?\{([^}]+)\}\s*from\s*['"](?:\.\./)+pages/[^'"]+\.page['"]""",
        spec_text,
    ):
        for raw in m.group(1).split(","):
            n = raw.split(" as ")[0].strip()
            n = re.sub(r"^type\s+", "", n)
            if n and n[0].isupper():
                pom_names.append(n)
    return pom_names


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
        for cls in classes:
            pages.append({"name": cls, "file": rel})
    return pages


def _extract_locator_value(body: str) -> tuple[str, bool]:
    parametric = body.lstrip().startswith("(")
    m = re.search(r"['\"`]([^'\"`]+)['\"`]", body)
    if not m:
        return ("", parametric)
    return (m.group(1), parametric)


def _extract_testid_targets(body: str) -> list[str]:
    found: set[str] = set()
    for rx in (_TESTID_IN_SELECTOR_RX, _TESTID_LITERAL_RX, _TESTID_TEMPLATE_RX):
        for m in rx.finditer(body):
            found.add(m.group(1))
    return sorted(found)


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


def _collect_app_testids(app_src: Path, project_root: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not app_src.is_dir():
        return out
    for ext in ("*.tsx", "*.ts"):
        for p in app_src.rglob(ext):
            if "node_modules" in p.parts:
                continue
            try:
                text = p.read_text(encoding="utf-8")
            except Exception:
                continue
            rel = str(p.relative_to(project_root))
            regexes = (
                (_TESTID_JSX_LITERAL_RX, _TESTID_JSX_PROP_RX, _TESTID_TEMPLATE_RX)
                if p.suffix == ".tsx"
                else (_TESTID_JSX_PROP_RX, _TESTID_TEMPLATE_RX, _TESTID_LITERAL_RX)
            )
            for rx in regexes:
                for m in rx.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    out.append({"value": m.group(1), "file": rel, "line": line})
    return out


def _write(project: str, specs, page_objects, locators, testids) -> dict[str, int]:
    pom_by_module: dict[str, list[str]] = {}
    for po in page_objects:
        mod = Path(po["file"]).stem.replace(".page", "")
        pom_by_module.setdefault(mod, []).append(po["name"])

    testid_values = sorted({t["value"] for t in testids} | {t for loc in locators for t in loc["testids"]})

    wipe_labels(project, list(LABELS))

    with session() as s:
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
                    MATCH (sp:E2eSpec    {project: $project, name: i.name, file: i.file, line: i.line})
                    MATCH (po:PageObject {project: $project, name: i.pom})
                    MERGE (sp)-[:USES_POM]->(po)
                    """,
                    project=project,
                    items=spec_pom,
                )

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


async def run(project: str) -> dict[str, Any]:
    cfg = settings.project(project)
    if cfg is None:
        return {"skipped": "no settings"}

    repo_root = cfg.repo_root
    e2e_root = repo_root / "e2e"
    app_src = cfg.code_root / "src"

    specs = _collect_specs(e2e_root, repo_root)
    page_objects = _collect_page_objects(e2e_root, repo_root)
    locators = _collect_locators(e2e_root, repo_root)
    testids = _collect_app_testids(app_src, repo_root)

    counts = _write(project, specs, page_objects, locators, testids)
    return {"written": counts}
