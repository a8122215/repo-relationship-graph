from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from repo_graph.core.model import (
    CONFIDENCE_VALUES,
    Edge,
    Evidence,
    FrontendApiCall,
    Node,
    PageNavigation,
    SourceFile,
    SourceParseResult,
    UnsupportedRecord,
)
from repo_graph.source_paths import DEFAULT_FRONTEND_TEST_ROOTS, is_frontend_test_path, normalize_test_roots


DEFAULT_JS_VUE_EXTENSIONS = (".js", ".vue")
DEFAULT_JS_VUE_ALIASES = {"@/": "client/src/"}
DEFAULT_VUE_ROUTER_FILES = ("client/src/router/index.js", "client/src/router/**/*.js")
DEFAULT_API_BASE = "/api"
DEFAULT_USE_API_FACTORY_NAMES = ("useApi",)
DEFAULT_API_CLIENT_NAMES = ("api", "apiClient")
DEFAULT_API_URL_HELPER_NAMES = ("getApiUrl",)
MISSING_RUNTIME_POLICIES = {"error", "skip"}


@dataclass(frozen=True)
class JavaScriptVueParserConfig:
    extensions: tuple[str, ...] = DEFAULT_JS_VUE_EXTENSIONS
    aliases: Mapping[str, str] | None = None
    router_files: tuple[str, ...] = DEFAULT_VUE_ROUTER_FILES
    vue_router_enabled: bool = True
    frontend_api_calls_enabled: bool = True
    api_base: str = DEFAULT_API_BASE
    use_api_factory_names: tuple[str, ...] = DEFAULT_USE_API_FACTORY_NAMES
    api_client_names: tuple[str, ...] = DEFAULT_API_CLIENT_NAMES
    api_url_helper_names: tuple[str, ...] = DEFAULT_API_URL_HELPER_NAMES
    playwright_enabled: bool = True
    missing_runtime: str = "error"

    def normalized_aliases(self) -> dict[str, str]:
        aliases = self.aliases if self.aliases is not None else DEFAULT_JS_VUE_ALIASES
        return {
            normalize_alias_prefix(alias): normalize_alias_target(target)
            for alias, target in aliases.items()
        }


