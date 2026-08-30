"""Analyzer registry / orchestration.

Runs the enabled analyzers in dependency order (AST first, because the
dependency and test analyzers consume its parsed trees) and aggregates the
results into a single evidence bundle.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.analyzers.ast_analyzer import ASTAnalyzer, FileAST
from app.analyzers.base import AnalysisRequest, AnalyzerResult, RawFinding
from app.analyzers.dependency import DependencyAnalyzer
from app.analyzers.diff import DiffAnalyzer
from app.analyzers.security import SecurityAnalyzer
from app.analyzers.static import StaticAnalyzer
from app.analyzers.tests_analyzer import TestAnalyzer
from app.core.logging import get_logger

logger = get_logger(__name__)

ALL_ANALYZERS = ("diff", "ast", "static", "dependency", "test", "security")


@dataclass(slots=True)
class AnalysisBundle:
    """Aggregated deterministic evidence for one review run."""

    results: dict[str, AnalyzerResult] = field(default_factory=dict)
    asts: dict[str, FileAST] = field(default_factory=dict)

    @property
    def findings(self) -> list[RawFinding]:
        collected: list[RawFinding] = []
        for result in self.results.values():
            collected.extend(result.findings)
        return collected

    def data(self, name: str) -> dict[str, Any]:
        result = self.results.get(name)
        return result.data if result else {}

    @property
    def warnings(self) -> list[str]:
        return [w for result in self.results.values() for w in result.warnings]

    def to_dict(self) -> dict[str, Any]:
        serialisable: dict[str, Any] = {}
        for name, result in self.results.items():
            payload = result.to_dict()
            payload["data"] = {k: v for k, v in payload["data"].items() if not k.startswith("_")}
            serialisable[name] = payload
        return serialisable

    def summary(self) -> dict[str, Any]:
        return {
            name: {
                "ok": result.ok,
                "skipped": result.skipped,
                "skip_reason": result.skip_reason,
                "duration_ms": result.duration_ms,
                "findings": len(result.findings),
                "error": result.error,
            }
            for name, result in self.results.items()
        }


def run_analyzers(
    request: AnalysisRequest,
    enabled: list[str] | None = None,
) -> AnalysisBundle:
    """Execute all enabled analyzers and return the aggregated evidence."""
    selected = [name for name in ALL_ANALYZERS if not enabled or name in enabled]
    bundle = AnalysisBundle()

    if "diff" in selected:
        bundle.results["diff"] = DiffAnalyzer().run(request)

    if "ast" in selected:
        ast_result = ASTAnalyzer().run(request)
        bundle.asts = ast_result.data.pop("_asts", {}) or {}
        bundle.results["ast"] = ast_result

    if "static" in selected:
        bundle.results["static"] = StaticAnalyzer().run(request)

    if "dependency" in selected:
        bundle.results["dependency"] = DependencyAnalyzer(bundle.asts).run(request)

    if "test" in selected:
        bundle.results["test"] = TestAnalyzer(bundle.asts).run(request)

    if "security" in selected:
        bundle.results["security"] = SecurityAnalyzer().run(request)

    for name, result in bundle.results.items():
        if not result.ok:
            logger.warning("analyzer_failed", analyzer=name, error=result.error)

    return bundle
