"""tsconfig.json path-alias resolver.

Bare-import paths like `@pages/foo/Bar` map to filesystem paths via the
`compilerOptions.paths` map in tsconfig.json. We need this to turn
React.lazy(() => import('@pages/foo/Bar')) into a real source file the
graph can refer to.

Only the subset we need is implemented:
  - exact-key aliases:        "@api": ["../api"]
  - star aliases:             "@pages/*": ["./src/pages/*"]
  - sibling-package aliases:  "@core/*": ["../core/*"]

JSON5 comments (//, /*…*/) are stripped before parsing so adsw's
tsconfig with its inline comments parses cleanly with the stdlib.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _strip_jsonc(text: str) -> str:
    """Remove // line comments and /* … */ block comments from a JSONC
    source, while leaving any `/*` / `//` that appear inside string
    literals untouched. tsconfig path aliases such as "@core/*" routinely
    contain `/*`, so a naive regex strip will eat real config."""
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == '"':
            # Copy a full JSON string, honouring backslash escapes.
            out.append(c)
            i += 1
            while i < n:
                ch = text[i]
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(text[i + 1])
                    i += 2
                    continue
                i += 1
                if ch == '"':
                    break
            continue
        if c == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                # // line comment — skip to end of line
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                # /* block comment — skip to closing */
                i += 2
                while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                    i += 1
                i += 2  # consume the */
                continue
        out.append(c)
        i += 1
    return "".join(out)


def _load_tsconfig(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    stripped = _strip_jsonc(raw)
    # Allow trailing commas.
    stripped = re.sub(r",\s*([\]\}])", r"\1", stripped)
    return json.loads(stripped)


@dataclass(slots=True, frozen=True)
class AliasEntry:
    """One paths entry. `pattern` may end with '*' (star alias)."""
    pattern: str
    target_template: str  # relative to tsconfig dir; may end with '*'


class TsConfigResolver:
    """Resolves bare imports against `compilerOptions.paths`."""

    def __init__(self, tsconfig_path: Path) -> None:
        self.tsconfig_path = tsconfig_path
        cfg = _load_tsconfig(tsconfig_path)
        co = cfg.get("compilerOptions") or {}
        self._base_url = (tsconfig_path.parent / (co.get("baseUrl") or ".")).resolve()
        self._aliases: list[AliasEntry] = []
        for pat, targets in (co.get("paths") or {}).items():
            if not targets:
                continue
            self._aliases.append(AliasEntry(pattern=pat, target_template=targets[0]))
        # Try longest patterns first so '@core/foo/*' wins over '@core/*'.
        self._aliases.sort(key=lambda a: -len(a.pattern))

    def resolve_to_real_path(self, module_path: str) -> Path | None:
        """Resolve `@pages/foo/Bar` → absolute Path to the source file
        (after trying .tsx, .ts, /index.tsx, /index.ts)."""
        candidate = self._apply_alias(module_path)
        if candidate is None:
            return None
        return self._first_existing_file(candidate)

    def resolve_to_repo_relative(self, module_path: str, repo_root: Path) -> str | None:
        real = self.resolve_to_real_path(module_path)
        if real is None:
            return None
        try:
            return str(real.relative_to(repo_root))
        except ValueError:
            return None

    def resolve_to_package_relative(self, module_path: str, package_root: Path) -> str | None:
        """Resolve to a path relative to a *package* root (the directory
        that contains `src/`). This is what scip-typescript emits as
        `Document.relative_path`, so it matches our File node keys."""
        real = self.resolve_to_real_path(module_path)
        if real is None:
            return None
        try:
            return str(real.relative_to(package_root))
        except ValueError:
            return None

    def _apply_alias(self, module_path: str) -> Path | None:
        for a in self._aliases:
            if a.pattern.endswith("/*"):
                prefix = a.pattern[:-1]  # keep trailing slash
                if module_path.startswith(prefix):
                    tail = module_path[len(prefix):]
                    template = a.target_template
                    if template.endswith("/*"):
                        template = template[:-1]
                    return (self._base_url / (template + tail)).resolve()
            elif a.pattern == module_path:
                return (self._base_url / a.target_template).resolve()
        return None

    @staticmethod
    def _first_existing_file(candidate: Path) -> Path | None:
        # If the candidate already has an extension, use it as-is.
        if candidate.suffix:
            return candidate if candidate.exists() else None
        for ext in (".tsx", ".ts", ".jsx", ".js"):
            f = candidate.with_suffix(ext)
            if f.exists():
                return f
        for stem in ("index.tsx", "index.ts", "index.jsx", "index.js"):
            f = candidate / stem
            if f.exists():
                return f
        return None


@lru_cache(maxsize=8)
def load_resolver(tsconfig_path: Path) -> TsConfigResolver:
    return TsConfigResolver(tsconfig_path)
