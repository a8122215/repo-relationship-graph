from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import unquote, urlparse

from repo_graph.core.model import (
    CODEQL_RELATION_TYPES,
    CONFIDENCE_VALUES,
    CodeqlCandidateRelation,
    CodeqlSymbol,
    Evidence,
)


def load_codeql_candidate_relations(
    path: Path | None,
    repo_root: Path,
    known_paths: Iterable[str],
) -> list[CodeqlCandidateRelation]:
    if path is None:
        return []
    if not path.exists():
        raise FileNotFoundError(f"CodeQL candidate results file does not exist: {path}")
    return parse_codeql_candidate_text(
        path.read_text(encoding="utf-8"),
        repo_root=repo_root,
        known_paths=known_paths,
    )


def parse_codeql_candidate_text(
    text: str,
    repo_root: Path,
    known_paths: Iterable[str],
) -> list[CodeqlCandidateRelation]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("CodeQL candidate payload must be a JSON object")
    rows = payload.get("relations", [])
    if not isinstance(rows, list):
        raise ValueError("CodeQL candidate payload `relations` must be a list")

    known_path_set = {normalize_known_path(path) for path in known_paths}
    relations = []
    for row in rows:
        relation = relation_from_row(row, repo_root=repo_root, known_paths=known_path_set)
        if relation is not None:
            relations.append(relation)
    return sorted(relations, key=relation_sort_key)


def relation_from_row(
    row: Any,
    repo_root: Path,
    known_paths: set[str],
) -> CodeqlCandidateRelation | None:
    if not isinstance(row, dict):
        raise ValueError("CodeQL candidate relation must be an object")
    relation_type = required_string(row, "type")
    if relation_type not in CODEQL_RELATION_TYPES:
        raise ValueError(f"unsupported CodeQL candidate relation type: {relation_type}")
    confidence = str(row.get("confidence", "medium"))
    if confidence not in CONFIDENCE_VALUES:
        raise ValueError(f"invalid CodeQL candidate confidence: {confidence}")

    source = symbol_from_row(row.get("source"), repo_root=repo_root, known_paths=known_paths)
    target = symbol_from_row(row.get("target"), repo_root=repo_root, known_paths=known_paths)
    evidence = evidence_from_row(row.get("evidence"), repo_root=repo_root, known_paths=known_paths)
    if source is None or target is None or evidence is None:
        return None

    query_id = required_string(row, "queryId")
    query_name = row.get("queryName")
    if query_name is not None and not isinstance(query_name, str):
        raise ValueError("CodeQL candidate `queryName` must be a string when present")

    return CodeqlCandidateRelation(
        source=source,
        target=target,
        relation_type=relation_type,
        evidence=evidence,
        query_id=query_id,
        confidence=confidence,
        query_name=query_name,
    )


def symbol_from_row(
    row: Any,
    repo_root: Path,
    known_paths: set[str],
) -> CodeqlSymbol | None:
    if not isinstance(row, dict):
        raise ValueError("CodeQL candidate symbol must be an object")
    relative_path = normalize_codeql_path(required_string(row, "path"), repo_root)
    if relative_path is None or relative_path not in known_paths:
        return None
    qualified_name = required_string(row, "qualifiedName")
    kind = str(row.get("kind", "function"))
    language = str(row.get("language", "python"))
    line = optional_int(row.get("line"), "line")
    return CodeqlSymbol(
        path=relative_path,
        qualified_name=qualified_name,
        kind=kind,
        language=language,
        line=line,
    )


def evidence_from_row(
    row: Any,
    repo_root: Path,
    known_paths: set[str],
) -> Evidence | None:
    if not isinstance(row, dict):
        raise ValueError("CodeQL candidate evidence must be an object")
    relative_path = normalize_codeql_path(required_string(row, "path"), repo_root)
    if relative_path is None or relative_path not in known_paths:
        return None
    line = optional_int(row.get("line"), "line")
    return Evidence(path=relative_path, kind=required_string(row, "kind"), line=line)


def normalize_codeql_path(raw_path: str, repo_root: Path) -> str | None:
    path_value = unquote(raw_path)
    parsed = urlparse(path_value)
    if parsed.scheme == "file":
        path_value = parsed.path
    path_value = path_value.replace("\\", "/")
    repo_root = repo_root.resolve()

    candidate = Path(path_value)
    if candidate.is_absolute():
        try:
            relative = candidate.resolve().relative_to(repo_root)
        except ValueError:
            return None
        return normalize_known_path(relative.as_posix())

    normalized = normalize_known_path(path_value)
    if normalized.startswith("../") or normalized == "..":
        return None
    return normalized


def normalize_known_path(path: str) -> str:
    parts = []
    for part in PurePosixPath(str(path).replace("\\", "/")).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    return "/".join(parts)


def required_string(row: dict[str, Any], key: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"CodeQL candidate `{key}` must be a non-empty string")
    return value


def optional_int(value: Any, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"CodeQL candidate `{key}` must be an integer or null")
    if isinstance(value, int) and value >= 1:
        return value
    raise ValueError(f"CodeQL candidate `{key}` must be an integer or null")


def relation_sort_key(relation: CodeqlCandidateRelation) -> tuple:
    return (
        relation.relation_type,
        relation.source.path,
        relation.source.qualified_name,
        relation.source.line or 0,
        relation.target.path,
        relation.target.qualified_name,
        relation.target.line or 0,
        relation.evidence.path,
        relation.evidence.kind,
        relation.evidence.line or 0,
        relation.query_id,
    )
