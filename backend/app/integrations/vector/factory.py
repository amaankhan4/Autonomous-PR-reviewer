"""Vector store selection."""

from __future__ import annotations

from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.vector.base import VectorStore
from app.integrations.vector.memory_store import InMemoryVectorStore

logger = get_logger(__name__)

_store: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store
    if _store is None:
        if settings.VECTOR_STORE == "qdrant":
            from app.integrations.vector.qdrant_store import QdrantVectorStore

            _store = QdrantVectorStore(
                url=settings.QDRANT_URL,
                collection=settings.QDRANT_COLLECTION,
                api_key=settings.QDRANT_API_KEY,
            )
        elif settings.VECTOR_STORE == "upstash":
            from app.integrations.vector.upstash_store import UpstashVectorStore

            if not settings.upstash_vector_configured:
                raise RuntimeError(
                    "VECTOR_STORE=upstash requires UPSTASH_VECTOR_REST_URL and "
                    "UPSTASH_VECTOR_REST_TOKEN to be set."
                )
            _store = UpstashVectorStore(
                url=settings.UPSTASH_VECTOR_REST_URL,  # type: ignore[arg-type]
                token=settings.UPSTASH_VECTOR_REST_TOKEN,  # type: ignore[arg-type]
            )
        else:
            persist = settings.VECTOR_PERSIST_PATH
            _store = InMemoryVectorStore(
                persist_path=f"{persist}/chunks.json" if persist else None
            )
        logger.info("vector_store_selected", store=_store.name)
    return _store


def set_vector_store(store: VectorStore | None) -> None:
    global _store
    _store = store
