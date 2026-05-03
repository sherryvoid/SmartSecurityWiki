import re
from dataclasses import dataclass

from app.services.security_detection import detect_security_tags


SYMBOL_PATTERN = re.compile(
    r"^\s*(?:public|private|protected|static|final|synchronized|native|abstract|\s)+"
    r"[\w<>\[\], ?]+\s+(?P<name>[A-Za-z_][\w]*)\s*\([^;]*\)\s*(?:throws [^{]+)?\{?\s*$"
)
CLASS_PATTERN = re.compile(r"^\s*(?:public|private|protected|abstract|final|\s)*(?:class|interface|enum)\s+(?P<name>[A-Za-z_][\w]*)")
GO_FUNCTION_PATTERN = re.compile(r"^\s*func\s+(?:\([^)]+\)\s*)?(?P<name>[A-Za-z_][\w]*)\s*\(")


@dataclass
class Chunk:
    chunk_type: str
    symbol_name: str | None
    class_name: str | None
    start_line: int
    end_line: int
    code: str
    security_tags: list[str]


def chunk_source(file_path: str, language: str, code: str) -> list[Chunk]:
    if language == "markdown":
        return _chunk_markdown(file_path, code)
    if language in {"java", "go", "cpp", "c"}:
        chunks = _chunk_symbols(file_path, language, code)
        if chunks:
            return chunks
    return _fallback_chunks(file_path, code)


def _chunk_symbols(file_path: str, language: str, code: str) -> list[Chunk]:
    lines = code.splitlines()
    chunks: list[Chunk] = []
    current_class: str | None = None
    for index, line in enumerate(lines, start=1):
        class_match = CLASS_PATTERN.match(line)
        if class_match:
            current_class = class_match.group("name")
            chunks.append(_bounded_chunk(file_path, "class", current_class, current_class, index, lines))
            continue
        match = GO_FUNCTION_PATTERN.match(line) if language == "go" else SYMBOL_PATTERN.match(line)
        if match:
            chunks.append(_bounded_chunk(file_path, "function" if language == "go" else "method", match.group("name"), current_class, index, lines))
    return _dedupe_chunks(chunks)


def _bounded_chunk(file_path: str, chunk_type: str, symbol: str | None, class_name: str | None, start: int, lines: list[str]) -> Chunk:
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
    return Chunk(chunk_type, symbol, class_name, start, end, snippet, detect_security_tags(snippet, file_path))


def _fallback_chunks(file_path: str, code: str, size: int = 80) -> list[Chunk]:
    lines = code.splitlines()
    chunks: list[Chunk] = []
    for start in range(1, len(lines) + 1, size):
        end = min(start + size - 1, len(lines))
        snippet = "\n".join(lines[start - 1 : end])
        chunks.append(Chunk("line_range_fallback", None, None, start, end, snippet, detect_security_tags(snippet, file_path)))
    return chunks or [Chunk("line_range_fallback", None, None, 1, 1, "", [])]


def _chunk_markdown(file_path: str, code: str) -> list[Chunk]:
    lines = code.splitlines()
    headings = [index for index, line in enumerate(lines, start=1) if line.startswith("#")]
    if not headings:
        return _fallback_chunks(file_path, code)
    chunks: list[Chunk] = []
    for offset, start in enumerate(headings):
        end = headings[offset + 1] - 1 if offset + 1 < len(headings) else len(lines)
        title = lines[start - 1].lstrip("#").strip() or "markdown_section"
        snippet = "\n".join(lines[start - 1 : end])
        chunks.append(Chunk("markdown_section", title, None, start, end, snippet, detect_security_tags(snippet, file_path)))
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
