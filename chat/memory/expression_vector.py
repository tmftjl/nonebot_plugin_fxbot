"""表达向量召回：用嵌入挑出候选，再交给模型按语境筛选。

对齐 MaiBot（`src/chat/replyer/expression_vector_index.py` 与
`maisaka_expression_selector.py`）：索引按情境+说法文本增量维护，落盘为 JSON；
向量召回失败或没有候选时回退普通候选，精细档仍通过上下文子代理筛选。
"""

from __future__ import annotations

import json
import math
import asyncio
from typing import Any
from pathlib import Path
from dataclasses import field, dataclass

from nonebot import logger

from .models import ChatExpression
from ...config import get_manager as get_config_manager
from ...utils.paths import data_dir, package_root
from ..providers.usage import track_model_task_usage
from ..providers.manager import provider_manager
from ..providers.routing import routed_task, fallback_provider

INDEX_VERSION = 2
EMBED_BATCH_SIZE = 64
MAX_SELECTED = 5
_SELECTOR_PROMPT = (
    "你是表达方式选择助手。根据真实聊天上下文，从候选里挑出最适合这一次回复的表达方式。\n"
    f"只选 0 到 {MAX_SELECTED} 条，优先自然、贴合语境、不生硬；没有明显合适的就返回空数组。\n"
    '只输出 JSON：{{"selected_ids":[]}}。\n\n候选表达方式：\n{lines}'
)


@dataclass
class _Index:
    """一个索引文件的嵌入身份与条目；向量按表达 id 保存。"""

    identity: str = ""
    entries: dict[int, dict[str, Any]] = field(default_factory=dict)


_indexes: dict[str, _Index] = {}
_index_lock: asyncio.Lock | None = None


def _lock() -> asyncio.Lock:
    global _index_lock
    if _index_lock is None:
        _index_lock = asyncio.Lock()
    return _index_lock


def resolve_index_path(value: str) -> Path:
    """相对路径按插件根目录解析；`data/...` 指向运行时可配置的数据目录。"""
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == "data":
        return data_dir() / Path(*path.parts[1:])
    return package_root() / path


def corpus_text(row: ChatExpression) -> str:
    """参与嵌入与展示的文本：情境和说法。"""
    return f"{str(row.situation or '').strip()} | {str(row.style or '').strip()}".strip(" |")


def cosine(left: list[float], right: list[float]) -> float:
    """余弦相似度；维度不一致或零向量记 0。"""
    if not left or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left <= 0 or norm_right <= 0:
        return 0.0
    return dot / (norm_left * norm_right)


def _embedding_provider_name() -> str:
    """embedding 路由指定的 Provider；未配置时留空交给 Provider 层选第一个。"""
    try:
        routing = get_config_manager().get_system()["chat"].get("model_routing") or {}
    except Exception:
        return ""
    name = str(routing.get("embedding") or "")
    return name if name in provider_manager.list_providers_by_type("embedding") else ""


def _provider_identity(provider: Any) -> str:
    """嵌入身份：Provider、模型与维度共同决定向量空间。"""
    try:
        dimension = int(provider.get_dimension())
    except Exception:
        dimension = 0
    return f"{getattr(provider, 'provider_id', '')}:{getattr(provider, 'model_name', '')}:{dimension}"


async def _embed(provider: Any, texts: list[str]) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), EMBED_BATCH_SIZE):
        async with track_model_task_usage("embedding", provider):
            vectors.extend([list(vector) for vector in await provider.embed(texts[start : start + EMBED_BATCH_SIZE])])
    return vectors


def _load(path: Path) -> _Index:
    """按路径缓存索引；首次使用时读取文件。"""
    key = str(path)
    index = _indexes.get(key)
    if index is not None:
        return index
    index = _Index()
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("version") == INDEX_VERSION:
                entries = data.get("entries")
                if isinstance(entries, list):
                    index.entries = {
                        int(item["id"]): item
                        for item in entries
                        if isinstance(item, dict) and str(item.get("id", "")).isdigit()
                    }
                index.identity = str(data.get("identity") or "")
        except (OSError, ValueError, TypeError):
            logger.warning(f"[ChatExpression] 表达向量索引读取失败，按空索引重建: {path}")
            index = _Index()
    _indexes[key] = index
    return index


def _save(path: Path, index: _Index) -> None:
    """索引原子落盘；失败只影响下次重建，不影响本次召回。"""
    payload = {"version": INDEX_VERSION, "identity": index.identity, "entries": list(index.entries.values())}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError:
        logger.opt(exception=True).warning(f"[ChatExpression] 表达向量索引写入失败: {path}")


