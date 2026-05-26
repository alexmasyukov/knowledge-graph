"""GraphQL operations + hooks extractor backed by SCIP.

Replaces the ts-morph version. The SCIP index gives us pre-resolved
cross-file references, so we just need to recognise *which* local
symbols are gql operations and which are hook wrappers — no AST
traversal required.

Detection rules (adsw conventions):
  - GqlOperation: top-level const in `src/gql/queries/**` whose name is
    in UPPER_SNAKE_CASE (e.g. `ADD_EDUCATION_BOOKING`).
  - GqlHook:      top-level function/const in `src/gql/hooks/**` whose
    name starts with `use` followed by an uppercase letter.

Both rules are applied to SCIP definition symbols, which the indexer
guarantees are real export-level definitions (not local vars).
"""

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from reader import ScipIndex, SymbolInfo, short_symbol

# SCIP symbol path (after stripping the `scip-typescript npm <pkg> <ver>`
# prefix) looks like:
#   `<dir>/<dir>/.../`<filename>`/<member>.`
# where the file name is quoted in backticks. Members may contain
# qualifiers like `Foo#field.` — we only care about plain top-level
# `<NAME>.` exports.
_SYMBOL_PATH_RX = re.compile(
    r"^(?P<dir>(?:[^`]+/)?)`(?P<file>[^`]+)`/(?P<name>[A-Za-z_][\w$]*)\.$"
)


def _parse_symbol(short: str) -> tuple[str, str] | None:
    """Return (relative_file_path, local_name) or None for symbols we skip."""
    m = _SYMBOL_PATH_RX.match(short)
    if not m:
        return None
    return m["dir"] + m["file"], m["name"]


def _is_upper_snake(name: str) -> bool:
    return name == name.upper() and "_" in name and name[0].isalpha()


def _is_hook_name(name: str) -> bool:
    return len(name) >= 4 and name.startswith("use") and name[3].isupper()


def extract(idx: ScipIndex) -> dict:
    """Return dict with `operations` and `hooks` lists."""
    operations: list[dict] = []
    hooks: list[dict] = []

    for info in idx.local_definitions():
        short = short_symbol(info.symbol)
        parsed = _parse_symbol(short)
        if not parsed:
            continue
        file_, name = parsed
        # File path here is package-relative, e.g.
        # `src/gql/queries/booking.ts`. Drop the surrounding backticks if
        # any leaked through (they shouldn't, after the regex).
        is_operation = (
            file_.startswith("src/gql/queries/") and _is_upper_snake(name)
        )
        is_hook = file_.startswith("src/gql/hooks/") and _is_hook_name(name)
        if not (is_operation or is_hook):
            continue

        # Use the *first* definition's location.
        d = info.definitions[0]
        # `d.file` is repo-relative (e.g. `packages/adsw/src/gql/...`).
        record = {
            "name": name,
            "file": d.file,
            "line": d.line,
            "column": d.column,
            "symbol": info.symbol,
            "callsites": len(info.references),
        }
        if is_operation:
            operations.append(record)
        else:
            hooks.append(record)

    operations.sort(key=lambda r: (r["file"], r["name"]))
    hooks.sort(key=lambda r: (r["file"], r["name"]))
    return {"operations": operations, "hooks": hooks}


def callsites(idx: ScipIndex, symbol: str) -> list[dict]:
    """All non-definition occurrences of the given full SCIP symbol."""
    info: SymbolInfo | None = idx.symbols.get(symbol)
    if info is None:
        return []
    return [
        {"file": o.file, "line": o.line, "column": o.column}
        for o in info.references
    ]


def _summary(data: dict) -> None:
    ops = data["operations"]
    hooks_ = data["hooks"]
    op_callsites = sum(r["callsites"] for r in ops)
    hook_callsites = sum(r["callsites"] for r in hooks_)
    print(f"operations:  {len(ops):5d}  (callsites: {op_callsites})")
    print(f"hooks:       {len(hooks_):5d}  (callsites: {hook_callsites})")
    by_file: dict[str, dict[str, int]] = defaultdict(lambda: {"ops": 0, "hooks": 0})
    for r in ops:
        by_file[r["file"]]["ops"] += 1
    for r in hooks_:
        by_file[r["file"]]["hooks"] += 1
    print(f"files with gql content: {len(by_file)}")


if __name__ == "__main__":
    here = Path(__file__).parent
    idx = ScipIndex.load(here / "adsw.scip")
    data = extract(idx)
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        json.dump(data, sys.stdout, indent=2)
    else:
        _summary(data)
