from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


# Only true repo-root markers. package.json / pyproject.toml live at
# every package and would falsely "anchor" the search inside a monorepo
# sub-package — they are NOT in this list.
_REPO_MARKERS = (".git", "pnpm-workspace.yaml", "lerna.json", "nx.json", "rush.json", "turbo.json")


def _walk_up_for_marker(start: Path) -> Path | None:
    """Walk parents looking for the repository root.

    We start from start.parent (so a package-level marker doesn't trap us)
    and walk up until we find .git or a workspace marker.
    """
    for c in start.parents:
        if any((c / m).exists() for m in _REPO_MARKERS):
            return c
    return None


class ProjectConfig(BaseModel):
    name: str
    root: Path

    @property
    def code_root(self) -> Path:
        """The source root configured via PROJECT_<NAME>=…
        Typically points at packages/<x>/ inside a monorepo."""
        return self.root

    @property
    def repo_root(self) -> Path:
        """The repository root — first ancestor with .git or a workspace
        marker; falls back to two levels up from code_root."""
        marker = _walk_up_for_marker(self.root)
        if marker is not None:
            return marker
        # last-ditch fallback: ../..
        return self.root.parent.parent


class Settings(BaseModel):
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    indexer_url: str
    api_host: str
    api_port: int
    projects: list[ProjectConfig]

    def project(self, name: str) -> ProjectConfig | None:
        return next((p for p in self.projects if p.name == name), None)

    @classmethod
    def load(cls) -> Settings:
        projects: list[ProjectConfig] = []
        for key, value in os.environ.items():
            m = re.fullmatch(r"PROJECT_([A-Z0-9_]+)", key)
            if not m or not value.strip():
                continue
            projects.append(
                ProjectConfig(
                    name=m.group(1).lower(),
                    root=Path(value).expanduser().resolve(),
                )
            )

        password = os.getenv("NEO4J_PASSWORD")
        if not password:
            raise RuntimeError(
                "NEO4J_PASSWORD is not set. Copy .env.example to .env and fill in real values."
            )

        return cls(
            neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=password,
            indexer_url=os.getenv("INDEXER_URL", "http://127.0.0.1:7401"),
            api_host=os.getenv("API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("API_PORT", "7400")),
            projects=projects,
        )


settings = Settings.load()
