"""为文档、父块和子块生成跨进程稳定标识。"""

from hashlib import sha256
from pathlib import Path

from langchain_core.documents import Document


def stable_hash(*parts: object) -> str:
    normalized = "\x1f".join(str(part).replace("\\", "/").strip() for part in parts)
    return sha256(normalized.encode("utf-8")).hexdigest()


def document_id(document: Document) -> str:
    existing = document.metadata.get("document_id")
    if existing:
        return str(existing)
    source = document.metadata.get("source", "unknown")
    return stable_hash(Path(str(source)).as_posix().lower(), document.page_content)


def chunk_id(document: Document) -> str:
    return str(
        document.metadata.get("chunk_id")
        or document.metadata.get("parent_id")
        or document_id(document)
    )
