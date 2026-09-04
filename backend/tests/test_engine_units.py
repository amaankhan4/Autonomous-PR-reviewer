"""Unit tests for the deterministic core: diff parsing, validation, risk scoring.

These are the parts that must never be delegated to the model, so they are
tested in isolation without a database or HTTP layer.
"""

from __future__ import annotations

import pytest

from app.analyzers.diff import DiffAnalysis, parse_patch, parse_unified_diff
from app.core.enums import Category, RiskBand, Severity
from app.services.finding_validator import (
    FindingValidator,
    compute_fingerprint,
)
from app.services.risk_scoring import score_review

PATCH = """@@ -1,6 +1,12 @@
 import os
 
 def charge(customer, amount):
-    return gateway.charge(customer, amount)
+    query = "SELECT * FROM cards WHERE id = '%s'" % customer
+    for attempt in range(3):
+        try:
+            return gateway.charge(customer, amount)
+        except Exception:
+            pass
+    return None
 
 def refund(customer):
     pass
"""


@pytest.fixture
def diff() -> DiffAnalysis:
    return DiffAnalysis(files=[parse_patch("payments/gateway.py", PATCH)])


def candidate(**overrides) -> dict:
    base = {
        "file": "payments/gateway.py",
        "line": 5,
        "title": "SQL injection via string interpolation",
        "description": "The customer id is interpolated straight into the SQL string.",
        "severity": "critical",
        "category": "security",
        "confidence": 0.9,
        "source": "llm",
    }
    base.update(overrides)
    return base


# --------------------------------------------------------------------- parsing
def test_parse_patch_tracks_line_numbers_and_positions(diff):
    file_diff = diff.files[0]
    assert file_diff.path == "payments/gateway.py"
    assert file_diff.additions == 7
    assert file_diff.deletions == 1
    assert file_diff.added_line_numbers == {4, 5, 6, 7, 8, 9, 10}
    # Context lines exist before and after the change.
    assert 1 in file_diff.context_line_numbers
    assert file_diff.changed_ranges == [(4, 10)]


def test_position_for_line_maps_to_github_diff_positions(diff):
    file_diff = diff.files[0]
    position = file_diff.position_for_line(5)
    assert position is not None and position > 0
    # A line that is not in the diff has no anchor.
    assert file_diff.position_for_line(9999) is None


def test_parse_patch_detects_language_and_signals(diff):
    file_diff = diff.files[0]
    assert file_diff.language.value == "python"
    assert file_diff.signals  # payments/error-handling signals were detected
    assert not file_diff.is_test


def test_binary_patch_is_flagged():
    file_diff = parse_patch("assets/logo.png", None)
    assert file_diff.is_binary
    assert file_diff.added_line_numbers == set()


def test_test_files_are_classified():
    file_diff = parse_patch("tests/test_gateway.py", PATCH)
    assert file_diff.is_test


def test_parse_unified_diff_splits_files():
    unified = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n+++ b/a.py\n"
        "@@ -1 +1,2 @@\n x = 1\n+y = 2\n"
        "diff --git a/b.py b/b.py\n"
        "--- a/b.py\n+++ b/b.py\n"
        "@@ -1 +1,2 @@\n z = 1\n+w = 2\n"
    )
    analysis = parse_unified_diff(unified)
    assert [f.path for f in analysis.files] == ["a.py", "b.py"]
    assert analysis.total_additions == 2


# ------------------------------------------------------------------ validation
def test_valid_finding_is_accepted(diff):
    outcome = FindingValidator(diff, repository_full_name="acme/pay").validate([candidate()])
    assert len(outcome.accepted) == 1
    finding = outcome.accepted[0]
    assert finding.severity is Severity.CRITICAL
    assert finding.category is Category.SECURITY
    assert finding.inline_eligible
    assert finding.fingerprint
    assert finding.code_snippet  # pulled from the diff


def test_hallucinated_file_is_rejected(diff):
    outcome = FindingValidator(diff).validate([candidate(file="does/not/exist.py")])
    assert not outcome.accepted
    assert outcome.rejected[0].reason == "file_not_in_diff"


def test_line_outside_the_diff_is_downgraded_not_dropped(diff):
    outcome = FindingValidator(diff).validate([candidate(line=900)])
    assert len(outcome.accepted) == 1
    finding = outcome.accepted[0]
    assert finding.inline_eligible is False
    assert any("not part of the diff" in note for note in finding.notes)


def test_near_miss_line_is_snapped(diff):
    outcome = FindingValidator(diff).validate([candidate(line=6)])
    finding = outcome.accepted[0]
    assert finding.inline_eligible
    assert finding.line_number in {4, 5, 6, 7, 8, 9, 10}


def test_missing_line_number_becomes_summary_only(diff):
    outcome = FindingValidator(diff).validate([candidate(line=None)])
    finding = outcome.accepted[0]
    assert finding.line_number is None
    assert finding.inline_eligible is False


def test_thin_findings_are_rejected(diff):
    outcome = FindingValidator(diff).validate(
        [candidate(title="Bug"), candidate(description="short")]
    )
    assert not outcome.accepted
    assert {r.reason for r in outcome.rejected} == {"missing_title", "missing_description"}


