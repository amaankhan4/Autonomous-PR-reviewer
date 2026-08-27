"""Application configuration.

All runtime configuration is loaded from environment variables so the same image
can run in local Docker Compose, CI and a real deployment without code changes.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import quote, urlparse

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ------------------------------------------------------------------ app
    APP_NAME: str = "Autonomous PR Reviewer"
    ENVIRONMENT: Literal["local", "test", "staging", "production"] = "local"
    DEBUG: bool = True
    API_V1_PREFIX: str = "/api/v1"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False

    # Public base URL of the frontend (used for GitHub install redirects).
    FRONTEND_URL: str = "http://localhost:5173"
    # NoDecode: pydantic-settings would otherwise JSON-decode list fields inside
    # the dotenv source, before validators run, making plain CSV a hard error.
    # The `_split_csv` validator below accepts both CSV and a JSON array.
    CORS_ORIGINS: Annotated[list[str], NoDecode] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # --------------------------------------------------------------- secrets
    SECRET_KEY: str = "dev-insecure-secret-change-me"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    JWT_ALGORITHM: str = "HS256"

    # -------------------------------------------------------------- database
    DATABASE_URL: str = "postgresql+asyncpg://prreviewer:prreviewer@localhost:5432/prreviewer"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20

    # ----------------------------------------------------------------- redis
    # Either REDIS_URL, or the Upstash REST pair below, must resolve for Celery.
    REDIS_URL: str = "redis://localhost:6379/0"
    # Upstash serves the same database over TLS-TCP; its REST token doubles as
    # the password, so a hosted broker needs no extra configuration and no
    # local Docker daemon.
    UPSTASH_REDIS_REST_URL: str | None = None
    UPSTASH_REDIS_REST_TOKEN: str | None = None
    CELERY_BROKER_URL: str | None = None
    CELERY_RESULT_BACKEND: str | None = None
    REVIEW_QUEUE: str = "review_queue"
    INDEX_QUEUE: str = "index_queue"
    # Hosted Redis plans commonly expose a single shared database, so all Celery
    # keys are prefixed to stay isolated from anything else using it.
    REDIS_KEY_PREFIX: str = "prreviewer:"
    # Run Celery tasks inline (no broker). Useful for tests and single-process demos.
    CELERY_TASK_ALWAYS_EAGER: bool = False

    # ------------------------------------------------------------ github app
    # When MOCK_GITHUB is true the MockGitHubProvider is used and no GitHub
    # credentials are required -- this is what powers demo mode.
    MOCK_GITHUB: bool = True
    GITHUB_APP_ID: str | None = None
    GITHUB_APP_SLUG: str | None = None
    GITHUB_APP_PRIVATE_KEY: str | None = None  # PEM contents (\n escaped ok)
    GITHUB_APP_PRIVATE_KEY_PATH: str | None = None
    GITHUB_WEBHOOK_SECRET: str = "dev-webhook-secret"
    GITHUB_CLIENT_ID: str | None = None
    GITHUB_CLIENT_SECRET: str | None = None
    GITHUB_API_URL: str = "https://api.github.com"
    GITHUB_OAUTH_URL: str = "https://github.com/login/oauth"
    GITHUB_TIMEOUT_SECONDS: float = 20.0

    # -------------------------------------------------------------------- ai
    LLM_PROVIDER: Literal["mock", "openai", "gemini"] = "mock"
    LLM_MODEL: str = "gpt-4o-mini"
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_OUTPUT_TOKENS: int = 4000
    LLM_TIMEOUT_SECONDS: float = 90.0
    LLM_MAX_RETRIES: int = 2
    # Make a second, cheap call to summarise the *validated* findings.
    LLM_SUMMARY_CALL: bool = True
    # When true a misconfigured real provider raises instead of falling back to mock.
    LLM_STRICT_PROVIDER: bool = False
    OPENAI_API_KEY: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    GEMINI_API_KEY: str | None = None
    GEMINI_BASE_URL: str = "https://generativelanguage.googleapis.com/v1beta"

    # Rough $/1M tokens used for cost estimation and budgeting.
    LLM_INPUT_COST_PER_MTOK: float = 0.15
    LLM_OUTPUT_COST_PER_MTOK: float = 0.60

    # ------------------------------------------------------------- embedding
    EMBEDDING_PROVIDER: Literal["hashing", "openai"] = "hashing"
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    EMBEDDING_DIM: int = 384

    # ---------------------------------------------------------- vector store
    VECTOR_STORE: Literal["memory", "qdrant", "upstash"] = "memory"
    QDRANT_URL: str = "http://localhost:6333"
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION: str = "repo_context"
    VECTOR_PERSIST_PATH: str = "./.vectorstore"
    # Upstash Vector (serverless). Create a *dense* index whose dimension equals
    # EMBEDDING_DIM with COSINE similarity and no hosted embedding model -- this
    # application always sends its own vectors.
    UPSTASH_VECTOR_REST_URL: str | None = None
    UPSTASH_VECTOR_REST_TOKEN: str | None = None
    UPSTASH_VECTOR_TIMEOUT_SECONDS: float = 20.0
    UPSTASH_VECTOR_BATCH_SIZE: int = 100
    # Upstash caps metadata per vector; chunk bodies are truncated to fit.
    UPSTASH_VECTOR_MAX_CONTENT_CHARS: int = 8_000

    # ----------------------------------------------------------- review caps
    MAX_CHANGED_FILES: int = 60
    MAX_DIFF_BYTES: int = 400_000
    MAX_FILE_BYTES: int = 200_000
    MAX_CONTEXT_CHARS: int = 60_000
    MAX_RETRIEVED_CHUNKS: int = 12
    MAX_FINDINGS_PUBLISHED: int = 25
    MIN_PUBLISH_CONFIDENCE: float = 0.55
    DEFAULT_MIN_SEVERITY: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = "LOW"
    REVIEW_JOB_TIMEOUT_SECONDS: int = 900

    # ------------------------------------------------- context assembly budget
    # Characters, not tokens: the budget must hold for every provider's tokenizer.
    CONTEXT_CHAR_BUDGET: int = 90_000
    CONTEXT_DIFF_SHARE: float = 0.55
    CONTEXT_RETRIEVAL_SHARE: float = 0.30
    CONTEXT_MAX_HUNK_LINES: int = 220
    CONTEXT_MAX_STATIC_FINDINGS: int = 60
    CONTEXT_MAX_SYMBOLS: int = 60

    # ------------------------------------------------------------- retrieval
    RETRIEVAL_TOP_K: int = 6
    RETRIEVAL_MIN_SCORE: float = 0.12
    RETRIEVAL_MAX_CHUNKS: int = 12
    RETRIEVAL_MAX_DOCS: int = 4
    RETRIEVAL_MAX_PER_FILE: int = 3
    RETRIEVAL_MAX_QUERIES: int = 12

    # Whether the worker is allowed to publish reviews back to GitHub.
    PUBLISH_REVIEWS: bool = True
    # MVP default per spec: COMMENT only, never auto REQUEST_CHANGES.
    DEFAULT_REVIEW_EVENT: Literal["COMMENT", "REQUEST_CHANGES", "APPROVE"] = "COMMENT"

    # ---------------------------------------------------- static analysis
    ENABLE_STATIC_ANALYSIS: bool = True
    STATIC_ANALYSIS_TIMEOUT_SECONDS: int = 60
    # A cold mypy run dominates review latency, so it is opt-in. When enabled it
    # uses a persistent cache directory to keep subsequent runs fast.
    STATIC_MYPY_ENABLED: bool = False
    STATIC_MYPY_TIMEOUT_SECONDS: int = 90
    MYPY_CACHE_DIR: str = "./.mypy_review_cache"
    ENABLED_ANALYZERS: Annotated[list[str], NoDecode] = [
        "diff",
        "ast",
        "static",
        "dependency",
        "test",
        "security",
    ]
    # Never execute repository test suites / build tooling on the host.
    ALLOW_REPO_CODE_EXECUTION: bool = False

    # ------------------------------------------------------------------ demo
    SEED_DEMO_DATA: bool = True
    DEMO_USER_EMAIL: str = "demo@prreviewer.dev"
    DEMO_USER_PASSWORD: str = "demo12345"

    # ------------------------------------------------------ incident engine
    INCIDENT_ENGINE_ENABLED: bool = False
    INCIDENT_ENGINE_URL: str | None = None
    INCIDENT_ENGINE_TOKEN: str | None = None

    @field_validator("CORS_ORIGINS", "ENABLED_ANALYZERS", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """Accept either a JSON array or a plain comma-separated string."""
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("["):
                import json

                try:
                    return json.loads(stripped)
                except ValueError:
                    return [stripped]
            return [item.strip() for item in stripped.split(",") if item.strip()]
        return value

    @field_validator("GITHUB_APP_PRIVATE_KEY", mode="before")
    @classmethod
    def _normalise_pem(cls, value: object) -> object:
        if isinstance(value, str) and "\\n" in value:
            return value.replace("\\n", "\n")
        return value

    # ------------------------------------------------------------- helpers
    @property
    def redis_url(self) -> str:
        """Resolved Redis URL, preferring an explicit URL over Upstash REST creds.

        Upstash exposes the same database over TLS-TCP on port 6379 with the
        REST token as the password, so a hosted broker can be derived from the
        REST pair without a second set of credentials.
        """
        if self.UPSTASH_REDIS_REST_URL and self.UPSTASH_REDIS_REST_TOKEN:
            host = urlparse(self.UPSTASH_REDIS_REST_URL).hostname
            if host:
                password = quote(self.UPSTASH_REDIS_REST_TOKEN, safe="")
                return f"rediss://default:{password}@{host}:6379"
        return self.REDIS_URL

    @property
    def broker_url(self) -> str:
        return self.CELERY_BROKER_URL or self.redis_url

    @property
    def result_backend(self) -> str:
        return self.CELERY_RESULT_BACKEND or self.redis_url

    @property
    def upstash_vector_configured(self) -> bool:
        return bool(self.UPSTASH_VECTOR_REST_URL and self.UPSTASH_VECTOR_REST_TOKEN)

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def demo_mode(self) -> bool:
        """True when neither a real GitHub App nor a real LLM is configured."""
        return self.MOCK_GITHUB or self.LLM_PROVIDER == "mock"

    def github_private_key(self) -> str | None:
        if self.GITHUB_APP_PRIVATE_KEY:
            return self.GITHUB_APP_PRIVATE_KEY
        if self.GITHUB_APP_PRIVATE_KEY_PATH:
            try:
                with open(self.GITHUB_APP_PRIVATE_KEY_PATH, encoding="utf-8") as handle:
                    return handle.read()
            except OSError:
                return None
        return None


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
