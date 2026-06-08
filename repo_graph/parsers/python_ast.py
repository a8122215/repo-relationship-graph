from __future__ import annotations

import ast
from collections.abc import Iterable
from pathlib import Path

from repo_graph.core.model import (
    EndpointDeclaration,
    Evidence,
    ImportRelation,
    Node,
    IncludeRouterRelation,
    PythonParseResult,
    UnsupportedRecord,
)


INTERNAL_ROOTS = {"server", "shared", "tests", "tools", "analysis", "training"}
HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


class PythonAstParser:
    def parse(
        self,
        path: str,
        module_name: str,
        source: str,
        known_modules: Iterable[str] | None = None,
    ) -> PythonParseResult:
        known = set(known_modules or ())
        result = PythonParseResult(
            path=path,
            module_name=module_name,
            source_id=f"py:{module_name}",
            node=Node(
                id=f"py:{module_name}",
                type="test_file" if is_python_test(path) else "python_module",
                name=module_name,
                path=path,
                language="python",
            ),
        )
        try:
            tree = ast.parse(source, filename=path)
        except SyntaxError as exc:
            result.unsupported.append(
                UnsupportedRecord(
                    path=path,
                    language="python",
                    reason="python_syntax_error",
                    message=f"SyntaxError: {exc.msg}",
                    line=exc.lineno,
                )
            )
            return result

        import_aliases: dict[str, str] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    target = alias.name
                    import_aliases[alias.asname or alias.name.split(".")[0]] = target
                    result.imports.append(
                        ImportRelation(module_name, target, Evidence(path, "ast_import", node.lineno))
                    )
            elif isinstance(node, ast.ImportFrom):
                targets = resolve_import_from(module_name, node.level, node.module, node.names, known)
                for alias, target in zip(node.names, targets, strict=False):
                    import_aliases[alias.asname or alias.name] = target
                    result.imports.append(
                        ImportRelation(module_name, target, Evidence(path, "ast_import", node.lineno))
                    )

        for node in ast.walk(tree):
            prefix = router_prefix_from_assignment(node)
            if prefix is not None:
                var_name, value = prefix
                result.router_prefixes[var_name] = value

            endpoint = endpoint_from_decorated_node(node, module_name, path)
            if endpoint is not None:
                result.endpoints.append(endpoint)

            include_router = include_router_from_call(node, module_name, path, import_aliases)
            if include_router is not None:
                result.include_routers.append(include_router)

        return result


def resolve_import_from(
    current_module: str,
    level: int,
    module: str | None,
    aliases: list[ast.alias],
    known_modules: set[str],
) -> list[str]:
    base_module = resolve_relative_module(current_module, level, module)
    targets = []
    for alias in aliases:
        if alias.name == "*":
            targets.append(base_module)
            continue
        module_alias = f"{base_module}.{alias.name}" if base_module else alias.name
        if known_modules:
            if module_alias in known_modules:
                targets.append(module_alias)
            elif base_module in known_modules:
                targets.append(base_module)
            else:
                targets.append(module_alias if starts_with_internal_root(base_module) else base_module)
        elif starts_with_internal_root(base_module) and len(base_module.split(".")) <= 2:
            targets.append(module_alias)
        else:
            targets.append(base_module)
    return targets


def resolve_relative_module(current_module: str, level: int, module: str | None) -> str:
    if level == 0:
        return module or ""
    parts = current_module.split(".")
    package_parts = parts[:-1]
    if level > 1:
        package_parts = package_parts[: -(level - 1)]
    if module:
        package_parts.extend(module.split("."))
    return ".".join(part for part in package_parts if part)


def starts_with_internal_root(module: str) -> bool:
    return bool(module) and module.split(".")[0] in INTERNAL_ROOTS


def router_prefix_from_assignment(node: ast.AST) -> tuple[str, str] | None:
    if not isinstance(node, ast.Assign):
        return None
    if len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
        return None
    if not isinstance(node.value, ast.Call) or not is_name_or_attr(node.value.func, "APIRouter"):
        return None
    prefix = string_keyword(node.value, "prefix") or ""
    return node.targets[0].id, prefix


def endpoint_from_decorated_node(
    node: ast.AST,
    module_name: str,
    path: str,
) -> EndpointDeclaration | None:
    if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return None
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        if not isinstance(decorator.func, ast.Attribute):
            continue
        method = decorator.func.attr.lower()
        if method not in HTTP_METHODS:
            continue
        if not isinstance(decorator.func.value, ast.Name):
            continue
        route_path = first_string_arg(decorator)
        if route_path is None:
            continue
        return EndpointDeclaration(
            source_module=module_name,
            router_name=decorator.func.value.id,
            method=method.upper(),
            path=route_path,
            evidence=Evidence(path, "fastapi_decorator", decorator.lineno),
        )
    return None


def include_router_from_call(
    node: ast.AST,
    module_name: str,
    path: str,
    import_aliases: dict[str, str],
) -> IncludeRouterRelation | None:
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None
    call = node.value
    if not isinstance(call.func, ast.Attribute) or call.func.attr != "include_router":
        return None
    if not call.args:
        return None
    router_arg = call.args[0]
    target_module = None
    if isinstance(router_arg, ast.Attribute) and isinstance(router_arg.value, ast.Name):
        target_module = import_aliases.get(router_arg.value.id)
    prefix = string_keyword(call, "prefix") or ""
    return IncludeRouterRelation(
        source_module=module_name,
        target_module=target_module,
        prefix=prefix,
        evidence=Evidence(path, "fastapi_include_router", call.lineno),
    )


def is_name_or_attr(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name
    if isinstance(node, ast.Attribute):
        return node.attr == name
    return False


def string_keyword(call: ast.Call, keyword_name: str) -> str | None:
    for keyword in call.keywords:
        if keyword.arg == keyword_name and isinstance(keyword.value, ast.Constant):
            if isinstance(keyword.value.value, str):
                return keyword.value.value
    return None


def first_string_arg(call: ast.Call) -> str | None:
    if not call.args:
        return ""
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def is_python_test(path: str) -> bool:
    if not path.endswith(".py"):
        return False
    name = Path(path).name
    return name.startswith("test_") or name.endswith("_test.py")
