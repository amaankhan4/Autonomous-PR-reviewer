"""Dependency / blast-radius analysis.

Answers three questions for every changed symbol:

1. what does it call (callees)?
2. who calls it (callers) -- resolved across the files available to the review;
3. what modules does the change pull in (new / removed imports)?
"""

from __future__ import annotations

import re
from typing import Any

from app.analyzers.ast_analyzer import FileAST
from app.analyzers.base import (
    AnalysisRequest,
    AnalyzerResult,
    Language,
    RawFinding,
    RepositoryAnalyzer,
)

_STDLIB_HINT = {
    "os", "sys", "re", "json", "time", "math", "typing", "pathlib", "logging",
    "asyncio", "datetime", "collections", "itertools", "functools", "dataclasses",
    "subprocess", "hashlib", "hmac", "secrets", "uuid", "abc", "enum", "contextlib",
}

_RISKY_PACKAGES = {
    "pickle": "Arbitrary code execution during deserialization.",
    "marshal": "Unsafe binary deserialization format.",
    "telnetlib": "Deprecated, transmits credentials in clear text.",
    "ftplib": "Unencrypted protocol.",
    "eval": "Dynamic evaluation.",
    "request": "Deprecated Node HTTP client, unmaintained.",
    "node-serialize": "Known RCE via unserialize().",
    "crypto-js": "Frequently misused; prefer platform crypto primitives.",
}


class DependencyAnalyzer(RepositoryAnalyzer):
    name = "dependency"

    def __init__(self, asts: dict[str, FileAST] | None = None) -> None:
        self.asts = asts or {}

    def analyze(self, request: AnalysisRequest) -> AnalyzerResult:
        diff = request.diff
        added_imports: dict[str, list[str]] = {}
        removed_imports: dict[str, list[str]] = {}
        dependency_manifest_changes: list[str] = []
        findings: list[RawFinding] = []

        for file_diff in diff.files:
            if file_diff.path.split("/")[-1] in {
                "requirements.txt",
                "pyproject.toml",
                "package.json",
                "Pipfile",
                "go.mod",
                "requirements-dev.txt",
            }:
                dependency_manifest_changes.append(file_diff.path)

            for hunk in file_diff.hunks:
                for line in hunk.added_lines:
                    module = _extract_import(line.content, file_diff.language)
                    if module:
                        added_imports.setdefault(file_diff.path, []).append(module)
                        risk = _risky(module)
                        if risk:
                            findings.append(
                                RawFinding(
                                    file_path=file_diff.path,
                                    line_number=line.new_lineno,
                                    severity="MEDIUM",
                                    category="security",
                                    title=f"Risky dependency introduced: {module}",
                                    message=risk,
                                    tool="dependency",
                                    rule_id="dep/risky-import",
                                    evidence=(
                                        f"{file_diff.path}:{line.new_lineno} "
                                        f"| {line.content.strip()[:160]}"
                                    ),
                                    suggested_fix="Prefer a safer, maintained alternative.",
                                    confidence=0.8,
                                )
                            )
                for line in hunk.removed_lines:
                    module = _extract_import(line.content, file_diff.language)
                    if module:
                        removed_imports.setdefault(file_diff.path, []).append(module)

        symbol_index = self._build_symbol_index()
        changed_symbols = self._changed_symbols(request)
        graph: list[dict[str, Any]] = []

        for path, symbol in changed_symbols:
            callees = [c for c in symbol.calls if c]
            resolved_callees = [
                {
                    "name": callee,
                    "defined_in": symbol_index.get(_basename(callee), {}).get("file"),
                    "line": symbol_index.get(_basename(callee), {}).get("line"),
                }
                for callee in callees[:20]
            ]
            callers = self._find_callers(symbol.name, exclude_file=path)
            graph.append(
                {
                    "symbol": symbol.qualified_name,
                    "file": path,
                    "start_line": symbol.start_line,
                    "kind": symbol.kind,
                    "callees": resolved_callees,
                    "callers": callers[:15],
                    "caller_count": len(callers),
                    "blast_radius": _blast_radius(len(callers)),
                }
            )

        external_dependencies = sorted(
            {
                module.split(".")[0]
                for modules in added_imports.values()
                for module in modules
                if module.split(".")[0] not in _STDLIB_HINT and not module.startswith(".")
            }
        )

        data: dict[str, Any] = {
            "added_imports": added_imports,
            "removed_imports": removed_imports,
            "external_dependencies_added": external_dependencies,
            "dependency_manifest_changes": dependency_manifest_changes,
            "symbol_graph": graph,
            "high_blast_radius_symbols": [
                entry["symbol"] for entry in graph if entry["blast_radius"] in ("high", "critical")
            ],
        }
        return AnalyzerResult(name=self.name, data=data, findings=findings)

    # -------------------------------------------------------------- helpers
    def _build_symbol_index(self) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for path, file_ast in self.asts.items():
            for symbol in file_ast.symbols:
                index.setdefault(
                    symbol.name,
                    {"file": path, "line": symbol.start_line, "kind": symbol.kind},
                )
        return index

    def _changed_symbols(self, request: AnalysisRequest) -> list[tuple[str, Any]]:
        changed: list[tuple[str, Any]] = []
        for file_diff in request.diff.files:
            file_ast = self.asts.get(file_diff.path)
            if file_ast is None or not file_ast.supported:
                continue
            for symbol in file_ast.symbols_covering(file_diff.added_line_numbers):
                if symbol.kind in ("function", "method"):
                    changed.append((file_diff.path, symbol))
        return changed

    def _find_callers(self, name: str, *, exclude_file: str) -> list[dict[str, Any]]:
        callers: list[dict[str, Any]] = []
        for path, file_ast in self.asts.items():
            if path == exclude_file:
                continue
            for symbol in file_ast.symbols:
                if any(_basename(call) == name for call in symbol.calls):
                    callers.append(
                        {
                            "symbol": symbol.qualified_name,
                            "file": path,
                            "line": symbol.start_line,
                        }
                    )
        return callers


_PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))")
_JS_IMPORT = re.compile(r"""(?:from\s+['"]([^'"]+)['"]|require\(\s*['"]([^'"]+)['"]\s*\))""")


def _extract_import(line: str, language: Language) -> str | None:
    if language == Language.PYTHON:
        match = _PY_IMPORT.match(line)
        if match:
            return match.group(1) or match.group(2)
        return None
    if language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
        match = _JS_IMPORT.search(line)
        if match:
            return match.group(1) or match.group(2)
    return None


def _risky(module: str) -> str | None:
    root = module.split(".")[0].split("/")[0]
    return _RISKY_PACKAGES.get(root)


def _basename(call: str) -> str:
    return call.split(".")[-1].split("(")[0].strip()


def _blast_radius(caller_count: int) -> str:
    if caller_count == 0:
        return "isolated"
    if caller_count <= 2:
        return "low"
    if caller_count <= 5:
        return "medium"
    if caller_count <= 12:
        return "high"
    return "critical"
