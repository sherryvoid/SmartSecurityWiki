# READ SUMMARY: This module filters source files, detects language by extension, reads files, and builds file trees.
# CHANGED: Added Kotlin, C#, and Rust as relevant source extensions for endpoint/security chunking.
from pathlib import Path
INCLUDE_EXTENSIONS = {
    ".java",
    ".go",
    ".cpp",
    ".c",
    ".h",
    ".aidl",
    ".py",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".mjs",
    ".cjs",
    ".xml",
    ".json",
    ".yaml",
    ".yml",
    ".te",
    ".kt",
    ".cs",
    ".rs",
    ".md",
    ".txt",
    ".rules",
}

IGNORE_FOLDERS = {
    ".git",
    "node_modules",
    "build",
    "dist",
    "out",
    "target",
    ".idea",
    ".vscode",
    ".venv",
    "venv",
    "env",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "site-packages",
    ".tox",
    ".nox",
    ".next",
    "coverage",
    ".turbo",
    ".parcel-cache",
}
MAX_FILE_BYTES = 1_000_000


def language_for_path(path: str) -> str:
    suffix = Path(path).suffix.lower()
    return {
        ".java": "java",
        ".go": "go",
        ".cpp": "cpp",
        ".c": "c",
        ".h": "c",
        ".aidl": "aidl",
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".mjs": "javascript",
        ".cjs": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".xml": "xml",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".te": "selinux",
        ".kt": "kotlin",
        ".cs": "csharp",
        ".rs": "rust",
        ".md": "markdown",
        ".txt": "text",
        ".rules": "text",
    }.get(suffix, "text")


def is_relevant_file(path: Path) -> bool:
    if any(part in IGNORE_FOLDERS for part in path.parts):
        return False
    if path.suffix.lower() not in INCLUDE_EXTENSIONS:
        return False
    try:
        return path.stat().st_size <= MAX_FILE_BYTES
    except OSError:
        return False


def safe_relative_path(root: Path, requested_path: str) -> Path:
    candidate = (root / requested_path).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        raise ValueError("Path escapes project repository")
    return candidate


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def build_file_tree(paths: list[str]) -> list[dict]:
    root: dict = {"name": "", "path": "", "type": "directory", "children": {}}
    for file_path in sorted(paths):
        cursor = root
        parts = file_path.replace("\\", "/").split("/")
        accumulated: list[str] = []
        for index, part in enumerate(parts):
            accumulated.append(part)
            node_path = "/".join(accumulated)
            is_file = index == len(parts) - 1
            children = cursor["children"]
            if part not in children:
                children[part] = {
                    "name": part,
                    "path": node_path,
                    "type": "file" if is_file else "directory",
                    "children": {},
                }
            cursor = children[part]

    def compact(node: dict) -> dict:
        children = node.get("children", {})
        result = {key: value for key, value in node.items() if key != "children"}
        if children:
            result["children"] = [compact(child) for child in children.values()]
        return result

    return [compact(child) for child in root["children"].values()]
