# READ SUMMARY: This module owns ChromaDB indexing/querying and embedding provider selection for code and wiki chunks.
# CHANGED: Added generic retrieval re-scoring metadata so code evidence outranks prose without re-indexing projects.
import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from app.core.config import get_settings


_logger = logging.getLogger(__name__)
logger = _logger
HASH_DIMENSIONS = 128
_embedding_provider: "BaseEmbeddingProvider | None" = None
_embedding_warning: str | None = None


class BaseEmbeddingProvider:
    name = "base"
    model = ""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    def __call__(self, input: list[str]) -> list[list[float]]:
        return self.embed(input)

    @property
    def label(self) -> str:
        return f"{self.name}:{self.model}" if self.model else self.name


class HashEmbeddingProvider(BaseEmbeddingProvider):
    name = "hash"
    model = "sha256-token-hash"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [hash_embedding(text) for text in texts]


class SentenceTransformerEmbeddingProvider(BaseEmbeddingProvider):
    name = "sentence-transformers"

    def __init__(self, model_name: str):
        self.model = model_name
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name, local_files_only=True)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [vector.tolist() for vector in vectors]


@dataclass
class VectorHit:
    source_type: str
    id: str
    metadata: dict
    document: str
    distance: float | None = None
    score: float | None = None


def active_embedding_provider() -> BaseEmbeddingProvider:
    global _embedding_provider, _embedding_warning
    if _embedding_provider is not None:
        return _embedding_provider

    settings = get_settings()
    requested = settings.embedding_provider.lower().strip()
    if requested in {"sentence-transformers", "sentence_transformers", "semantic"}:
        try:
            _embedding_provider = SentenceTransformerEmbeddingProvider(settings.embedding_model)
            _embedding_warning = None
            logger.info("Using semantic embedding provider %s", _embedding_provider.label)
            return _embedding_provider
        except Exception as exc:
            _embedding_warning = f"Falling back to hash embeddings because sentence-transformers is unavailable: {exc}"
            logger.warning(_embedding_warning)

    _embedding_provider = HashEmbeddingProvider()
    if requested not in {"hash", "dev", "fallback"} and not _embedding_warning:
        _embedding_warning = f"Unknown embedding provider '{settings.embedding_provider}', using hash fallback."
    logger.info("Using fallback embedding provider %s", _embedding_provider.label)
    return _embedding_provider


def embedding_status() -> dict:
    provider = active_embedding_provider()
    return {
        "provider": provider.name,
        "model": provider.model,
        "semantic": provider.name == "sentence-transformers",
        "fallback_used": provider.name != "sentence-transformers",
        "warning": _embedding_warning,
        "label": provider.label,
    }


def get_embedding_mode() -> str:
    """Returns 'semantic' or 'hash-fallback' depending on what is active."""
    provider = active_embedding_provider()
    return "semantic" if provider.name == "sentence-transformers" else "hash-fallback"


def hash_embedding(text: str) -> list[float]:
    vector = [0.0] * HASH_DIMENSIONS
    tokens = [token.lower() for token in _tokens(text)]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % HASH_DIMENSIONS
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = sum(value * value for value in vector) ** 0.5 or 1.0
    return [value / norm for value in vector]


def code_embedding_text(chunk: dict) -> str:
    return "\n".join(
        [
            f"Source Type: code",
            f"File: {chunk['file_path']}",
            f"Language: {chunk.get('language') or ''}",
            f"Class: {chunk.get('class_name') or ''}",
            f"Symbol: {chunk.get('symbol_name') or ''}",
            f"Lines: {chunk['start_line']}-{chunk['end_line']}",
            f"Security Tags: {chunk.get('security_tags') or ''}",
            "Code:",
            chunk["code"],
        ]
    )


def wiki_embedding_text(chunk: dict) -> str:
    return "\n".join(
        [
            "Source Type: wiki",
            f"Title: {chunk['title']}",
            f"Section: {chunk['section_title']}",
            f"Selected Module: {chunk.get('module_id') or ''}",
            "Content:",
            chunk["content"],
        ]
    )


