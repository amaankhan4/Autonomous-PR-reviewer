"""Deterministic static analysis via external tools.

Security model
--------------
Repository content is **untrusted input**. Files are materialised into a
throw-away temporary directory with path-traversal protection, and only tools
that *parse* code (never execute it) are invoked:

* ``ruff``   -- lint / bug patterns
* ``bandit`` -- security lint
* ``mypy``   -- type checking with ``--follow-imports=skip`` (no imports executed)

Every invocation uses an argument list (never ``shell=True``), an absolute
executable path resolved from ``PATH``, a hard timeout, and an isolated
configuration so a malicious repository cannot supply plugin config.

ESLint/``tsc`` need an installed ``node_modules`` tree for the target repository,
which the reviewer deliberately does not create (that would mean executing
untrusted install scripts). They are reported as *skipped with a reason* rather
than silently ignored.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.analyzers.base import (
    AnalysisRequest,
    AnalyzerResult,
    Language,
    RawFinding,
    RepositoryAnalyzer,
)
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_RUFF_CATEGORY = {
    "S": "security",
    "B": "correctness",
    "E": "style",
    "W": "style",
    "F": "correctness",
    "C": "maintainability",
    "N": "style",
    "PERF": "performance",
    "ASYNC": "reliability",
    "RUF": "maintainability",
    "SIM": "maintainability",
    "PL": "maintainability",
    "TRY": "reliability",
}

_RUFF_SEVERITY = {
    "F": "MEDIUM",
    "B": "MEDIUM",
    "S": "HIGH",
    "ASYNC": "HIGH",
    "TRY": "LOW",
    "E": "LOW",
    "W": "LOW",
    "C": "LOW",
    "SIM": "LOW",
    "PERF": "LOW",
    "RUF": "LOW",
    "PL": "LOW",
    "N": "INFO",
}

_BANDIT_SEVERITY = {"HIGH": "HIGH", "MEDIUM": "MEDIUM", "LOW": "LOW"}


@dataclass(slots=True)
class ToolRun:
    tool: str
    available: bool
    ok: bool = True
    skipped: bool = False
    reason: str | None = None
    duration_ms: int = 0
    findings: int = 0
    stderr: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "available": self.available,
            "ok": self.ok,
            "skipped": self.skipped,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "findings": self.findings,
            "stderr": (self.stderr or "")[:500] or None,
        }


class SafeCommandRunner:
    """Runs an allow-listed external tool without a shell."""

    ALLOWED = {"ruff", "bandit", "mypy", "eslint", "tsc", "npx", "semgrep"}

    def __init__(self, timeout: int) -> None:
        self.timeout = timeout

    def resolve(self, tool: str) -> str | None:
        if tool not in self.ALLOWED:
            raise ValueError(f"Tool '{tool}' is not allow-listed")
        # Prefer a tool installed in the same environment as the backend.
        candidate = Path(sys.executable).parent / (
            f"{tool}.exe" if os.name == "nt" else tool
        )
        if candidate.exists():
            return str(candidate)
        return shutil.which(tool)

    def run(self, tool: str, args: list[str], cwd: Path, timeout: int | None = None) -> tuple[int, str, str]:
        executable = self.resolve(tool)
        if executable is None:
            raise FileNotFoundError(tool)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
            "TEMP": str(cwd),
            "TMP": str(cwd),
            "HOME": str(cwd),
            "PYTHONDONTWRITEBYTECODE": "1",
            "NO_COLOR": "1",
        }
        completed = subprocess.run(  # noqa: S603 - argv list, allow-listed executable, no shell
            [executable, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout or self.timeout,
            shell=False,
            env={k: v for k, v in env.items() if v},
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr


def _safe_write(root: Path, relative_path: str, content: str) -> Path | None:
    """Write ``content`` under ``root`` refusing any path escaping the sandbox."""
    normalised = relative_path.replace("\\", "/").lstrip("/")
    if ".." in normalised.split("/"):
        logger.warning("static_analysis_path_rejected", path=relative_path)
        return None
    target = (root / normalised).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        logger.warning("static_analysis_path_escape", path=relative_path)
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", errors="replace")
    return target


class StaticAnalyzer(RepositoryAnalyzer):
    name = "static"

    def __init__(self, timeout: int | None = None) -> None:
        self.timeout = timeout or settings.STATIC_ANALYSIS_TIMEOUT_SECONDS
        self.runner = SafeCommandRunner(self.timeout)

    # ------------------------------------------------------------------ run
    def analyze(self, request: AnalysisRequest) -> AnalyzerResult:
        if not settings.ENABLE_STATIC_ANALYSIS:
            return AnalyzerResult(
                name=self.name, skipped=True, skip_reason="Static analysis disabled by config"
            )

        python_files: dict[str, str] = {}
        js_files: dict[str, str] = {}
        for file_diff in request.diff.files:
            content = request.content_for(file_diff.path)
            if content is None or file_diff.change_type == "removed":
                continue
            if file_diff.language == Language.PYTHON:
                python_files[file_diff.path] = content
            elif file_diff.language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
                js_files[file_diff.path] = content

        if not python_files and not js_files:
            return AnalyzerResult(
                name=self.name,
                skipped=True,
                skip_reason="No Python/JavaScript/TypeScript files in the changed set",
            )

        findings: list[RawFinding] = []
        runs: list[ToolRun] = []

        with tempfile.TemporaryDirectory(prefix="pr-review-static-") as tmp:
            root = Path(tmp).resolve()
            written: list[str] = []
            for path, content in python_files.items():
                if _safe_write(root, path, content):
                    written.append(path.replace("\\", "/").lstrip("/"))

            if written:
                # ruff / bandit / mypy are independent -- run them concurrently so the
                # slowest tool (mypy) dominates instead of the sum of all three.
                jobs = {
                    "ruff": self._run_ruff,
                    "bandit": self._run_bandit,
                }
                if settings.STATIC_MYPY_ENABLED:
                    jobs["mypy"] = self._run_mypy

                with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
                    futures = {
                        name: pool.submit(fn, root, written) for name, fn in jobs.items()
                    }
                    for name, future in futures.items():
                        try:
                            run, tool_findings = future.result()
                        except Exception as exc:  # pragma: no cover - defensive
                            runs.append(
                                ToolRun(
                                    tool=name,
                                    available=True,
                                    ok=False,
                                    reason=f"{type(exc).__name__}: {exc}",
                                )
                            )
                            continue
                        runs.append(run)
                        findings.extend(tool_findings)

                if not settings.STATIC_MYPY_ENABLED:
                    runs.append(
                        ToolRun(
                            tool="mypy",
                            available=True,
                            skipped=True,
                            reason=(
                                "mypy is disabled by configuration (STATIC_MYPY_ENABLED=false) "
                                "because a cold type-check dominates review latency."
                            ),
                        )
                    )

        if js_files:
            runs.append(
                ToolRun(
                    tool="eslint",
                    available=False,
                    skipped=True,
                    reason=(
                        "ESLint requires the target repository's own configuration and installed "
                        "node_modules. Installing dependencies would execute untrusted lifecycle "
                        "scripts, so JS/TS linting is intentionally not run on the host."
                    ),
                )
            )
            runs.append(
                ToolRun(
                    tool="tsc",
                    available=False,
                    skipped=True,
                    reason=(
                        "Type checking requires the repository's tsconfig and dependency types, "
                        "which are not materialised in the review sandbox."
                    ),
                )
            )

        data: dict[str, Any] = {
            "tools": [run.to_dict() for run in runs],
            "python_files_scanned": len(python_files),
            "js_files_detected": len(js_files),
            "findings_count": len(findings),
        }
        warnings = [f"{r.tool}: {r.reason}" for r in runs if r.skipped and r.reason]
        return AnalyzerResult(name=self.name, findings=findings, data=data, warnings=warnings)

    # ----------------------------------------------------------------- ruff
    def _run_ruff(self, root: Path, files: list[str]) -> tuple[ToolRun, list[RawFinding]]:
        run = ToolRun(tool="ruff", available=True)
        findings: list[RawFinding] = []
        try:
            code, stdout, stderr = self.runner.run(
                "ruff",
                [
                    "check",
                    "--isolated",
                    "--output-format",
                    "json",
                    "--select",
                    "E,F,B,S,C4,SIM,ASYNC,PERF,RUF,TRY",
                    "--ignore",
                    "E501",
                    "--no-cache",
                    *files,
                ],
                root,
            )
            run.stderr = stderr
            payload = json.loads(stdout or "[]")
            for item in payload:
                rule = str(item.get("code") or "")
                prefix = _rule_prefix(rule)
                findings.append(
                    RawFinding(
                        file_path=_relative(item.get("filename", ""), root),
                        line_number=(item.get("location") or {}).get("row"),
                        severity=_RUFF_SEVERITY.get(prefix, "LOW"),
                        category=_RUFF_CATEGORY.get(prefix, "maintainability"),
                        title=short_title(
                            str(item.get("message") or ""), fallback=f"ruff {rule}"
                        ),
                        message=str(item.get("message") or ""),
                        tool="ruff",
                        rule_id=rule,
                        evidence=(
                            f"{_relative(item.get('filename', ''), root)}:"
                            f"{(item.get('location') or {}).get('row')}"
                        ),
                        suggested_fix=(item.get("fix") or {}).get("message")
                        if isinstance(item.get("fix"), dict)
                        else None,
                        confidence=0.95,
                    )
                )
            run.findings = len(findings)
            run.ok = code in (0, 1)
        except FileNotFoundError:
            run.available = False
            run.skipped = True
            run.reason = "ruff executable not found in the backend environment"
        except subprocess.TimeoutExpired:
            run.ok = False
            run.reason = f"ruff timed out after {self.timeout}s"
        except (json.JSONDecodeError, ValueError) as exc:
            run.ok = False
            run.reason = f"could not parse ruff output: {exc}"
        return run, findings

    # --------------------------------------------------------------- bandit
    def _run_bandit(self, root: Path, files: list[str]) -> tuple[ToolRun, list[RawFinding]]:
        run = ToolRun(tool="bandit", available=True)
        findings: list[RawFinding] = []
        try:
            code, stdout, stderr = self.runner.run(
                "bandit", ["-f", "json", "-q", "--exit-zero", *files], root
            )
            run.stderr = stderr
            payload = json.loads(stdout or "{}")
            for item in payload.get("results", []):
                severity = _BANDIT_SEVERITY.get(
                    str(item.get("issue_severity", "MEDIUM")).upper(), "MEDIUM"
                )
                confidence_map = {"HIGH": 0.95, "MEDIUM": 0.8, "LOW": 0.6}
                findings.append(
                    RawFinding(
                        file_path=_relative(item.get("filename", ""), root),
                        line_number=item.get("line_number"),
                        severity=severity,
                        category="security",
                        title=short_title(
                            str(item.get("issue_text") or ""),
                            fallback=humanise_rule_name(str(item.get("test_name") or "")),
                        ),
                        message=str(item.get("issue_text") or ""),
                        tool="bandit",
                        rule_id=str(item.get("test_id") or ""),
                        evidence=(item.get("code") or "")[:400] or None,
                        suggested_fix=item.get("more_info"),
                        confidence=confidence_map.get(
                            str(item.get("issue_confidence", "MEDIUM")).upper(), 0.8
                        ),
                    )
                )
            run.findings = len(findings)
            run.ok = code == 0
        except FileNotFoundError:
            run.available = False
            run.skipped = True
            run.reason = "bandit executable not found in the backend environment"
        except subprocess.TimeoutExpired:
            run.ok = False
            run.reason = f"bandit timed out after {self.timeout}s"
        except (json.JSONDecodeError, ValueError) as exc:
            run.ok = False
            run.reason = f"could not parse bandit output: {exc}"
        return run, findings

    # ----------------------------------------------------------------- mypy
    def _run_mypy(self, root: Path, files: list[str]) -> tuple[ToolRun, list[RawFinding]]:
        run = ToolRun(tool="mypy", available=True)
        findings: list[RawFinding] = []
        cache_dir = Path(settings.MYPY_CACHE_DIR).resolve()
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:  # pragma: no cover - defensive
            cache_dir = root / ".mypy_cache"
        try:
            code, stdout, stderr = self.runner.run(
                "mypy",
                [
                    "--cache-dir",
                    str(cache_dir),
                    "--no-site-packages",
                    "--ignore-missing-imports",
                    "--follow-imports=skip",
                    "--no-error-summary",
                    "--no-color-output",
                    "--hide-error-context",
                    "--show-error-codes",
                    *files,
                ],
                root,
                timeout=settings.STATIC_MYPY_TIMEOUT_SECONDS,
            )
            run.stderr = stderr
            for line in (stdout or "").splitlines():
                parsed = _parse_mypy_line(line)
                if parsed is None:
                    continue
                path, lineno, level, message, rule = parsed
                if level != "error":
                    continue
                findings.append(
                    RawFinding(
                        file_path=path,
                        line_number=lineno,
                        severity="MEDIUM",
                        category="correctness",
                        title=short_title(message, fallback="Type error"),
                        message=message,
                        tool="mypy",
                        rule_id=rule,
                        evidence=f"{path}:{lineno}",
                        confidence=0.8,
                    )
                )
            run.findings = len(findings)
            run.ok = code in (0, 1)
        except FileNotFoundError:
            run.available = False
            run.skipped = True
            run.reason = "mypy executable not found in the backend environment"
        except subprocess.TimeoutExpired:
            run.ok = False
            run.reason = f"mypy timed out after {settings.STATIC_MYPY_TIMEOUT_SECONDS}s"
        return run, findings


def _rule_prefix(rule: str) -> str:
    prefix = ""
    for char in rule:
        if char.isalpha():
            prefix += char
        else:
            break
    for candidate in (prefix, prefix[:4], prefix[:2], prefix[:1]):
        if candidate in _RUFF_SEVERITY:
            return candidate
    return prefix[:1]


def short_title(message: str, *, fallback: str = "Static analysis finding", limit: int = 90) -> str:
    """Turn a tool message into a short, human-readable title.

    Tool messages are full sentences (sometimes several). Titles that are
    hard-truncated mid-word read as broken UI, so we cut on the first sentence
    boundary and then on a word boundary.
    """
    text = " ".join(str(message or "").split())
    if not text:
        return fallback
    for terminator in (". ", "; ", " -- ", ": instead", ", instead"):
        index = text.find(terminator)
        if 15 < index < limit + 25:
            text = text[:index]
            break
    text = text.rstrip(" .;,")
    if len(text) > limit:
        cut = text[:limit].rsplit(" ", 1)[0]
        text = (cut or text[:limit]).rstrip(" .;,") + "..."
    return text[0].upper() + text[1:] if text else fallback


def humanise_rule_name(name: str) -> str:
    """``hardcoded_sql_expressions`` -> ``Hardcoded SQL expressions``."""
    words = str(name or "").replace("-", "_").split("_")
    if not words or not words[0]:
        return "Security issue"
    acronyms = {"sql", "url", "ssl", "tls", "xml", "api", "md5", "sha1", "os", "jwt", "http"}
    rendered = [words[0].upper() if words[0] in acronyms else words[0].capitalize()]
    rendered += [w.upper() if w in acronyms else w for w in words[1:]]
    return " ".join(rendered)


def _relative(path: str, root: Path) -> str:
    if not path:
        return ""
    cleaned = path.replace("\\", "/")
    if cleaned.startswith("./"):
        cleaned = cleaned[2:]
    try:
        return str(Path(path).resolve().relative_to(root.resolve())).replace("\\", "/")
    except (ValueError, OSError):
        return cleaned


def _parse_mypy_line(line: str) -> tuple[str, int | None, str, str, str | None] | None:
    # example: app/main.py:12: error: Incompatible return value  [return-value]
    parts = line.split(":", 3)
    if len(parts) < 4:
        return None
    path = parts[0].replace("\\", "/")
    try:
        lineno: int | None = int(parts[1])
    except ValueError:
        return None
    remainder = parts[3].strip()
    level = parts[2].strip()
    rule = None
    if remainder.endswith("]") and "[" in remainder:
        rule = remainder[remainder.rfind("[") + 1 : -1]
        remainder = remainder[: remainder.rfind("[")].strip()
    return path, lineno, level, remainder, rule
