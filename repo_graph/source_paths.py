from __future__ import annotations

from pathlib import PurePosixPath

DEFAULT_FRONTEND_TEST_ROOTS = ("client/e2e/",)


def is_frontend_test_path(path: str, test_roots: tuple[str, ...] = DEFAULT_FRONTEND_TEST_ROOTS) -> bool:
    if not path.endswith((".js", ".jsx", ".ts", ".tsx")):
        return False
    normalized_path = normalize_posix_path(path)
    if any(path_is_inside_test_root(normalized_path, root) for root in normalize_test_roots(test_roots)):
        return True
    name = PurePosixPath(normalized_path).name
    return (
        name.endswith(".spec.js")
        or name.endswith(".test.js")
        or name.endswith(".spec.jsx")
        or name.endswith(".test.jsx")
        or name.endswith(".spec.ts")
        or name.endswith(".test.ts")
        or name.endswith(".spec.tsx")
        or name.endswith(".test.tsx")
    )


def normalize_test_roots(test_roots: tuple[str, ...]) -> tuple[str, ...]:
    normalized = []
    for root in test_roots:
        normalized_root = normalize_posix_path(root)
        if normalized_root and not normalized_root.endswith("/"):
            normalized_root = f"{normalized_root}/"
        if normalized_root:
            normalized.append(normalized_root)
    return tuple(normalized)


def path_is_inside_test_root(path: str, test_root: str) -> bool:
    return path.startswith(test_root)


def normalize_posix_path(path: str) -> str:
    normalized = path.replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized
