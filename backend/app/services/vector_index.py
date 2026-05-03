import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Literal

from app.core.config import get_settings


logger = logging.getLogger(__name__)
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

        self._model = SentenceTransformer(model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        return [vector.tolist() for vector in vectors]


@dataclass
class VectorHit:
    source_type: str
    id: str
    metadata: dict
    document: str


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
        hits: list[VectorHit] = []
        for hit_id, metadata, document in zip(ids, metadatas, documents):
            if not metadata:
                continue
            hits.append(VectorHit(source_type=metadata.get("source_type", ""), id=hit_id, metadata=metadata, document=document or ""))
        return hits
    except Exception as exc:
        logger.warning("Vector query failed for project %s: %s", project_id, exc)
        return []


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
