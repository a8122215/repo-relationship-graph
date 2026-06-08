from __future__ import annotations

import re
from dataclasses import dataclass


GENERATOR_VERSION = "code-graph@0.1.0"
PLUGIN_NAME = "repo-relationship-graph"
PLUGIN_SEMVER = "0.1.0"
PLUGIN_VERSION = f"{PLUGIN_NAME}@{PLUGIN_SEMVER}"

VERSION_REFERENCE_RE = re.compile(
    r"^(?P<name>[A-Za-z0-9_.-]+)@(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:[-+][A-Za-z0-9_.+-]+)?$"
)


@dataclass(frozen=True)
class VersionReference:
    name: str
    major: int
    minor: int
    patch: int

    @property
    def version_tuple(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def ensure_plugin_version_compatible(
    required_version: str,
    runtime_version: str = PLUGIN_VERSION,
    allow_newer_minor: bool = True,
) -> None:
    required = parse_version_reference(required_version)
    runtime = parse_version_reference(runtime_version)
    if required.name != runtime.name:
        raise ValueError(
            f"incompatible code graph plugin package: required {required.name}, runtime {runtime.name}"
        )
    if required.major != runtime.major:
        raise ValueError(
            f"incompatible code graph plugin major version: required {required_version}, runtime {runtime_version}"
        )
    if runtime.version_tuple < required.version_tuple:
        raise ValueError(
            f"code graph plugin runtime is older than required: required {required_version}, runtime {runtime_version}"
        )
    if not allow_newer_minor and runtime.minor > required.minor:
        raise ValueError(
            f"code graph plugin newer minor version is not allowed: required {required_version}, "
            f"runtime {runtime_version}"
        )


def parse_version_reference(value: str) -> VersionReference:
    match = VERSION_REFERENCE_RE.match(value)
    if match is None:
        raise ValueError(f"invalid code graph version reference: {value}")
    return VersionReference(
        name=match.group("name"),
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
    )
