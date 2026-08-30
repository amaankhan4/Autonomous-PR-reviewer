"""Test coverage analysis (static only).

Per the security model the reviewer never *executes* repository test suites --
that would require running untrusted code on the host. Instead test files are
analysed statically: which tests exist, which changed symbols they reference,
and which new behaviour arrived without a corresponding test.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any

from app.analyzers.ast_analyzer import FileAST
from app.analyzers.base import (
    AnalysisRequest,
    AnalyzerResult,
    RawFinding,
    RepositoryAnalyzer,
)

_TEST_NAME_PATTERNS = (
    re.compile(r"^test_(?P<stem>.+)\.py$"),
    re.compile(r"^(?P<stem>.+)_test\.py$"),
    re.compile(r"^(?P<stem>.+)\.test\.[jt]sx?$"),
    re.compile(r"^(?P<stem>.+)\.spec\.[jt]sx?$"),
)


class TestAnalyzer(RepositoryAnalyzer):
    name = "test"

    def __init__(self, asts: dict[str, FileAST] | None = None) -> None:
        self.asts = asts or {}

    def analyze(self, request: AnalysisRequest) -> AnalyzerResult:
        diff = request.diff
        test_files = [f for f in diff.files if f.is_test]
        source_files = [
            f
            for f in diff.source_files
            if f.change_type != "removed" and not f.is_binary
        ]

        related_map: dict[str, list[str]] = {}
        for source in source_files:
            related = self._related_tests(source.path, diff.files)
            if related:
                related_map[source.path] = related

        new_public_symbols: list[dict[str, Any]] = []
        tested_symbol_names = self._symbols_referenced_by_tests()

        for source in source_files:
            file_ast = self.asts.get(source.path)
            if file_ast is None or not file_ast.supported:
                continue
            for symbol in file_ast.symbols_covering(source.added_line_numbers):
                if symbol.kind not in ("function", "method"):
                    continue
                if symbol.name.startswith("_"):
                    continue
                if source.change_type == "added" or symbol.start_line in source.added_line_numbers:
                    new_public_symbols.append(
                        {
                            "symbol": symbol.qualified_name,
                            "file": source.path,
                            "line": symbol.start_line,
                            "signature": symbol.signature,
                            "referenced_by_tests": symbol.name in tested_symbol_names,
                        }
                    )

        untested = [s for s in new_public_symbols if not s["referenced_by_tests"]]
        findings: list[RawFinding] = []

        if source_files and not test_files:
            changed_source = ", ".join(f.path for f in source_files[:5])
            findings.append(
                RawFinding(
                    file_path=source_files[0].path,
                    line_number=None,
                    severity="MEDIUM" if untested else "LOW",
                    category="testing",
                    title="Source changed without any test changes",
                    message=(
                        f"{len(source_files)} source file(s) changed "
                        f"({changed_source}) but no test file was added or modified in this "
                        "pull request."
                    ),
                    tool="test",
                    rule_id="test/no-tests-changed",
                    evidence=f"changed source files: {changed_source}",
                    suggested_fix="Add regression tests covering the new behaviour.",
                    confidence=0.95,
                )
            )

        for entry in untested[:10]:
            findings.append(
                RawFinding(
                    file_path=entry["file"],
                    line_number=entry["line"],
                    severity="LOW",
                    category="testing",
                    title=f"New public function '{entry['symbol']}' has no referencing test",
                    message=(
                        f"`{entry['signature']}` was introduced in this pull request and no test "
                        "file in the changed set references it."
                    ),
                    tool="test",
                    rule_id="test/untested-symbol",
                    evidence=f"{entry['file']}:{entry['line']} {entry['signature']}",
                    suggested_fix=f"Add a test exercising `{entry['symbol']}`.",
                    confidence=0.8,
                )
            )

        data: dict[str, Any] = {
            "test_files_changed": [f.path for f in test_files],
            "test_files_added": [f.path for f in test_files if f.change_type == "added"],
            "source_files_changed": [f.path for f in source_files],
            "related_tests": related_map,
            "source_files_without_related_tests": [
                f.path for f in source_files if f.path not in related_map
            ],
            "new_public_symbols": new_public_symbols,
            "untested_new_symbols": untested,
            "test_to_source_ratio": (
                round(len(test_files) / len(source_files), 2) if source_files else None
            ),
            "execution_note": (
                "Repository test suites are never executed by the reviewer; this is a static "
                "analysis of test files only."
            ),
        }
        return AnalyzerResult(name=self.name, data=data, findings=findings)

    # -------------------------------------------------------------- helpers
    def _related_tests(self, source_path: str, all_files: list[Any]) -> list[str]:
        stem = PurePosixPath(source_path).stem
        related: list[str] = []
        candidates = [f.path for f in all_files if f.is_test]
        candidates += [p for p in self.asts if _looks_like_test(p)]
        for candidate in dict.fromkeys(candidates):
            candidate_stem = _test_stem(PurePosixPath(candidate).name)
            if candidate_stem and (candidate_stem == stem or stem in candidate_stem):
                related.append(candidate)
        return related

    def _symbols_referenced_by_tests(self) -> set[str]:
        referenced: set[str] = set()
        for path, file_ast in self.asts.items():
            if not _looks_like_test(path):
                continue
            for symbol in file_ast.symbols:
                for call in symbol.calls:
                    referenced.add(call.split(".")[-1].strip())
            for imported in file_ast.imports:
                referenced.add(imported.split(".")[-1])
        return referenced


def _looks_like_test(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    return (
        "/tests/" in lowered
        or "/test/" in lowered
        or "__tests__" in lowered
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.py")
    )


def _test_stem(filename: str) -> str | None:
    for pattern in _TEST_NAME_PATTERNS:
        match = pattern.match(filename)
        if match:
            return match.group("stem")
    return None
