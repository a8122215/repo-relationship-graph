from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


CONFIDENCE_VALUES = {"high", "medium", "low"}
SOURCE_FILE_ROLES = {"source", "manifest", "inventory_only"}
CODEQL_RELATION_TYPES = {"calls", "data_flows_to"}


@dataclass(frozen=True)
class Evidence:
    path: str
    kind: str
    line: int | None = None


@dataclass(frozen=True)
class SourceFile:
    path: str
    language: str
    role: str = "source"


@dataclass(frozen=True)
class Node:
    id: str
    type: str
    name: str
    path: str | None
    language: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    type: str
    confidence: str
    evidence: list[Evidence]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class UnsupportedRecord:
    path: str
    language: str
    reason: str
    message: str
    phase: str = "mvp_v0_1"
    line: int | None = None


@dataclass
class Graph:
    source_manifest: list[SourceFile] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    unsupported: list[UnsupportedRecord] = field(default_factory=list)


@dataclass(frozen=True)
class ImportRelation:
    source_module: str
    target_module: str
    evidence: Evidence


@dataclass(frozen=True)
class EndpointDeclaration:
    source_module: str
    router_name: str
    method: str
    path: str
    evidence: Evidence


@dataclass(frozen=True)
class IncludeRouterRelation:
    source_module: str
    target_module: str | None
    prefix: str
    evidence: Evidence


@dataclass(frozen=True)
class FrontendApiCall:
    source_path: str
    method: str | None
    path: str
    call_kind: str
    confidence: str
    evidence: Evidence


@dataclass(frozen=True)
class PageNavigation:
    source_path: str
    route_path: str
    evidence: Evidence


@dataclass(frozen=True)
class PackageDependency:
    source_id: str
    target_id: str
    source_name: str
    target_name: str
    manager: str
    section: str
    evidence: Evidence


@dataclass(frozen=True)
class CodeqlSymbol:
    path: str
    qualified_name: str
    kind: str
    language: str = "python"
    line: int | None = None


@dataclass(frozen=True)
class CodeqlCandidateRelation:
    source: CodeqlSymbol
    target: CodeqlSymbol
    relation_type: str
    evidence: Evidence
    query_id: str
    confidence: str = "medium"
    query_name: str | None = None


@dataclass
class SourceParseResult:
    path: str
    module_name: str
    language: str = "python"
    source_id: str = ""
    node: Node | None = None
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    imports: list[ImportRelation] = field(default_factory=list)
    router_prefixes: dict[str, str] = field(default_factory=dict)
    endpoints: list[EndpointDeclaration] = field(default_factory=list)
    include_routers: list[IncludeRouterRelation] = field(default_factory=list)
    api_calls: list[FrontendApiCall] = field(default_factory=list)
    page_navigations: list[PageNavigation] = field(default_factory=list)
    unsupported: list[UnsupportedRecord] = field(default_factory=list)


PythonParseResult = SourceParseResult
