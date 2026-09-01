"""Finding validation, deduplication and suppression.

This is the guardrail between "the model said something" and "we published a
review comment". Nothing reaches GitHub without passing through here.

Checks performed
----------------
1. **File existence** -- the cited path must appear in the diff. A finding about
   a file the PR never touched is a hallucination and is dropped.
2. **Line anchoring** -- the cited line must be an added/changed line. If it is
   close to one (within ``LINE_SNAP_DISTANCE``) it is snapped and flagged;
   otherwise the finding is downgraded to a summary-only finding rather than an
   inline comment.
3. **Enum validity** -- severity/category/certainty are coerced to known values.
4. **Deduplication** -- identical issues reported by several tools (ruff `S608`
   and bandit `B608` both flag SQL injection, for example) collapse into one
   finding, keeping the highest-priority source.
5. **Suppression** -- repository settings can mute categories, set a minimum
   severity, and cap the number of published comments.

Every dropped finding is recorded with a reason so the UI can show *why* the
reviewer stayed quiet.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from app.analyzers.diff import DiffAnalysis
from app.core.enums import Category, Confidence, Severity
from app.core.logging import get_logger

logger = get_logger(__name__)

LINE_SNAP_DISTANCE = 8

# Tools that report the same class of problem. Higher wins when deduping.
SOURCE_PRIORITY: dict[str, int] = {
    "llm": 50,
    "security": 40,
    "bandit": 30,
    "ruff": 20,
    "mypy": 20,
    "dependency": 15,
    "test": 10,
    "ast": 10,
    "diff": 5,
}

# Cross-tool rule equivalences (normalised issue key -> matching rule ids).
EQUIVALENT_RULES: dict[str, set[str]] = {
    "sql-injection": {"S608", "B608", "SEC/SQL-INJECTION", "SEC/SQL-FSTRING"},
    "shell-injection": {
        "S602", "S603", "S604", "S605", "B602", "B603", "B604", "B605", "B607",
        "SEC/SHELL-INJECTION",
    },
    "weak-hash": {"S324", "B324", "B303", "SEC/WEAK-HASH"},
    "silent-except": {"S110", "S112", "B110", "B112", "E722", "SEC/SILENT-EXCEPT"},
    "assert-used": {"S101", "B101"},
    "hardcoded-secret": {
        "S105", "S106", "S107", "B105", "B106", "B107", "SEC/HARDCODED-SECRET",
        "SEC/PRIVATE-KEY-MATERIAL",
    },
    "insecure-random": {"S311", "B311", "SEC/INSECURE-RANDOM"},
    "yaml-load": {"S506", "B506", "SEC/UNSAFE-DESERIALIZATION"},
    "request-no-timeout": {"S113", "B113"},
    "verify-false": {"S501", "B501", "SEC/TLS-VERIFICATION-DISABLED"},
    "eval-used": {"S307", "B307", "SEC/DANGEROUS-EVAL"},
    "xml-parse": {"S314", "S320", "B314", "B320"},
    "path-traversal": {"S108", "B108", "SEC/PATH-TRAVERSAL"},
    "unclosed-resource": {"SIM115", "SEC/UNCLOSED-RESOURCE"},
    "debug-enabled": {"S201", "B201", "SEC/DEBUG-ENABLED"},
}
_RULE_TO_KEY: dict[str, str] = {
    rule: key for key, rules in EQUIVALENT_RULES.items() for rule in rules
}

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(slots=True)
class ValidatedFinding:
    file_path: str
    line_number: int | None
    severity: Severity
    category: Category
    title: str
    description: str
    evidence: str
    confidence: float
    confidence_label: Confidence
    source: str
    rule_id: str | None = None
    suggested_fix: str | None = None
    code_snippet: str | None = None
    related_symbols: list[str] = field(default_factory=list)
    start_line: int | None = None
    end_line: int | None = None
    inline_eligible: bool = True
    fingerprint: str = ""
    historical_context: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "line_number": self.line_number,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "severity": str(self.severity),
            "category": str(self.category),
            "title": self.title,
            "description": self.description,
            "evidence": self.evidence,
            "confidence": self.confidence,
            "confidence_label": str(self.confidence_label),
            "source": self.source,
            "rule_id": self.rule_id,
            "suggested_fix": self.suggested_fix,
            "code_snippet": self.code_snippet,
            "related_symbols": self.related_symbols,
            "inline_eligible": self.inline_eligible,
            "fingerprint": self.fingerprint,
            "historical_context": self.historical_context,
            "notes": self.notes,
        }


@dataclass(slots=True)
class RejectedFinding:
    reason: str
    detail: str
    payload: dict[str, Any]


@dataclass(slots=True)
class ValidationOutcome:
    accepted: list[ValidatedFinding] = field(default_factory=list)
    rejected: list[RejectedFinding] = field(default_factory=list)
    suppressed: list[RejectedFinding] = field(default_factory=list)

    @property
    def stats(self) -> dict[str, Any]:
        reasons: dict[str, int] = {}
        for item in self.rejected + self.suppressed:
            reasons[item.reason] = reasons.get(item.reason, 0) + 1
        return {
            "accepted": len(self.accepted),
            "rejected": len(self.rejected),
            "suppressed": len(self.suppressed),
            "reasons": reasons,
        }


def _norm_title(title: str) -> frozenset[str]:
    stop = {"the", "a", "an", "in", "of", "to", "is", "are", "on", "for", "with", "and", "this"}
    return frozenset(w for w in _WORD_RE.findall(title.lower()) if w not in stop and len(w) > 2)


def compute_fingerprint(
    repository: str, file_path: str, category: str, title: str, snippet: str | None
) -> str:
    """Stable identity for a finding across commits.

    Deliberately excludes the line number: the same defect that shifts down by
    three lines after a rebase must keep its fingerprint so we can recognise
    recurring issues and avoid re-notifying about them.
    """
    payload = "|".join(
        [
            repository.lower(),
            file_path.lower(),
            category.lower(),
            " ".join(sorted(_norm_title(title))),
            re.sub(r"\s+", " ", (snippet or "")).strip().lower()[:200],
        ]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class FindingValidator:
    """Validates model and analyzer findings against the actual diff."""

    def __init__(
        self,
        diff: DiffAnalysis,
        *,
        repository_full_name: str = "",
        file_contents: dict[str, str] | None = None,
        min_severity: Severity = Severity.LOW,
        max_findings: int = 25,
        disabled_categories: Iterable[str] = (),
        min_confidence: float = 0.0,
    ) -> None:
        self.diff = diff
        self.repository_full_name = repository_full_name
        self.file_contents = file_contents or {}
        self.min_severity = min_severity
        self.max_findings = max_findings
        self.disabled_categories = {str(c).lower() for c in disabled_categories}
        self.min_confidence = min_confidence
        self._severity_rank = {
            Severity.INFO: 0,
            Severity.LOW: 1,
            Severity.MEDIUM: 2,
            Severity.HIGH: 3,
            Severity.CRITICAL: 4,
        }

    # ------------------------------------------------------------------ public
    def validate(self, candidates: list[dict[str, Any]]) -> ValidationOutcome:
        outcome = ValidationOutcome()
        prepared: list[ValidatedFinding] = []

        for raw in candidates:
            try:
                result = self._validate_one(raw)
            except Exception as exc:  # pragma: no cover - defensive
                outcome.rejected.append(
                    RejectedFinding("exception", f"{type(exc).__name__}: {exc}", raw)
                )
                continue
            if isinstance(result, RejectedFinding):
                outcome.rejected.append(result)
            else:
                prepared.append(result)

        deduped, duplicates = self._dedupe(prepared)
        outcome.rejected.extend(duplicates)

        kept: list[ValidatedFinding] = []
        for finding in deduped:
            if str(finding.category).lower() in self.disabled_categories:
                outcome.suppressed.append(
                    RejectedFinding(
                        "category_disabled",
                        f"category '{finding.category}' is disabled for this repository",
                        finding.to_dict(),
                    )
                )
                continue
            if self._severity_rank[finding.severity] < self._severity_rank[self.min_severity]:
                outcome.suppressed.append(
                    RejectedFinding(
                        "below_min_severity",
                        f"{finding.severity} < {self.min_severity}",
                        finding.to_dict(),
                    )
                )
                continue
            if finding.confidence < self.min_confidence:
                outcome.suppressed.append(
                    RejectedFinding(
                        "below_min_confidence",
                        f"{finding.confidence:.2f} < {self.min_confidence:.2f}",
                        finding.to_dict(),
                    )
                )
                continue
            kept.append(finding)

        kept.sort(
            key=lambda f: (
                -self._severity_rank[f.severity],
                -f.confidence,
                f.file_path,
                f.line_number or 0,
            )
        )
        if len(kept) > self.max_findings:
            for extra in kept[self.max_findings :]:
                outcome.suppressed.append(
                    RejectedFinding(
                        "max_findings_exceeded",
                        f"only the top {self.max_findings} findings are published",
                        extra.to_dict(),
                    )
                )
            kept = kept[: self.max_findings]

        outcome.accepted = kept
        logger.info("findings.validated", **outcome.stats)
        return outcome

    # ----------------------------------------------------------------- private
    def _validate_one(self, raw: dict[str, Any]) -> ValidatedFinding | RejectedFinding:
        file_path = str(raw.get("file") or raw.get("file_path") or "").strip()
        file_path = file_path.replace("\\", "/").lstrip("./")
        if not file_path:
            return RejectedFinding("missing_file", "no file path supplied", raw)

        file_diff = self.diff.by_path(file_path)
        if file_diff is None:
            # Tolerate a basename match before giving up -- models sometimes drop
            # a leading directory. Anything less certain than that is rejected.
            matches = [f for f in self.diff.files if f.path.endswith("/" + file_path)]
            if len(matches) == 1:
                file_diff = matches[0]
                file_path = file_diff.path
            else:
                return RejectedFinding(
                    "file_not_in_diff",
                    f"'{file_path}' is not part of this pull request",
                    raw,
                )

        title = str(raw.get("title") or "").strip()
        description = str(raw.get("description") or raw.get("message") or "").strip()
        if len(title) < 4:
            return RejectedFinding("missing_title", "title too short", raw)
        if len(description) < 10:
            return RejectedFinding("missing_description", "description too short", raw)

        severity = Severity.coerce(raw.get("severity"))
        category = Category.coerce(raw.get("category"))
        certainty = self._coerce_certainty(raw.get("certainty"))
        try:
            confidence = float(raw.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = min(1.0, max(0.0, confidence))

        line = raw.get("line", raw.get("line_number"))
        line = int(line) if isinstance(line, int | float) and int(line) > 0 else None
        notes: list[str] = []
        inline_eligible = True

        added = file_diff.added_line_numbers
        if line is None:
            inline_eligible = False
            notes.append("no line number supplied; reported as a summary comment")
        elif added and line not in added:
            nearest = min(added, key=lambda candidate: abs(candidate - line))
            if abs(nearest - line) <= LINE_SNAP_DISTANCE:
                notes.append(f"line {line} snapped to nearest changed line {nearest}")
                line = nearest
            else:
                inline_eligible = False
                notes.append(
                    f"line {line} is not part of the diff (nearest changed line is {nearest}); "
                    "reported as a summary comment"
                )
        elif not added:
            inline_eligible = False
            notes.append("file has no added lines; reported as a summary comment")

        snippet = raw.get("code_snippet") or self._snippet(file_path, line)
        rule_id = raw.get("rule_id") or raw.get("rule")
        source = str(raw.get("source") or raw.get("tool") or "llm")

        finding = ValidatedFinding(
            file_path=file_path,
            line_number=line,
            severity=severity,
            category=category,
            title=title[:400],
            description=description[:8000],
            evidence=str(raw.get("evidence") or "")[:6000],
            confidence=confidence,
            confidence_label=certainty,
            source=source,
            rule_id=str(rule_id)[:120] if rule_id else None,
            suggested_fix=(str(raw["suggested_fix"])[:6000] if raw.get("suggested_fix") else None),
            code_snippet=snippet,
            related_symbols=[str(s)[:200] for s in (raw.get("related_symbols") or [])][:20],
            start_line=raw.get("start_line") if isinstance(raw.get("start_line"), int) else None,
            end_line=raw.get("end_line") if isinstance(raw.get("end_line"), int) else None,
            inline_eligible=inline_eligible,
            historical_context=raw.get("historical_context"),
            notes=notes,
        )
        finding.fingerprint = compute_fingerprint(
            self.repository_full_name, file_path, str(category), title, snippet
        )
        return finding

    @staticmethod
    def _coerce_certainty(value: object) -> Confidence:
        if isinstance(value, Confidence):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            for member in Confidence:
                if member.value == lowered:
                    return member
        return Confidence.LIKELY

    def _snippet(self, file_path: str, line: int | None) -> str | None:
        if line is None:
            return None
        content = self.file_contents.get(file_path)
        if not content:
            file_diff = self.diff.by_path(file_path)
            if file_diff is None:
                return None
            for hunk in file_diff.hunks:
                for diff_line in hunk.lines:
                    if diff_line.new_lineno == line:
                        return diff_line.content.rstrip()[:400]
            return None
        lines = content.splitlines()
        if 1 <= line <= len(lines):
            return lines[line - 1].strip()[:400]
        return None

    def _dedupe(
        self, findings: list[ValidatedFinding]
    ) -> tuple[list[ValidatedFinding], list[RejectedFinding]]:
        """Collapse the same issue reported by several tools into one finding."""
        kept: list[ValidatedFinding] = []
        duplicates: list[RejectedFinding] = []

        def priority(item: ValidatedFinding) -> int:
            return SOURCE_PRIORITY.get(item.source.lower(), 25)

        ordered = sorted(findings, key=lambda f: (-priority(f), -f.confidence))
        for candidate in ordered:
            match = self._find_duplicate(candidate, kept)
            if match is None:
                kept.append(candidate)
                continue
            match.notes.append(
                f"also reported by {candidate.source}"
                + (f" ({candidate.rule_id})" if candidate.rule_id else "")
            )
            if candidate.confidence > match.confidence:
                match.confidence = candidate.confidence
            if self._severity_rank[candidate.severity] > self._severity_rank[match.severity]:
                match.severity = candidate.severity
            if not match.suggested_fix and candidate.suggested_fix:
                match.suggested_fix = candidate.suggested_fix
            duplicates.append(
                RejectedFinding(
                    "duplicate",
                    f"duplicate of '{match.title}' from {match.source}",
                    candidate.to_dict(),
                )
            )
        return kept, duplicates

    @staticmethod
    def _equivalence_key(finding: ValidatedFinding) -> str | None:
        if not finding.rule_id:
            return None
        rule = finding.rule_id.strip().upper()
        if rule.startswith("SEC/"):
            return _RULE_TO_KEY.get(rule)
        rule = rule.split(":")[0].split(".")[0]
        return _RULE_TO_KEY.get(rule)

    def _find_duplicate(
        self, candidate: ValidatedFinding, kept: list[ValidatedFinding]
    ) -> ValidatedFinding | None:
        candidate_key = self._equivalence_key(candidate)
        candidate_words = _norm_title(candidate.title)
        for existing in kept:
            if existing.file_path != candidate.file_path:
                continue
            same_line = (
                existing.line_number is not None
                and candidate.line_number is not None
                and abs(existing.line_number - candidate.line_number) <= 2
            )
            if not same_line:
                continue
            if candidate_key and candidate_key == self._equivalence_key(existing):
                return existing
            if existing.category == candidate.category:
                existing_words = _norm_title(existing.title)
                if not existing_words or not candidate_words:
                    continue
                overlap = len(existing_words & candidate_words)
                smaller = min(len(existing_words), len(candidate_words))
                if smaller and overlap / smaller >= 0.6:
                    return existing
        return None
