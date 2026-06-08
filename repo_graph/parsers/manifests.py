from __future__ import annotations

import json
import tomllib

from repo_graph.core.model import Evidence, PackageDependency


def parse_pyproject(path: str, source: str) -> list[PackageDependency]:
    data = tomllib.loads(source)
    project = data.get("project", {})
    project_name = str(project.get("name") or "python-project")
    source_id = f"package:python:{normalize_package_name(project_name)}"
    dependencies = []
    for spec in project.get("dependencies", []) or []:
        dependencies.append(
            python_dependency(path, source_id, project_name, str(spec), "dependencies")
        )
    for group_name, specs in (data.get("dependency-groups", {}) or {}).items():
        for spec in specs or []:
            dependencies.append(
                python_dependency(path, source_id, project_name, str(spec), f"dependency-groups.{group_name}")
            )
    return dependencies


def parse_package_json(path: str, source: str) -> list[PackageDependency]:
    data = json.loads(source)
    package_name = str(data.get("name") or package_name_from_path(path))
    source_id = f"package:npm:{normalize_package_name(package_name)}"
    dependencies = []
    for section in ("dependencies", "devDependencies"):
        for name in sorted((data.get(section) or {}).keys()):
            normalized = normalize_package_name(name)
            dependencies.append(
                PackageDependency(
                    source_id=source_id,
                    target_id=f"npm:{normalized}",
                    source_name=package_name,
                    target_name=name,
                    manager="npm",
                    section=section,
                    evidence=Evidence(path=path, kind="package_manifest"),
                )
            )
    return dependencies


def python_dependency(
    path: str,
    source_id: str,
    project_name: str,
    spec: str,
    section: str,
) -> PackageDependency:
    name = dependency_name_from_spec(spec)
    normalized = normalize_package_name(name)
    return PackageDependency(
        source_id=source_id,
        target_id=f"pypi:{normalized}",
        source_name=project_name,
        target_name=name,
        manager="pypi",
        section=section,
        evidence=Evidence(path=path, kind="package_manifest"),
    )


def dependency_name_from_spec(spec: str) -> str:
    name_chars = []
    for char in spec.strip():
        if char.isalnum() or char in {"-", "_", "."}:
            name_chars.append(char)
            continue
        break
    return "".join(name_chars)


def normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def package_name_from_path(path: str) -> str:
    parts = path.split("/")
    if len(parts) >= 2:
        return parts[-2]
    return "npm-package"
