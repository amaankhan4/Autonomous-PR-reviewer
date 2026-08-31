"""Embedding providers.

``hashing`` (default)
    A deterministic, dependency-free hashed bag-of-identifiers embedding.
    Identifiers are split on ``camelCase``/``snake_case``, sub-tokens plus
    character trigrams are hashed into a fixed-width signed vector with
    sub-linear term weighting, and the vector is L2-normalised.

    It is genuinely useful for code retrieval (identifiers dominate code
    similarity) and requires no model download, no GPU and no API key -- which
    is what makes the whole product runnable offline. It is *not* a semantic
    transformer embedding; see the README's Limitations section.

``openai``
    Real API embeddings (``text-embedding-3-small`` by default) for production.
"""

from __future__ import annotations

import abc
import hashlib
import math
import re
from collections.abc import Iterable

import httpx

from app.core.config import settings
from app.core.errors import LLMError
from app.core.logging import get_logger

logger = get_logger(__name__)

_SPLIT_RE = re.compile(r"[^A-Za-z0-9_]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "your",
    "self", "def", "class", "return", "import", "const", "let", "var",
    "function", "async", "await", "true", "false", "none", "null",
}


class EmbeddingProvider(abc.ABC):
    name: str = "embedding"
    dimension: int = 384

    @abc.abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def embed_one(self, text: str) -> list[float]:
        vectors = await self.embed([text])
        return vectors[0]


def tokenize_code(text: str) -> list[str]:
    """Split code into normalised sub-tokens."""
    tokens: list[str] = []
    for raw in _SPLIT_RE.split(text):
        if not raw:
            continue
        for part in _CAMEL_RE.split(raw):
            for piece in part.split("_"):
                piece = piece.strip().lower()
                if not piece or piece in _STOPWORDS or len(piece) > 40:
                    continue
                tokens.append(piece)
    return tokens


class HashingEmbeddingProvider(EmbeddingProvider):
    name = "hashing"

    def __init__(self, dimension: int | None = None) -> None:
        self.dimension = dimension or settings.EMBEDDING_DIM

    def _vector(self, text: str) -> list[float]:
        counts: dict[int, float] = {}
        tokens = tokenize_code(text)

        def add(feature: str, weight: float) -> None:
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            sign = 1.0 if digest[4] & 1 else -1.0
            counts[index] = counts.get(index, 0.0) + sign * weight

        for token in tokens:
            add(token, 1.0)
            # Character trigrams give partial credit to near-miss identifiers
            # (e.g. `retry_policy` vs `retryPolicies`).
            padded = f"^{token}$"
            for i in range(len(padded) - 2):
                add(padded[i : i + 3], 0.25)

        for first, second in zip(tokens, tokens[1:], strict=False):
            add(f"{first}~{second}", 0.5)

        vector = [0.0] * self.dimension
        for index, value in counts.items():
            # Sub-linear scaling keeps repeated identifiers from dominating.
            vector[index] = math.copysign(math.log1p(abs(value)), value)

        norm = math.sqrt(sum(v * v for v in vector))
        if norm == 0:
            return vector
        return [v / norm for v in vector]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(text) for text in texts]


class OpenAIEmbeddingProvider(EmbeddingProvider):
    name = "openai"

    _MODEL_DIMENSIONS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = api_key or settings.OPENAI_API_KEY
        self.model = model or settings.EMBEDDING_MODEL
        self.dimension = self._MODEL_DIMENSIONS.get(self.model, settings.EMBEDDING_DIM)
        if not self.api_key:
            raise LLMError(
                "OPENAI_API_KEY is required for the OpenAI embedding provider",
                code="EMBEDDING_NOT_CONFIGURED",
                status_code=503,
            )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(
            base_url=settings.OPENAI_BASE_URL, timeout=settings.LLM_TIMEOUT_SECONDS
        ) as client:
            response = await client.post(
                "/embeddings",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={"model": self.model, "input": texts},
            )
        if response.status_code >= 400:
            raise LLMError(
                f"Embedding request failed with HTTP {response.status_code}",
                code="EMBEDDING_ERROR",
            )
        payload = response.json()
        ordered = sorted(payload.get("data", []), key=lambda item: item.get("index", 0))
        return [item["embedding"] for item in ordered]


_provider: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider
    if _provider is None:
        if settings.EMBEDDING_PROVIDER == "openai":
            _provider = OpenAIEmbeddingProvider()
        else:
            _provider = HashingEmbeddingProvider()
        logger.info(
            "embedding_provider_selected",
            provider=_provider.name,
            dimension=_provider.dimension,
        )
    return _provider


def set_embedding_provider(provider: EmbeddingProvider | None) -> None:
    global _provider
    _provider = provider


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    a_list, b_list = list(a), list(b)
    dot = sum(x * y for x, y in zip(a_list, b_list, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a_list))
    norm_b = math.sqrt(sum(y * y for y in b_list))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
