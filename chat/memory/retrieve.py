"""无向量检索：字符 bigram BM25、Episode 打分与类型化关系扩展。"""

from __future__ import annotations

import re
import json
import math
from typing import Any
from datetime import datetime
from collections import Counter
from dataclasses import dataclass

from .models import ChatMemory, ChatEpisode, utc_now_naive
from .repository import chat_repository
from ...utils.cache import cache

_DOMAIN = "chat-memory-index"
_ASCII_WORD = re.compile(r"[a-z0-9_]+")
_CJK_RUN = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯]+")
_K1 = 1.5
_B = 0.75


@dataclass(frozen=True)
class FactHit:
    """注入用的事实快照，不持有 ORM 对象。"""

    id: int
    subject_id: str
    subject_name: str
    key: str
    value: str
    asserted_by: str
    importance: float
    confidence: float
    updated_at: datetime | None


@dataclass(frozen=True)
class EpisodeHit:
    """注入用的 Episode 快照。"""

    id: int
    episode_key: str
    title: str
    summary: str
    keywords: tuple[str, ...]
    participants: tuple[str, ...]
    updated_at: datetime | None
    created_at: datetime | None


def tokenize(text: str) -> list[str]:
    """ASCII 词按词切，CJK 连续段按字符 bigram。"""
    folded = " ".join(str(text or "").casefold().split())
    tokens = _ASCII_WORD.findall(folded)
    for chunk in _CJK_RUN.findall(folded):
        if len(chunk) == 1:
            tokens.append(chunk)
        else:
            tokens.extend(chunk[index : index + 2] for index in range(len(chunk) - 1))
    return tokens


def _normalize(scores: list[float]) -> list[float]:
    """候选集内 min-max；单值或全相等用 v/(v+1)，零匹配保持 0。"""
    positive = [score for score in scores if score > 0]
    if not positive:
        return [0.0] * len(scores)
    low, high = min(positive), max(positive)
    if high - low <= 1e-12:
        return [score / (score + 1) if score > 0 else 0.0 for score in scores]
    return [(score - low) / (high - low) if score > 0 else 0.0 for score in scores]


class Index:
    """一个租户一类文档的倒排统计；文档值是不可变的。"""

    def __init__(self, docs: list[tuple[Any, str]]) -> None:
        self.docs = tuple(docs)
        self._tokens: list[Counter[str]] = []
        self._df: Counter[str] = Counter()
        total = 0
        for _, text in docs:
            counts = Counter(tokenize(text))
            self._tokens.append(counts)
            self._df.update(counts.keys())
            total += sum(counts.values())
        self._avgdl = total / len(docs) if docs else 0.0

    def scores(self, query: str) -> list[float]:
        """返回与 docs 对齐的 BM25 分数；无命中全为 0。"""
        tokens = [token for token in dict.fromkeys(tokenize(query)) if token in self._df]
        if not tokens or not self.docs:
            return [0.0] * len(self.docs)
        size = len(self.docs)
        result: list[float] = []
        for counts in self._tokens:
            length = sum(counts.values())
            norm = _B * (length / self._avgdl) if self._avgdl else 0.0
            score = 0.0
            for token in tokens:
                frequency = counts.get(token, 0)
                if not frequency:
                    continue
                df = self._df[token]
                idf = math.log(1 + (size - df + 0.5) / (df + 0.5))
                score += idf * frequency * (_K1 + 1) / (frequency + _K1 * (1 - _B + norm))
            result.append(score)
        return result


def _json_list(value: str) -> tuple[str, ...]:
    """解析 JSON 字符串数组；坏数据按空处理。"""
    try:
        items = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    return tuple(str(item) for item in items if str(item).strip()) if isinstance(items, list) else ()


def _fact_hit(fact: ChatMemory) -> FactHit:
    return FactHit(
        id=int(fact.id or 0),
        subject_id=fact.subject_id,
        subject_name=fact.subject_name,
        key=fact.key,
        value=fact.value,
        asserted_by=fact.asserted_by,
        importance=float(fact.importance),
        confidence=float(fact.confidence),
        updated_at=fact.updated_at,
    )


def _episode_hit(episode: ChatEpisode) -> EpisodeHit:
    return EpisodeHit(
        id=int(episode.id or 0),
        episode_key=episode.episode_key,
        title=episode.title,
        summary=episode.summary,
        keywords=_json_list(episode.keywords),
        participants=_json_list(episode.participants),
        updated_at=episode.updated_at,
        created_at=episode.created_at,
    )


def _fact_text(fact: FactHit) -> str:
    key = fact.key.split(".", 1)[-1]
    return " ".join((fact.subject_name, key, fact.value))


def _episode_text(episode: EpisodeHit) -> str:
    return " ".join((episode.title, " ".join(episode.keywords), episode.summary))


def _recency(moment: datetime | None) -> float:
    """按天衰减的时效分，30 天减半到 0.5。"""
    if moment is None:
        return 0.0
    days = max(0.0, (utc_now_naive() - moment).total_seconds() / 86400)
    return 1 / (1 + days / 30)


