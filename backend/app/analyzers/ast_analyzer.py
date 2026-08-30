"""AST analysis.

* **Python** -- the standard library ``ast`` module (always available, exact).
* **JavaScript / TypeScript** -- ``tree-sitter`` grammars when installed.

Languages without a parser are reported explicitly as *unsupported* rather than
silently pretending to have been analysed.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any

from app.analyzers.base import (
    AnalysisRequest,
    AnalyzerResult,
    Language,
    RawFinding,
    RepositoryAnalyzer,
)
from app.core.logging import get_logger

logger = get_logger(__name__)

_TS_PARSERS: dict[Language, Any] = {}
_TS_AVAILABLE: bool | None = None


@dataclass(slots=True)
class Symbol:
    name: str
    kind: str  # function | method | class | variable
    start_line: int
    end_line: int
    file_path: str
    signature: str = ""
    parent: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    docstring: str | None = None
    calls: list[str] = field(default_factory=list)
    raises: list[str] = field(default_factory=list)
    complexity: int = 1
    returns_count: int = 0
    has_try: bool = False
    loops: int = 0
    param_names: list[str] = field(default_factory=list)

    @property
    def qualified_name(self) -> str:
        return f"{self.parent}.{self.name}" if self.parent else self.name

    def covers(self, line: int) -> bool:
        return self.start_line <= line <= self.end_line

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "qualified_name": self.qualified_name,
            "kind": self.kind,
            "file": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "signature": self.signature,
            "is_async": self.is_async,
            "decorators": self.decorators,
            "calls": self.calls[:40],
            "raises": self.raises,
            "complexity": self.complexity,
            "has_try": self.has_try,
            "loops": self.loops,
            "docstring": (self.docstring or "")[:400] or None,
            "params": self.param_names,
        }


@dataclass(slots=True)
class FileAST:
    file_path: str
    language: Language
    supported: bool
    symbols: list[Symbol] = field(default_factory=list)
    imports: list[str] = field(default_factory=list)
    import_details: list[dict[str, Any]] = field(default_factory=list)
    module_docstring: str | None = None
    parse_error: str | None = None
    loc: int = 0

    def symbols_covering(self, lines: set[int]) -> list[Symbol]:
        return [s for s in self.symbols if any(s.covers(line) for line in lines)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file_path,
            "language": self.language.value,
            "supported": self.supported,
            "loc": self.loc,
            "imports": self.imports,
            "symbols": [s.to_dict() for s in self.symbols],
            "parse_error": self.parse_error,
        }


# ------------------------------------------------------------------- python
class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.symbols: list[Symbol] = []
        self.imports: list[str] = []
        self.import_details: list[dict[str, Any]] = []
        self._stack: list[str] = []

    # -- imports
    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.imports.append(alias.name)
            self.import_details.append(
                {"module": alias.name, "name": None, "alias": alias.asname, "line": node.lineno}
            )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        prefix = "." * (node.level or 0)
        for alias in node.names:
            full = f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}"
            self.imports.append(full)
            self.import_details.append(
                {
                    "module": f"{prefix}{module}",
                    "name": alias.name,
                    "alias": alias.asname,
                    "line": node.lineno,
                }
            )
        self.generic_visit(node)

    # -- definitions
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        symbol = Symbol(
            name=node.name,
            kind="class",
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            file_path=self.file_path,
            signature=f"class {node.name}({', '.join(_unparse(b) for b in node.bases)})",
            parent=self._stack[-1] if self._stack else None,
            decorators=[_unparse(d) for d in node.decorator_list],
            docstring=ast.get_docstring(node),
        )
        self.symbols.append(symbol)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._function(node, is_async=True)

    def _function(self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, is_async: bool) -> None:
        params = [arg.arg for arg in node.args.args + node.args.kwonlyargs]
        if node.args.vararg:
            params.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            params.append(f"**{node.args.kwarg.arg}")
        prefix = "async def" if is_async else "def"
        symbol = Symbol(
            name=node.name,
            kind="method" if self._stack else "function",
            start_line=node.lineno,
            end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            file_path=self.file_path,
            signature=f"{prefix} {node.name}({', '.join(params)})",
            parent=self._stack[-1] if self._stack else None,
            decorators=[_unparse(d) for d in node.decorator_list],
            is_async=is_async,
            docstring=ast.get_docstring(node),
            param_names=params,
        )
        _fill_body_metrics(node, symbol)
        self.symbols.append(symbol)
        self._stack.append(node.name)
        self.generic_visit(node)
        self._stack.pop()


def _fill_body_metrics(node: ast.AST, symbol: Symbol) -> None:
    complexity = 1
    for child in ast.walk(node):
        if isinstance(child, ast.If | ast.For | ast.AsyncFor | ast.While | ast.ExceptHandler):
            complexity += 1
        elif isinstance(child, ast.BoolOp):
            complexity += max(0, len(child.values) - 1)
        elif isinstance(child, ast.IfExp | ast.Assert):
            complexity += 1
        if isinstance(child, ast.For | ast.AsyncFor | ast.While):
            symbol.loops += 1
        if isinstance(child, ast.Try):
            symbol.has_try = True
        if isinstance(child, ast.Return):
            symbol.returns_count += 1
        if isinstance(child, ast.Raise) and child.exc is not None:
            symbol.raises.append(_unparse(child.exc).split("(")[0])
        if isinstance(child, ast.Call):
            name = _unparse(child.func)
            if name and name not in symbol.calls:
                symbol.calls.append(name)
    symbol.complexity = complexity


def _unparse(node: ast.AST | None) -> str:
    if node is None:
        return ""
    try:
        return ast.unparse(node)
    except Exception:  # pragma: no cover - defensive
        return getattr(node, "id", "") or ""


def parse_python(file_path: str, source: str) -> FileAST:
    result = FileAST(
        file_path=file_path,
        language=Language.PYTHON,
        supported=True,
        loc=source.count("\n") + 1,
    )
    try:
        tree = ast.parse(source, filename=file_path)
    except SyntaxError as exc:
        result.parse_error = f"SyntaxError: {exc.msg} (line {exc.lineno})"
        return result
    visitor = _PythonVisitor(file_path)
    visitor.visit(tree)
    result.symbols = visitor.symbols
    result.imports = visitor.imports
    result.import_details = visitor.import_details
    result.module_docstring = ast.get_docstring(tree)
    return result


# -------------------------------------------------------------- tree-sitter
def _load_tree_sitter() -> bool:
    global _TS_AVAILABLE
    if _TS_AVAILABLE is not None:
        return _TS_AVAILABLE
    try:
        import tree_sitter  # noqa: F401
        import tree_sitter_javascript as tsjs
        import tree_sitter_typescript as tsts
        from tree_sitter import Language as TSLanguage
        from tree_sitter import Parser

        js = Parser(TSLanguage(tsjs.language()))
        ts = Parser(TSLanguage(tsts.language_typescript()))
        tsx = Parser(TSLanguage(tsts.language_tsx()))
        _TS_PARSERS[Language.JAVASCRIPT] = js
        _TS_PARSERS[Language.TYPESCRIPT] = ts
        _TS_PARSERS["tsx"] = tsx  # type: ignore[index]
        _TS_AVAILABLE = True
    except Exception as exc:  # pragma: no cover - environment dependent
        logger.warning("tree_sitter_unavailable", error=str(exc))
        _TS_AVAILABLE = False
    return _TS_AVAILABLE


_JS_FUNCTION_NODES = {
    "function_declaration",
    "generator_function_declaration",
    "method_definition",
    "function_expression",
    "arrow_function",
}
_JS_CLASS_NODES = {"class_declaration", "class"}


def parse_javascript(file_path: str, source: str, language: Language) -> FileAST:
    result = FileAST(
        file_path=file_path,
        language=language,
        supported=False,
        loc=source.count("\n") + 1,
    )
    if not _load_tree_sitter():
        result.parse_error = (
            "tree-sitter grammars are not installed; JavaScript/TypeScript AST analysis "
            "is unavailable in this environment."
        )
        return result

    key: Any = language
    if file_path.endswith((".tsx", ".jsx")):
        key = "tsx"
    parser = _TS_PARSERS.get(key) or _TS_PARSERS.get(language)
    if parser is None:
        result.parse_error = f"No tree-sitter parser for {language.value}"
        return result

    data = source.encode("utf-8")
    tree = parser.parse(data)
    result.supported = True

    def text(node: Any) -> str:
        return data[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def child_field(node: Any, field_name: str) -> Any:
        try:
            return node.child_by_field_name(field_name)
        except Exception:  # pragma: no cover
            return None

    def node_name(node: Any) -> str:
        name_node = child_field(node, "name")
        if name_node is not None:
            return text(name_node)
        if node.type in {"function_expression", "arrow_function"}:
            parent = node.parent
            if parent is not None and parent.type == "variable_declarator":
                ident = child_field(parent, "name")
                if ident is not None:
                    return text(ident)
        return "<anonymous>"

    stack: list[tuple[Any, str | None]] = [(tree.root_node, None)]
    while stack:
        node, parent_name = stack.pop()
        for child in reversed(node.children):
            next_parent = parent_name
            if node.type in _JS_CLASS_NODES:
                next_parent = node_name(node)
            stack.append((child, next_parent))

        if node.type in {"import_statement", "import_declaration"}:
            source_node = child_field(node, "source")
            module = text(source_node).strip("'\"") if source_node is not None else text(node)
            result.imports.append(module)
            result.import_details.append(
                {"module": module, "name": None, "alias": None, "line": node.start_point[0] + 1}
            )
            continue

        if node.type == "call_expression":
            fn = child_field(node, "function")
            if fn is not None and text(fn) == "require":
                args = child_field(node, "arguments")
                if args is not None:
                    module = text(args).strip("()").strip("'\" ")
                    if module:
                        result.imports.append(module)
            continue

        if node.type in _JS_CLASS_NODES:
            name = node_name(node)
            result.symbols.append(
                Symbol(
                    name=name,
                    kind="class",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    file_path=file_path,
                    signature=f"class {name}",
                    parent=parent_name,
                )
            )
            continue

        if node.type in _JS_FUNCTION_NODES:
            name = node_name(node)
            params_node = child_field(node, "parameters")
            params_text = text(params_node) if params_node is not None else "()"
            body = text(node)
            symbol = Symbol(
                name=name,
                kind="method" if node.type == "method_definition" else "function",
                start_line=node.start_point[0] + 1,
                end_line=node.end_point[0] + 1,
                file_path=file_path,
                signature=f"{name}{params_text}",
                parent=parent_name,
                is_async="async" in text(node)[:24],
                param_names=[
                    p.strip()
                    for p in params_text.strip("()").split(",")
                    if p.strip()
                ],
            )
            symbol.complexity = 1 + sum(
                body.count(token)
                for token in (" if ", " for ", " while ", " catch", " && ", " || ", " ? ")
            )
            symbol.loops = body.count("for (") + body.count("while (")
            symbol.has_try = "try {" in body
            symbol.returns_count = body.count("return ")
            result.symbols.append(symbol)

    result.symbols.sort(key=lambda s: (s.start_line, s.name))
    return result


# ------------------------------------------------------------------ analyzer
def parse_source(file_path: str, source: str, language: Language) -> FileAST:
    if language == Language.PYTHON:
        return parse_python(file_path, source)
    if language in (Language.JAVASCRIPT, Language.TYPESCRIPT):
        return parse_javascript(file_path, source, language)
    return FileAST(
        file_path=file_path,
        language=language,
        supported=False,
        parse_error=f"AST analysis is not supported for '{language.value}' files.",
        loc=source.count("\n") + 1,
    )


class ASTAnalyzer(RepositoryAnalyzer):
    """Maps changed lines onto the symbols that contain them."""

    name = "ast"

    #: Functions above this cyclomatic complexity are flagged deterministically.
    complexity_threshold = 15

    def analyze(self, request: AnalysisRequest) -> AnalyzerResult:
        files: list[dict[str, Any]] = []
        changed_symbols: list[dict[str, Any]] = []
        unsupported: list[str] = []
        parse_errors: list[dict[str, str]] = []
        findings: list[RawFinding] = []
        asts: dict[str, FileAST] = {}

        for file_diff in request.diff.files:
            if file_diff.is_binary or file_diff.change_type == "removed":
                continue
            source = request.content_for(file_diff.path)
            if source is None:
                continue
            file_ast = parse_source(file_diff.path, source, file_diff.language)
            asts[file_diff.path] = file_ast
            files.append(file_ast.to_dict())

            if not file_ast.supported:
                unsupported.append(file_diff.path)
                continue
            if file_ast.parse_error:
                parse_errors.append({"file": file_diff.path, "error": file_ast.parse_error})
                continue

            touched = file_ast.symbols_covering(file_diff.added_line_numbers)
            for symbol in touched:
                changed_symbols.append(
                    {
                        **symbol.to_dict(),
                        "change_type": file_diff.change_type,
                        "is_test": file_diff.is_test,
                    }
                )
                if (
                    symbol.kind in ("function", "method")
                    and symbol.complexity > self.complexity_threshold
                    and not file_diff.is_test
                ):
                    findings.append(
                        RawFinding(
                            file_path=file_diff.path,
                            line_number=symbol.start_line,
                            severity="LOW",
                            category="maintainability",
                            title=f"High cyclomatic complexity in {symbol.qualified_name}",
                            message=(
                                f"`{symbol.qualified_name}` has an estimated cyclomatic complexity "
                                f"of {symbol.complexity} (threshold {self.complexity_threshold})."
                            ),
                            tool="ast",
                            rule_id="ast/complexity",
                            evidence=f"{file_diff.path}:{symbol.start_line} {symbol.signature}",
                            confidence=0.95,
                        )
                    )

        data: dict[str, Any] = {
            "files": files,
            "changed_symbols": changed_symbols,
            "unsupported_files": unsupported,
            "parse_errors": parse_errors,
            "tree_sitter_available": _load_tree_sitter(),
            "symbol_count": sum(len(a.symbols) for a in asts.values()),
        }
        warnings = []
        if unsupported:
            warnings.append(
                f"{len(unsupported)} file(s) have no AST support and were not structurally analysed."
            )
        for err in parse_errors:
            warnings.append(f"{err['file']}: {err['error']}")

        result = AnalyzerResult(name=self.name, data=data, warnings=warnings, findings=findings)
        # Expose parsed ASTs to downstream analyzers without serialising them.
        result.data["_asts"] = asts
        return result
