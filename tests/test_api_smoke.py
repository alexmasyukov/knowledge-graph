"""HTTP smoke tests.

The fixtures don't reindex — they seed a small Memgraph slice directly
so we exercise the API code paths without burning a SCIP run."""

from __future__ import annotations

import pytest

from kg.db import session


@pytest.fixture()
def seed_routes(wipe_test_project, project_name):
    with session() as s:
        s.run(
            """
            MERGE (p:Project {name: $proj})
            CREATE (r:Route {project: $proj, path: '/billing', index: false, depth: 1, file: 'router.tsx', line: 12})
            CREATE (c:Component {project: $proj, name: 'Billing', file: 'pages/billing/Billing.tsx', line: 1})
            CREATE (g:Guard {project: $proj, name: 'RouterGuard'})
            CREATE (perm:Permission {project: $proj, key: 'billing.read', roles: ['ADMIN'], file: 'perms.ts', line: 4})
            CREATE (r)-[:RENDERS]->(c)
            CREATE (r)-[:GUARDED_BY]->(g)
            CREATE (r)-[:REQUIRES]->(perm)
            """,
            proj=project_name,
        )


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["service"] == "kg-core"
    assert data["memgraph"]["ok"] is True


def test_routes_list_and_resolve(client, project_name, seed_routes):
    r = client.get("/routes/list", params={"project": project_name})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1
    row = body["routes"][0]
    assert row["path"] == "/billing"
    assert row["components"] == ["Billing"]
    assert row["guards"] == ["RouterGuard"]
    assert row["permissions"] == ["billing.read"]

    r = client.get("/routes/resolve", params={"project": project_name, "path": "/billing"})
    assert r.status_code == 200
    body = r.json()
    assert body["route"]["path"] == "/billing"
    assert body["guards"] == ["RouterGuard"]
    assert any(c["name"] == "Billing" for c in body["components"])


def test_routes_resolve_404(client, project_name, wipe_test_project):
    r = client.get("/routes/resolve", params={"project": project_name, "path": "/nope"})
    assert r.status_code == 404


def test_routes_by_component(client, project_name, seed_routes):
    r = client.get("/routes/by-component", params={"project": project_name, "name": "Billing"})
    assert r.status_code == 200
    body = r.json()
    assert body["component"]["name"] == "Billing"
    assert "/billing" in body["routes"]


def test_permissions_info(client, project_name, seed_routes):
    r = client.get("/permissions/info/billing.read", params={"project": project_name})
    assert r.status_code == 200
    body = r.json()
    assert body["key"] == "billing.read"
    assert body["routes"] == ["/billing"]
    # Roles are read off the Permission node (no Role nodes were seeded
    # here, but the `roles` field is set on the node itself).


def test_sanity_runs(client, project_name, wipe_test_project):
    r = client.get("/sanity", params={"project": project_name})
    assert r.status_code == 200
    body = r.json()
    assert "summary" in body
    assert "total_issues" in body["summary"]
    assert isinstance(body["findings"], dict)
    # Every probe key is present even on an empty graph.
    for probe in (
        "duplicate_hook_names",
        "orphan_components",
        "template_in_route_paths",
        "permissions_unused",
    ):
        assert probe in body["findings"]
