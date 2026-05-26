"""Pydantic response models — the wire contract every endpoint adheres to.

These are the source of truth for FastAPI's auto-generated OpenAPI
schema and for downstream consumers (MCP wrappers in arenadata-mcp).
Changing a field here is a breaking change.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ── shared ──────────────────────────────────────────────────────────


class Location(BaseModel):
    file: str
    line: int


GqlKind = Literal["query", "mutation", "subscription", "fragment"]


class Caller(BaseModel):
    file: str
    line: int
    column: int = 0


# ── gql ─────────────────────────────────────────────────────────────


class GqlOperationRow(BaseModel):
    symbol: str
    gql_name: str | None = None
    kind: GqlKind | None = None
    file: str
    line: int
    callsites: int = 0


class GqlOperationListResponse(BaseModel):
    project: str
    count: int
    operations: list[GqlOperationRow]


class GqlOperationBrief(BaseModel):
    symbol: str
    gql_name: str | None = None
    kind: GqlKind | None = None


class GqlHookDefinition(BaseModel):
    location: Location
    operations: list[GqlOperationBrief] = Field(default_factory=list)
    callers: list[Caller] = Field(default_factory=list)


class GqlHookInfoResponse(BaseModel):
    project: str
    name: str
    definitions: list[GqlHookDefinition] = Field(default_factory=list)


class GqlCallsiteRow(BaseModel):
    location: Location
    column: int = 0


class GqlOperationCallsites(BaseModel):
    definition: Location
    callsites: list[Caller] = Field(default_factory=list)


class GqlCallsitesResponse(BaseModel):
    project: str
    target: str
    operation_matches: list[GqlOperationCallsites] = Field(default_factory=list)
    hook_matches: list[GqlHookDefinition] = Field(default_factory=list)


# ── routes ──────────────────────────────────────────────────────────


class RouteRow(BaseModel):
    path: str
    file: str
    line: int
    components: list[str] = Field(default_factory=list)
    guards: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)


class RouteListResponse(BaseModel):
    project: str
    count: int
    routes: list[RouteRow]


class ComponentBrief(BaseModel):
    name: str
    file: str
    line: int


class HookBrief(BaseModel):
    name: str
    file: str


class RouteResolveResponse(BaseModel):
    project: str
    path: str
    file: str
    line: int
    guards: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    components: list[ComponentBrief] = Field(default_factory=list)
    hooks: list[HookBrief] = Field(default_factory=list)
    operations: list[GqlOperationBrief] = Field(default_factory=list)


class ComponentRoutesResponse(BaseModel):
    project: str
    component: ComponentBrief
    routes: list[str] = Field(default_factory=list)


# ── permissions ─────────────────────────────────────────────────────


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


class PermissionInfoResponse(BaseModel):
    project: str
    key: str
    file: str | None = None
    line: int | None = None
    roles: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)


# ── pages ───────────────────────────────────────────────────────────


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


class PageGetResponse(BaseModel):
    project: str
    domain: str
    entity: str
    dir: str
    files: list[str] = Field(default_factory=list)
    subdirs: list[str] = Field(default_factory=list)
    components: list[ComponentBrief] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    hooks: list[str] = Field(default_factory=list)
    operations: list[GqlOperationBrief] = Field(default_factory=list)


# ── e2e ─────────────────────────────────────────────────────────────


class SpecRow(BaseModel):
    name: str
    file: str
    line: int
    kind: str = "test"
    poms: list[str] = Field(default_factory=list)


class SpecListResponse(BaseModel):
    project: str
    count: int
    specs: list[SpecRow]


class TestIdSource(BaseModel):
    file: str
    line: int


class TestIdLocatorRef(BaseModel):
    name: str
    file: str
    line: int


class TestIdSpecRef(BaseModel):
    name: str
    file: str
    line: int


class TestIdInfoResponse(BaseModel):
    project: str
    value: str
    adsw_sources: list[TestIdSource] = Field(default_factory=list)
    locators: list[TestIdLocatorRef] = Field(default_factory=list)
    page_objects: list[str] = Field(default_factory=list)
    specs: list[TestIdSpecRef] = Field(default_factory=list)


class CoverageResponse(BaseModel):
    project: str
    adsw_total: int = 0
    covered: int = 0
    uncovered: int = 0
    stale_e2e_only: int = 0


class UncoveredTestId(BaseModel):
    value: str
    pattern: bool = False
    file: str
    line: int


class UncoveredFileGroup(BaseModel):
    file: str
    count: int
    testids: list[UncoveredTestId]


class UncoveredResponse(BaseModel):
    project: str
    files: int
    total: int
    groups: list[UncoveredFileGroup] = Field(default_factory=list)


# ── scss ────────────────────────────────────────────────────────────


class ScssModuleRow(BaseModel):
    file: str
    classes: list[str] = Field(default_factory=list)
    consumers: list[str] = Field(default_factory=list)


class ScssListResponse(BaseModel):
    project: str
    count: int
    modules: list[ScssModuleRow]


class ScssClassModule(BaseModel):
    file: str
    consumers: list[str] = Field(default_factory=list)


class ScssClassUsageResponse(BaseModel):
    project: str
    class_name: str
    modules: list[ScssClassModule]


# ── docs ────────────────────────────────────────────────────────────


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


class DocGetResponse(BaseModel):
    project: str
    name: str
    file: str
    content: str


# ── sanity ──────────────────────────────────────────────────────────


class DuplicateGroup(BaseModel):
    label: str
    name: str
    files: list[str]


class SanityReport(BaseModel):
    project: str
    duplicates: list[DuplicateGroup] = Field(default_factory=list)
    orphan_components: list[str] = Field(default_factory=list)
    orphan_hooks: list[str] = Field(default_factory=list)
    templated_route_paths: list[str] = Field(default_factory=list)
    templated_testid_values: list[str] = Field(default_factory=list)
    summary: dict[str, int] = Field(default_factory=dict)


# ── reindex ─────────────────────────────────────────────────────────


class ExtractorRun(BaseModel):
    name: str
    nodes: dict[str, int] = Field(default_factory=dict)
    edges: int = 0
    duration_ms: int = 0
    error: str | None = None


class ReindexResponse(BaseModel):
    ok: bool
    project: str
    scip_duration_ms: int
    total_duration_ms: int
    extractors: list[ExtractorRun] = Field(default_factory=list)
