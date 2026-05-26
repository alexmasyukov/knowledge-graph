"""Shared Pydantic response models.

These give FastAPI an OpenAPI schema for /docs and act as a contract
between the core API and downstream consumers (MCP wrapper, future
Web UI). Endpoints declare them via `response_model=`.

Optional fields use `None` defaults; collection fields default to empty
lists so absent edges don't blow up MCP formatting code.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── shared ────────────────────────────────────────────────────────────


class Location(BaseModel):
    file: str
    line: int


# ── gql ───────────────────────────────────────────────────────────────

GqlKind = Literal["query", "mutation", "subscription", "fragment"]


class GqlOperationRow(BaseModel):
    symbol: str
    gql_name: str
    kind: GqlKind
    file: str
    line: int
    exported: bool
    hooks: list[str] = Field(default_factory=list)


class GqlListResponse(BaseModel):
    project: str
    count: int
    operations: list[GqlOperationRow]


class GqlHookOperationBrief(BaseModel):
    symbol: str
    gql_name: str
    kind: GqlKind


class GqlCaller(BaseModel):
    file: str
    line: int
    column: int


class GqlHookInfoResponse(BaseModel):
    project: str
    name: str
    location: Location
    operations: list[GqlHookOperationBrief] = Field(default_factory=list)
    callers: list[GqlCaller] = Field(default_factory=list)


class GqlOperationCallsites(BaseModel):
    def_: dict = Field(alias="def")
    callsites: list[GqlCaller] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class GqlHookCallsites(BaseModel):
    def_: dict = Field(alias="def")
    callsites: list[GqlCaller] = Field(default_factory=list)

    model_config = {"populate_by_name": True}


class GqlCallsitesResponse(BaseModel):
    project: str
    target: str
    as_operation: list[GqlOperationCallsites] = Field(default_factory=list)
    as_hook: GqlHookCallsites | None = None


# ── routes ────────────────────────────────────────────────────────────


class RouteRow(BaseModel):
    path: str
    index: bool
    depth: int
    file: str
    line: int
    components: list[str] = Field(default_factory=list)
    guards: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class RoutesListResponse(BaseModel):
    project: str
    count: int
    routes: list[RouteRow]


class RouteSummary(BaseModel):
    path: str
    index: bool
    depth: int
    file: str
    line: int


class ComponentBrief(BaseModel):
    name: str
    file: str
    line: int


class HookBrief(BaseModel):
    hook: str
    file: str


class OperationBrief(BaseModel):
    symbol: str
    gql_name: str
    kind: GqlKind


class RouteResolveResponse(BaseModel):
    project: str
    route: RouteSummary
    guards: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    components: list[ComponentBrief] = Field(default_factory=list)
    hooks_via_components: list[HookBrief] = Field(default_factory=list)
    operations_via_components: list[OperationBrief] = Field(default_factory=list)
    operations_via_hooks: list[OperationBrief] = Field(default_factory=list)


class ComponentRoutesResponse(BaseModel):
    project: str
    component: ComponentBrief
    routes: list[str] = Field(default_factory=list)


# ── permissions ───────────────────────────────────────────────────────


class PermissionRow(BaseModel):
    key: str
    roles: list[str] = Field(default_factory=list)
    file: str | None = None
    line: int | None = None
    routes: list[str] = Field(default_factory=list)


class PermissionsListResponse(BaseModel):
    project: str
    count: int
    permissions: list[PermissionRow]


class PermissionDef(BaseModel):
    key: str
    roles: list[str] = Field(default_factory=list)
    file: str | None = None
    line: int | None = None


class PermissionInfoResponse(BaseModel):
    project: str
    permission: PermissionDef
    routes: list[str] = Field(default_factory=list)
    roles_via_edges: list[str] = Field(default_factory=list)


class RoleRow(BaseModel):
    code: str
    permission_count: int


class RolesListResponse(BaseModel):
    project: str
    count: int
    roles: list[RoleRow]


# ── pages ─────────────────────────────────────────────────────────────


class PageRow(BaseModel):
    domain: str
    entity: str
    dir: str
    files: list[str] = Field(default_factory=list)
    subdirs: list[str] = Field(default_factory=list)
    components: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)


class PagesListResponse(BaseModel):
    project: str
    count: int
    pages: list[PageRow]


class PageSummary(BaseModel):
    domain: str
    entity: str
    dir: str
    files: list[str] = Field(default_factory=list)
    subdirs: list[str] = Field(default_factory=list)


class PageGetResponse(BaseModel):
    project: str
    page: PageSummary
    components: list[ComponentBrief] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    ops_direct: list[OperationBrief] = Field(default_factory=list)
    ops_via_hooks: list[OperationBrief] = Field(default_factory=list)


class DomainRow(BaseModel):
    name: str
    pages: int


class DomainsListResponse(BaseModel):
    project: str
    count: int
    domains: list[DomainRow]


# ── docs ──────────────────────────────────────────────────────────────


class DocRow(BaseModel):
    name: str
    file: str
    title: str | None = None
    size: int = 0
    headings: list[str] = Field(default_factory=list)


class DocsListResponse(BaseModel):
    project: str
    count: int
    docs: list[DocRow]


class DocSearchHit(BaseModel):
    name: str
    file: str
    title: str | None = None
    matched_headings: list[str] = Field(default_factory=list)


class DocsSearchResponse(BaseModel):
    project: str
    query: str
    count: int
    hits: list[DocSearchHit]


class DocGetResponse(BaseModel):
    project: str
    name: str
    file: str
    content: str


# ── e2e ───────────────────────────────────────────────────────────────


class SpecRow(BaseModel):
    name: str
    file: str
    line: int
    kind: str
    poms: list[str] = Field(default_factory=list)


class SpecsListResponse(BaseModel):
    project: str
    count: int
    specs: list[SpecRow]


class TestIdSource(BaseModel):
    file: str
    line: int


class TestIdLocator(BaseModel):
    name: str
    file: str
    line: int


class TestIdSpec(BaseModel):
    name: str
    file: str
    line: int


class TestIdInfoResponse(BaseModel):
    project: str
    value: str
    adsw_sources: list[TestIdSource] = Field(default_factory=list)
    locators: list[TestIdLocator] = Field(default_factory=list)
    page_objects: list[str] = Field(default_factory=list)
    specs: list[TestIdSpec] = Field(default_factory=list)


class CoverageResponse(BaseModel):
    project: str
    adsw_total: int = 0
    covered: int = 0
    uncovered: int = 0
    stale_e2e_only: int = 0


class TestIdSearchHit(BaseModel):
    value: str
    has_locator: bool


class TestIdSearchResponse(BaseModel):
    project: str
    query: str
    count: int
    matches: list[TestIdSearchHit]


# ── scss ──────────────────────────────────────────────────────────────


class ScssModuleRow(BaseModel):
    file: str
    classes: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)


class ScssListResponse(BaseModel):
    project: str
    count: int
    modules: list[ScssModuleRow]


class ScssClassModule(BaseModel):
    module: str
    consumers: list[str] = Field(default_factory=list)


class ScssClassResponse(BaseModel):
    project: str
    class_name: str
    modules: list[ScssClassModule]


# ── meta ──────────────────────────────────────────────────────────────


class HealthResponse(BaseModel):
    service: str = "kg-core"
    ok: bool
    neo4j: dict | None = None
    indexer: dict | None = None
    projects_configured: list[str] = Field(default_factory=list)


class ExtractorResult(BaseModel):
    stats: dict | None = None
    written: dict | None = None
    error: str | None = None
    skipped: str | None = None
    domains: list[str] | None = None


class ReindexProjectResult(BaseModel):
    project: str
    register: dict
    stats: dict
    extractors: dict[str, ExtractorResult]


class ReindexResponse(BaseModel):
    ok: bool = True
    results: list[ReindexProjectResult]