def index_code_chunk(chunk: dict) -> str | None:
    try:
        collection = _collection(chunk["project_id"])
        embedding_id = f"code:{chunk['id']}"
        collection.upsert(
            ids=[embedding_id],
            documents=[code_embedding_text(chunk)],
            metadatas=[
                {
                    "source_type": "code",
                    "chunk_id": chunk["id"],
                    "project_id": chunk["project_id"],
                    "file_path": chunk["file_path"],
                    "symbol_name": chunk.get("symbol_name") or "",
                    "start_line": chunk["start_line"],
                    "end_line": chunk["end_line"],
                    "language": chunk.get("language") or "",
                    "security_tags": chunk.get("security_tags") or "",
                    "tags": chunk.get("security_tags") or "",
                    "chunk_type": chunk.get("chunk_type") or "",
                }
            ],
        )
        return embedding_id
    except Exception as exc:
        logger.warning("Could not index code chunk %s: %s", chunk.get("id"), exc)
        return None


def index_wiki_page(project_id: str, wiki_page_id: str, module_id: str | None, title: str, markdown: str) -> list[str]:
    chunks = split_wiki_markdown(markdown)
    if not chunks:
        return []

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict] = []
    created_at = datetime.now(timezone.utc).isoformat()
    for index, chunk in enumerate(chunks):
        item = {
            "title": title,
            "section_title": chunk["section_title"],
            "module_id": module_id,
            "content": chunk["content"],
        }
        ids.append(f"wiki:{wiki_page_id}:{index}")
        documents.append(wiki_embedding_text(item))
        metadatas.append(
            {
                "source_type": "wiki",
                "project_id": project_id,
                "wiki_page_id": wiki_page_id,
                "module_id": module_id or "",
                "title": title,
                "section_title": chunk["section_title"],
                "chunk_index": index,
                "created_at": created_at,
            }
        )

    try:
        _collection(project_id).upsert(ids=ids, documents=documents, metadatas=metadatas)
        return ids
    except Exception as exc:
        logger.warning("Could not index wiki page %s: %s", wiki_page_id, exc)
        return []


def split_wiki_markdown(markdown: str, max_lines: int = 80) -> list[dict]:
    lines = markdown.splitlines()
    chunks: list[dict] = []
    current_title = "Overview"
    current_lines: list[str] = []

    def flush() -> None:
        if not current_lines:
            return
        content = "\n".join(current_lines).strip()
        if content:
            chunks.append({"section_title": current_title, "content": content})

    for line in lines:
        if line.startswith("#"):
            flush()
            current_title = line.lstrip("#").strip() or "Untitled Section"
            current_lines = [line]
        else:
            current_lines.append(line)
            if len(current_lines) >= max_lines:
                flush()
                current_lines = []
    flush()
    return chunks


def clear_project(project_id: str) -> None:
    try:
        client = _client()
        client.delete_collection(_collection_name(project_id))
    except Exception:
        return


def query(
    project_id: str,
    text: str,
    limit: int = 8,
    source_type: Literal["code", "wiki"] | None = None,
    module_id: str | None = None,
) -> list[VectorHit]:
    try:
        where: dict | None = None
        filters = []
        if source_type:
            filters.append({"source_type": source_type})
        if module_id:
            filters.append({"module_id": module_id})
        if len(filters) == 1:
            where = filters[0]
        elif len(filters) > 1:
            where = {"$and": filters}

        result = _collection(project_id).query(query_texts=[text], n_results=limit, where=where)
        ids = result.get("ids", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]
        documents = result.get("documents", [[]])[0]
        distances = result.get("distances", [[]])[0] if result.get("distances") else []
        hits: list[VectorHit] = []
        for index, (hit_id, metadata, document) in enumerate(zip(ids, metadatas, documents)):
            if not metadata:
                continue
            distance = distances[index] if index < len(distances) else None
            hits.append(VectorHit(source_type=metadata.get("source_type", ""), id=hit_id, metadata=metadata, document=document or "", distance=distance))
        return hits
    except Exception as exc:
        logger.warning("Vector query failed for project %s: %s", project_id, exc)
        return []


FILE_EXTENSION_WEIGHTS = {
    ".java": 1.3,
    ".kt": 1.3,
    ".go": 1.3,
    ".py": 1.3,
    ".rb": 1.3,
    ".php": 1.3,
    ".rs": 1.3,
    ".cs": 1.3,
    ".swift": 1.3,
    ".ts": 1.2,
    ".js": 1.2,
    ".jsx": 1.2,
    ".tsx": 1.2,
    ".c": 1.2,
    ".cpp": 1.2,
    ".cc": 1.2,
    ".cxx": 1.2,
    ".aidl": 1.2,
    ".te": 1.2,
    ".proto": 1.2,
    ".h": 1.1,
    ".hpp": 1.1,
    ".xml": 1.1,
    ".yaml": 1.1,
    ".yml": 1.1,
    ".json": 1.1,
    ".conf": 1.1,
    ".toml": 1.0,
    ".ini": 1.0,
    ".env": 1.0,
    ".md": 0.7,
    ".txt": 0.7,
    ".rst": 0.7,
    ".html": 0.8,
    ".css": 0.8,
}

