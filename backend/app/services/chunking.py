"""Semantic chunking for repository indexing.

Rather than splitting files into fixed token windows, code is chunked along its
own structure -- one chunk per function, method or class -- so a retrieved chunk
is always a complete, meaningful unit. Markdown is chunked by heading section and
anything without a parser falls back to bounded line windows.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable

from app.analyzers.ast_analyzer import parse_source
from app.analyzers.base import detect_language
from app.core.enums import Language
from app.integrations.vector.base import CodeChunk

MAX_CHUNK_CHARS = 6000
MIN_CHUNK_CHARS = 40
FALLBACK_WINDOW_LINES = 80

_HEADING_RE = re.compile(r"^(#{1,4})\s+(.*)$")

#: Paths that are never indexed regardless of configuration.
DEFAULT_EXCLUDES = (
    ".git/",
    "node_modules/",
    "venv/",
    ".venv/",
    "__pycache__/",
    "dist/",
    "build/",
    "coverage/",
    ".next/",
    ".mypy_cache/",
    ".pytest_cache/",
    "vendor/",
    "site-packages/",
)

BINARY_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip", ".gz", ".tar",
    ".whl", ".so", ".dll", ".dylib", ".exe", ".bin", ".woff", ".woff2",
    ".ttf", ".eot", ".mp4", ".mp3", ".webp", ".class", ".jar", ".pyc",
)

LOCK_FILES = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Pipfile.lock", "Cargo.lock", "composer.lock", "go.sum",
)

INDEXABLE_LANGUAGES = {
    Language.PYTHON,
    Language.JAVASCRIPT,
    Language.TYPESCRIPT,
    Language.MARKDOWN,
    Language.SQL,
}


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:32]


def should_index(path: str, size: int, *, extra_excludes: Iterable[str] = ()) -> tuple[bool, str]:
    """Decide whether a repository file is worth embedding."""
    normalised = path.replace("\\", "/")
    lowered = normalised.lower()

    for pattern in DEFAULT_EXCLUDES:
        if pattern in lowered:
            return False, f"excluded path pattern '{pattern}'"
    for pattern in extra_excludes:
        if _glob_match(normalised, pattern):
            return False, f"excluded by repository settings '{pattern}'"
    if lowered.endswith(BINARY_EXTENSIONS):
        return False, "binary file"
    if normalised.rsplit("/", 1)[-1] in LOCK_FILES:
        return False, "dependency lock file"
    if size > 400_000:
        return False, "file larger than 400KB"
    if detect_language(normalised) not in INDEXABLE_LANGUAGES:
        return False, f"language '{detect_language(normalised).value}' is not indexed"
    return True, ""


def _glob_match(path: str, pattern: str) -> bool:
    from fnmatch import fnmatch

    if fnmatch(path, pattern):
        return True
    # Support `docs/**` meaning "everything under docs/".
    if pattern.endswith("/**") and path.startswith(pattern[:-2]):
        return True
    return fnmatch(path, f"*/{pattern.lstrip('/')}")


def chunk_file(
    *,
    repository_id: str,
    commit_sha: str,
    file_path: str,
    content: str,
) -> list[CodeChunk]:
    language = detect_language(file_path)
    if language == Language.MARKDOWN:
        return _chunk_markdown(repository_id, commit_sha, file_path, content)
    if language in (Language.PYTHON, Language.JAVASCRIPT, Language.TYPESCRIPT):
        chunks = _chunk_code(repository_id, commit_sha, file_path, content, language)
        if chunks:
            return chunks
    return _chunk_lines(repository_id, commit_sha, file_path, content, language)


def _make_chunk(
    repository_id: str,
    commit_sha: str,
    file_path: str,
    language: Language,
    symbol: str,
    kind: str,
    start: int,
    end: int,
    body: str,
    metadata: dict | None = None,
) -> CodeChunk | None:
    body = body.strip("\n")
    if len(body.strip()) < MIN_CHUNK_CHARS:
        return None
    if len(body) > MAX_CHUNK_CHARS:
        body = body[:MAX_CHUNK_CHARS] + "\n# ... truncated ..."
    return CodeChunk(
        chunk_id=f"{repository_id}:{file_path}:{start}-{end}:{symbol}",
        repository_id=repository_id,
        commit_sha=commit_sha,
        file_path=file_path,
        language=language.value,
        symbol=symbol,
        kind=kind,
        start_line=start,
        end_line=end,
        content=body,
        content_hash=content_hash(body),
        metadata=metadata or {},
    )


def _chunk_code(
    repository_id: str,
    commit_sha: str,
    file_path: str,
    content: str,
    language: Language,
) -> list[CodeChunk]:
    file_ast = parse_source(file_path, content, language)
    if not file_ast.supported or file_ast.parse_error:
        return []

    lines = content.splitlines()
    is_test = _is_test_path(file_path)
    chunks: list[CodeChunk] = []
    covered: set[int] = set()

    # Prefer the finest meaningful unit: standalone functions and methods.
    for symbol in file_ast.symbols:
        if symbol.kind == "class":
            continue
        start, end = symbol.start_line, min(symbol.end_line, len(lines))
        if start > len(lines):
            continue
        body = "\n".join(lines[start - 1 : end])
        chunk = _make_chunk(
            repository_id,
            commit_sha,
            file_path,
            language,
            symbol.qualified_name,
            "test" if is_test else symbol.kind,
            start,
            end,
            body,
            metadata={
                "signature": symbol.signature,
                "decorators": symbol.decorators,
                "is_async": symbol.is_async,
                "complexity": symbol.complexity,
                "docstring": (symbol.docstring or "")[:300] or None,
            },
        )
        if chunk:
            chunks.append(chunk)
            covered.update(range(start, end + 1))

    # Classes without any captured method still deserve a chunk.
    for symbol in file_ast.symbols:
        if symbol.kind != "class":
            continue
        start, end = symbol.start_line, min(symbol.end_line, len(lines))
        if any(line in covered for line in range(start, end + 1)):
            header_end = min(start + 12, end)
            body = "\n".join(lines[start - 1 : header_end])
        else:
            body = "\n".join(lines[start - 1 : end])
        chunk = _make_chunk(
            repository_id,
            commit_sha,
            file_path,
            language,
            symbol.qualified_name,
            "class",
            start,
            end,
            body,
            metadata={"signature": symbol.signature, "docstring": symbol.docstring},
        )
        if chunk:
            chunks.append(chunk)

    # Module preamble (imports + module docstring) provides conventions context.
    first_symbol_line = min((s.start_line for s in file_ast.symbols), default=len(lines) + 1)
    if first_symbol_line > 3:
        preamble = "\n".join(lines[: first_symbol_line - 1])
        chunk = _make_chunk(
            repository_id,
            commit_sha,
            file_path,
            language,
            f"{file_path}:module",
            "module",
            1,
            max(1, first_symbol_line - 1),
            preamble,
            metadata={"imports": file_ast.imports[:40]},
        )
        if chunk:
            chunks.append(chunk)

    return chunks


def _chunk_markdown(
    repository_id: str, commit_sha: str, file_path: str, content: str
) -> list[CodeChunk]:
    lines = content.splitlines()
    chunks: list[CodeChunk] = []
    current_title = file_path.rsplit("/", 1)[-1]
    start = 1
    buffer: list[str] = []

    def flush(end_line: int) -> None:
        if not buffer:
            return
        chunk = _make_chunk(
            repository_id,
            commit_sha,
            file_path,
            Language.MARKDOWN,
            current_title,
            "doc",
            start,
            end_line,
            "\n".join(buffer),
        )
        if chunk:
            chunks.append(chunk)

    for index, line in enumerate(lines, start=1):
        heading = _HEADING_RE.match(line)
        if heading and buffer:
            flush(index - 1)
            buffer = []
            start = index
        if heading:
            current_title = heading.group(2).strip()
        buffer.append(line)

    flush(len(lines))
    return chunks


def _chunk_lines(
    repository_id: str,
    commit_sha: str,
    file_path: str,
    content: str,
    language: Language,
) -> list[CodeChunk]:
    lines = content.splitlines()
    chunks: list[CodeChunk] = []
    for offset in range(0, len(lines), FALLBACK_WINDOW_LINES):
        window = lines[offset : offset + FALLBACK_WINDOW_LINES]
        chunk = _make_chunk(
            repository_id,
            commit_sha,
            file_path,
            language,
            f"{file_path}:{offset + 1}",
            "module",
            offset + 1,
            offset + len(window),
            "\n".join(window),
        )
        if chunk:
            chunks.append(chunk)
    return chunks


def _is_test_path(path: str) -> bool:
    lowered = path.replace("\\", "/").lower()
    name = lowered.rsplit("/", 1)[-1]
    return (
        "/tests/" in lowered
        or "/test/" in lowered
        or "__tests__" in lowered
        or name.startswith("test_")
        or ".test." in name
        or ".spec." in name
        or name.endswith("_test.py")
    )