async def _refresh(index: _Index, rows: list[ChatExpression], path: Path, provider: Any) -> None:
    """嵌入身份变化时整代重建；否则只补算新增或文本已变的表达。"""
    identity = _provider_identity(provider)
    if index.identity != identity:
        if index.entries:
            logger.info(f"[ChatExpression] 嵌入身份变化，重建表达向量索引: {index.identity} -> {identity}")
        index.entries.clear()
        index.identity = identity
        _save(path, index)
    missing = [
        row
        for row in rows
        if int(row.id or 0) and (index.entries.get(int(row.id)) or {}).get("text") != corpus_text(row)
    ]
    if not missing:
        return
    vectors = await _embed(provider, [corpus_text(row) for row in missing])
    if len(vectors) != len(missing):
        raise ValueError("嵌入数量与表达数量不一致")
    for row, vector in zip(missing, vectors):
        index.entries[int(row.id)] = {"id": int(row.id), "text": corpus_text(row), "vector": vector}
    _save(path, index)


async def vector_candidates(
    query: str,
    rows: list[ChatExpression],
    *,
    index_path: str,
    pool_size: int,
) -> list[ChatExpression]:
    """按查询嵌入召回前 pool_size 条表达；失败时抛出，由调用方回退。"""
    if not query.strip() or not rows or pool_size <= 0:
        return []
    path = resolve_index_path(index_path)
    provider = await provider_manager.get_embedding_provider(_embedding_provider_name())
    async with _lock():
        index = _load(path)
        await _refresh(index, rows, path, provider)
    query_vectors = await _embed(provider, [query])
    scored: list[tuple[float, int, ChatExpression]] = []
    for row in rows:
        entry = index.entries.get(int(row.id or 0))
        if entry is None:
            continue
        score = cosine(query_vectors[0], entry.get("vector") or [])
        if score > 0:
            scored.append((score, int(row.id or 0), row))
    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [row for _, _, row in scored[: min(max(pool_size, 1), 50)]]


def build_selector_prompt(query: str, rows: list[ChatExpression]) -> str:
    """候选列表只给编号、情境和说法，要求模型返回编号数组。"""
    lines = [f"{int(row.id or 0)}: 情景={row.situation} | 风格={row.style}" for row in rows if int(row.id or 0)]
    return _SELECTOR_PROMPT.format(lines="\n".join(lines)) + f"\n\n表达选择意图与回复信息参考：\n{query}"


def parse_selected_ids(text: str, rows: list[ChatExpression]) -> list[int]:
    """解析模型输出并只保留候选内的编号。"""
    allowed = {int(row.id or 0) for row in rows}
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return []
    ids = data.get("selected_ids") if isinstance(data, dict) else None
    if not isinstance(ids, list):
        return []
    selected = []
    for value in ids:
        if isinstance(value, int) and not isinstance(value, bool) and value in allowed and value not in selected:
            selected.append(value)
    return selected[:MAX_SELECTED]


async def select_by_vectors(
    tenant: str,
    query: str,
    rows: list[ChatExpression],
    *,
    index_path: str,
    pool_size: int,
    timeout: float = 20.0,
    history: list[dict[str, Any]] | None = None,
) -> list[ChatExpression]:
    """向量召回失败时改用普通候选，精细档始终继续上下文二次筛选。"""
    try:
        candidates = await asyncio.wait_for(
            vector_candidates(query, rows, index_path=index_path, pool_size=pool_size),
            timeout=timeout,
        )
    except Exception:
        logger.opt(exception=True).debug("[ChatExpression] 表达向量召回失败，回退普通候选")
        candidates = []
    if not candidates:
        from .expression import sample_expression_candidates

        candidates = sample_expression_candidates(rows)
    if not candidates:
        return []
    by_id = {int(row.id or 0): row for row in candidates}
    try:
        async with fallback_provider(routed_task("fast_model", "utils"), tenant) as provider:
            messages = [{"role": "system", "content": build_selector_prompt(query, candidates)}]
            # 子代理只接收最近十条真实收发消息，不能把 Planner 工具协议当聊天内容。
            visible_history = [
                message
                for message in (history or [])
                if message.get("role") in {"user", "assistant"}
                and (message.get("_sender_id") or message.get("_self_message"))
                and not message.get("_planner_internal")
                and not message.get("_reference")
                and not message.get("tool_calls")
            ]
            messages.extend(
                {"role": "user" if message.get("_self_message") else message["role"], "content": message["content"]}
                for message in visible_history[-10:]
            )
            response = await asyncio.wait_for(
                provider.chat(
                    messages=messages,
                    temperature=0.2,
                ),
                timeout=timeout,
            )
    except Exception:
        logger.opt(exception=True).debug("[ChatExpression] 表达筛选失败，直接注入向量候选")
        return candidates
    return [by_id[value] for value in parse_selected_ids(str(response.content or ""), candidates)]