CHUNK_TYPE_WEIGHTS = {
    "method": 1.2,
    "function": 1.2,
    "async_function": 1.2,
    "route_handler": 1.2,
    "class": 1.1,
    "decorator_class": 1.1,
    "arrow_function": 1.1,
    "interface": 1.1,
    "constructor": 1.1,
    "markdown_section": 0.6,
    "line_range_fallback": 0.8,
    "file_summary": 0.7,
}

SECURITY_BOOST_TERMS = {
    "permission",
    "auth",
    "access",
    "security",
    "role",
    "policy",
    "binder",
    "selinux",
    "rbac",
    "jwt",
    "token",
    "credential",
    "checkpermission",
    "enforcepermission",
    "preauthorize",
    "requestmapping",
    "webmvctest",
    "filter",
    "interceptor",
}


def rescore_chunks(chunks: list) -> list:
    """Apply generic source/chunk/security weighting after Chroma returns candidates."""
    rescored = []
    for chunk in chunks:
        item = dict(chunk)
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        base_similarity = _base_similarity(item)
        file_path = item.get("file_path") or metadata.get("file_path") or ""
        chunk_type = item.get("chunk_type") or metadata.get("chunk_type") or ""
        tags = item.get("tags", item.get("security_tags", metadata.get("tags", metadata.get("security_tags", []))))
        file_weight = FILE_EXTENSION_WEIGHTS.get(os.path.splitext(str(file_path))[1].lower(), 1.0) if file_path else 1.0
        chunk_type_weight = CHUNK_TYPE_WEIGHTS.get(str(chunk_type), 1.0) if chunk_type else 1.0
        security_boost = 0.15 if _has_security_boost_tag(tags) else 0.0
        final_score = (base_similarity * file_weight * chunk_type_weight) + security_boost
        item["base_similarity"] = base_similarity
        item["final_score"] = final_score
        item["file_weight"] = file_weight
        item["chunk_type_weight"] = chunk_type_weight
        item["security_boost"] = security_boost
        rescored.append(item)
    return sorted(rescored, key=lambda chunk: chunk.get("final_score", 0.0), reverse=True)


def _base_similarity(chunk: dict) -> float:
    if chunk.get("base_similarity") is not None:
        return _clamp(float(chunk.get("base_similarity") or 0.0))
    if chunk.get("score") is not None:
        return _clamp(float(chunk.get("score") or 0.0))
    if chunk.get("distance") is not None:
        return _clamp(1.0 - float(chunk.get("distance") or 0.0))
    return 1.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _has_security_boost_tag(tags) -> bool:
    if tags is None:
        return False
    if isinstance(tags, str):
        tag_values = [tag.strip() for tag in tags.replace(";", ",").split(",")]
    elif isinstance(tags, list):
        tag_values = [str(tag) for tag in tags]
    else:
        tag_values = [str(tags)]
    return any(term in tag.lower() for tag in tag_values for term in SECURITY_BOOST_TERMS)


def code_chunk_exists(project_id: str, chunk_id: str) -> bool:
    try:
        result = _collection(project_id).get(ids=[f"code:{chunk_id}"], limit=1)
        return bool(result.get("ids"))
    except Exception as exc:
        logger.warning("Vector lookup failed for code chunk %s in project %s: %s", chunk_id, project_id, exc)
        return False


def _client():
    import chromadb

    settings = get_settings()
    return chromadb.PersistentClient(path=settings.chroma_db_path)


def _collection(project_id: str):
    return _client().get_or_create_collection(name=_collection_name(project_id), embedding_function=active_embedding_provider())


def _collection_name(project_id: str) -> str:
    return f"project_{project_id.replace('-', '_')}"


def _tokens(text: str) -> Iterable[str]:
    token = []
    for char in text:
        if char.isalnum() or char == "_":
            token.append(char)
        elif token:
            yield "".join(token)
            token = []
    if token:
        yield "".join(token)
