"""Formats and publishes reviews back to GitHub.

Design notes
------------
* **Never auto-block.** The default review event is ``COMMENT``. An automated
  reviewer that can request changes will eventually block a release on a false
  positive, so blocking is opt-in per repository.
* **Every comment is evidence-backed.** Each inline comment states the source
  (which analyzer or the model), the certainty, and the evidence relied on.
* **Graceful degradation.** If GitHub rejects the inline anchors -- which it
  does whenever a line is not part of the diff it knows about -- we fall back to
  a summary-only review that still contains every finding, rather than losing
  the review entirely.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings
from app.core.enums import Confidence, Severity
from app.core.logging import get_logger
from app.integrations.github.base import GitHubProvider, InlineComment, PublishedReview
from app.services.finding_validator import ValidatedFinding
from app.services.risk_scoring import RiskAssessment

logger = get_logger(__name__)

SEVERITY_EMOJI: dict[str, str] = {
    "CRITICAL": "🔴",
    "HIGH": "🟠",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "INFO": "⚪",
}

CERTAINTY_NOTE: dict[str, str] = {
    Confidence.CONFIRMED.value: "Confirmed by the cited evidence.",
    Confidence.LIKELY.value: "Likely, based on the cited evidence.",
    Confidence.UNCERTAIN.value: "Uncertain -- please verify before acting.",
}

FOOTER = (
    "\n\n---\n<sub>Automated review. Findings are evidence-backed but not infallible: "
    "verify before acting. This review does not replace human judgement.</sub>"
)


@dataclass(slots=True)
class PublishPlan:
    summary_body: str
    inline_comments: list[InlineComment] = field(default_factory=list)
    summary_only_findings: list[ValidatedFinding] = field(default_factory=list)
    event: str = "COMMENT"


def _severity_badge(severity: str) -> str:
    return f"{SEVERITY_EMOJI.get(severity.upper(), '⚪')} **{severity.upper()}**"


def render_inline_comment(finding: ValidatedFinding) -> str:
    parts = [f"{_severity_badge(str(finding.severity))} · `{finding.category}` — **{finding.title}**"]
    parts.append("")
    parts.append(finding.description.strip())

    if finding.evidence:
        parts.append("")
        parts.append("<details><summary>Evidence</summary>\n")
        parts.append(f"```\n{finding.evidence.strip()[:1500]}\n```")
        parts.append("</details>")

    if finding.suggested_fix:
        parts.append("")
        parts.append("**Suggested fix**")
        parts.append(finding.suggested_fix.strip()[:1500])

    if finding.historical_context:
        parts.append("")
        parts.append(
            "> ℹ️ A similar issue was reported in this repository before "
            f"({finding.historical_context.get('occurrences', 1)} time(s)). "
            "This is context, not proof."
        )

    meta = [
        f"source: `{finding.source}`",
        f"confidence: {finding.confidence:.0%} ({finding.confidence_label})",
    ]
    if finding.rule_id:
        meta.append(f"rule: `{finding.rule_id}`")
    parts.append("")
    parts.append(
        f"<sub>{' · '.join(meta)} — {CERTAINTY_NOTE.get(str(finding.confidence_label), '')}</sub>"
    )
    return "\n".join(parts)


def render_summary(
    *,
    summary: str,
    findings: Sequence[ValidatedFinding],
    risk: RiskAssessment,
    summary_only: Sequence[ValidatedFinding] = (),
    suppressed_count: int = 0,
    context_stats: dict[str, Any] | None = None,
    analyzers: dict[str, Any] | None = None,
    insufficient_evidence: bool = False,
    llm_failed: bool = False,
    warnings: Sequence[str] = (),
) -> str:
    lines: list[str] = ["## 🤖 Automated Pull Request Review", ""]

    counts: dict[str, int] = {}
    for finding in findings:
        key = str(finding.severity).upper()
        counts[key] = counts.get(key, 0) + 1
    if counts:
        badge = " · ".join(
            f"{SEVERITY_EMOJI.get(sev, '⚪')} {count} {sev.title()}"
            for sev, count in sorted(
                counts.items(), key=lambda kv: -list(SEVERITY_EMOJI).index(kv[0])
            )
        )
        lines.append(badge)
        lines.append("")

    lines.append(summary.strip() or "No summary was produced for this review.")
    lines.append("")

    lines.append(
        f"**Risk score: {risk.score:.0f}/100 ({str(risk.band).upper()})** "
        "— computed from the factors below, not chosen by the model."
    )
    lines.append("")
    if risk.factors:
        lines.append("<details><summary>How this score was calculated</summary>\n")
        lines.append("| Factor | Points | Why |")
        lines.append("| --- | ---: | --- |")
        for factor in risk.factors:
            detail = factor.detail.replace("|", "\\|")[:220]
            lines.append(f"| {factor.label} | {factor.points:.0f} | {detail} |")
        lines.append("\n</details>")
        lines.append("")

    if findings:
        lines.append("### Findings")
        lines.append("")
        lines.append("| Severity | Category | Location | Finding | Confidence |")
        lines.append("| --- | --- | --- | --- | --- |")
        for finding in findings:
            location = finding.file_path + (
                f"#L{finding.line_number}" if finding.line_number else ""
            )
            title = finding.title.replace("|", "\\|")[:120]
            lines.append(
                f"| {_severity_badge(str(finding.severity))} | `{finding.category}` | "
                f"`{location}` | {title} | {finding.confidence:.0%} "
                f"({finding.confidence_label}) |"
            )
        lines.append("")
    elif insufficient_evidence:
        lines.append(
            "### No findings\n\n"
            "The analysis did not surface evidence supporting a concrete issue. "
            "**This is not an approval** — it means the automated checks found nothing "
            "they could substantiate. Areas outside the analyzers' coverage were not assessed."
        )
        lines.append("")

    if summary_only:
        lines.append("<details><summary>")
        lines.append(
            f"{len(summary_only)} finding(s) could not be anchored to a diff line</summary>\n"
        )
        for finding in summary_only:
            lines.append(
                f"- {_severity_badge(str(finding.severity))} `{finding.file_path}"
                f"{':' + str(finding.line_number) if finding.line_number else ''}` — "
                f"{finding.title}"
            )
            if finding.notes:
                lines.append(f"  <br/><sub>{'; '.join(finding.notes)}</sub>")
        lines.append("\n</details>")
        lines.append("")

    if suppressed_count:
        lines.append(
            f"<sub>{suppressed_count} additional finding(s) were suppressed by this "
            "repository's review settings (severity threshold, disabled categories or "
            "the per-review cap).</sub>"
        )
        lines.append("")

    transparency: list[str] = []
    if analyzers:
        rendered = []
        for name, info in analyzers.items():
            if info.get("skipped"):
                rendered.append(f"- `{name}`: skipped — {info.get('skip_reason')}")
            elif not info.get("ok"):
                rendered.append(f"- `{name}`: failed — {info.get('error')}")
            else:
                rendered.append(
                    f"- `{name}`: {info.get('findings', 0)} finding(s) in "
                    f"{info.get('duration_ms', 0)} ms"
                )
        transparency.append("**Analyzers**\n" + "\n".join(rendered))
    if context_stats:
        retrieval = context_stats.get("retrieval") or {}
        transparency.append(
            "**Context**\n"
            f"- {context_stats.get('diff_files_included', 0)}/"
            f"{context_stats.get('diff_files_total', 0)} changed files included\n"
            f"- {context_stats.get('retrieved_chunks', 0)} repository chunk(s) retrieved "
            f"from {retrieval.get('queries', 0)} semantic quer(ies)\n"
            f"- {context_stats.get('convention_docs', 0)} convention document(s) consulted\n"
            f"- {context_stats.get('chars_used', 0)} / {context_stats.get('char_budget', 0)} "
            "context characters used"
        )
    if llm_failed:
        transparency.append(
            "**Note:** model reasoning was unavailable for this run; only deterministic "
            "analyzer findings are reported."
        )
    if warnings:
        transparency.append(
            "**Warnings**\n" + "\n".join(f"- {w}" for w in list(warnings)[:8])
        )
    if transparency:
        lines.append("<details><summary>How this review was produced</summary>\n")
        lines.append("\n\n".join(transparency))
        lines.append("\n</details>")

    return "\n".join(lines).strip() + FOOTER


def build_plan(
    *,
    summary: str,
    findings: Sequence[ValidatedFinding],
    risk: RiskAssessment,
    diff_positions: dict[str, dict[int, int]] | None = None,
    suppressed_count: int = 0,
    context_stats: dict[str, Any] | None = None,
    analyzers: dict[str, Any] | None = None,
    insufficient_evidence: bool = False,
    llm_failed: bool = False,
    warnings: Sequence[str] = (),
    event: str | None = None,
) -> PublishPlan:
    inline: list[InlineComment] = []
    summary_only: list[ValidatedFinding] = []
    diff_positions = diff_positions or {}

    for finding in findings:
        if not finding.inline_eligible or finding.line_number is None:
            summary_only.append(finding)
            continue
        position = (diff_positions.get(finding.file_path) or {}).get(finding.line_number)
        inline.append(
            InlineComment(
                path=finding.file_path,
                body=render_inline_comment(finding),
                line=finding.line_number,
                side="RIGHT",
                position=position,
            )
        )

    body = render_summary(
        summary=summary,
        findings=findings,
        risk=risk,
        summary_only=summary_only,
        suppressed_count=suppressed_count,
        context_stats=context_stats,
        analyzers=analyzers,
        insufficient_evidence=insufficient_evidence,
        llm_failed=llm_failed,
        warnings=warnings,
    )
    return PublishPlan(
        summary_body=body,
        inline_comments=inline,
        summary_only_findings=summary_only,
        event=event or settings.DEFAULT_REVIEW_EVENT,
    )


class GitHubPublisher:
    def __init__(self, provider: GitHubProvider) -> None:
        self.provider = provider

    async def publish(
        self,
        *,
        installation_id: int,
        repository_full_name: str,
        pr_number: int,
        commit_sha: str,
        plan: PublishPlan,
    ) -> PublishedReview:
        logger.info(
            "publish.start",
            repository=repository_full_name,
            pr=pr_number,
            inline=len(plan.inline_comments),
            review_event=plan.event,
        )
        review = await self.provider.create_review(
            installation_id,
            repository_full_name,
            pr_number,
            commit_sha=commit_sha,
            body=plan.summary_body,
            event=plan.event,
            comments=plan.inline_comments,
        )
        if review.rejected_comments:
            logger.warning(
                "publish.comments_rejected",
                repository=repository_full_name,
                pr=pr_number,
                count=len(review.rejected_comments),
            )
        return review


def escalate_event(risk: RiskAssessment, findings: Sequence[ValidatedFinding]) -> str:
    """Decide the review event.

    Kept deliberately conservative: the reviewer only escalates beyond a plain
    comment when explicitly allowed by configuration *and* there is a confirmed
    critical finding. Everything else is a comment.
    """
    if settings.DEFAULT_REVIEW_EVENT != "REQUEST_CHANGES":
        return settings.DEFAULT_REVIEW_EVENT
    for finding in findings:
        if (
            finding.severity == Severity.CRITICAL
            and finding.confidence_label == Confidence.CONFIRMED
        ):
            return "REQUEST_CHANGES"
    return "COMMENT"
