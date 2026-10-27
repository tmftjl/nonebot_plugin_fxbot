"""租户知识库：切块、入库与 BM25 检索。"""

from __future__ import annotations

import json
import hashlib
from typing import Any
from collections import Counter
from dataclasses import dataclass

from ..config import get_manager as get_config_manager
from .memory.retrieve import Index, tokenize
from .memory.repository import chat_repository

_MAX_DOC_CHARS = 200_000
_KEYWORD_COUNT = 20


def knowledge_config() -> dict[str, Any]:
    """读取知识库配置；缺失或非法时按关闭处理。"""
    try:
        value = get_config_manager().get_system()["chat"].get("knowledge") or {}
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def knowledge_enabled() -> bool:
    """知识库是否启用。"""
    return bool(knowledge_config().get("enabled"))


def _chunk_chars() -> int:
    value = knowledge_config().get("chunk_chars", 400)
    return value if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 2000 else 400


def _top_k() -> int:
    value = knowledge_config().get("top_k", 4)
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 10 else 4


def chunk_text(text: str, size: int = 400, overlap: int = 50) -> list[str]:
    """按字符切块，相邻块重叠固定字数。"""
    clean = "\n".join(line.strip() for line in str(text or "").splitlines() if line.strip())
    if not clean:
        return []
    step = max(1, size - overlap)
    return [clean[start : start + size] for start in range(0, len(clean), step) if clean[start:]]


def _keywords(text: str) -> str:
    """取高频 bigram/词作为切片关键词。"""
    tokens = [token for token, _ in Counter(tokenize(text)).most_common(_KEYWORD_COUNT)]
    return json.dumps(tokens, ensure_ascii=False)


async def import_document(tenant: str, title: str, source: str, text: str) -> Any:
    """按配置切块并整份入库；同内容重复上传直接复用。"""
    content = str(text or "").strip()
    if not content:
        raise ValueError("文档内容为空")
    if len(content) > _MAX_DOC_CHARS:
        raise ValueError("文档不能超过 20 万字")
    chunks = [(chunk, _keywords(chunk)) for chunk in chunk_text(content, _chunk_chars())]
    if not chunks:
        raise ValueError("文档内容为空")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    return await chat_repository.save_knowledge_doc(
        tenant,
        title=str(title or "").strip() or "未命名文档",
        source=str(source or "").strip(),
        digest=digest,
        chunks=chunks,
    )


@dataclass(frozen=True)
class KnowledgeHit:
    """知识库检索结果。"""

    title: str
    content: str
    score: float


async def search(tenant: str, query: str, *, limit: int | None = None) -> list[KnowledgeHit]:
    """在当前租户内检索 top-k 切片；无资料返回空列表。"""
    value = str(query or "").strip()
    if not value:
        return []
    try:
        rows = await chat_repository.knowledge_chunks(tenant)
    except Exception:
        return []
    if not rows:
        return []
    index = Index([(position, content) for position, (_, content) in enumerate(rows)])
    scores = index.scores(value)
    ranked = sorted(
        ((score, rows[position]) for position, score in enumerate(scores) if score > 0),
        key=lambda item: item[0],
        reverse=True,
    )
    return [
        KnowledgeHit(title=title, content=content, score=score)
        for score, (title, content) in ranked[: limit or _top_k()]
    ]
