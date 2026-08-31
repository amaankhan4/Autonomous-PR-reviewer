"""LLM provider abstraction and the strict structured-output schema.

The reviewer never consumes free-form model prose: every provider must return
JSON matching :class:`LLMReviewOutput`. Anything that fails validation is
retried and then discarded -- a malformed response can never become a published
review.
"""

from __future__ import annotations

import abc
import json
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.core.config import settings
from app.core.enums import Category, Confidence, Severity


class LLMFinding(BaseModel):
    """A single model-proposed finding."""

    file: str = Field(min_length=1, max_length=1000)
    line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)
    severity: Severity
    category: Category
    title: str = Field(min_length=4, max_length=300)
    description: str = Field(min_length=10, max_length=4000)
    evidence: str = Field(default="", max_length=4000)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    certainty: Confidence = Confidence.LIKELY
    suggested_fix: str | None = Field(default=None, max_length=4000)
    related_symbols: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: object) -> object:
        return Severity.coerce(value) if not isinstance(value, Severity) else value

    @field_validator("category", mode="before")
    @classmethod
    def _coerce_category(cls, value: object) -> object:
        return Category.coerce(value) if not isinstance(value, Category) else value

    @field_validator("certainty", mode="before")
    @classmethod
    def _coerce_certainty(cls, value: object) -> object:
        if isinstance(value, Confidence):
            return value
        if isinstance(value, str):
            candidate = value.strip().lower()
            for member in Confidence:
                if member.value == candidate:
                    return member
        return Confidence.LIKELY

    @field_validator("file", mode="before")
    @classmethod
    def _normalise_file(cls, value: object) -> object:
        if isinstance(value, str):
            return value.replace("\\", "/").lstrip("./")
        return value


class LLMReviewOutput(BaseModel):
    """Top-level structured review returned by every provider."""

    summary: str = Field(default="", max_length=6000)
    findings: list[LLMFinding] = Field(default_factory=list, max_length=60)
    insufficient_evidence: bool = False
    notes: list[str] = Field(default_factory=list, max_length=20)


@dataclass(slots=True)
class LLMUsageRecord:
    provider: str
    model: str
    operation: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    success: bool = True
    cached: bool = False
    error_message: str | None = None

    @property
    def estimated_cost_usd(self) -> float:
        return round(
            (self.input_tokens / 1_000_000) * settings.LLM_INPUT_COST_PER_MTOK
            + (self.output_tokens / 1_000_000) * settings.LLM_OUTPUT_COST_PER_MTOK,
            6,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "operation": self.operation,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms,
            "estimated_cost_usd": self.estimated_cost_usd,
            "success": self.success,
            "cached": self.cached,
            "error_message": self.error_message,
        }


@dataclass(slots=True)
class LLMResult:
    output: LLMReviewOutput
    usage: LLMUsageRecord
    raw_text: str = ""
    attempts: int = 1
    warnings: list[str] = field(default_factory=list)


class LLMProvider(abc.ABC):
    """Interface every model backend implements."""

    name: str = "llm"
    is_mock: bool = False

    def __init__(self, model: str | None = None) -> None:
        self.model = model or settings.LLM_MODEL

    @abc.abstractmethod
    async def generate_review(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> LLMResult: ...

    @abc.abstractmethod
    async def generate_summary(self, system_prompt: str, user_prompt: str) -> tuple[str, LLMUsageRecord]: ...

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None


# --------------------------------------------------------------- json utils
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any]:
    """Best-effort extraction of a JSON object from a model response."""
    if not text or not text.strip():
        raise ValueError("empty response")

    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)

    fenced = _FENCE_RE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"findings": parsed}
    raise ValueError("response did not contain a JSON object")


def parse_review_output(text: str) -> LLMReviewOutput:
    """Parse and validate a raw model response into the strict schema."""
    payload = extract_json(text)
    if "findings" not in payload and "issues" in payload:
        payload["findings"] = payload.pop("issues")
    findings = payload.get("findings")
    if findings is None:
        payload["findings"] = []
    elif not isinstance(findings, list):
        raise ValueError("'findings' must be a list")

    # Drop individually malformed findings rather than failing the whole review.
    valid: list[dict[str, Any]] = []
    dropped = 0
    for item in payload.get("findings", []):
        if not isinstance(item, dict):
            dropped += 1
            continue
        try:
            LLMFinding.model_validate(item)
        except Exception:
            dropped += 1
            continue
        valid.append(item)
    payload["findings"] = valid

    notes = payload.get("notes")
    payload["notes"] = [str(n)[:400] for n in notes] if isinstance(notes, list) else []
    if dropped:
        payload["notes"].append(f"{dropped} malformed finding(s) were discarded during validation.")

    return LLMReviewOutput.model_validate(payload)


def estimate_tokens(text: str) -> int:
    """Cheap token estimate (~4 chars/token) used when a provider omits usage."""
    return max(1, len(text) // 4)


REVIEW_JSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "findings", "insufficient_evidence"],
    "properties": {
        "summary": {"type": "string"},
        "insufficient_evidence": {"type": "boolean"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "file",
                    "line",
                    "severity",
                    "category",
                    "title",
                    "description",
                    "evidence",
                    "confidence",
                    "certainty",
                    "suggested_fix",
                ],
                "properties": {
                    "file": {"type": "string"},
                    "line": {"type": ["integer", "null"]},
                    "severity": {
                        "type": "string",
                        "enum": ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                    "category": {
                        "type": "string",
                        "enum": [c.value for c in Category],
                    },
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                    "certainty": {
                        "type": "string",
                        "enum": ["confirmed", "likely", "uncertain"],
                    },
                    "suggested_fix": {"type": ["string", "null"]},
                },
            },
        },
    },
}