async def _indexed(tenant: str, kind: str, version: int, loader: Any) -> Index:
    """懒加载索引；异步读取后版本未变才写缓存。"""
    key = (_DOMAIN, tenant, kind, version)
    index = cache.get(key)
    if index is not None:
        return index
    built = await loader()
    state = await chat_repository.get_session_state(tenant)
    current = 0
    if state is not None:
        current = int(state.episodes_version if kind == "episodes" else state.facts_version)
    if current == version:
        cache.set(key, built)
    return built


async def _version(tenant: str, kind: str) -> int:
    state = await chat_repository.get_session_state(tenant)
    if state is None:
        return 0
    return int(state.episodes_version if kind == "episodes" else state.facts_version)


async def _episode_index(tenant: str) -> Index:
    async def load() -> Index:
        hits = [_episode_hit(row) for row in await chat_repository.list_active_episodes(tenant)]
        return Index([(hit, _episode_text(hit)) for hit in hits])

    return await _indexed(tenant, "episodes", await _version(tenant, "episodes"), load)


async def _fact_index(tenant: str) -> Index:
    async def load() -> Index:
        hits = [_fact_hit(row) for row in await chat_repository.list_active_facts(tenant)]
        return Index([(hit, _fact_text(hit)) for hit in hits])

    return await _indexed(tenant, "facts", await _version(tenant, "facts"), load)


async def recall_episodes(tenant: str, subject_ids: list[str], query: str = "", limit: int = 2) -> list[EpisodeHit]:
    """按 BM25、参与人重合与时效打分，取最相关的 Episode。"""
    index = await _episode_index(tenant)
    if not index.docs:
        return []
    normalized = _normalize(index.scores(query))
    subjects = {str(item) for item in subject_ids if item}
    ranked: list[tuple[float, int, EpisodeHit]] = []
    for (episode, _), bm25 in zip(index.docs, normalized):
        participants = set(episode.participants)
        overlap = len(participants & subjects) / len(participants) if participants else 0.0
        score = 0.6 * bm25 + 0.2 * overlap + 0.1 * _recency(episode.updated_at or episode.created_at)
        ranked.append((score, episode.id, episode))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [episode for _, _, episode in ranked[:limit]]


async def _related_facts(tenant: str, seeds: set[str]) -> list[tuple[FactHit, float]]:
    """从发言人/@ 目标出发走 1–2 跳 user 边，返回带权事实。"""
    first = await chat_repository.get_relations(tenant, sorted(seeds))
    hop1 = list(
        dict.fromkeys(
            relation.object_key
            for relation in first
            if relation.object_kind == "user" and relation.object_key and relation.object_key not in seeds
        )
    )
    if not hop1:
        return []
    second = await chat_repository.get_relations(tenant, hop1)
    known = seeds | set(hop1)
    hop2 = list(
        dict.fromkeys(
            relation.object_key
            for relation in second
            if relation.object_kind == "user" and relation.object_key and relation.object_key not in known
        )
    )
    rows = await chat_repository.get_facts_for_subjects(tenant, hop1 + hop2, limit=max(60, 6 * (len(hop1) + len(hop2))))
    weights = {subject_id: 1.0 for subject_id in hop1}
    weights.update({subject_id: 0.5 for subject_id in hop2})
    result: list[tuple[FactHit, float]] = []
    used: Counter[str] = Counter()
    for row in rows:
        weight = weights.get(row.subject_id)
        if weight is None or used[row.subject_id] >= 6:
            continue
        used[row.subject_id] += 1
        result.append((_fact_hit(row), weight))
    return result


async def recall_facts(
    tenant: str, speaker_id: str, subject_ids: list[str], query: str = "", limit: int = 10
) -> list[FactHit]:
    """直接事实 + BM25 补充 + 关系扩展，按 id 去重后截断。"""
    candidates: dict[int, tuple[FactHit, float]] = {}

    def add(fact: FactHit, weight: float) -> None:
        if not fact.id:
            return
        current = candidates.get(fact.id)
        if current is None or weight > current[1]:
            candidates[fact.id] = (fact, weight)

    for row in await chat_repository.get_facts(tenant, speaker_id, limit=6):
        add(_fact_hit(row), 1.0)
    index = await _fact_index(tenant)
    if query and index.docs:
        raw = index.scores(query)
        normalized = _normalize(raw)
        for position in sorted(range(len(raw)), key=lambda item: raw[item], reverse=True)[:4]:
            if raw[position] <= 0:
                break
            add(index.docs[position][0], normalized[position])
    seeds = {str(item) for item in [speaker_id, *subject_ids] if item}
    for fact, weight in await _related_facts(tenant, seeds):
        add(fact, weight)
    ordered = sorted(
        candidates.values(),
        key=lambda item: (item[1], item[0].importance, item[0].confidence, item[0].id),
        reverse=True,
    )
    return [fact for fact, _ in ordered[:limit]]
