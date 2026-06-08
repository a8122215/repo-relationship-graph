from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Protocol

from repo_graph.core.model import PackageDependency, SourceFile, SourceParseResult, UnsupportedRecord


class SourceParser(Protocol):
    def parse(
        self,
        path: str,
        module_name: str,
        source: str,
        known_modules: Iterable[str] | None = None,
    ) -> SourceParseResult:
        ...


class ParserRegistry:
    def __init__(
        self,
        parsers: Mapping[str, SourceParser],
        deferred_languages: Mapping[str, str] | None = None,
    ) -> None:
        self._parsers = dict(parsers)
        self._deferred_languages = dict(deferred_languages or {})

    def parser_for(self, language: str) -> SourceParser | None:
        return self._parsers.get(language)

    def require(self, language: str) -> SourceParser:
        parser = self.parser_for(language)
        if parser is None:
            raise KeyError(f"parser is not registered for language: {language}")
        return parser

    def unsupported_record_for(self, source_file: SourceFile) -> UnsupportedRecord | None:
        message = self._deferred_languages.get(source_file.language)
        if message is None:
            return None
        return UnsupportedRecord(
            path=source_file.path,
            language=source_file.language,
            reason="parser_not_enabled",
            message=message,
        )


ManifestParser = Callable[[str, str], list[PackageDependency]]


class ManifestParserRegistry:
    def __init__(self, parsers_by_path: Mapping[str, ManifestParser]) -> None:
        self._parsers_by_path = dict(parsers_by_path)

    def supports(self, path: str) -> bool:
        return path in self._parsers_by_path

    def parse(self, path: str, source: str) -> list[PackageDependency]:
        parser = self._parsers_by_path.get(path)
        if parser is None:
            return []
        return parser(path, source)
