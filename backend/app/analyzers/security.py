"""Deterministic security analysis.

The LLM is *never* the sole security detector. This analyzer applies
pattern- and AST-based rules to the added lines of a diff so that every security
finding it reports is backed by a concrete code reference.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.analyzers.base import (
    AnalysisRequest,
    AnalyzerResult,
    Language,
    RawFinding,
    RepositoryAnalyzer,
)

# Lines carrying these markers are treated as intentional/false-positive-suppressed.
_SUPPRESSIONS = ("# nosec", "# noqa: s", "eslint-disable", "pragma: allowlist secret")


@dataclass(frozen=True, slots=True)
class SecurityRule:
    rule_id: str
    title: str
    severity: str
    pattern: re.Pattern[str]
    message: str
    suggested_fix: str
    languages: tuple[Language, ...] = ()
    negative: tuple[re.Pattern[str], ...] = ()
    confidence: float = 0.85

    def matches(self, line: str) -> bool:
        if not self.pattern.search(line):
            return False
        return not any(neg.search(line) for neg in self.negative)


PLACEHOLDER = re.compile(
    r"(?:example|placeholder|dummy|changeme|your[_-]?|xxx+|\.\.\.|<[^>]+>|\$\{|process\.env|os\.environ|getenv)",
    re.I,
)

SECURITY_RULES: tuple[SecurityRule, ...] = (
    SecurityRule(
        rule_id="sec/hardcoded-secret",
        title="Possible hardcoded secret",
        severity="HIGH",
        pattern=re.compile(
            r"""(?ix)
            (?<![A-Za-z0-9])
            (?:api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret
              |password|passwd|private[_-]?key|aws_secret_access_key|gateway[_-]?key
              |[a-z0-9_]*secret|[a-z0-9_]*token)
            (?![A-Za-z0-9])
            \s*[:=]\s*
            ['"][^'"\s]{8,}['"]
            """
        ),
        message=(
            "A credential-looking literal is assigned directly in source code. "
            "Committed secrets are exposed to everyone with repository access and "
            "remain in git history after removal."
        ),
        suggested_fix="Load the value from an environment variable or a secret manager.",
        negative=(PLACEHOLDER,),
        confidence=0.75,
    ),
    SecurityRule(
        rule_id="sec/private-key-material",
        title="Private key material committed",
        severity="CRITICAL",
        pattern=re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
        message="A PEM private key block was added to the repository.",
        suggested_fix="Remove the key, rotate it immediately, and load it from a secret store.",
        confidence=0.98,
    ),
    SecurityRule(
        rule_id="sec/shell-injection",
        title="Unsafe shell execution",
        severity="HIGH",
        pattern=re.compile(
            r"(?:subprocess\.(?:run|call|check_output|Popen|check_call)\([^)]*shell\s*=\s*True"
            r"|os\.system\(|os\.popen\(|child_process\.exec\(|exec\(\s*`)"
        ),
        message=(
            "A shell is spawned to execute a command. If any part of the command string is "
            "attacker-controlled this allows arbitrary command execution."
        ),
        suggested_fix=(
            "Pass an argument list instead of a string and avoid `shell=True` "
            "(or `execFile`/`spawn` in Node)."
        ),
        confidence=0.9,
    ),
    SecurityRule(
        rule_id="sec/sql-injection",
        title="SQL statement built with string interpolation",
        severity="HIGH",
        pattern=re.compile(
            r"""(?ix)
            (?:execute|executemany|raw|query|cursor\.execute|text)\s*\(\s*
            (?:f['"]|['"][^'"]*['"]\s*(?:%|\+|\.format)|`[^`]*\$\{)
            [^)]*\b(?:select|insert|update|delete|drop|union|where|from)\b
            """
        ),
        message=(
            "A SQL statement is assembled through string interpolation/concatenation. "
            "Values embedded this way are not escaped and enable SQL injection."
        ),
        suggested_fix="Use parameterised queries (bind parameters) instead of interpolation.",
        confidence=0.88,
    ),
    SecurityRule(
        rule_id="sec/sql-fstring",
        title="f-string used to build a SQL query",
        severity="HIGH",
        pattern=re.compile(
            r"""(?ix)f['"][^'"]*\b(?:select\s|insert\s+into|update\s|delete\s+from)\b[^'"]*\{"""
        ),
        message="An f-string containing a SQL statement interpolates a runtime value.",
        suggested_fix="Use bind parameters, e.g. `cursor.execute(sql, (value,))`.",
        languages=(Language.PYTHON,),
        confidence=0.9,
    ),
    SecurityRule(
        rule_id="sec/dangerous-eval",
        title="Dangerous dynamic evaluation",
        severity="HIGH",
        pattern=re.compile(
            r"(?<![\w.])(?:eval|exec)\s*\(|new\s+Function\s*\(|setTimeout\s*\(\s*['\"]"
        ),
        message=(
            "Dynamic code evaluation executes its argument as code. With any untrusted input "
            "this is remote code execution."
        ),
        suggested_fix="Replace with an explicit parser, a lookup table, or `json.loads`.",
        negative=(re.compile(r"\b(?:ast\.literal_eval|# *safe-eval)"),),
        confidence=0.85,
    ),
    SecurityRule(
        rule_id="sec/unsafe-deserialization",
        title="Unsafe deserialization",
        severity="HIGH",
        pattern=re.compile(
            r"\b(?:pickle\.loads?|cPickle\.loads?|yaml\.load\s*\((?![^)]*SafeLoader)"
            r"|marshal\.loads|jsonpickle\.decode|dill\.loads)\b"
        ),
        message=(
            "Deserializing untrusted data with this API can execute arbitrary code during load."
        ),
        suggested_fix="Use `json`, or `yaml.safe_load`, and validate the payload schema.",
        confidence=0.9,
    ),
    SecurityRule(
        rule_id="sec/path-traversal",
        title="Path built from unvalidated input",
        severity="MEDIUM",
        pattern=re.compile(
            r"(?:open|os\.path\.join|Path|readFile|createReadStream|send_file|sendFile)\s*\("
            r"[^)]*(?:request\.|req\.(?:params|query|body)|params\[|argv|user_input|filename)"
        ),
        message=(
            "A filesystem path is constructed from request/user input. Without normalisation "
            "an attacker can traverse outside the intended directory using `../`."
        ),
        suggested_fix=(
            "Resolve the path and assert it stays within an allow-listed base directory."
        ),
        confidence=0.7,
    ),
    SecurityRule(
        rule_id="sec/weak-hash",
        title="Weak cryptographic hash",
        severity="MEDIUM",
        pattern=re.compile(
            r"\b(?:hashlib\.(?:md5|sha1)\s*\(|createHash\(\s*['\"](?:md5|sha1)['\"])"
        ),
        message="MD5/SHA-1 are broken for security purposes (collision resistance).",
        suggested_fix=(
            "Use SHA-256+ for integrity, and bcrypt/argon2/scrypt for passwords. "
            "If the hash is non-security (e.g. cache key), pass `usedforsecurity=False`."
        ),
        negative=(re.compile(r"usedforsecurity\s*=\s*False"),),
        confidence=0.8,
    ),
    SecurityRule(
        rule_id="sec/insecure-random",
        title="Insecure randomness for a security value",
        severity="MEDIUM",
        pattern=re.compile(
            r"(?i)\b(?:random\.(?:random|randint|choice)|Math\.random)\s*\(\s*\)?"
            r"[^\n]*\b(?:token|secret|password|salt|nonce|otp|session)\b"
        ),
        message="A non-cryptographic PRNG is used to generate a security-sensitive value.",
        suggested_fix="Use `secrets` (Python) or `crypto.randomBytes` (Node).",
        confidence=0.85,
    ),
    SecurityRule(
        rule_id="sec/tls-verification-disabled",
        title="TLS certificate verification disabled",
        severity="HIGH",
        pattern=re.compile(
            r"verify\s*=\s*False|rejectUnauthorized\s*:\s*false"
            r"|NODE_TLS_REJECT_UNAUTHORIZED\s*=\s*['\"]?0|ssl\._create_unverified_context"
        ),
        message="Disabling certificate verification makes the connection trivially MITM-able.",
        suggested_fix="Keep verification enabled and install the proper CA bundle instead.",
        confidence=0.95,
    ),
    SecurityRule(
        rule_id="sec/cors-wildcard-credentials",
        title="Permissive CORS configuration",
        severity="MEDIUM",
        pattern=re.compile(
            r"allow_origins\s*=\s*\[?\s*['\"]\*['\"]|Access-Control-Allow-Origin['\"]?\s*[:,]\s*['\"]\*"
        ),
        message=(
            "A wildcard CORS origin allows any site to call this API from a browser; "
            "combined with credentials it enables cross-site data theft."
        ),
        suggested_fix="Restrict `allow_origins` to an explicit allow-list.",
        confidence=0.75,
    ),
    SecurityRule(
        rule_id="sec/debug-enabled",
        title="Debug mode enabled",
        severity="MEDIUM",
        pattern=re.compile(r"\bdebug\s*=\s*True\b|app\.run\([^)]*debug\s*=\s*True"),
        message="Debug mode exposes stack traces and, in some frameworks, a remote console.",
        suggested_fix="Drive the flag from configuration and keep it off in production.",
        confidence=0.7,
    ),
    SecurityRule(
        rule_id="sec/jwt-unverified",
        title="JWT decoded without signature verification",
        severity="HIGH",
        pattern=re.compile(
            r"jwt\.decode\([^)]*verify(?:_signature)?\s*[:=]\s*(?:False|false)"
            r"|jwt\.decode\([^)]*algorithms\s*=\s*\[\s*['\"]none['\"]"
            r"|jwt\.decode\([^)]*options\s*=\s*\{[^}]*['\"]verify_signature['\"]\s*:\s*False"
        ),
        message="A JWT is decoded without verifying its signature, so any token is accepted.",
        suggested_fix="Verify the signature with an explicit allow-listed algorithm.",
        confidence=0.95,
    ),
    SecurityRule(
        rule_id="sec/xss-innerhtml",
        title="Raw HTML injection sink",
        severity="MEDIUM",
        pattern=re.compile(
            r"dangerouslySetInnerHTML|\.innerHTML\s*=|document\.write\(|v-html\s*="
        ),
        message="Assigning unsanitised values to an HTML sink enables cross-site scripting.",
        suggested_fix="Render as text, or sanitise with a vetted library such as DOMPurify.",
        confidence=0.7,
    ),
)


# ------------------------------------------------------- AST-based rules (py)
def _python_missing_authorization(tree: ast.AST, path: str) -> list[RawFinding]:
    """Flag HTTP route handlers that mutate state without an auth dependency."""
    findings: list[RawFinding] = []
    auth_markers = ("current_user", "require_", "auth", "permission", "principal", "token")
    mutating = {"post", "put", "patch", "delete"}

    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        decorator_texts = []
        for dec in node.decorator_list:
            try:
                decorator_texts.append(ast.unparse(dec))
            except Exception:  # pragma: no cover
                continue
        route_decorators = [
            d
            for d in decorator_texts
            if re.search(r"\.(get|post|put|patch|delete)\(", d)
        ]
        if not route_decorators:
            continue
        if not any(re.search(rf"\.{verb}\(", d) for d in route_decorators for verb in mutating):
            continue
        haystack = " ".join(decorator_texts).lower()
        try:
            signature = ast.unparse(node.args).lower()
        except Exception:  # pragma: no cover
            signature = ""
        body_head = " ".join(
            ast.unparse(stmt) for stmt in node.body[:4] if isinstance(stmt, ast.stmt)
        ).lower()
        if any(marker in haystack + signature + body_head for marker in auth_markers):
            continue
        findings.append(
            RawFinding(
                file_path=path,
                line_number=node.lineno,
                severity="HIGH",
                category="security",
                title=f"State-changing endpoint '{node.name}' has no visible authorization check",
                message=(
                    f"`{node.name}` handles a mutating HTTP method but neither its decorators, "
                    "its signature (dependencies) nor the start of its body reference an "
                    "authentication/authorization primitive."
                ),
                tool="security",
                rule_id="sec/missing-authorization",
                evidence=f"{path}:{node.lineno} {route_decorators[0]}",
                suggested_fix=(
                    "Add the project's auth dependency (e.g. "
                    "`user = Depends(get_current_user)`) and verify resource ownership."
                ),
                confidence=0.6,
            )
        )
    return findings


def _python_broad_except(tree: ast.AST, path: str) -> list[RawFinding]:
    findings: list[RawFinding] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ExceptHandler):
            continue
        is_bare = node.type is None
        is_broad = isinstance(node.type, ast.Name) and node.type.id in {"Exception", "BaseException"}
        if not (is_bare or is_broad):
            continue
        swallows = all(isinstance(stmt, ast.Pass | ast.Continue) for stmt in node.body)
        if not swallows:
            continue
        findings.append(
            RawFinding(
                file_path=path,
                line_number=node.lineno,
                severity="MEDIUM",
                category="reliability",
                title="Exception is silently swallowed",
                message=(
                    "A broad `except` block discards the exception without logging or "
                    "re-raising, which hides real failures at runtime."
                ),
                tool="security",
                rule_id="sec/silent-except",
                evidence=f"{path}:{node.lineno}",
                suggested_fix="Log the exception (with context) or narrow and re-raise it.",
                confidence=0.9,
            )
        )
    return findings


def _python_unclosed_resource(tree: ast.AST, path: str) -> list[RawFinding]:
    """Detect `open(...)` results that are never used as a context manager."""
    findings: list[RawFinding] = []
    managed_lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.With | ast.AsyncWith):
            for item in node.items:
                for sub in ast.walk(item.context_expr):
                    managed_lines.add(getattr(sub, "lineno", -1))

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
        if func_name not in {"open", "urlopen", "socket", "connect"}:
            continue
        if node.lineno in managed_lines:
            continue
        parent_is_assign = False
        for candidate in ast.walk(tree):
            if isinstance(candidate, ast.Assign) and candidate.value is node:
                parent_is_assign = True
                break
        if not parent_is_assign:
            continue
        findings.append(
            RawFinding(
                file_path=path,
                line_number=node.lineno,
                severity="MEDIUM",
                category="reliability",
                title=f"Resource from `{func_name}()` is not closed deterministically",
                message=(
                    f"`{func_name}()` returns a resource that is assigned but never managed by a "
                    "`with` block. On an exception the handle leaks until GC."
                ),
                tool="security",
                rule_id="sec/unclosed-resource",
                evidence=f"{path}:{node.lineno}",
                suggested_fix=f"Use `with {func_name}(...) as handle:` or `contextlib.closing`.",
                confidence=0.75,
            )
        )
    return findings


_PY_AST_RULES: tuple[Callable[[ast.AST, str], list[RawFinding]], ...] = (
    _python_missing_authorization,
    _python_broad_except,
    _python_unclosed_resource,
)


class SecurityAnalyzer(RepositoryAnalyzer):
    """Pattern + AST security scanning restricted to changed lines."""

    name = "security"

    def analyze(self, request: AnalysisRequest) -> AnalyzerResult:
        findings: list[RawFinding] = []
        matched_rules: dict[str, int] = {}

        for file_diff in request.diff.files:
            if file_diff.is_binary or file_diff.change_type == "removed":
                continue

            for hunk in file_diff.hunks:
                for line in hunk.added_lines:
                    content = line.content
                    lowered = content.lower()
                    if any(marker in lowered for marker in _SUPPRESSIONS):
                        continue
                    for rule in SECURITY_RULES:
                        if rule.languages and file_diff.language not in rule.languages:
                            continue
                        if not rule.matches(content):
                            continue
                        matched_rules[rule.rule_id] = matched_rules.get(rule.rule_id, 0) + 1
                        findings.append(
                            RawFinding(
                                file_path=file_diff.path,
                                line_number=line.new_lineno,
                                severity=rule.severity,
                                category="security",
                                title=rule.title,
                                message=rule.message,
                                tool="security",
                                rule_id=rule.rule_id,
                                evidence=(
                                    f"{file_diff.path}:{line.new_lineno} "
                                    f"| {content.strip()[:200]}"
                                ),
                                suggested_fix=rule.suggested_fix,
                                confidence=(
                                    rule.confidence * (0.6 if file_diff.is_test else 1.0)
                                ),
                            )
                        )

            source = request.content_for(file_diff.path)
            if source and file_diff.language == Language.PYTHON and not file_diff.is_test:
                try:
                    tree = ast.parse(source, filename=file_diff.path)
                except SyntaxError:
                    continue
                changed = file_diff.added_line_numbers
                for rule_fn in _PY_AST_RULES:
                    for finding in rule_fn(tree, file_diff.path):
                        # Only report issues that the PR actually introduced/touched.
                        if finding.line_number is None:
                            continue
                        if not any(
                            abs(finding.line_number - line) <= 30 for line in changed
                        ):
                            continue
                        matched_rules[finding.rule_id or "?"] = (
                            matched_rules.get(finding.rule_id or "?", 0) + 1
                        )
                        findings.append(finding)

        deduped = _dedupe(findings)
        data: dict[str, Any] = {
            "rules_evaluated": len(SECURITY_RULES) + len(_PY_AST_RULES),
            "matched_rules": matched_rules,
            "findings_count": len(deduped),
        }
        return AnalyzerResult(name=self.name, findings=deduped, data=data)


def _dedupe(findings: list[RawFinding]) -> list[RawFinding]:
    seen: set[tuple[str, int | None, str | None]] = set()
    unique: list[RawFinding] = []
    for finding in findings:
        key = (finding.file_path, finding.line_number, finding.rule_id)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)
    return unique
