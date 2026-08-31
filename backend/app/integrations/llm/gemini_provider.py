"""Google Gemini provider (generateContent REST API).

Gemini is wired through the same :class:`LLMProvider` contract as OpenAI, so the
review engine is model-agnostic. ``responseMimeType`` + ``responseSchema`` give
us native structured output.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger
from app.integrations.llm.base import (
    LLMProvider,
    LLMResult,
    LLMUsageRecord,
    estimate_tokens,
    parse_review_output,
)

logger = get_logger(__name__)

_RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

# Gemini's schema dialect is a subset of JSON Schema: no additionalProperties,
# and enums must be plain string lists.
_GEMINI_REVIEW_SCHEMA: dict[str, Any] = {
    "type": "OBJECT",
    "properties": {
        "summary": {"type": "STRING"},
        "insufficient_evidence": {"type": "BOOLEAN"},
        "findings": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "file": {"type": "STRING"},
                    "line": {"type": "INTEGER"},
                    "severity": {
                        "type": "STRING",
                        "enum": ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"],
                    },
                    "category": {
                        "type": "STRING",
                        "enum": [
                            "correctness",
                            "security",
                            "performance",
                            "reliability",
                            "maintainability",
                            "testing",
                            "architecture",
                            "style",
                        ],
                    },
                    "title": {"type": "STRING"},
                    "description": {"type": "STRING"},
                    "evidence": {"type": "STRING"},
                    "confidence": {"type": "NUMBER"},
                    "certainty": {
                        "type": "STRING",
                        "enum": ["confirmed", "likely", "uncertain"],
                    },
                    "suggested_fix": {"type": "STRING"},
                },
                "required": [
                    "file",
                    "severity",
                    "category",
                    "title",
                    "description",
                    "evidence",
                    "confidence",
                ],
            },
        },
    },
    "required": ["summary", "insufficient_evidence", "findings"],
}


class _RetryableHTTP(Exception):
    pass


class GeminiProvider(LLMProvider):
    name = "gemini"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        super().__init__(model or "gemini-1.5-flash")
        self.api_key = api_key or settings.GEMINI_API_KEY
        if not self.api_key:
            raise LLMError("GEMINI_API_KEY is not configured")
        self.base_url = settings.GEMINI_BASE_URL.rstrip("/")
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(settings.LLM_TIMEOUT_SECONDS))

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(_RetryableHTTP),
        stop=stop_after_attempt(settings.LLM_MAX_RETRIES + 1),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base_url}/models/{self.model}:generateContent"
        try:
            response = await self._client.post(
                url, json=payload, headers={"x-goog-api-key": self.api_key}
            )
        except httpx.TimeoutException as exc:
            raise _RetryableHTTP(f"timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"Gemini transport error: {exc}") from exc

        if response.status_code in _RETRYABLE_STATUS:
            raise _RetryableHTTP(f"status {response.status_code}: {response.text[:300]}")
        if response.status_code >= 400:
            raise LLMError(f"Gemini request failed ({response.status_code}): {response.text[:300]}")
        return response.json()

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        candidates = data.get("candidates") or []
        if not candidates:
            feedback = data.get("promptFeedback") or {}
            raise LLMError(f"Gemini returned no candidates (feedback={json.dumps(feedback)[:200]})")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if isinstance(part, dict))
        if not text:
            raise LLMError("Gemini returned an empty candidate")
        return text

    async def generate_review(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> LLMResult:
        payload = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
            "generationConfig": {
                "temperature": settings.LLM_TEMPERATURE,
                "maxOutputTokens": max_tokens or settings.LLM_MAX_OUTPUT_TOKENS,
                "responseMimeType": "application/json",
                "responseSchema": _GEMINI_REVIEW_SCHEMA,
            },
        }
        started = time.perf_counter()
        data = await self._post(payload)
        raw = self._content(data)
        try:
            output = parse_review_output(raw)
            warnings: list[str] = []
        except ValueError as exc:
            logger.warning("llm.invalid_json", provider=self.name, error=str(exc))
            raise LLMError(f"Gemini returned unparsable JSON: {exc}") from exc

        usage_block = data.get("usageMetadata") or {}
        usage = LLMUsageRecord(
            provider=self.name,
            model=self.model,
            operation="review",
            input_tokens=int(usage_block.get("promptTokenCount") or estimate_tokens(user_prompt)),
            output_tokens=int(usage_block.get("candidatesTokenCount") or estimate_tokens(raw)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return LLMResult(output=output, usage=usage, raw_text=raw, warnings=warnings)

    async def generate_summary(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, LLMUsageRecord]:
        started = time.perf_counter()
        data = await self._post(
            {
                "systemInstruction": {"parts": [{"text": system_prompt}]},
                "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
                "generationConfig": {
                    "temperature": settings.LLM_TEMPERATURE,
                    "maxOutputTokens": 600,
                },
            }
        )
        text = self._content(data)
        usage_block = data.get("usageMetadata") or {}
        usage = LLMUsageRecord(
            provider=self.name,
            model=self.model,
            operation="summary",
            input_tokens=int(usage_block.get("promptTokenCount") or estimate_tokens(user_prompt)),
            output_tokens=int(usage_block.get("candidatesTokenCount") or estimate_tokens(text)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return text.strip(), usage
