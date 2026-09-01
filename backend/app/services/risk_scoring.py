"""Explainable risk scoring.

The risk score is computed by this module, **not** chosen by the LLM. Every
point is attributable to a named factor with its own evidence, so the UI can
show *why* a pull request scored 78 rather than asking the user to trust a
number a model invented.

Score = clamp(sum(factor.points), 0, 100), where each factor is bounded.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from app.analyzers.diff import DiffAnalysis
from app.core.enums import RiskBand, Severity

SEVERITY_POINTS: dict[Severity, float] = {
    Severity.CRITICAL: 26.0,
    Severity.HIGH: 14.0,
    Severity.MEDIUM: 6.0,
    Severity.LOW: 2.0,
    Severity.INFO: 0.5,
}

SENSITIVE_SIGNAL_POINTS: dict[str, float] = {
    "auth": 9.0,
    "security": 9.0,
    "payments": 9.0,
    "database": 7.0,
    "migration": 8.0,
    "concurrency": 6.0,
    "networking": 4.0,
    "config": 4.0,
    "infrastructure": 5.0,
    "dependency": 4.0,
    "api": 3.0,
    "error_handling": 3.0,
}


@dataclass(slots=True)
class RiskFactor:
    key: str
    label: str
    points: float
    detail: str
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["points"] = round(self.points, 1)
        return payload


@dataclass(slots=True)
class RiskAssessment:
    score: float
    band: RiskBand
    factors: list[RiskFactor]

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "band": str(self.band),
            "factors": [f.to_dict() for f in self.factors],
        }


def _band(score: float) -> RiskBand:
    if score >= 80:
        return RiskBand.CRITICAL
    if score >= 60:
        return RiskBand.HIGH
    if score >= 40:
        return RiskBand.ELEVATED
    if score >= 20:
        return RiskBand.MODERATE
    return RiskBand.LOW


def score_review(
    diff: DiffAnalysis,
    findings: list[dict[str, Any]],
    *,
    test_data: dict[str, Any] | None = None,
    dependency_data: dict[str, Any] | None = None,
    historical_hits: int = 0,
) -> RiskAssessment:
    """Compute an explainable 0-100 risk score for a review run."""
    factors: list[RiskFactor] = []
    test_data = test_data or {}
    dependency_data = dependency_data or {}

    # 1. Findings, weighted by severity (the dominant factor).
    counts: dict[Severity, int] = {}
    for finding in findings:
        severity = Severity.coerce(finding.get("severity"))
        counts[severity] = counts.get(severity, 0) + 1
    finding_points = 0.0
    for severity, count in counts.items():
        # Diminishing returns: the 5th HIGH does not double the risk of the 4th.
        weight = SEVERITY_POINTS[severity]
        finding_points += weight * min(count, 3) + (weight * 0.25) * max(0, count - 3)
    finding_points = min(finding_points, 60.0)
    if finding_points:
        factors.append(
            RiskFactor(
                key="findings",
                label="Severity-weighted findings",
                points=finding_points,
                detail=", ".join(
                    f"{count} {str(sev).lower()}" for sev, count in sorted(counts.items())
                ),
                evidence=[
                    f"{f.get('severity')} {f.get('file_path') or f.get('file')}: {f.get('title')}"
                    for f in findings[:5]
                ],
            )
        )

    # 2. Size of the change.
    total_lines = diff.total_additions + diff.total_deletions
    if total_lines > 100:
        size_points = min(12.0, 3.0 + (total_lines / 400.0) * 6.0)
        factors.append(
            RiskFactor(
                key="change_size",
                label="Change size",
                points=size_points,
                detail=(
                    f"{len(diff.files)} files, +{diff.total_additions}/-{diff.total_deletions} "
                    "lines; large diffs are reviewed less carefully by humans"
                ),
            )
        )

    # 3. Sensitive areas touched.
    signals = diff.aggregate_signals
    signal_points = 0.0
    hit: list[str] = []
    for signal, weight in SENSITIVE_SIGNAL_POINTS.items():
        if signals.get(signal):
            signal_points += weight
            hit.append(f"{signal} ({signals[signal]} matches)")
    if hit:
        signal_points = min(signal_points, 22.0)
        factors.append(
            RiskFactor(
                key="sensitive_areas",
                label="Sensitive areas touched",
                points=signal_points,
                detail="; ".join(hit),
                evidence=hit,
            )
        )

    # 4. Missing test coverage for changed behaviour.
    untested = test_data.get("untested_symbols") or []
    changed_symbols = test_data.get("changed_symbol_count") or 0
    if untested:
        ratio = len(untested) / max(1, changed_symbols or len(untested))
        test_points = min(12.0, 4.0 + ratio * 8.0)
        factors.append(
            RiskFactor(
                key="test_coverage",
                label="Untested changed behaviour",
                points=test_points,
                detail=(
                    f"{len(untested)} changed symbol(s) have no test references"
                    + (f" out of {changed_symbols}" if changed_symbols else "")
                ),
                evidence=[
                    str(item.get("name") if isinstance(item, dict) else item)
                    for item in untested[:6]
                ],
            )
        )
    elif diff.source_files and not diff.test_files:
        factors.append(
            RiskFactor(
                key="test_coverage",
                label="No test files changed",
                points=5.0,
                detail="Source files changed but no test file was added or modified",
            )
        )

    # 5. Dependency churn.
    dependency_changes = dependency_data.get("manifest_changes") or []
    if dependency_changes:
        factors.append(
            RiskFactor(
                key="dependencies",
                label="Dependency changes",
                points=min(10.0, 3.0 + 1.5 * len(dependency_changes)),
                detail=f"{len(dependency_changes)} dependency manifest change(s)",
                evidence=[str(c)[:200] for c in dependency_changes[:5]],
            )
        )

    # 6. Blast radius: changed symbols with many callers.
    high_fanout = dependency_data.get("high_fanout_symbols") or []
    if high_fanout:
        factors.append(
            RiskFactor(
                key="blast_radius",
                label="Wide blast radius",
                points=min(10.0, 2.5 * len(high_fanout)),
                detail=f"{len(high_fanout)} changed symbol(s) have many callers",
                evidence=[str(s)[:200] for s in high_fanout[:5]],
            )
        )

    # 7. Recurring issues seen in this repository before.
    if historical_hits:
        factors.append(
            RiskFactor(
                key="recurrence",
                label="Recurring issue pattern",
                points=min(8.0, 2.0 * historical_hits),
                detail=(
                    f"{historical_hits} finding(s) match issues previously reported in this "
                    "repository"
                ),
            )
        )

    # 8. Truncated analysis reduces confidence -- flag it rather than hide it.
    if diff.truncated:
        factors.append(
            RiskFactor(
                key="incomplete_analysis",
                label="Incomplete analysis",
                points=6.0,
                detail=(
                    f"{len(diff.dropped_files)} file(s) were excluded because the diff exceeded "
                    "the analysis budget, so parts of this PR were not reviewed"
                ),
                evidence=diff.dropped_files[:8],
            )
        )

    score = max(0.0, min(100.0, sum(f.points for f in factors)))
    return RiskAssessment(score=score, band=_band(score), factors=factors)
