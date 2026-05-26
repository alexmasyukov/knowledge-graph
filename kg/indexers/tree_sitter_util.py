"""Tree-sitter helpers shared by every non-SCIP extractor.

Tree-sitter doesn't carry type information, but it does give us a
precise AST for any source it parses. We use it for things SCIP can't
see — JSX route trees, SCSS class declarations, markdown headings,
e2e spec testid literals — and rely on SCIP for everything else.

The `tree_sitter_language_pack` ships pre-built grammars for the
languages we care about (tsx, typescript, scss, markdown).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterator

import tree_sitter
from tree_sitter_language_pack import get_language


@lru_cache(maxsize=8)
def parser(lang: str) -> tree_sitter.Parser:
    return tree_sitter.Parser(get_language(lang))


def parse(lang: str, path: Path) -> tuple[tree_sitter.Tree, bytes]:
    """Parse a file. Returns (tree, source_bytes) — text helpers need
    the bytes to slice."""
    data = path.read_bytes()
    return parser(lang).parse(data), data


def text(node: tree_sitter.Node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def walk(node: tree_sitter.Node) -> Iterator[tree_sitter.Node]:
    yield node
    for c in node.children:
        yield from walk(c)


def find_pair(obj: tree_sitter.Node, key: str, src: bytes) -> tree_sitter.Node | None:
    """Find a `pair` child of an `object` node whose key matches.
    Returns the value node, or None."""
    if obj.type != "object":
        return None
    for pair in obj.named_children:
        if pair.type != "pair":
            continue
        k = pair.child_by_field_name("key")
        if k is None:
            continue
        kt = text(k, src)
        if kt == key or kt == f"'{key}'" or kt == f'"{key}"':
            return pair.child_by_field_name("value")
    return None
