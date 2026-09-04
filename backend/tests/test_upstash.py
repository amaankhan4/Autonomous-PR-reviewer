"""Unit tests for the Upstash integration.

These are deliberately offline: they cover the pure logic that is easy to get
wrong (filter grammar, namespace sanitisation, credential handling) without
requiring network access or real credentials. The live round-trip is exercised
separately against a real index.
"""

from __future__ import annotations

from urllib.parse import unquote, urlparse

import pytest

from app.core.config import Settings
from app.integrations.vector.upstash_store import (
    UpstashVectorError,
    _build_filter,
    _chunk_from_metadata,
    _namespace,
    _quote,
)


# --------------------------------------------------------------- namespaces
class TestNamespace:
    def test_prefixes_repository_id(self):
        assert _namespace("123") == "repo_123"

    def test_strips_path_traversal_and_separators(self):
        # A namespace becomes a URL path segment, so anything that could escape
        # the segment must be neutralised. "../../admin" has six unsafe
        # characters, each replaced by an underscore.
        assert _namespace("../../admin") == "repo_" + "_" * 6 + "admin"
        assert _namespace("owner/name") == "repo_owner_name"

    def test_allows_hyphen_and_underscore(self):
        assert _namespace("acme-corp_payments") == "repo_acme-corp_payments"

    def test_distinct_repositories_never_collide_with_safe_ids(self):
        assert _namespace("repo-a") != _namespace("repo-b")


# ------------------------------------------------------------------ quoting
class TestQuote:
    def test_wraps_in_single_quotes(self):
        assert _quote("app.py") == "'app.py'"

    def test_escapes_embedded_quote(self):
        # Without escaping this would terminate the literal and inject filter syntax.
        assert _quote("it's") == "'it\\'s'"

    def test_escapes_backslash_before_quote(self):
        assert _quote("a\\b") == "'a\\\\b'"


# ------------------------------------------------------------------ filters
class TestBuildFilter:
    def test_empty_when_no_predicates(self):
        assert _build_filter(None, None, None) == ""

    def test_commit_sha(self):
        assert _build_filter("abc123", None, None) == "commit_sha = 'abc123'"

    def test_kinds_use_in_clause(self):
        assert _build_filter(None, ["function", "class"], None) == (
            "kind IN ('function', 'class')"
        )

    def test_excluded_files_are_negated_individually(self):
        assert _build_filter(None, None, ["a.py", "b.py"]) == (
            "file_path != 'a.py' AND file_path != 'b.py'"
        )

    def test_predicates_are_anded_together(self):
        result = _build_filter("sha1", ["test"], ["x.py"])
        assert result == "commit_sha = 'sha1' AND kind IN ('test') AND file_path != 'x.py'"

    def test_empty_collections_are_ignored(self):
        assert _build_filter(None, [], []) == ""

    def test_injection_attempt_is_neutralised(self):
        hostile = "x' OR kind = 'function"
        built = _build_filter(None, None, [hostile])
        # The injected quote is escaped, so the whole value stays one literal.
        assert built == "file_path != 'x\\' OR kind = \\'function'"


# -------------------------------------------------------- metadata decoding
class TestChunkFromMetadata:
    def _metadata(self, **overrides):
        base = {
            "repository_id": "7",
            "commit_sha": "deadbeef",
            "file_path": "app/main.py",
            "language": "python",
            "symbol": "main",
            "kind": "function",
            "start_line": 10,
            "end_line": 20,
            "content": "def main(): ...",
            "content_hash": "abc",
            "metadata": {"origin": "index"},
        }
        base.update(overrides)
        return base

    def test_round_trips_all_fields(self):
        chunk = _chunk_from_metadata("chunk-1", self._metadata())
        assert chunk is not None
        assert chunk.chunk_id == "chunk-1"
        assert chunk.file_path == "app/main.py"
        assert chunk.start_line == 10 and chunk.end_line == 20
        assert chunk.metadata == {"origin": "index"}

    def test_tolerates_missing_optional_fields(self):
        # Vectors written by an older version must not break retrieval.
        chunk = _chunk_from_metadata("chunk-2", {"file_path": "a.py"})
        assert chunk is not None
        assert chunk.file_path == "a.py"
        assert chunk.kind == "module"
        assert chunk.start_line == 0

    def test_coerces_numeric_strings(self):
        chunk = _chunk_from_metadata("c", self._metadata(start_line="3", end_line="9"))
        assert chunk is not None
        assert chunk.start_line == 3 and chunk.end_line == 9

    def test_returns_none_on_unusable_metadata(self):
        assert _chunk_from_metadata("c", self._metadata(start_line="not-a-number")) is None


