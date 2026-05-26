from __future__ import annotations

import os
import re
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel


load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class ProjectConfig(BaseModel):
    name: str
    root: Path


class Settings(BaseModel):
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    indexer_url: str
    api_host: str
    api_port: int
    projects: list[ProjectConfig]

    @classmethod
    def load(cls) -> "Settings":
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

        return cls(
            neo4j_uri=os.getenv("NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=os.getenv("NEO4J_USER", "neo4j"),
            neo4j_password=os.getenv("NEO4J_PASSWORD", "knowledge-graph"),
            indexer_url=os.getenv("INDEXER_URL", "http://127.0.0.1:7401"),
            api_host=os.getenv("API_HOST", "127.0.0.1"),
            api_port=int(os.getenv("API_PORT", "7400")),
            projects=projects,
        )


settings = Settings.load()
