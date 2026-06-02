import ast as python_ast
import hashlib
import logging
import re
from dataclasses import dataclass

from app.services.security_detection import detect_security_tags


logger = logging.getLogger(__name__)

SYMBOL_PATTERN = re.compile(
    r"^\s*(?:public|private|protected|static|final|synchronized|native|abstract|\s)+"
    r"[\w<>\[\], ?]+\s+(?P<name>[A-Za-z_][\w]*)\s*\([^;]*\)\s*(?:throws [^{]+)?\{?\s*$"
)
CLASS_PATTERN = re.compile(r"^\s*(?:public|private|protected|abstract|final|\s)*(?:class|interface|enum)\s+(?P<name>[A-Za-z_][\w]*)")
GO_FUNCTION_PATTERN = re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?(?P<name>[A-Za-z_][\w]*)\s*\(")
PY_CLASS_PATTERN = re.compile(r"^(?P<indent>\s*)class\s+(?P<name>[A-Za-z_][\w]*)\s*(?:\([^)]*\))?\s*:")
PY_FUNCTION_PATTERN = re.compile(r"^(?P<indent>\s*)(?P<async>async\s+)?def\s+(?P<name>[A-Za-z_][\w]*)\s*\(")
JS_FUNCTION_PATTERN = re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+(?P<name>[A-Za-z_$][\w$]*)\s*\(")
JS_CLASS_PATTERN = re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+(?P<name>[A-Za-z_$][\w$]*)")
JS_ARROW_PATTERN = re.compile(
    r"^\s*(?:export\s+)?(?:const|let|var)\s+(?P<name>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"
)
JS_ROUTE_PATTERN = re.compile(r"^\s*(?P<router>app|router)\.(?P<method>get|post|put|patch|delete|use)\s*\(")
NEST_DECORATOR_PATTERN = re.compile(r"^\s*@(Controller|Get|Post|Put|Patch|Delete|UseGuards)\b")


@dataclass
class Chunk:
    chunk_id: str
    file_path: str
    language: str
    class_name: str | None
    symbol: str | None
    start_line: int
    end_line: int
    content: str
    chunk_type: str
    tags: list[str]

    @property
    def symbol_name(self) -> str | None:
        return self.symbol

    @property
    def code(self) -> str:
        return self.content

    @property
    def security_tags(self) -> list[str]:
        return self.tags

    def __getitem__(self, key: str):
        return self.as_dict()[key]

    def as_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "file_path": self.file_path,
            "language": self.language,
            "class_name": self.class_name,
            "symbol": self.symbol,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "content": self.content,
            "chunk_type": self.chunk_type,
            "tags": self.tags,
        }


def chunk_source(file_path: str, language: str, code: str) -> list[Chunk]:
    if language == "markdown":
        return _chunk_markdown(file_path, code)
    if language == "python":
        chunks = _chunk_python_ast(file_path, language, code)
        if chunks:
            return chunks
        chunks = _chunk_python(file_path, language, code)
        if chunks:
            return chunks
    if language in {"javascript", "typescript"}:
        chunks = _chunk_javascript(file_path, language, code)
        if chunks:
            return chunks
    if language == "java":
        chunks = _chunk_java_tree_sitter(file_path, language, code)
        if chunks:
            return chunks
        chunks = _chunk_symbols(file_path, language, code)
        if chunks:
            return chunks
    if language == "go":
        chunks = _chunk_go_tree_sitter(file_path, language, code)
        if chunks:
            return chunks
        chunks = _chunk_symbols(file_path, language, code)
        if chunks:
            return chunks
    if language in {"cpp", "c"}:
        chunks = _chunk_symbols(file_path, language, code)
        if chunks:
            return chunks
    return _fallback_chunks(file_path, code, language)


def _chunk_symbols(file_path: str, language: str, code: str) -> list[Chunk]:
    lines = code.splitlines()
    chunks: list[Chunk] = []
    current_class: str | None = None
    for index, line in enumerate(lines, start=1):
        class_match = CLASS_PATTERN.match(line)
        if class_match:
            current_class = class_match.group("name")
            chunks.append(_bounded_chunk(file_path, language, "class", current_class, current_class, index, lines))
            continue
        match = GO_FUNCTION_PATTERN.match(line) if language == "go" else SYMBOL_PATTERN.match(line)
        if match:
            chunks.append(_bounded_chunk(file_path, language, "function" if language == "go" else "method", match.group("name"), current_class, index, lines))
    return _dedupe_chunks(chunks)


def _chunk_python_ast(file_path: str, language: str, code: str) -> list[Chunk]:
    try:
        tree = python_ast.parse(code)
    except SyntaxError as exc:
        logger.warning("Python AST parse failed for %s; falling back to regex parser: %s", file_path, exc)
        return []

    lines = code.splitlines()
    class Visitor(python_ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[str] = []
            self.chunks: list[Chunk] = []

        def visit_ClassDef(self, node: python_ast.ClassDef) -> None:
            self.chunks.append(_line_chunk(file_path, language, "class", node.name, node.name, node.lineno, getattr(node, "end_lineno", node.lineno), lines))
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        def visit_FunctionDef(self, node: python_ast.FunctionDef) -> None:
            class_name = self.stack[-1] if self.stack else None
            self.chunks.append(_line_chunk(file_path, language, "function", node.name, class_name, node.lineno, getattr(node, "end_lineno", node.lineno), lines))
            self.generic_visit(node)

        def visit_AsyncFunctionDef(self, node: python_ast.AsyncFunctionDef) -> None:
            class_name = self.stack[-1] if self.stack else None
            self.chunks.append(_line_chunk(file_path, language, "async_function", node.name, class_name, node.lineno, getattr(node, "end_lineno", node.lineno), lines))
            self.generic_visit(node)

    visitor = Visitor()
    visitor.visit(tree)
    return _dedupe_chunks(visitor.chunks)


def _chunk_python(file_path: str, language: str, code: str) -> list[Chunk]:
    lines = code.splitlines()
    chunks: list[Chunk] = []
    current_class: str | None = None
    for index, line in enumerate(lines, start=1):
        class_match = PY_CLASS_PATTERN.match(line)
        if class_match:
            current_class = class_match.group("name")
            chunks.append(_python_block_chunk(file_path, language, "class", current_class, current_class, index, lines, len(class_match.group("indent"))))
            continue
        function_match = PY_FUNCTION_PATTERN.match(line)
        if function_match:
            chunk_type = "async_function" if function_match.group("async") else "function"
            chunks.append(_python_block_chunk(file_path, language, chunk_type, function_match.group("name"), current_class, index, lines, len(function_match.group("indent"))))
    return _dedupe_chunks(chunks)


def _python_block_chunk(file_path: str, language: str, chunk_type: str, symbol: str | None, class_name: str | None, start: int, lines: list[str], indent: int) -> Chunk:
    end = start
    for line_number in range(start + 1, len(lines) + 1):
        line = lines[line_number - 1]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            end = line_number
            continue
        current_indent = len(line) - len(line.lstrip(" "))
        if current_indent <= indent:
            break
        end = line_number
    snippet = "\n".join(lines[start - 1 : end])
    return _make_chunk(file_path, language, chunk_type, symbol, class_name, start, end, snippet)


def _chunk_javascript(file_path: str, language: str, code: str) -> list[Chunk]:
    lines = code.splitlines()
    chunks: list[Chunk] = []
    pending_decorator: tuple[int, str] | None = None
    for index, line in enumerate(lines, start=1):
        decorator_match = NEST_DECORATOR_PATTERN.match(line)
        if decorator_match:
            pending_decorator = (index, decorator_match.group(1))
            chunks.append(_bounded_chunk(file_path, language, "decorator", decorator_match.group(1), None, index, lines))
            continue
        class_match = JS_CLASS_PATTERN.match(line)
        if class_match:
            chunks.append(_bounded_chunk(file_path, language, "class", class_match.group("name"), class_match.group("name"), index, lines))
            continue
        function_match = JS_FUNCTION_PATTERN.match(line)
        if function_match:
            start = pending_decorator[0] if pending_decorator else index
            chunks.append(_bounded_chunk(file_path, language, "function", function_match.group("name"), None, start, lines))
            pending_decorator = None
            continue
        arrow_match = JS_ARROW_PATTERN.match(line)
        if arrow_match:
            start = pending_decorator[0] if pending_decorator else index
            chunks.append(_bounded_chunk(file_path, language, "function", arrow_match.group("name"), None, start, lines))
            pending_decorator = None
            continue
        route_match = JS_ROUTE_PATTERN.match(line)
        if route_match:
            symbol = f"{route_match.group('router')}.{route_match.group('method')}"
            chunks.append(_bounded_chunk(file_path, language, "route_handler", symbol, None, index, lines))
    return _dedupe_chunks(chunks)


def _bounded_chunk(file_path: str, language: str, chunk_type: str, symbol: str | None, class_name: str | None, start: int, lines: list[str]) -> Chunk:
    brace_balance = 0
    seen_brace = False
    end = min(len(lines), start + 80)
    for line_number in range(start, len(lines) + 1):
        line = lines[line_number - 1]
        brace_balance += line.count("{") - line.count("}")
        if "{" in line:
            seen_brace = True
        if seen_brace and brace_balance <= 0 and line_number > start:
            end = line_number
            break
    snippet = "\n".join(lines[start - 1 : end])
    return _make_chunk(file_path, language, chunk_type, symbol, class_name, start, end, snippet)


def _fallback_chunks(file_path: str, code: str, language: str = "unknown", size: int = 80) -> list[Chunk]:
    lines = code.splitlines()
    chunks: list[Chunk] = []
    for start in range(1, len(lines) + 1, size):
        end = min(start + size - 1, len(lines))
        snippet = "\n".join(lines[start - 1 : end])
        chunks.append(_make_chunk(file_path, language, "line_range_fallback", None, None, start, end, snippet))
    return chunks or [_make_chunk(file_path, language, "line_range_fallback", None, None, 1, 1, "")]


def _chunk_markdown(file_path: str, code: str) -> list[Chunk]:
    lines = code.splitlines()
    headings = [index for index, line in enumerate(lines, start=1) if line.startswith("#")]
    if not headings:
        return _fallback_chunks(file_path, code, "markdown")
    chunks: list[Chunk] = []
    for offset, start in enumerate(headings):
        end = headings[offset + 1] - 1 if offset + 1 < len(headings) else len(lines)
        title = lines[start - 1].lstrip("#").strip() or "markdown_section"
        snippet = "\n".join(lines[start - 1 : end])
        chunks.append(_make_chunk(file_path, "markdown", "markdown_section", title, None, start, end, snippet))
    return chunks


def _dedupe_chunks(chunks: list[Chunk]) -> list[Chunk]:
    seen: set[tuple[int, int, str | None]] = set()
    result: list[Chunk] = []
    for chunk in chunks:
        key = (chunk.start_line, chunk.end_line, chunk.symbol_name)
        if key not in seen:
            seen.add(key)
            result.append(chunk)
    return result


def _chunk_java_tree_sitter(file_path: str, language: str, code: str) -> list[Chunk]:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_java as tsjava

        parser = Parser()
        parser.language = Language(tsjava.language())
        source_bytes = code.encode("utf-8")
        tree = parser.parse(source_bytes)
        chunks: list[Chunk] = []
        for node in _walk_tree_sitter(tree.root_node):
            if node.type not in {"method_declaration", "constructor_declaration"}:
                continue
            name = _tree_sitter_node_text(_named_child(node, "identifier"), source_bytes)
            if not name:
                name = _tree_sitter_node_text(node.child_by_field_name("name"), source_bytes)
            class_name = _java_containing_class_name(node, source_bytes)
            chunks.append(_node_chunk(file_path, language, "method", name or "<anonymous>", class_name, node, source_bytes))
        return _dedupe_chunks(chunks)
    except Exception as exc:
        logger.warning("Tree-sitter Java parse failed for %s; falling back to regex parser: %s", file_path, exc)
        return []


def _chunk_go_tree_sitter(file_path: str, language: str, code: str) -> list[Chunk]:
    try:
        from tree_sitter import Language, Parser
        import tree_sitter_go as tsgo

        parser = Parser()
        parser.language = Language(tsgo.language())
        source_bytes = code.encode("utf-8")
        tree = parser.parse(source_bytes)
        chunks: list[Chunk] = []
        for node in _walk_tree_sitter(tree.root_node):
            if node.type not in {"function_declaration", "method_declaration"}:
                continue
            name_node = node.child_by_field_name("name") or _named_child(node, "identifier")
            name = _tree_sitter_node_text(name_node, source_bytes) or "<anonymous>"
            receiver = _go_receiver_name(node, source_bytes) if node.type == "method_declaration" else None
            chunks.append(_node_chunk(file_path, language, "method" if receiver else "function", name, receiver, node, source_bytes))
        return _dedupe_chunks(chunks)
    except Exception as exc:
        logger.warning("Tree-sitter Go parse failed for %s; falling back to regex parser: %s", file_path, exc)
        return []


def _walk_tree_sitter(node):
    yield node
    for child in node.children:
        yield from _walk_tree_sitter(child)


def _named_child(node, node_type: str):
    for child in node.children:
        if child.type == node_type:
            return child
    return None


def _tree_sitter_node_text(node, source_bytes: bytes) -> str | None:
    if node is None:
        return None
    return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _java_containing_class_name(node, source_bytes: bytes) -> str | None:
    current = node.parent
    while current is not None:
        if current.type == "class_declaration":
            name = _tree_sitter_node_text(current.child_by_field_name("name"), source_bytes)
            if name:
                return name
            return _tree_sitter_node_text(_named_child(current, "identifier"), source_bytes)
        current = current.parent
    return None


def _go_receiver_name(node, source_bytes: bytes) -> str | None:
    receiver = node.child_by_field_name("receiver")
    if receiver is None:
        return None
    text = _tree_sitter_node_text(receiver, source_bytes) or ""
    text = text.strip().strip("()").strip()
    if not text:
        return None
    parts = text.replace("*", " ").split()
    return parts[-1] if parts else text


def _node_chunk(file_path: str, language: str, chunk_type: str, symbol: str | None, class_name: str | None, node, source_bytes: bytes) -> Chunk:
    start_line = node.start_point[0] + 1
    end_line = node.end_point[0] + 1
    content = source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
    return _make_chunk(file_path, language, chunk_type, symbol, class_name, start_line, end_line, content)


def _line_chunk(file_path: str, language: str, chunk_type: str, symbol: str | None, class_name: str | None, start_line: int, end_line: int, lines: list[str]) -> Chunk:
    content = "\n".join(lines[start_line - 1 : end_line])
    return _make_chunk(file_path, language, chunk_type, symbol, class_name, start_line, end_line, content)


def _make_chunk(file_path: str, language: str, chunk_type: str, symbol: str | None, class_name: str | None, start_line: int, end_line: int, content: str) -> Chunk:
    return Chunk(
        chunk_id=_chunk_id(file_path, start_line, end_line, symbol),
        file_path=file_path,
        language=language,
        class_name=class_name,
        symbol=symbol,
        start_line=start_line,
        end_line=end_line,
        content=content,
        chunk_type=chunk_type,
        tags=detect_security_tags(content, file_path),
    )


def _chunk_id(file_path: str, start_line: int, end_line: int, symbol: str | None) -> str:
    raw = f"{file_path}:{start_line}:{end_line}:{symbol or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