# -------------------------------------------------------------- error class
class TestUpstashVectorError:
    def test_404_is_treated_as_missing_namespace(self):
        # Upstash phrases this as "does not exist", so the code must match on
        # status rather than on wording.
        exc = UpstashVectorError("Namespace repo_9 ... does not exist", status_code=404)
        assert exc.is_missing_namespace is True

    def test_other_statuses_are_real_failures(self):
        assert UpstashVectorError("boom", status_code=500).is_missing_namespace is False
        assert UpstashVectorError("unauthorized", status_code=401).is_missing_namespace is False

    def test_status_is_optional(self):
        assert UpstashVectorError("transport blew up").is_missing_namespace is False


# ------------------------------------------------------------------ settings
class TestRedisUrlDerivation:
    def _settings(self, **overrides) -> Settings:
        base = {
            "UPSTASH_REDIS_REST_URL": None,
            "UPSTASH_REDIS_REST_TOKEN": None,
            "CELERY_BROKER_URL": None,
            "CELERY_RESULT_BACKEND": None,
            "REDIS_URL": "redis://localhost:6379/0",
        }
        base.update(overrides)
        return Settings(**base)

    def test_falls_back_to_plain_redis_url(self):
        assert self._settings().redis_url == "redis://localhost:6379/0"

    def test_derives_tls_url_from_upstash_rest_pair(self):
        s = self._settings(
            UPSTASH_REDIS_REST_URL="https://fine-werewolf-1558.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="sometoken",
        )
        assert s.redis_url == "rediss://default:sometoken@fine-werewolf-1558.upstash.io:6379"

    def test_token_is_percent_encoded(self):
        # Upstash tokens are base64 and routinely contain '=' and '+', which
        # would otherwise corrupt the URL's userinfo section.
        token = "abc+/=="
        s = self._settings(
            UPSTASH_REDIS_REST_URL="https://x.upstash.io",
            UPSTASH_REDIS_REST_TOKEN=token,
        )
        parsed = urlparse(s.redis_url)
        assert parsed.scheme == "rediss"
        assert parsed.port == 6379
        assert "+" not in parsed.netloc.split(":")[1]
        assert unquote(parsed.password) == token

    def test_requires_both_halves_of_the_pair(self):
        assert self._settings(UPSTASH_REDIS_REST_URL="https://x.upstash.io").redis_url == (
            "redis://localhost:6379/0"
        )
        assert self._settings(UPSTASH_REDIS_REST_TOKEN="tok").redis_url == (
            "redis://localhost:6379/0"
        )

    def test_explicit_broker_url_wins_over_upstash(self):
        s = self._settings(
            UPSTASH_REDIS_REST_URL="https://x.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="tok",
            CELERY_BROKER_URL="redis://explicit:6379/2",
        )
        assert s.broker_url == "redis://explicit:6379/2"
        # ...but the result backend still derives from Upstash.
        assert s.result_backend.startswith("rediss://")

    def test_broker_and_backend_default_to_resolved_redis_url(self):
        s = self._settings(
            UPSTASH_REDIS_REST_URL="https://x.upstash.io",
            UPSTASH_REDIS_REST_TOKEN="tok",
        )
        assert s.broker_url == s.redis_url == s.result_backend


class TestVectorConfiguredFlag:
    def test_false_when_unset(self):
        assert Settings(UPSTASH_VECTOR_REST_URL=None, UPSTASH_VECTOR_REST_TOKEN=None).upstash_vector_configured is False

    def test_false_when_only_one_half_present(self):
        assert Settings(
            UPSTASH_VECTOR_REST_URL="https://x.upstash.io",
            UPSTASH_VECTOR_REST_TOKEN=None,
        ).upstash_vector_configured is False

    def test_true_when_both_present(self):
        assert Settings(
            UPSTASH_VECTOR_REST_URL="https://x.upstash.io",
            UPSTASH_VECTOR_REST_TOKEN="tok",
        ).upstash_vector_configured is True


class TestCsvSettingsParsing:
    """A `.env` file supplies plain CSV; pydantic-settings would JSON-decode it."""

    def test_accepts_comma_separated_string(self):
        s = Settings(CORS_ORIGINS="http://a.test, http://b.test")
        assert s.CORS_ORIGINS == ["http://a.test", "http://b.test"]

    def test_accepts_json_array(self):
        assert Settings(CORS_ORIGINS='["http://a.test"]').CORS_ORIGINS == ["http://a.test"]

    def test_accepts_native_list(self):
        assert Settings(CORS_ORIGINS=["http://a.test"]).CORS_ORIGINS == ["http://a.test"]

    def test_analyzers_csv(self):
        assert Settings(ENABLED_ANALYZERS="diff, security").ENABLED_ANALYZERS == [
            "diff",
            "security",
        ]


@pytest.mark.parametrize(
    "repository_id",
    ["1", "42", "acme-corp", "a_b-c"],
)
def test_namespace_is_url_path_safe(repository_id: str):
    namespace = _namespace(repository_id)
    assert "/" not in namespace and ".." not in namespace
    assert namespace.startswith("repo_")
