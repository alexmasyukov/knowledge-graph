"""Shared fixtures.

Tests assume a running Memgraph at MEMGRAPH_URI (the same instance
the dev server points at). Each test runs against a dedicated test
project name to avoid trampling the live adsw graph.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kg.db import session, wipe_project
from kg.server import app

TEST_PROJECT = "kgtest"


@pytest.fixture()
def project_name() -> str:
    return TEST_PROJECT


@pytest.fixture()
def wipe_test_project(project_name: str):
    """Clean state before and after each test."""
    wipe_project(project_name)
    yield
    wipe_project(project_name)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture()
def sample_workspace(tmp_path: Path) -> Path:
    """Build a tiny project layout on disk for extractors that don't
    need a real SCIP index. Returns the package root."""
    root = tmp_path / "pkg"
    src = root / "src"

    # permissions
    perms = src / "common" / "permissions"
    perms.mkdir(parents=True)
    (perms / "index.ts").write_text(
        """
        import { UserRoleCode } from '@shared-types/users'

        export const PERMISSIONS = {
          home: {
            read: ['ADMIN', 'USER'],
          },
          billing: {
            invoice: {
              read: ['ADMIN'],
              update: ['ADMIN'],
            },
          },
        }
        """,
        encoding="utf-8",
    )

    # router + types
    router = src / "router"
    router.mkdir()
    (router / "types.ts").write_text(
        "export enum PageMode { CREATE = 'create', EDIT = 'edit' }\n",
        encoding="utf-8",
    )
    (router / "index.tsx").write_text(
        """
        import React from 'react'
        import { useRoutes, Navigate } from 'react-router-dom'
        import { PERMISSIONS } from '@common/permissions'
        import { RouterGuard } from '@guards/RouterGuard'
        import { PageMode } from './types'

        const Home = React.lazy(() => import('@pages/home/Home').then((m) => ({ default: m.Home })))
        const Billing = React.lazy(() => import('@pages/billing/Billing').then((m) => ({ default: m.Billing })))

        const routes = [
          { path: '/', element: <Home /> },
          {
            path: '/billing',
            element: (
              <RouterGuard roles={PERMISSIONS.billing.invoice.read}>
                <Billing />
              </RouterGuard>
            ),
            children: [
              { path: PageMode.CREATE, element: <Billing /> },
              { path: ':id/${PageMode.EDIT}'.replace('${PageMode.EDIT}', 'edit'), element: <Billing /> },
            ],
          },
        ]

        export const Router = () => useRoutes(routes)
        """,
        encoding="utf-8",
    )

    # pages
    (src / "pages" / "home").mkdir(parents=True)
    (src / "pages" / "home" / "Home.tsx").write_text("export const Home = () => null\n")
    (src / "pages" / "billing").mkdir(parents=True)
    (src / "pages" / "billing" / "Billing.tsx").write_text(
        "import 'data-testid'\n"
        "export const Billing = () => <div data-testid='billing-root' />\n",
        encoding="utf-8",
    )

    # types
    (src / "types").mkdir(parents=True)
    (src / "types" / "users.ts").write_text(
        """
        export type UserRoleCode = 'ADMIN' | 'USER'

        export enum UserPolicyType {
          TERM = 'TERM',
          EMAIL = 'EMAIL',
        }

        export interface User {
          id: string
          role: UserRoleCode
        }
        """,
        encoding="utf-8",
    )

    # tsconfig with path aliases
    (root / "tsconfig.json").write_text(
        """{
          "compilerOptions": {
            "baseUrl": ".",
            "paths": {
              "@pages/*": ["./src/pages/*"],
              "@common/*": ["./src/common/*"],
              "@guards/*": ["./src/guards/*"],
              "@router/*": ["./src/router/*"],
              "@shared-types/*": ["./src/types/*"]
            }
          }
        }""",
        encoding="utf-8",
    )

    # scss
    scss_dir = src / "components" / "Button"
    scss_dir.mkdir(parents=True)
    (scss_dir / "Button.module.scss").write_text(
        """
        .root {
          color: red;
          .nested { color: blue; }
        }

        .primary {
          font-weight: bold;
        }
        """,
        encoding="utf-8",
    )
    (scss_dir / "Button.tsx").write_text(
        "import styles from './Button.module.scss'\n"
        "export const Button = () => <div className={styles.root} />\n",
        encoding="utf-8",
    )

    return root


@pytest.fixture()
def sample_project(sample_workspace: Path, project_name: str):
    """A ProjectConfig pointing at the sample workspace."""
    from kg.settings import ProjectConfig
    return ProjectConfig(name=project_name, code_root=sample_workspace)


def _has_label(label: str) -> int:
    with session() as s:
        rec = s.run(
            f"MATCH (n:{label} {{project: $p}}) RETURN count(n) AS n",
            p=TEST_PROJECT,
        ).single()
    return rec["n"] if rec else 0


@pytest.fixture()
def count_label():
    """Test helper: count(:Label {project: kgtest})."""
    return _has_label
