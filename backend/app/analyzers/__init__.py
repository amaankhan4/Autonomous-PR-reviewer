"""Repository analyzers producing structured, evidence-backed signals."""

from app.analyzers.ast_analyzer import ASTAnalyzer, FileAST, Symbol, parse_source
from app.analyzers.base import (
    AnalysisRequest,
    AnalyzerResult,
    RawFinding,
    RepositoryAnalyzer,
    detect_language,
)
from app.analyzers.dependency import DependencyAnalyzer
from app.analyzers.diff import (
    DiffAnalysis,
    DiffAnalyzer,
    DiffHunk,
    DiffLine,
    FileDiff,
    build_diff_analysis,
    parse_patch,
    parse_unified_diff,
)
from app.analyzers.registry import ALL_ANALYZERS, AnalysisBundle, run_analyzers
from app.analyzers.security import SECURITY_RULES, SecurityAnalyzer
from app.analyzers.static import StaticAnalyzer
from app.analyzers.tests_analyzer import TestAnalyzer

__all__ = [
    "ALL_ANALYZERS",
    "ASTAnalyzer",
    "AnalysisBundle",
    "AnalysisRequest",
    "AnalyzerResult",
    "DependencyAnalyzer",
    "DiffAnalysis",
    "DiffAnalyzer",
    "DiffHunk",
    "DiffLine",
    "FileAST",
    "FileDiff",
    "RawFinding",
    "RepositoryAnalyzer",
    "SECURITY_RULES",
    "SecurityAnalyzer",
    "StaticAnalyzer",
    "Symbol",
    "TestAnalyzer",
    "build_diff_analysis",
    "detect_language",
    "parse_patch",
    "parse_source",
    "parse_unified_diff",
    "run_analyzers",
]