class JavaScriptVueStructureParser:
    def __init__(
        self,
        repo_root: Path,
        source_files: Iterable[SourceFile],
        client_package_root: Path | None = None,
        node_bin: str = "node",
        frontend_test_roots: tuple[str, ...] = DEFAULT_FRONTEND_TEST_ROOTS,
        parser_config: JavaScriptVueParserConfig | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.client_package_root = client_package_root or repo_root / "client"
        self.node_bin = node_bin
        self.config = parser_config or JavaScriptVueParserConfig()
        if self.config.missing_runtime not in MISSING_RUNTIME_POLICIES:
            raise ValueError(f"unsupported JS/Vue missing runtime policy: {self.config.missing_runtime}")
        self.frontend_test_roots = normalize_test_roots(frontend_test_roots)
        self.known_paths = {source.path for source in source_files}
        self.extractor_path = Path(__file__).with_name("js_vue_extract_structure.mjs")
        self.import_extensions = normalize_extensions(self.config.extensions)
        self.aliases = self.config.normalized_aliases()
        self.router_files = normalize_patterns(self.config.router_files)
        self.runtime_unavailable_message: str | None = None
        self._preflight_runtime()

    def parse(
        self,
        path: str,
        module_name: str,
        source: str,
        known_modules: Iterable[str] | None = None,
    ) -> SourceParseResult:
        language = language_for_path(path)
        result = SourceParseResult(
            path=path,
            module_name=module_name,
            language=language,
            source_id=file_node_id(path),
            node=Node(
                id=file_node_id(path),
                type="test_file" if is_frontend_test_path(path, self.frontend_test_roots) else "file",
                name=path,
                path=path,
                language=language,
            ),
        )
        if self.runtime_unavailable_message is not None:
            result.unsupported.append(
                UnsupportedRecord(
                    path=path,
                    language=language,
                    reason="runtime_missing",
                    message=self.runtime_unavailable_message,
                    phase="phase_2",
                )
            )
            return result
        extracted = self._extract_structure(path, language, source)
        for record in extracted.get("unsupported", []):
            result.unsupported.append(
                UnsupportedRecord(
                    path=path,
                    language=language,
                    reason=str(record["reason"]),
                    message=str(record["message"]),
                    line=record.get("line"),
                    phase="phase_2",
                )
            )
        if result.unsupported:
            return result

        evidence_kind = "vue_import" if language == "vue" else "js_import"
        for import_record in extracted.get("imports", []):
            target_path = self.resolve_import(path, str(import_record["specifier"]))
            if target_path is None:
                continue
            result.edges.append(
                Edge(
                    source=file_node_id(path),
                    target=file_node_id(target_path),
                    type="imports",
                    confidence="high",
                    evidence=[
                        Evidence(
                            path=path,
                            kind=evidence_kind,
                            line=import_record.get("line"),
                        )
                    ],
                )
            )
        for route_record in extracted.get("routes", []):
            component_specifier = route_record.get("component")
            if component_specifier is None:
                continue
            component_path = self.resolve_import(path, str(component_specifier))
            if component_path is None:
                continue
            route_id = route_node_id(path, route_record)
            route_name = route_record.get("name")
            route_path = str(route_record.get("path") or "")
            result.nodes.append(
                Node(
                    id=route_id,
                    type="vue_route",
                    name=str(route_name or route_path),
                    path=None,
                    language="virtual",
                    metadata={
                        "routeName": route_name,
                        "routePath": route_path,
                        "routeSourcePath": path,
                        "componentPath": component_path,
                    },
                )
            )
            result.edges.append(
                Edge(
                    source=route_id,
                    target=file_node_id(component_path),
                    type="renders_view",
                    confidence="high",
                    evidence=[
                        Evidence(
                            path=path,
                            kind="vue_router_route",
                            line=route_record.get("line"),
                        )
                    ],
                    metadata={
                        "routeName": route_name,
                        "routePath": route_path,
                        "routeSourcePath": path,
                    },
                )
            )
        for api_call_record in extracted.get("apiCalls", []):
            api_path = api_call_record.get("path")
            call_kind = api_call_record.get("callKind")
            if not isinstance(api_path, str) or not api_path:
                continue
            if not isinstance(call_kind, str) or not call_kind:
                continue
            method = api_call_record.get("method")
            confidence = api_call_record.get("confidence")
            if not isinstance(confidence, str) or confidence not in CONFIDENCE_VALUES:
                confidence = "high"
            result.api_calls.append(
                FrontendApiCall(
                    source_path=path,
                    method=str(method).upper() if isinstance(method, str) and method else None,
                    path=api_path,
                    call_kind=call_kind,
                    confidence=confidence,
                    evidence=Evidence(path=path, kind="frontend_api_call", line=api_call_record.get("line")),
                )
            )
        for navigation_record in extracted.get("pageNavigations", []):
            route_path = navigation_record.get("routePath")
            if not isinstance(route_path, str) or not route_path:
                continue
            evidence_kind = navigation_record.get("kind")
            if not isinstance(evidence_kind, str) or not evidence_kind:
                evidence_kind = "playwright_page_goto"
            result.page_navigations.append(
                PageNavigation(
                    source_path=path,
                    route_path=route_path,
                    evidence=Evidence(path=path, kind=evidence_kind, line=navigation_record.get("line")),
                )
            )
        return result

    def resolve_import(self, source_path: str, specifier: str) -> str | None:
        for alias, target in sorted(self.aliases.items(), key=lambda item: len(item[0]), reverse=True):
            if specifier.startswith(alias):
                base = PurePosixPath(target) / specifier[len(alias):].lstrip("/")
                return self._resolve_candidate(base)
        if not specifier.startswith("."):
            return None
        base = PurePosixPath(source_path).parent / specifier
        return self._resolve_candidate(base)

    def _resolve_candidate(self, base: PurePosixPath) -> str | None:
        raw = normalized_posix(base)
        candidates = [raw]
        candidates.extend(f"{raw}{extension}" for extension in self.import_extensions)
        candidates.extend(f"{raw}/index{extension}" for extension in self.import_extensions)
        for candidate in candidates:
            if candidate in self.known_paths:
                return candidate
        return None

    def is_vue_router_file(self, path: str) -> bool:
        normalized = normalize_config_path(path)
        return any(path_matches_pattern(normalized, pattern) for pattern in self.router_files)

    def _extract_structure(self, path: str, language: str, source: str) -> dict[str, Any]:
        try:
            completed = subprocess.run(
                [self.node_bin, str(self.extractor_path)],
                input=json.dumps(
                    {
                        "path": path,
                        "language": language,
                        "source": source,
                        "config": self.extractor_config(path),
                    }
                ),
                cwd=self.repo_root,
                env={**os.environ, "CODE_GRAPH_CLIENT_ROOT": str(self.client_package_root)},
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError as exc:
            raise RuntimeError("Node.js is required for JS/Vue code graph parsing.") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("JS/Vue code graph parser timed out.") from exc
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(
                "JS/Vue structure parser failed. Run `cd client && npm ci` and retry."
            ) from exc
        return json.loads(completed.stdout)

    def extractor_config(self, path: str) -> dict[str, Any]:
        return {
            "isVueRouterFile": self.config.vue_router_enabled and self.is_vue_router_file(path),
            "frontendApiCallsEnabled": self.config.frontend_api_calls_enabled,
            "playwrightEnabled": self.config.playwright_enabled,
            "apiBase": normalize_api_base(self.config.api_base),
            "useApiFactoryNames": list(self.config.use_api_factory_names),
            "apiClientNames": list(self.config.api_client_names),
            "apiUrlHelperNames": list(self.config.api_url_helper_names),
        }

    def _preflight_runtime(self) -> None:
        package_json = self.client_package_root / "package.json"
        if not package_json.exists():
            self._handle_missing_runtime(
                f"JS/Vue code graph parser requires {display_path(package_json, self.repo_root)}."
            )
            return
        try:
            subprocess.run(
                [self.node_bin, "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except FileNotFoundError as exc:
            if self.config.missing_runtime == "skip":
                self.runtime_unavailable_message = "Node.js is required for JS/Vue code graph parsing."
                return
            raise RuntimeError("Node.js is required for JS/Vue code graph parsing.") from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            if self.config.missing_runtime == "skip":
                self.runtime_unavailable_message = "Node.js preflight failed for JS/Vue code graph parsing."
                return
            raise RuntimeError("Node.js preflight failed for JS/Vue code graph parsing.") from exc

        script = """
const path = require("node:path");
const { createRequire } = require("node:module");
const requireFromClient = createRequire(path.join(process.env.CODE_GRAPH_CLIENT_ROOT, "package.json"));
requireFromClient.resolve("@babel/parser");
requireFromClient.resolve("@vue/compiler-sfc");
"""
        try:
            subprocess.run(
                [self.node_bin, "-e", script],
                cwd=self.repo_root,
                env={**os.environ, "CODE_GRAPH_CLIENT_ROOT": str(self.client_package_root)},
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except subprocess.CalledProcessError as exc:
            if self.config.missing_runtime == "skip":
                self.runtime_unavailable_message = (
                    "JS/Vue structure parser dependencies are missing. Run `cd client && npm ci` and retry."
                )
                return
            raise RuntimeError(
                "JS/Vue structure parser dependencies are missing. Run `cd client && npm ci` and retry."
            ) from exc
        except subprocess.TimeoutExpired as exc:
            if self.config.missing_runtime == "skip":
                self.runtime_unavailable_message = "JS/Vue code graph parser dependency preflight timed out."
                return
            raise RuntimeError("JS/Vue code graph parser dependency preflight timed out.") from exc

    def _handle_missing_runtime(self, message: str) -> None:
        if self.config.missing_runtime == "skip":
            self.runtime_unavailable_message = message
            return
        raise RuntimeError(message)


def language_for_path(path: str) -> str:
    if path.endswith(".vue"):
        return "vue"
    return "javascript"


def file_node_id(path: str) -> str:
    return f"file:{path}"


def route_node_id(source_path: str, route_record: dict[str, Any]) -> str:
    route_path = route_record.get("path") or "unnamed"
    route_name = route_record.get("name")
    if route_name:
        return f"vue_route:{source_path}:{route_path}#{route_name}"
    line = route_record.get("line") or "unknown"
    return f"vue_route:{source_path}:{route_path}@{line}"


def normalized_posix(path: PurePosixPath) -> str:
    parts = []
    for part in path.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def normalize_alias_prefix(alias: str) -> str:
    if not alias or not alias.endswith("/"):
        raise ValueError("JS/Vue alias prefixes must be non-empty strings ending with '/'")
    return alias.replace("\\", "/")


def normalize_alias_target(target: str) -> str:
    if not target:
        raise ValueError("JS/Vue alias targets must be non-empty strings")
    return normalize_config_path(target).rstrip("/")


def normalize_config_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def normalize_patterns(patterns: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(normalize_config_path(pattern) for pattern in patterns if pattern)


def normalize_extensions(extensions: tuple[str, ...]) -> tuple[str, ...]:
    normalized: list[str] = []
    for extension in extensions:
        item = extension if extension.startswith(".") else f".{extension}"
        if item not in normalized:
            normalized.append(item)
    return tuple(normalized)


def normalize_api_base(api_base: str) -> str:
    normalized = api_base.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = f"/{normalized}"
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    if len(normalized) > 1 and normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def path_matches_pattern(path: str, pattern: str) -> bool:
    if fnmatchcase(path, pattern):
        return True
    if "/**/" in pattern:
        return fnmatchcase(path, pattern.replace("/**/", "/"))
    return False
