"""Review context assembly (retrieval-augmented, budget-aware).

This is the component that makes the reviewer *repository-aware* rather than a
diff-summariser. For every pull request it assembles:

* the deterministic evidence bundle (diff, AST, static, security, dependency,
  test analyzers);
* semantically retrieved repository code that the change interacts with,
  queried per changed symbol rather than once per PR;
* documented repository conventions (``README``/``docs``/``CONTRIBUTING``);
* findings previously reported for the same fingerprints, as *context* only;
* related production incidents when an incident source is configured.

Everything is packed under an explicit character budget with a documented
priority order, so a 4,000-line PR degrades predictably instead of blowing the
context window.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from app.analyzers.diff import DiffAnalysis, FileDiff
from app.analyzers.registry import AnalysisBundle
from app.core.config import settings
from app.core.logging import get_logger
from app.integrations.vector.base import SearchResult, VectorStore
from app.integrations.vector.embeddings import EmbeddingProvider

logger = get_logger(__name__)

DOC_HINTS = (
    "readme",
    "contributing",
    "architecture",
    "conventions",
    "docs/",
    "adr",
    "style-guide",
    "styleguide",
)


@dataclass(slots=True)
class ReviewContext:
    """The assembled evidence bundle handed to the prompt builder."""

    pull_request: dict[str, Any]
    change_summary: dict[str, Any]
    diffs: list[dict[str, Any]] = field(default_factory=list)
    static_findings: list[dict[str, Any]] = field(default_factory=list)
    changed_symbols: list[dict[str, Any]] = field(default_factory=list)
    dependency_graph: list[dict[str, Any]] = field(default_factory=list)
    related_code: list[dict[str, Any]] = field(default_factory=list)
    conventions: list[dict[str, Any]] = field(default_factory=list)
    test_context: dict[str, Any] = field(default_factory=dict)
    historical_findings: list[dict[str, Any]] = field(default_factory=list)
    incidents: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pull_request": self.pull_request,
            "change_summary": self.change_summary,
            "diffs": self.diffs,
            "static_findings": self.static_findings,
            "changed_symbols": self.changed_symbols,
            "dependency_graph": self.dependency_graph,
            "related_code": self.related_code,
            "conventions": self.conventions,
            "test_context": self.test_context,
            "historical_findings": self.historical_findings,
            "incidents": self.incidents,
        }


class ReviewContextBuilder:
    """Builds a :class:`ReviewContext` from analysis output plus retrieval."""

    def __init__(
        self,
        *,
        vector_store: VectorStore | None = None,
        embedder: EmbeddingProvider | None = None,
        char_budget: int | None = None,
    ) -> None:
        self.vector_store = vector_store
        self.embedder = embedder
        self.char_budget = char_budget or settings.CONTEXT_CHAR_BUDGET

    async def build(
        self,
        *,
        pull_request: dict[str, Any],
        repository_id: str,
        diff: DiffAnalysis,
        bundle: AnalysisBundle,
        historical_findings: Sequence[dict[str, Any]] = (),
        incidents: Sequence[dict[str, Any]] = (),
        commit_sha: str | None = None,
    ) -> ReviewContext:
        started = time.perf_counter()
        used = 0

        change_summary = self._change_summary(diff, bundle)
        static_findings = [f.to_dict() for f in bundle.findings]
        changed_symbols = self._changed_symbols(bundle)
        dependency_graph = self._dependency_graph(bundle)
        test_context = self._test_context(bundle)

        diffs, used = self._pack_diffs(diff, used)
        retrieval_stats: dict[str, Any] = {"queries": 0, "hits": 0, "unique_files": 0}
        related_code: list[dict[str, Any]] = []
        conventions: list[dict[str, Any]] = []

        if self.vector_store is not None and self.embedder is not None:
            related_code, conventions, retrieval_stats, used = await self._retrieve(
                repository_id=repository_id,
                diff=diff,
                changed_symbols=changed_symbols,
                used=used,
                commit_sha=commit_sha,
            )
        else:
            retrieval_stats["skipped"] = "no vector store configured"

        static_findings = static_findings[: settings.CONTEXT_MAX_STATIC_FINDINGS]

        context = ReviewContext(
            pull_request=pull_request,
            change_summary=change_summary,
            diffs=diffs,
            static_findings=static_findings,
            changed_symbols=changed_symbols[: settings.CONTEXT_MAX_SYMBOLS],
            dependency_graph=dependency_graph[:40],
            related_code=related_code,
            conventions=conventions,
            test_context=test_context,
            historical_findings=list(historical_findings)[:15],
            incidents=list(incidents)[:10],
        )
        context.stats = {
            "char_budget": self.char_budget,
            "chars_used": used,
            "diff_files_included": len(diffs),
            "diff_files_total": len(diff.files),
            "diff_truncated": diff.truncated,
            "dropped_files": diff.dropped_files[:20],
            "static_findings": len(static_findings),
            "changed_symbols": len(context.changed_symbols),
            "retrieved_chunks": len(related_code),
            "convention_docs": len(conventions),
            "historical_findings": len(context.historical_findings),
            "incidents": len(context.incidents),
            "retrieval": retrieval_stats,
            "build_ms": int((time.perf_counter() - started) * 1000),
        }
        logger.info("context.built", **{k: v for k, v in context.stats.items() if k != "retrieval"})
        return context

    # ---------------------------------------------------------------- sections
    @staticmethod
    def _change_summary(diff: DiffAnalysis, bundle: AnalysisBundle) -> dict[str, Any]:
        diff_data = bundle.data("diff")
        return {
            "files_changed": len(diff.files),
            "additions": diff.total_additions,
            "deletions": diff.total_deletions,
            "languages": sorted({str(f.language) for f in diff.files if str(f.language)}),
            "risk_signals": sorted(diff.aggregate_signals.keys()),
            "signal_counts": diff.aggregate_signals,
            "test_files_changed": [f.path for f in diff.test_files],
            "generated_files": [f.path for f in diff.files if f.is_generated],
            "binary_files": [f.path for f in diff.files if f.is_binary],
            "truncated": diff.truncated,
            "dropped_files": diff.dropped_files[:20],
            "change_types": diff_data.get("change_types", {}),
        }

    @staticmethod
    def _changed_symbols(bundle: AnalysisBundle) -> list[dict[str, Any]]:
        data = bundle.data("ast")
        symbols = data.get("changed_symbols") or []
        return [s for s in symbols if isinstance(s, dict)]

    @staticmethod
    def _dependency_graph(bundle: AnalysisBundle) -> list[dict[str, Any]]:
        data = bundle.data("dependency")
        graph = data.get("blast_radius") or []
        return [g for g in graph if isinstance(g, dict)]

    @staticmethod
    def _test_context(bundle: AnalysisBundle) -> dict[str, Any]:
        data = dict(bundle.data("test"))
        data.pop("_asts", None)
        return data

    def _pack_diffs(self, diff: DiffAnalysis, used: int) -> tuple[list[dict[str, Any]], int]:
        """Include diffs first: without the changed code there is no review."""
        budget = int(self.char_budget * settings.CONTEXT_DIFF_SHARE)
        packed: list[dict[str, Any]] = []
        ordered = self._priority_order(diff.files)
        for file_diff in ordered:
            if file_diff.is_binary:
                continue
            patch = file_diff.to_text(max_lines_per_hunk=settings.CONTEXT_MAX_HUNK_LINES)
            if not patch.strip():
                continue
            if used + len(patch) > budget and packed:
                break
            packed.append(
                {
                    "path": file_diff.path,
                    "change_type": file_diff.change_type,
                    "language": str(file_diff.language),
                    "additions": file_diff.additions,
                    "deletions": file_diff.deletions,
                    "is_test": file_diff.is_test,
                    "patch": patch,
                }
            )
            used += len(patch)
        return packed, used

    @staticmethod
    def _priority_order(files: list[FileDiff]) -> list[FileDiff]:
        def rank(file_diff: FileDiff) -> tuple[int, int]:
            if file_diff.is_generated:
                tier = 3
            elif file_diff.is_test:
                tier = 2
            elif file_diff.signals:
                tier = 0
            else:
                tier = 1
            return (tier, -(file_diff.additions + file_diff.deletions))

        return sorted(files, key=rank)

    # --------------------------------------------------------------- retrieval
    async def _retrieve(
        self,
        *,
        repository_id: str,
        diff: DiffAnalysis,
        changed_symbols: list[dict[str, Any]],
        used: int,
        commit_sha: str | None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], int]:
        assert self.vector_store is not None and self.embedder is not None
        budget = int(self.char_budget * settings.CONTEXT_RETRIEVAL_SHARE)
        changed_files = [f.path for f in diff.files]
        queries = self._build_queries(diff, changed_symbols)
        stats: dict[str, Any] = {"queries": len(queries), "hits": 0, "unique_files": 0}

        if not queries:
            return [], [], stats, used

        try:
            vectors = await self.embedder.embed([q["text"] for q in queries])
        except Exception as exc:
            logger.warning("context.embedding_failed", error=str(exc))
            stats["error"] = f"embedding failed: {exc}"
            return [], [], stats, used

        merged: dict[str, tuple[SearchResult, str]] = {}
        for query, vector in zip(queries, vectors, strict=False):
            try:
                results = await self.vector_store.search(
                    repository_id,
                    vector,
                    limit=settings.RETRIEVAL_TOP_K,
                    exclude_files=changed_files,
                )
            except Exception as exc:
                logger.warning("context.search_failed", error=str(exc), query=query["label"])
                stats["error"] = f"search failed: {exc}"
                continue
            for result in results:
                if result.score < settings.RETRIEVAL_MIN_SCORE:
                    continue
                key = result.chunk.chunk_id
                existing = merged.get(key)
                if existing is None or result.score > existing[0].score:
                    merged[key] = (result, query["label"])

        stats["hits"] = len(merged)
        ranked = sorted(merged.values(), key=lambda item: item[0].score, reverse=True)

        related: list[dict[str, Any]] = []
        conventions: list[dict[str, Any]] = []
        seen_files: set[str] = set()
        per_file: dict[str, int] = {}
        spent = 0

        for result, label in ranked:
            chunk = result.chunk
            body = chunk.content
            if spent + len(body) > budget:
                continue
            is_doc = chunk.kind == "doc" or any(
                hint in chunk.file_path.lower() for hint in DOC_HINTS
            )
            if is_doc:
                if len(conventions) >= settings.RETRIEVAL_MAX_DOCS:
                    continue
                conventions.append(
                    {
                        "file": chunk.file_path,
                        "symbol": chunk.symbol,
                        "score": round(result.score, 4),
                        "content": body,
                    }
                )
            else:
                if len(related) >= settings.RETRIEVAL_MAX_CHUNKS:
                    continue
                if per_file.get(chunk.file_path, 0) >= settings.RETRIEVAL_MAX_PER_FILE:
                    continue
                per_file[chunk.file_path] = per_file.get(chunk.file_path, 0) + 1
                related.append(
                    {
                        "file": chunk.file_path,
                        "symbol": chunk.symbol,
                        "kind": chunk.kind,
                        "start_line": chunk.start_line,
                        "end_line": chunk.end_line,
                        "score": round(result.score, 4),
                        "matched_query": label,
                        "content": body,
                    }
                )
            seen_files.add(chunk.file_path)
            spent += len(body)

        stats["unique_files"] = len(seen_files)
        stats["chars"] = spent
        return related, conventions, stats, used + spent

    @staticmethod
    def _build_queries(
        diff: DiffAnalysis, changed_symbols: list[dict[str, Any]]
    ) -> list[dict[str, str]]:
        """One retrieval query per changed symbol, plus repository-level queries.

        Querying per symbol matters: a single PR-level query returns whatever is
        globally similar to the diff, whereas per-symbol queries surface the
        callers and helpers of each specific thing that changed.
        """
        queries: list[dict[str, str]] = []
        seen: set[str] = set()

        for symbol in changed_symbols[: settings.RETRIEVAL_MAX_QUERIES]:
            name = str(symbol.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            signature = str(symbol.get("signature") or "")
            doc = str(symbol.get("docstring") or "")[:200]
            queries.append(
                {
                    "label": f"symbol:{name}",
                    "text": f"{name} {signature} {doc} {symbol.get('file', '')}".strip(),
                }
            )

        signals = sorted(diff.aggregate_signals.keys())
        if signals:
            queries.append(
                {
                    "label": "signals",
                    "text": " ".join(signals) + " error handling convention helper utility",
                }
            )
        queries.append(
            {
                "label": "conventions",
                "text": (
                    "repository engineering conventions architecture guidelines contributing "
                    "coding standards required patterns"
                ),
            }
        )
        touched = " ".join(f.path for f in diff.files[:20])
        if touched:
            queries.append({"label": "files", "text": touched})
        return queries
