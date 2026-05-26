"""Runtime configuration, loaded from .env at import time."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")


@dataclass(slots=True, frozen=True)
class ProjectConfig:
    """One indexed project. `name` is the lowercased PROJECT_<X> suffix
    and serves as the project id in the graph."""

    name: str
    code_root: Path

    @property
    def repo_root(self) -> Path:
        """Walk up from code_root until we hit something that looks like a
        repo root (`.git`, pnpm-workspace, etc). Used to resolve paths
        relative to the repo as a whole, not the indexed package."""
        markers = (".git", "pnpm-workspace.yaml", "lerna.json", "nx.json", "turbo.json")
        for d in [self.code_root, *self.code_root.parents]:
            if any((d / m).exists() for m in markers):
                return d
        return self.code_root


@dataclass(slots=True, frozen=True)
class Settings:
    memgraph_uri: str
    memgraph_user: str
    memgraph_password: str
    api_host: str
    api_port: int
    projects: list[ProjectConfig] = field(default_factory=list)

    def project(self, name: str) -> ProjectConfig | None:
        for p in self.projects:
            if p.name == name:
                return p
        return None

    @classmethod
    def load(cls) -> Settings:
        projects: list[ProjectConfig] = []
        for key, value in os.environ.items():
            m = re.fullmatch(r"PROJECT_(\w+)", key)
            if not m or not value:
                continue
            projects.append(
                ProjectConfig(
                    name=m.group(1).lower(),
                    code_root=Path(value).expanduser().resolve(),
                )
            )

        return cls(
            memgraph_uri=os.getenv("MEMGRAPH_URI", "bolt://localhost:7687"),
            memgraph_user=os.getenv("MEMGRAPH_USER", ""),
            memgraph_password=os.getenv("MEMGRAPH_PASSWORD", ""),
            api_host=os.getenv("API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("API_PORT", "7400")),
            projects=projects,
        )


settings = Settings.load()
