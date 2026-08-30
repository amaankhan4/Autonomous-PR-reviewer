"""Shared analyzer contracts.

Every analyzer turns raw pull-request material into *structured evidence*. The
LLM only ever reasons over these structures -- it never receives raw repository
dumps. Each analyzer is independent and failure-isolated so one broken tool can
never take down a review.
"""

from __future__ import annotations

import abc
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.core.enums import Language

if TYPE_CHECKING:
    from app.analyzers.diff import DiffAnalysis


EXTENSION_LANGUAGE: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyi": Language.PYTHON,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".mjs": Language.JAVASCRIPT,
    ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".mts": Language.TYPESCRIPT,
    ".json": Language.JSON,
    ".yaml": Language.YAML,
    ".yml": Language.YAML,
    ".md": Language.MARKDOWN,
    ".sql": Language.SQL,
    ".sh": Language.SHELL,
    ".bash": Language.SHELL,
    ".html": Language.HTML,
    ".css": Language.CSS,
    ".scss": Language.CSS,
    ".go": Language.GO,
    ".java": Language.JAVA,
    ".rb": Language.RUBY,
}

ANALYZABLE_LANGUAGES = {Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT}


def detect_language(path: str) -> Language:
    lowered = path.lower()
    for ext, language in EXTENSION_LANGUAGE.items():
        if lowered.endswith(ext):
            return language
    return Language.UNKNOWN


@dataclass(slots=True)
class RawFinding:
    """A deterministic (non-LLM) finding produced by an analyzer."""

    file_path: str
    line_number: int | None
    severity: str
    category: str
    title: str
    message: str
    tool: str
    rule_id: str | None = None
    evidence: str | None = None
    suggested_fix: str | None = None
    confidence: float = 0.9

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "line": self.line_number,
            "severity": self.severity,
            "category": self.category,
            "title": self.title,
            "message": self.message,
            "tool": self.tool,
            "rule": self.rule_id,
            "evidence": self.evidence,
            "suggested_fix": self.suggested_fix,
            "confidence": self.confidence,
        }


@dataclass(slots=True)
class AnalyzerResult:
    """Uniform analyzer output."""

    name: str
    ok: bool = True
    skipped: bool = False
    skip_reason: str | None = None
    duration_ms: int = 0
    findings: list[RawFinding] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "ok": self.ok,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
            "duration_ms": self.duration_ms,
            "findings": [f.to_dict() for f in self.findings],
            "data": self.data,
            "warnings": self.warnings,
            "error": self.error,
        }


@dataclass(slots=True)
class AnalysisRequest:
    """Everything an analyzer may need, assembled once by the pipeline."""

    repository_full_name: str
    head_sha: str
    base_sha: str
    diff: DiffAnalysis
    # path -> full file content at head (only for changed, analyzable files)
    file_contents: dict[str, str] = field(default_factory=dict)
    # Optional read-through accessor for repository files outside the diff.
    read_file: Any | None = None
    settings: dict[str, Any] = field(default_factory=dict)

    def content_for(self, path: str) -> str | None:
        return self.file_contents.get(path)


class RepositoryAnalyzer(abc.ABC):
    """Base class for all analyzers."""

    name: str = "analyzer"

    @abc.abstractmethod
    def analyze(self, request: AnalysisRequest) -> AnalyzerResult:  # pragma: no cover - abstract
        ...

    def run(self, request: AnalysisRequest) -> AnalyzerResult:
        """Execute the analyzer, converting unexpected failures into results."""
        started = time.perf_counter()
        try:
            result = self.analyze(request)
        except Exception as exc:  # pragma: no cover - defensive
            result = AnalyzerResult(name=self.name, ok=False, error=f"{type(exc).__name__}: {exc}")
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result
