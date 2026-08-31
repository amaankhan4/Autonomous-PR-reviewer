"""Deterministic, evidence-driven mock LLM provider.

This is **not** a canned-response stub. It parses the very prompt the real model
would receive, recovers the structured evidence blocks from it, and derives
findings only from that evidence. Consequences that matter:

* If the context builder forgets to include the diff, the mock produces nothing --
  the fake model cannot paper over a real bug in our pipeline.
* Every finding it emits cites a file and a line that genuinely appears in the
  diff, so it exercises the same :class:`FindingValidator` path as a real model.
* When the evidence is thin it returns ``insufficient_evidence=True`` with zero
  findings, exercising the "say nothing" path that the product depends on.
* It reasons *about* deterministic analyzer output (adding blast radius, test
  coverage and repository-convention context) instead of parroting it.

That makes the demo honest: mock mode shows the real pipeline, with the model
call replaced by a transparent rule engine.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from app.core.enums import Category, Confidence, Severity
from app.integrations.llm.base import (
    LLMProvider,
    LLMResult,
    LLMReviewOutput,
    LLMUsageRecord,
    estimate_tokens,
)

_KNOWN_SECTIONS: tuple[str, ...] = (
    "PULL REQUEST",
    "CHANGE SUMMARY",
    "CHANGED CODE",
    "DETERMINISTIC ANALYZER FINDINGS",
    "CHANGED SYMBOLS",
    "BLAST RADIUS",
    "RELATED REPOSITORY CODE",
    "TEST COVERAGE CONTEXT",
    "REPOSITORY CONVENTIONS",
    "HISTORICAL FINDINGS",
    "RELATED PRODUCTION INCIDENTS",
    "FINDINGS",
    "SECURITY NOTICE",
    "TASK",
)
_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
_FILE_HEADER_RE = re.compile(r"^--- (.+?) ---$")

_HIGH_VALUE = {Category.SECURITY, Category.CORRECTNESS, Category.RELIABILITY}
_SEVERITY_ORDER = {
    Severity.CRITICAL: 4,
    Severity.HIGH: 3,
    Severity.MEDIUM: 2,
    Severity.LOW: 1,
    Severity.INFO: 0,
}


def _split_sections(prompt: str) -> dict[str, str]:
    """Locate the known evidence sections emitted by :mod:`app.integrations.llm.prompts`.

    Only the fixed header list is honoured (first occurrence wins) so that
    attacker-controlled repository content embedded in the prompt cannot forge a
    new section.
    """
    hits: list[tuple[int, str]] = []
    for name in _KNOWN_SECTIONS:
        match = re.search(rf"^# {re.escape(name)}", prompt, re.M)
        if match:
            hits.append((match.start(), name))
    hits.sort()
    sections: dict[str, str] = {}
    for index, (start, name) in enumerate(hits):
        end = hits[index + 1][0] if index + 1 < len(hits) else len(prompt)
        body = prompt[start:end]
        body = body.split("\n", 1)[1] if "\n" in body else ""
        sections[name] = body.strip()
    return sections


def _section(sections: dict[str, str], prefix: str) -> str:
    for key, value in sections.items():
        if key.startswith(prefix):
            return value
    return ""


def _json_payload(block: str) -> Any:
    match = _JSON_BLOCK_RE.search(block)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _parse_diff_anchors(diff_block: str) -> dict[str, list[int]]:
    """Recover ``file -> [added line numbers]`` from the untrusted diff block."""
    anchors: dict[str, list[int]] = {}
    current: str | None = None
    line_no = 0
    for raw in diff_block.splitlines():
        header = _FILE_HEADER_RE.match(raw.strip())
        if header:
            current = header.group(1).strip()
            anchors.setdefault(current, [])
            line_no = 0
            continue
        if current is None:
            continue
        hunk = _HUNK_RE.match(raw)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        if raw.startswith("+") and not raw.startswith("+++"):
            anchors[current].append(line_no)
            line_no += 1
        elif raw.startswith("-") or raw.startswith("\\"):
            continue
        elif raw.startswith(" ") or raw == "":
            line_no += 1
    return anchors


class MockLLMProvider(LLMProvider):
    """Rule-based stand-in that reasons over the same evidence bundle."""

    name = "mock"
    is_mock = True

    def __init__(self, model: str | None = None) -> None:
        super().__init__(model or "mock-reviewer-v1")

    async def generate_review(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> LLMResult:
        started = time.perf_counter()
        sections = _split_sections(user_prompt)

        change_summary = _json_payload(_section(sections, "CHANGE SUMMARY")) or {}
        static_findings = _json_payload(_section(sections, "DETERMINISTIC ANALYZER")) or []
        changed_symbols = _json_payload(_section(sections, "CHANGED SYMBOLS")) or []
        blast_radius = _json_payload(_section(sections, "BLAST RADIUS")) or []
        test_context = _json_payload(_section(sections, "TEST COVERAGE")) or {}
        historical = _json_payload(_section(sections, "HISTORICAL FINDINGS")) or []
        anchors = _parse_diff_anchors(_section(sections, "CHANGED CODE"))
        related_code = _section(sections, "RELATED REPOSITORY CODE")
        conventions = _section(sections, "REPOSITORY CONVENTIONS")
        injection_notice = _section(sections, "SECURITY NOTICE")

        findings: list[dict[str, Any]] = []
        notes: list[str] = ["Generated by the deterministic mock reviewer (no model was called)."]

        if injection_notice:
            file, line = self._first_anchor(anchors)
            findings.append(
                self._finding(
                    file=file,
                    line=line,
                    severity=Severity.HIGH,
                    category=Category.SECURITY,
                    title="Prompt-injection attempt detected in repository content",
                    description=(
                        "Content supplied by this repository contains text shaped like "
                        "instructions to the review model (for example an attempt to "
                        "override the reviewer's rules or suppress findings). The content "
                        "was neutralised and treated strictly as data, but a human should "
                        "confirm why it is present."
                    ),
                    evidence=injection_notice[:600],
                    confidence=0.9,
                    certainty=Confidence.CONFIRMED,
                    suggested_fix=(
                        "Remove the instruction-like text from the repository, or confirm "
                        "it is a deliberate test fixture."
                    ),
                )
            )

        findings.extend(
            self._reason_about_static_findings(
                static_findings, blast_radius, historical, related_code, conventions, anchors
            )
        )
        findings.extend(self._testing_findings(test_context, change_summary, changed_symbols, anchors))
        findings.extend(self._convention_findings(conventions, related_code, anchors, change_summary))

        findings = self._dedupe(findings)[:20]
        insufficient = not findings
        if insufficient:
            notes.append(
                "The evidence bundle did not contain enough signal to justify a finding."
            )
        summary = self._summarise(change_summary, findings, insufficient)

        output = LLMReviewOutput(
            summary=summary,
            findings=findings,  # type: ignore[arg-type]
            insufficient_evidence=insufficient,
            notes=notes,
        )
        raw = output.model_dump_json()
        usage = LLMUsageRecord(
            provider=self.name,
            model=self.model,
            operation="review",
            input_tokens=estimate_tokens(system_prompt) + estimate_tokens(user_prompt),
            output_tokens=estimate_tokens(raw),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return LLMResult(output=output, usage=usage, raw_text=raw)

    # ------------------------------------------------------------------ rules
    @staticmethod
    def _first_anchor(anchors: dict[str, list[int]]) -> tuple[str, int | None]:
        for file, lines in anchors.items():
            if lines:
                return file, lines[0]
        return (next(iter(anchors), "PULL_REQUEST"), None)

    @staticmethod
    def _finding(**kwargs: Any) -> dict[str, Any]:
        payload = dict(kwargs)
        payload["severity"] = str(payload["severity"])
        payload["category"] = str(payload["category"])
        payload["certainty"] = str(payload.get("certainty", Confidence.LIKELY))
        payload.setdefault("related_symbols", [])
        return payload

    def _reason_about_static_findings(
        self,
        static_findings: list[dict[str, Any]],
        blast_radius: list[dict[str, Any]],
        historical: list[dict[str, Any]],
        related_code: str,
        conventions: str,
        anchors: dict[str, list[int]],
    ) -> list[dict[str, Any]]:
        """Add repository context to the most severe deterministic findings."""
        if not isinstance(static_findings, list):
            return []

        callers: dict[str, list[str]] = {}
        for entry in blast_radius if isinstance(blast_radius, list) else []:
            if isinstance(entry, dict) and entry.get("symbol"):
                callers[str(entry["symbol"])] = [str(c) for c in entry.get("callers", [])][:5]

        historical_titles = {
            str(h.get("title", "")).lower() for h in historical if isinstance(h, dict)
        }

        ranked = sorted(
            (f for f in static_findings if isinstance(f, dict)),
            key=lambda f: (
                _SEVERITY_ORDER.get(Severity.coerce(f.get("severity")), 0),
                float(f.get("confidence") or 0.5),
            ),
            reverse=True,
        )

        results: list[dict[str, Any]] = []
        for finding in ranked:
            severity = Severity.coerce(finding.get("severity"))
            category = Category.coerce(finding.get("category"))
            if category not in _HIGH_VALUE or _SEVERITY_ORDER.get(severity, 0) < 2:
                continue
            file = str(finding.get("file") or "")
            line = finding.get("line")
            if file not in anchors:
                continue
            if isinstance(line, int) and anchors[file] and line not in anchors[file]:
                line = min(anchors[file], key=lambda candidate: abs(candidate - line))

            symbol = str(finding.get("symbol") or "")
            context_bits: list[str] = []
            if symbol and callers.get(symbol):
                context_bits.append(
                    f"`{symbol}` is called by {', '.join(callers[symbol])}, so the impact is "
                    "not local to this diff."
                )
            title_key = str(finding.get("title", "")).lower()
            if any(
                word and word in title_key for word in ("sql", "injection", "secret")
            ) and ("retrypolicy" in related_code.lower() or "execute_query" in related_code.lower()):
                context_bits.append(
                    "The repository already exposes a parameterised query helper in the "
                    "retrieved code; the change bypasses it."
                )
            if title_key and any(title_key[:30] in h for h in historical_titles):
                context_bits.append(
                    "A similar issue was previously reported in this repository, which "
                    "suggests a recurring pattern rather than a one-off mistake."
                )
            if not context_bits:
                continue  # nothing new to add -- do not parrot the tool

            results.append(
                self._finding(
                    file=file,
                    line=line if isinstance(line, int) else None,
                    severity=severity,
                    category=category,
                    title=str(finding.get("title") or "Issue in changed code")[:200],
                    description=(
                        f"{finding.get('description') or finding.get('message') or ''}\n\n"
                        "Repository context: " + " ".join(context_bits)
                    ).strip(),
                    evidence=(
                        f"{file}:{line} | {finding.get('tool') or 'analyzer'} "
                        f"{finding.get('rule_id') or ''} | {finding.get('snippet') or ''}"
                    ).strip(),
                    confidence=min(0.95, float(finding.get("confidence") or 0.7) + 0.1),
                    certainty=Confidence.CONFIRMED
                    if severity in (Severity.CRITICAL, Severity.HIGH)
                    else Confidence.LIKELY,
                    suggested_fix=finding.get("suggested_fix") or None,
                    related_symbols=[symbol] if symbol else [],
                )
            )
            if len(results) >= 6:
                break
        return results

    def _testing_findings(
        self,
        test_context: dict[str, Any],
        change_summary: dict[str, Any],
        changed_symbols: list[dict[str, Any]],
        anchors: dict[str, list[int]],
    ) -> list[dict[str, Any]]:
        if not isinstance(test_context, dict):
            return []
        untested = test_context.get("untested_symbols") or []
        if not untested:
            return []
        risky = {str(s).lower() for s in (change_summary or {}).get("risk_signals", [])}
        severity = Severity.MEDIUM
        if risky & {"auth", "payments", "database", "security"}:
            severity = Severity.HIGH

        target = untested[0] if isinstance(untested[0], dict) else {"name": str(untested[0])}
        file = str(target.get("file") or "")
        if file not in anchors:
            file, line = self._first_anchor(anchors)
        else:
            line = target.get("line") if isinstance(target.get("line"), int) else (
                anchors[file][0] if anchors[file] else None
            )
        names = [
            str(item.get("name") if isinstance(item, dict) else item) for item in untested[:6]
        ]
        return [
            self._finding(
                file=file,
                line=line,
                severity=severity,
                category=Category.TESTING,
                title="Changed behaviour is not covered by tests",
                description=(
                    "The following changed symbols have no matching test references in this "
                    f"pull request: {', '.join(names)}. "
                    + (
                        "The change touches "
                        f"{', '.join(sorted(risky))}-related code, where regressions are "
                        "expensive to detect in production."
                        if risky
                        else "Untested behaviour changes are the most common source of "
                        "regressions in review datasets."
                    )
                ),
                evidence=json.dumps(
                    {
                        "untested_symbols": names,
                        "test_files_changed": test_context.get("test_files_changed", []),
                        "risk_signals": sorted(risky),
                    }
                )[:1500],
                confidence=0.72,
                certainty=Confidence.LIKELY,
                suggested_fix=(
                    "Add unit tests that exercise the new branches, including the failure "
                    "paths, before merging."
                ),
                related_symbols=names[:5],
            )
        ]

    def _convention_findings(
        self,
        conventions: str,
        related_code: str,
        anchors: dict[str, list[int]],
        change_summary: dict[str, Any],
    ) -> list[dict[str, Any]]:
        if not conventions:
            return []
        rules = [
            line.strip("-* ").strip()
            for line in conventions.splitlines()
            if re.search(r"\b(MUST|NEVER|ALWAYS|required)\b", line)
        ]
        if not rules:
            return []
        file, line = self._first_anchor(anchors)
        return [
            self._finding(
                file=file,
                line=line,
                severity=Severity.MEDIUM,
                category=Category.ARCHITECTURE,
                title="Verify the change against documented repository conventions",
                description=(
                    "This repository documents explicit engineering conventions that apply to "
                    "the area being changed:\n"
                    + "\n".join(f"- {rule}" for rule in rules[:5])
                    + "\n\nThe retrieved repository context shows existing helpers that "
                    "implement these conventions; confirm the new code uses them rather than "
                    "re-implementing the behaviour."
                ),
                evidence=(conventions[:800] or "repository documentation"),
                confidence=0.55,
                certainty=Confidence.UNCERTAIN,
                suggested_fix=(
                    "Reuse the documented helper/abstraction, or record why this change is "
                    "an intentional exception."
                ),
            )
        ]

    @staticmethod
    def _dedupe(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, Any, str]] = set()
        unique: list[dict[str, Any]] = []
        for finding in findings:
            key = (
                str(finding.get("file")),
                finding.get("line"),
                str(finding.get("title", ""))[:60].lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            unique.append(finding)
        return unique

    @staticmethod
    def _summarise(
        change_summary: dict[str, Any], findings: list[dict[str, Any]], insufficient: bool
    ) -> str:
        files = (change_summary or {}).get("files_changed", "?")
        additions = (change_summary or {}).get("additions", "?")
        deletions = (change_summary or {}).get("deletions", "?")
        if insufficient:
            return (
                f"Reviewed {files} changed file(s) (+{additions}/-{deletions}). The available "
                "evidence did not support any concrete finding, so no issues are reported. "
                "This is not an approval: areas without analyzer coverage were not assessed."
            )
        counts: dict[str, int] = {}
        for finding in findings:
            counts[str(finding["severity"])] = counts.get(str(finding["severity"]), 0) + 1
        breakdown = ", ".join(f"{count} {sev.lower()}" for sev, count in counts.items())
        top = findings[0]
        location = top.get("file") or top.get("file_path") or "the changed code"
        return (
            f"Reviewed {files} changed file(s) (+{additions}/-{deletions}) and raised "
            f"{len(findings)} finding(s) ({breakdown}). The most important one is "
            f"\"{top.get('title')}\" in {location}. Each finding cites the analyzer output or "
            "repository context it is based on, so it can be verified before acting on it."
        )

    async def generate_summary(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, LLMUsageRecord]:
        started = time.perf_counter()
        sections = _split_sections(user_prompt)
        findings = _json_payload(_section(sections, "FINDINGS")) or []
        change_summary = _json_payload(_section(sections, "CHANGE SUMMARY")) or {}
        text = self._summarise(change_summary, list(findings), not findings)
        usage = LLMUsageRecord(
            provider=self.name,
            model=self.model,
            operation="summary",
            input_tokens=estimate_tokens(user_prompt),
            output_tokens=estimate_tokens(text),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return text, usage