def test_basename_only_path_is_resolved(diff):
    outcome = FindingValidator(diff).validate([candidate(file="gateway.py")])
    assert outcome.accepted[0].file_path == "payments/gateway.py"


def test_equivalent_rules_from_two_tools_are_deduped(diff):
    outcome = FindingValidator(diff).validate(
        [
            candidate(source="ruff", rule_id="S608", confidence=0.6),
            candidate(source="bandit", rule_id="B608", confidence=0.8, title="SQL injection risk"),
        ]
    )
    assert len(outcome.accepted) == 1
    survivor = outcome.accepted[0]
    assert survivor.confidence == 0.8  # the more confident report wins
    assert any("also reported by" in note for note in survivor.notes)
    assert outcome.rejected[0].reason == "duplicate"


def test_min_severity_suppresses_noise(diff):
    outcome = FindingValidator(diff, min_severity=Severity.HIGH).validate(
        [candidate(severity="low", title="Style nit here", category="style")]
    )
    assert not outcome.accepted
    assert outcome.suppressed[0].reason == "below_min_severity"


def test_disabled_category_is_suppressed(diff):
    outcome = FindingValidator(diff, disabled_categories=["security"]).validate([candidate()])
    assert not outcome.accepted
    assert outcome.suppressed[0].reason == "category_disabled"


def test_max_findings_caps_output(diff):
    candidates = [
        candidate(line=line, title=f"Issue number {line} found", severity="medium")
        for line in (4, 5, 6, 7, 8)
    ]
    outcome = FindingValidator(diff, max_findings=2).validate(candidates)
    assert len(outcome.accepted) == 2
    assert all(item.reason == "max_findings_exceeded" for item in outcome.suppressed)


def test_accepted_findings_are_sorted_by_severity(diff):
    outcome = FindingValidator(diff).validate(
        [
            candidate(severity="low", title="Minor naming issue", line=4),
            candidate(severity="critical", title="Injection risk found", line=5),
            candidate(severity="medium", title="Retry loop swallows errors", line=8),
        ]
    )
    assert [str(f.severity) for f in outcome.accepted] == ["CRITICAL", "MEDIUM", "LOW"]


def test_validation_stats_explain_the_outcome(diff):
    outcome = FindingValidator(diff).validate([candidate(), candidate(file="ghost.py")])
    stats = outcome.stats
    assert stats["accepted"] == 1
    assert stats["rejected"] == 1
    assert stats["reasons"]["file_not_in_diff"] == 1


# ----------------------------------------------------------------- fingerprint
def test_fingerprint_is_stable_across_line_moves():
    first = compute_fingerprint("acme/pay", "a.py", "security", "SQL injection", "x = 1")
    second = compute_fingerprint("acme/pay", "a.py", "security", "injection SQL the", "x  =  1")
    assert first == second


def test_fingerprint_differs_across_files():
    first = compute_fingerprint("acme/pay", "a.py", "security", "SQL injection", "x = 1")
    second = compute_fingerprint("acme/pay", "b.py", "security", "SQL injection", "x = 1")
    assert first != second


# ---------------------------------------------------------------------- risk
def test_risk_score_is_explainable(diff):
    assessment = score_review(
        diff,
        [{"severity": "CRITICAL", "file_path": "payments/gateway.py", "title": "SQLi"}],
        test_data={"untested_symbols": [{"name": "charge"}], "changed_symbol_count": 2},
        dependency_data={"manifest_changes": ["requirements.txt"]},
        historical_hits=2,
    )
    keys = {factor.key for factor in assessment.factors}
    assert "findings" in keys
    assert "test_coverage" in keys
    assert "dependencies" in keys
    assert "recurrence" in keys
    assert assessment.score == pytest.approx(sum(f.points for f in assessment.factors), abs=0.01)
    assert all(factor.detail for factor in assessment.factors)


def test_clean_diff_scores_low(diff):
    assessment = score_review(DiffAnalysis(), [])
    assert assessment.score == 0.0
    assert assessment.band is RiskBand.LOW


def test_risk_score_is_bounded(diff):
    findings = [
        {"severity": "CRITICAL", "file_path": "payments/gateway.py", "title": f"Issue {i}"}
        for i in range(50)
    ]
    assessment = score_review(
        diff,
        findings,
        test_data={"untested_symbols": [f"sym{i}" for i in range(30)]},
        dependency_data={
            "manifest_changes": [f"pkg{i}" for i in range(20)],
            "high_fanout_symbols": [f"fn{i}" for i in range(20)],
        },
        historical_hits=20,
    )
    assert assessment.score <= 100.0


def test_severity_drives_the_band(diff):
    low = score_review(diff, [{"severity": "LOW", "title": "Nit", "file_path": "a.py"}])
    high = score_review(
        diff,
        [
            {"severity": "CRITICAL", "title": "SQLi", "file_path": "a.py"},
            {"severity": "HIGH", "title": "Silent except", "file_path": "a.py"},
        ],
    )
    assert high.score > low.score


def test_truncated_diff_is_reported_as_a_risk_factor():
    analysis = DiffAnalysis(files=[], truncated=True, dropped_files=["huge.py"])
    assessment = score_review(analysis, [])
    assert any(factor.key == "incomplete_analysis" for factor in assessment.factors)
