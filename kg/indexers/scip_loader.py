"""Reads a `.scip` file and groups occurrences by symbol.

The SCIP format is protobuf-encoded. We use the bindings generated
from `scip-indexer/scip.proto` (Python module sits in scip-indexer/).
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

# Generated bindings live in scip-indexer/ — make them importable.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scip-indexer"))
import scip_pb2  # noqa: E402

ROLE_DEFINITION = 1


@dataclass(slots=True)
class Occurrence:
    symbol: str
    file: str
    line: int
    column: int
    is_definition: bool


@dataclass(slots=True)
class SymbolInfo:
    symbol: str
    definitions: list[Occurrence] = field(default_factory=list)
    references: list[Occurrence] = field(default_factory=list)


_PREFIX_RX = re.compile(r"^scip-typescript\s+npm\s+\S+\s+\S+\s+")


def short_symbol(symbol: str) -> str:
    """Drop `scip-typescript npm <pkg> <ver>` prefix."""
    return _PREFIX_RX.sub("", symbol)


def is_local_symbol(symbol: str, project_pkg: str = "adsw") -> bool:
    """True if the symbol belongs to the indexed package, not a dep."""
    return symbol.startswith(f"scip-typescript npm {project_pkg} ") or symbol.startswith("local ")


@dataclass(slots=True)
class ScipIndex:
    documents: dict[str, scip_pb2.Document]
    symbols: dict[str, SymbolInfo]

    @classmethod
    def load(cls, scip_path: Path) -> ScipIndex:
        idx = scip_pb2.Index()
        with open(scip_path, "rb") as f:
            idx.ParseFromString(f.read())

        documents: dict[str, scip_pb2.Document] = {}
        symbols: dict[str, SymbolInfo] = defaultdict(lambda: SymbolInfo(""))

        for doc in idx.documents:
            documents[doc.relative_path] = doc
            for occ in doc.occurrences:
                rec = Occurrence(
                    symbol=occ.symbol,
                    file=doc.relative_path,
                    line=occ.range[0] + 1,
                    column=occ.range[1],
                    is_definition=bool(occ.symbol_roles & ROLE_DEFINITION),
                )
                info = symbols[occ.symbol]
                if not info.symbol:
                    info.symbol = occ.symbol
                (info.definitions if rec.is_definition else info.references).append(rec)

        return cls(documents=documents, symbols=dict(symbols))

    def local_definitions(self, project_pkg: str = "adsw") -> list[SymbolInfo]:
        return [
            info for info in self.symbols.values()
            if info.definitions and is_local_symbol(info.symbol, project_pkg)
        ]


# Parser for the short symbol path produced by short_symbol().
# Layout:  <dir>/`<filename>`/<NAME>.
# `<NAME>` may include `Type#field.` qualifiers — for now we only want
# plain top-level identifiers, so the regex demands NAME without `#`.
_SYMBOL_PATH_RX = re.compile(
    r"^(?P<dir>(?:[^`]+/)?)`(?P<file>[^`]+)`/(?P<name>[A-Za-z_][\w$]*)\.$"
)


def parse_symbol_path(short: str) -> tuple[str, str] | None:
    """Return (relative_file_path, identifier) or None for symbols we
    can't classify (locals, type members, etc)."""
    m = _SYMBOL_PATH_RX.match(short)
    if not m:
        return None
    return m["dir"] + m["file"], m["name"]
