"""Reader for a SCIP index emitted by scip-typescript.

SCIP is a protobuf-encoded code intelligence format. Every cross-file
reference comes pre-resolved by the indexer, so we don't need
ts-morph's heuristics or findReferences(): the symbol ID is enough.

Symbol format (TypeScript flavour):
  `scip-typescript npm <package> <version> <relative-path>/<member>.`

We strip the prefix to expose just `<relative-path>` and `<local>` for
convenient grep/match downstream.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scip_pb2

ROLE_DEFINITION = 1  # SCIP SymbolRole.Definition bitmask


@dataclass(slots=True)
class Occurrence:
    symbol: str
    file: str
    line: int           # 1-based
    column: int         # 0-based
    is_definition: bool


@dataclass(slots=True)
class SymbolInfo:
    symbol: str
    definitions: list[Occurrence] = field(default_factory=list)
    references: list[Occurrence] = field(default_factory=list)


# `scip-typescript npm <pkg> <ver> <path>` — strip down to <path>
_SYMBOL_PREFIX_RX = re.compile(r"^scip-typescript\s+npm\s+\S+\s+\S+\s+")


def short_symbol(symbol: str) -> str:
    """Drop the `scip-typescript npm <pkg> <ver>` prefix.

    `scip-typescript npm adsw 1.0.0 src/gql/queries/booking.ts/ADD_EDUCATION_BOOKING.`
    →
    `src/gql/queries/booking.ts/ADD_EDUCATION_BOOKING.`
    """
    return _SYMBOL_PREFIX_RX.sub("", symbol)


def is_local_symbol(symbol: str) -> bool:
    """True if the symbol belongs to the indexed package, not a dependency."""
    return symbol.startswith("scip-typescript npm adsw ") or symbol.startswith("local ")


@dataclass
class ScipIndex:
    documents: dict[str, scip_pb2.Document]  # relative_path → Document
    symbols: dict[str, SymbolInfo]           # global symbol id → info

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
                line = occ.range[0] + 1
                col = occ.range[1]
                is_def = bool(occ.symbol_roles & ROLE_DEFINITION)
                rec = Occurrence(
                    symbol=occ.symbol,
                    file=doc.relative_path,
                    line=line,
                    column=col,
                    is_definition=is_def,
                )
                info = symbols[occ.symbol]
                if not info.symbol:
                    info.symbol = occ.symbol
                if is_def:
                    info.definitions.append(rec)
                else:
                    info.references.append(rec)

        return cls(documents=documents, symbols=dict(symbols))

    def local_definitions(self) -> list[SymbolInfo]:
        """All symbols defined in the indexed package (not external deps)."""
        return [
            info for info in self.symbols.values()
            if info.definitions and is_local_symbol(info.symbol)
        ]


def _self_test() -> None:
    here = Path(__file__).parent
    scip_file = here / "adsw.scip"
    if not scip_file.exists():
        print(f"missing {scip_file}", file=sys.stderr)
        sys.exit(1)
    idx = ScipIndex.load(scip_file)
    locals_ = idx.local_definitions()
    print(f"documents:        {len(idx.documents)}")
    print(f"symbols (total):  {len(idx.symbols)}")
    print(f"local definitions: {len(locals_)}")
    sample = locals_[:5]
    for info in sample:
        d = info.definitions[0]
        print(f"  {short_symbol(info.symbol)}  @ {d.file}:{d.line}")


if __name__ == "__main__":
    _self_test()
