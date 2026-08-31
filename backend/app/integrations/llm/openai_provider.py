"""OpenAI-compatible chat completions provider.

Uses raw ``httpx`` rather than the vendor SDK so the same code path works with
any OpenAI-compatible endpoint (Azure OpenAI, vLLM, Ollama, OpenRouter, ...)
by changing ``OPENAI_BASE_URL``.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

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

_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


class _RetryableHTTP(Exception):
    pass


class OpenAIProvider(LLMProvider):
    name = "openai"

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        super().__init__(model)
        self.api_key = api_key or settings.OPENAI_API_KEY
        if not self.api_key:
            raise LLMError("OPENAI_API_KEY is not configured")
        self.base_url = settings.OPENAI_BASE_URL.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.LLM_TIMEOUT_SECONDS),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()

    @retry(
        retry=retry_if_exception_type(_RetryableHTTP),
        stop=stop_after_attempt(settings.LLM_MAX_RETRIES + 1),
        wait=wait_exponential(multiplier=1, min=1, max=20),
        reraise=True,
    )
    async def _post(self, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            response = await self._client.post(f"{self.base_url}/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise _RetryableHTTP(f"timeout: {exc}") from exc
        except httpx.HTTPError as exc:
            raise LLMError(f"OpenAI transport error: {exc}") from exc

        if response.status_code in _RETRYABLE_STATUS:
            raise _RetryableHTTP(f"status {response.status_code}: {response.text[:300]}")
        if response.status_code >= 400:
            raise LLMError(
                f"OpenAI request failed ({response.status_code}): {response.text[:300]}"
            )
        return response.json()

    @staticmethod
    def _content(data: dict[str, Any]) -> str:
        choices = data.get("choices") or []
        if not choices:
            raise LLMError("OpenAI returned no choices")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):  # some gateways return content parts
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        if not content:
            raise LLMError("OpenAI returned an empty message")
        return str(content)

    async def generate_review(
        self, system_prompt: str, user_prompt: str, *, max_tokens: int | None = None
    ) -> LLMResult:
        payload: dict[str, Any] = {
            "model": self.model,
            "temperature": settings.LLM_TEMPERATURE,
            "max_tokens": max_tokens or settings.LLM_MAX_OUTPUT_TOKENS,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        started = time.perf_counter()
        warnings: list[str] = []
        attempts = 0
        last_error: Exception | None = None
        raw = ""
        data: dict[str, Any] = {}

        for attempts in range(1, settings.LLM_MAX_RETRIES + 2):
            try:
                data = await self._post(payload)
                raw = self._content(data)
                output = parse_review_output(raw)
                break
            except (LLMError, _RetryableHTTP) as exc:
                last_error = exc
                break
            except ValueError as exc:  # malformed JSON -> ask once more, strictly
                last_error = exc
                warnings.append(f"attempt {attempts}: {exc}")
                logger.warning("llm.invalid_json", attempt=attempts, error=str(exc))
                payload["messages"] = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                    {"role": "assistant", "content": raw[:2000]},
                    {
                        "role": "user",
                        "content": (
                            "Your previous response was not valid JSON matching the "
                            "required schema. Return ONLY the JSON object, no prose."
                        ),
                    },
                ]
        else:
            output = None  # type: ignore[assignment]

        latency_ms = int((time.perf_counter() - started) * 1000)
        usage_block = data.get("usage") or {}
        usage = LLMUsageRecord(
            provider=self.name,
            model=self.model,
            operation="review",
            input_tokens=int(usage_block.get("prompt_tokens") or estimate_tokens(user_prompt)),
            output_tokens=int(usage_block.get("completion_tokens") or estimate_tokens(raw)),
            latency_ms=latency_ms,
            success=last_error is None,
            error_message=str(last_error)[:500] if last_error else None,
        )
        if last_error is not None:
            raise LLMError(f"OpenAI review generation failed: {last_error}") from last_error
        return LLMResult(output=output, usage=usage, raw_text=raw, attempts=attempts, warnings=warnings)

    async def generate_summary(
        self, system_prompt: str, user_prompt: str
    ) -> tuple[str, LLMUsageRecord]:
        started = time.perf_counter()
        data = await self._post(
            {
                "model": self.model,
                "temperature": settings.LLM_TEMPERATURE,
                "max_tokens": 600,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            }
        )
        text = self._content(data)
        usage_block = data.get("usage") or {}
        usage = LLMUsageRecord(
            provider=self.name,
            model=self.model,
            operation="summary",
            input_tokens=int(usage_block.get("prompt_tokens") or estimate_tokens(user_prompt)),
            output_tokens=int(usage_block.get("completion_tokens") or estimate_tokens(text)),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
        return text.strip(), usage
