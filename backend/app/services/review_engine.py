"""The review engine: deterministic analysis + retrieval + LLM reasoning.

Deliberately free of database and GitHub concerns so it can be unit tested end
to end with plain dictionaries. The worker (:mod:`app.workers.pipeline`) owns
persistence and publication; this module owns *thinking*.

Pipeline
--------
1. Parse the diff and run every enabled analyzer (failure-isolated).
2. Build a retrieval-augmented, budget-bounded evidence context.
3. Ask the model to reason over that evidence under a strict output schema.
4. Validate every proposed finding against the real diff, dedupe across tools,
   and merge in the deterministic findings worth publishing.
5. Compute an explainable risk score from the surviving evidence.

The model can never widen this pipeline: it cannot add files, cannot invent
lines, and cannot raise a finding that the validator rejects.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.analyzers.base import AnalysisRequest
from app.analyzers.diff import DiffAnalysis, build_diff_analysis, parse_unified_diff
from app.analyzers.registry import AnalysisBundle, run_analyzers
from app.core.config import settings
from app.core.enums import Category, Severity
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.integrations.llm.base import LLMProvider, LLMResult, LLMReviewOutput, LLMUsageRecord
from app.integrations.llm.prompts import (
    SUMMARY_SYSTEM_PROMPT,
    SYSTEM_PROMPT,
    build_review_prompt,
    build_summary_prompt,
)
from app.services.context_builder import ReviewContext, ReviewContextBuilder
from app.services.finding_validator import (
    FindingValidator,
    ValidatedFinding,
    ValidationOutcome,
)
from app.services.risk_scoring import RiskAssessment, score_review

logger = get_logger(__name__)

# Deterministic findings we always surface, even if the model says nothing.
ALWAYS_PUBLISH_SOURCES = {"security", "bandit"}
ALWAYS_PUBLISH_MIN_SEVERITY = Severity.MEDIUM


@dataclass(slots=True)
class ReviewSettings:
    min_severity: Severity = Severity.LOW
    max_findings: int = 25
    disabled_categories: list[str] = field(default_factory=list)
    min_confidence: float = 0.0
    enabled_analyzers: list[str] = field(default_factory=list)
    include_deterministic: bool = True

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> ReviewSettings:
        data = data or {}
        return cls(
            min_severity=Severity.coerce(data.get("min_severity", settings.DEFAULT_MIN_SEVERITY)),
            max_findings=int(data.get("max_findings", settings.MAX_FINDINGS_PUBLISHED)),
            disabled_categories=[str(c) for c in (data.get("disabled_categories") or [])],
            min_confidence=float(data.get("min_confidence", 0.0)),
            enabled_analyzers=[str(a) for a in (data.get("enabled_analyzers") or [])]
            or list(settings.ENABLED_ANALYZERS),
            include_deterministic=bool(data.get("include_deterministic", True)),
        )


@dataclass(slots=True)
class ReviewResult:
    diff: DiffAnalysis
    bundle: AnalysisBundle
    context: ReviewContext
    llm_output: LLMReviewOutput
    validation: ValidationOutcome
    risk: RiskAssessment
    usages: list[LLMUsageRecord] = field(default_factory=list)
    injection_attempts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duration_ms: int = 0
    llm_failed: bool = False

    @property
    def findings(self) -> list[ValidatedFinding]:
        return self.validation.accepted

    @property
    def summary(self) -> str:
        return self.llm_output.summary

    def analysis_payload(self) -> dict[str, Any]:
        return {
            "analyzers": self.bundle.summary(),
            "diff": self.diff.to_dict(),
            "validation": self.validation.stats,
            "risk": self.risk.to_dict(),
            "warnings": self.warnings,
            "injection_attempts": self.injection_attempts,
            "insufficient_evidence": self.llm_output.insufficient_evidence,
            "llm_notes": self.llm_output.notes,
            "llm_failed": self.llm_failed,
            "duration_ms": self.duration_ms,
        }


class ReviewEngine:
    def __init__(
        self,
        llm: LLMProvider,
        context_builder: ReviewContextBuilder,
        *,
        review_settings: ReviewSettings | None = None,
        progress: Callable[[str, int], Any] | None = None,
    ) -> None:
        self.llm = llm
        self.context_builder = context_builder
        self.settings = review_settings or ReviewSettings()
        self._progress = progress

    async def _report(self, stage: str, percent: int) -> None:
        logger.info("review.stage", stage=stage, progress=percent)
        if self._progress is None:
            return
        result = self._progress(stage, percent)
        if hasattr(result, "__await__"):
            await result

    async def run(
        self,
        *,
        repository_full_name: str,
        repository_id: str,
        pull_request: dict[str, Any],
        changed_files: Sequence[dict[str, Any]] | None = None,
        patch_text: str | None = None,
        file_contents: dict[str, str] | None = None,
        historical_findings: Sequence[dict[str, Any]] = (),
        incidents: Sequence[dict[str, Any]] = (),
    ) -> ReviewResult:
        started = time.perf_counter()
        warnings: list[str] = []
        file_contents = file_contents or {}

        await self._report("parsing_diff", 10)
        if changed_files:
            diff = build_diff_analysis(
                list(changed_files),
                max_files=settings.MAX_CHANGED_FILES,
                max_total_bytes=settings.MAX_DIFF_BYTES,
            )
        elif patch_text:
            diff = parse_unified_diff(patch_text)
        else:
            raise ValueError("either changed_files or patch_text must be supplied")
        if diff.truncated:
            warnings.append(
                f"{len(diff.dropped_files)} file(s) were excluded from analysis because the "
                "diff exceeded the configured budget."
            )

        await self._report("running_analyzers", 25)
        request = AnalysisRequest(
            repository_full_name=repository_full_name,
            head_sha=str(pull_request.get("head_sha") or ""),
            base_sha=str(pull_request.get("base_sha") or ""),
            diff=diff,
            file_contents=file_contents,
        )
        bundle = run_analyzers(request, enabled=self.settings.enabled_analyzers or None)
        warnings.extend(bundle.warnings)

        await self._report("building_context", 45)
        context = await self.context_builder.build(
            pull_request=pull_request,
            repository_id=repository_id,
            diff=diff,
            bundle=bundle,
            historical_findings=historical_findings,
            incidents=incidents,
            commit_sha=str(pull_request.get("head_sha") or ""),
        )

        await self._report("reasoning", 60)
        user_prompt, injection_attempts = build_review_prompt(context.to_dict())
        if injection_attempts:
            logger.warning(
                "prompt.injection_detected",
                repository=repository_full_name,
                count=len(injection_attempts),
            )

        usages: list[LLMUsageRecord] = []
        llm_failed = False
        try:
            llm_result: LLMResult = await self.llm.generate_review(SYSTEM_PROMPT, user_prompt)
            llm_output = llm_result.output
            usages.append(llm_result.usage)
            warnings.extend(llm_result.warnings)
        except LLMError as exc:
            # A model outage must not lose the deterministic analysis: we still
            # publish the analyzer findings and say plainly that reasoning failed.
            llm_failed = True
            logger.error("review.llm_failed", error=str(exc))
            warnings.append(f"LLM reasoning unavailable: {exc}")
            llm_output = LLMReviewOutput(
                summary=(
                    "Automated reasoning was unavailable for this review, so only "
                    "deterministic analyzer findings are reported below."
                ),
                findings=[],
                insufficient_evidence=False,
                notes=[f"llm_error: {exc}"],
            )
            usages.append(
                LLMUsageRecord(
                    provider=getattr(self.llm, "name", "unknown"),
                    model=getattr(self.llm, "model", "unknown"),
                    operation="review",
                    input_tokens=0,
                    output_tokens=0,
                    latency_ms=0,
                    success=False,
                    error_message=str(exc)[:500],
                )
            )

        await self._report("validating", 80)
        candidates: list[dict[str, Any]] = []
        for finding in llm_output.findings:
            payload = finding.model_dump()
            payload["source"] = "llm"
            candidates.append(payload)

        if self.settings.include_deterministic:
            candidates.extend(self._deterministic_candidates(bundle))

        if injection_attempts:
            candidates.append(self._injection_finding(diff, injection_attempts))

        validator = FindingValidator(
            diff,
            repository_full_name=repository_full_name,
            file_contents=file_contents,
            min_severity=self.settings.min_severity,
            max_findings=self.settings.max_findings,
            disabled_categories=self.settings.disabled_categories,
            min_confidence=self.settings.min_confidence,
        )
        validation = validator.validate(candidates)
        validation.accepted = self._attach_history(validation.accepted, historical_findings)

        await self._report("scoring", 90)
        risk = score_review(
            diff,
            [f.to_dict() for f in validation.accepted],
            test_data=context.test_context,
            dependency_data=bundle.data("dependency"),
            historical_hits=sum(1 for f in validation.accepted if f.historical_context),
        )

        summary = llm_output.summary
        if settings.LLM_SUMMARY_CALL and not llm_failed:
            summary = await self._final_summary(context, validation.accepted, usages, summary)
        if not summary.strip() or (
            llm_output.insufficient_evidence and validation.accepted and not llm_failed
        ):
            # The model saw no reason to speak, but deterministic analysis did.
            # Never publish "nothing found" next to a list of findings.
            summary = self._fallback_summary(diff, validation.accepted)
        llm_output.summary = summary

        return ReviewResult(
            diff=diff,
            bundle=bundle,
            context=context,
            llm_output=llm_output,
            validation=validation,
            risk=risk,
            usages=usages,
            injection_attempts=injection_attempts,
            warnings=warnings,
            duration_ms=int((time.perf_counter() - started) * 1000),
            llm_failed=llm_failed,
        )

    # ----------------------------------------------------------------- helpers
    async def _final_summary(
        self,
        context: ReviewContext,
        findings: list[ValidatedFinding],
        usages: list[LLMUsageRecord],
        current: str,
    ) -> str:
        """Summarise the *validated* findings.

        The first model call sees candidate findings; the published summary must
        describe what actually survived validation and deduplication, otherwise
        the narrative and the comment list disagree.
        """
        try:
            prompt = build_summary_prompt(
                context.to_dict(), [f.to_dict() for f in findings]
            )
            text, usage = await self.llm.generate_summary(SUMMARY_SYSTEM_PROMPT, prompt)
            usages.append(usage)
            return text or current
        except Exception as exc:
            logger.warning("review.summary_failed", error=str(exc))
            return current

    @staticmethod
    def _deterministic_candidates(bundle: AnalysisBundle) -> list[dict[str, Any]]:
        """Promote high-signal analyzer findings into publishable candidates."""
        severity_rank = {
            Severity.INFO: 0,
            Severity.LOW: 1,
            Severity.MEDIUM: 2,
            Severity.HIGH: 3,
            Severity.CRITICAL: 4,
        }
        threshold = severity_rank[ALWAYS_PUBLISH_MIN_SEVERITY]
        candidates: list[dict[str, Any]] = []
        for raw in bundle.findings:
            severity = Severity.coerce(raw.severity)
            tool = (raw.tool or "").lower()
            if tool not in ALWAYS_PUBLISH_SOURCES and severity_rank[severity] < threshold:
                continue
            candidates.append(
                {
                    "file": raw.file_path,
                    "line": raw.line_number,
                    "severity": str(severity),
                    "category": str(Category.coerce(raw.category)),
                    "title": raw.title,
                    "description": raw.message,
                    "evidence": raw.evidence or f"{raw.tool} {raw.rule_id or ''}".strip(),
                    "confidence": raw.confidence,
                    "certainty": "confirmed" if raw.confidence >= 0.85 else "likely",
                    "suggested_fix": raw.suggested_fix,
                    "source": raw.tool,
                    "rule_id": raw.rule_id,
                }
            )
        return candidates

    @staticmethod
    def _injection_finding(diff: DiffAnalysis, attempts: list[str]) -> dict[str, Any]:
        target = next((f for f in diff.files if f.added_line_numbers), None)
        line = min(target.added_line_numbers) if target else None
        return {
            "file": target.path if target else (diff.files[0].path if diff.files else ""),
            "line": line,
            "severity": str(Severity.HIGH),
            "category": str(Category.SECURITY),
            "title": "Prompt-injection attempt detected in repository content",
            "description": (
                "Content from this repository contained text shaped like instructions to the "
                "review model. It was neutralised and treated strictly as data, but a human "
                "should confirm why instruction-like text is present. Detected fragments: "
                + "; ".join(attempts[:5])
            ),
            "evidence": "; ".join(attempts[:5])[:2000],
            "confidence": 0.9,
            "certainty": "confirmed",
            "suggested_fix": "Remove the instruction-like text or confirm it is a test fixture.",
            "source": "security",
            "rule_id": "PROMPT-INJECTION",
        }

    @staticmethod
    def _attach_history(
        findings: list[ValidatedFinding], historical: Sequence[dict[str, Any]]
    ) -> list[ValidatedFinding]:
        if not historical:
            return findings
        by_fingerprint: dict[str, dict[str, Any]] = {}
        for item in historical:
            fingerprint = str(item.get("fingerprint") or "")
            if fingerprint:
                by_fingerprint[fingerprint] = item
        for finding in findings:
            match = by_fingerprint.get(finding.fingerprint)
            if not match:
                continue
            finding.historical_context = {
                "previously_reported": True,
                "occurrences": match.get("occurrences", 1),
                "last_seen": match.get("last_seen"),
                "last_status": match.get("status"),
                "note": (
                    "A similar issue was reported in this repository before. This is context, "
                    "not proof that the current change is wrong."
                ),
            }
        return findings

    @staticmethod
    def _fallback_summary(diff: DiffAnalysis, findings: list[ValidatedFinding]) -> str:
        if not findings:
            return (
                f"Reviewed {len(diff.files)} changed file(s) "
                f"(+{diff.total_additions}/-{diff.total_deletions}). No issues were found that "
                "the available evidence supports. This is not an approval."
            )
        return (
            f"Reviewed {len(diff.files)} changed file(s) "
            f"(+{diff.total_additions}/-{diff.total_deletions}) and raised {len(findings)} "
            f"finding(s). Highest severity: {findings[0].severity} in {findings[0].file_path}."
        )
