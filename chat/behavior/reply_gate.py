"""主动接话的概率估计与动态门控。

概率常数取自 MaiBot 发布权重（`src/maisaka/turn_trigger/`）；fxbot 尚未用自身样本校准。
门控状态按租户保存在内存，重启即清零；是否参与路由由群级 `#主动 开/关` 决定。
"""

from __future__ import annotations

import time
from math import exp, log1p
from typing import Any, Callable, Sequence
from datetime import datetime
from collections import OrderedDict, deque
from dataclasses import dataclass

from nonebot import logger

from ...config import SYSTEM_DEFAULTS, get_manager as get_config_manager
from ...utils.tz import now as shanghai_now
from ..memory.learning import GROUP, PRIVATE, TenantTarget, tenant_target
from ..memory.state import is_ready as memory_ready
from ..memory.repository import chat_repository

MAX_PENDING_COUNT = 20
DEFAULT_SECONDS_SINCE_BOT_MESSAGE = 3600.0
MEDIA_PLACEHOLDER_PREFIXES = ("[表情包", "[图片", "[消息类型]", "[CQ:image", "[语音", "[文件]", "[卡片")

_INTERCEPT = -1.2005
_WEIGHT_MENTION_BOT = 1.5852
_WEIGHT_AT_OTHER = -1.1157
_WEIGHT_QUESTION_MARK = 0.4807
_WEIGHT_PLACEHOLDER_ONLY = -1.0281
_WEIGHT_RECENT_SELF_RATIO = 2.6525
_WEIGHT_RECENT_MESSAGE_COUNT = -0.0367
_WEIGHT_LOG_SECONDS_SINCE_BOT = 0.0571
_WEIGHT_PENDING_COUNT = 0.1012

REPLY_WINDOW_SECONDS = 3600.0
FORCED_TURN_EXPECTED_REPLIES = 0.91
MIN_WINDOW_SCORE_COUNT = 20
VIRTUAL_ROUND_SECONDS = 40.0
MAX_COUNTED_MESSAGE_IDS = 2000
_STATIC_KEEP_RATIO_THRESHOLDS = (
    (0.0, 1.0),
    (0.1, 0.605),
    (0.2, 0.482),
    (0.3, 0.430),
    (0.4, 0.376),
    (0.5, 0.336),
    (0.6, 0.313),
    (0.7, 0.290),
    (0.8, 0.250),
    (0.9, 0.206),
    (1.0, 0.0),
)

RECENT_WINDOW_SECONDS = 300.0
MAX_RECENT_EVENTS = 2048
MAX_OBSERVATIONS = 200
MAX_TENANTS = 2000
MAX_SWITCH_TENANTS = 2000

REPLY_TIMING_DEFAULTS: dict[str, Any] = SYSTEM_DEFAULTS["chat"]["reply_timing"]


def reply_timing_config() -> dict[str, Any]:
    """返回带默认值的回复时机配置；每次使用重新读取以支持热更新。"""
    try:
        value = get_config_manager().get_system()["chat"].get("reply_timing")
    except Exception:
        value = None
    merged = dict(REPLY_TIMING_DEFAULTS)
    if isinstance(value, dict):
        merged.update(value)
    return merged


def _setting_int(cfg: dict[str, Any], key: str, minimum: int = 0) -> int:
    value = cfg.get(key, REPLY_TIMING_DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, int):
        return int(REPLY_TIMING_DEFAULTS[key])
    return max(minimum, value)


def _minute_of_day(value: str) -> int | None:
    """把 HH:MM 解析成当天分钟数，非法值返回 None。"""
    try:
        hour_text, minute_text = value.split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
    except (ValueError, AttributeError):
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _in_time_range(minutes: int, start: int, end: int) -> bool:
    """支持跨午夜的闭区间。"""
    return start <= minutes <= end if start <= end else minutes >= start or minutes <= end


_ALL_DAY_TIMES = frozenset({"", "*"})


def _matching_rule_value(rules: list[dict[str, Any]], minutes: int) -> float | None:
    """同范围内先看命中的时间段，没有命中再退到留空或 * 的全天规则。"""
    fallback: float | None = None
    for rule in rules:
        value = rule.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        time_range = str(rule.get("time") or "").strip()
        if time_range in _ALL_DAY_TIMES:
            if fallback is None:
                fallback = min(1.0, max(0.0, float(value)))
            continue
        try:
            start_text, end_text = time_range.split("-", 1)
        except ValueError:
            continue
        start, end = _minute_of_day(start_text), _minute_of_day(end_text)
        if start is None or end is None or not _in_time_range(minutes, start, end):
            continue
        return min(1.0, max(0.0, float(value)))
    return fallback


def _rule_rank(rule: dict[str, Any], tenant: str, target: TenantTarget | None) -> int:
    """0 表示不命中；5 精确聊天流，4 平台+会话，3 平台默认，2 任意平台+会话，1 全局。"""
    legacy = str(rule.get("tenant") or "").strip()
    if legacy:
        return 5 if legacy == tenant else 0
    platform = str(rule.get("platform") or "").strip()
    item_id = str(rule.get("item_id") or "").strip()
    is_group = {GROUP: True, PRIVATE: False}.get(str(rule.get("type") or "").strip())
    if target is None:
        return 1 if not platform and not item_id else 0
    if is_group is not None and is_group != target.is_group:
        return 0
    if platform not in {"", "*"} and platform != target.platform:
        return 0
    if item_id in {"", "*"}:
        return 3 if platform not in {"", "*"} else 1
    if item_id != target.item_id:
        return 0
    return 4 if platform not in {"", "*"} else 2


def talk_value_for(tenant: str, *, at: datetime | None = None) -> float:
    """读取当前生效的 talk_value；范围越具体越优先，同范围内时间段优先于全天。"""
    cfg = reply_timing_config()
    try:
        default = float(cfg.get("talk_value", REPLY_TIMING_DEFAULTS["talk_value"]))
    except (TypeError, ValueError):
        default = float(REPLY_TIMING_DEFAULTS["talk_value"])
    default = min(1.0, max(0.0, default))
    if not bool(cfg.get("enable_talk_value_rules")):
        return default
    current = at or shanghai_now()
    minutes = current.hour * 60 + current.minute
    rules = cfg.get("talk_value_rules")
    if not isinstance(rules, list):
        return default
    target = tenant_target(tenant)
    for rank in (5, 4, 3, 2, 1):
        scoped = [rule for rule in rules if isinstance(rule, dict) and _rule_rank(rule, tenant, target) == rank]
        if scoped:
            value = _matching_rule_value(scoped, minutes)
            if value is not None:
                return value
    return default


class IdleBackoff:
    """连续无动作轮次后的指数退避。"""

    def __init__(self) -> None:
        self.count = 0
        self.until = 0.0

    def record_idle(self, now: float, *, base: int, cap: int, start_count: int) -> None:
        self.count += 1
        if self.count < max(1, start_count) or base <= 0 or cap <= 0:
            self.until = 0.0
            return
        delay = min(cap, base * (2 ** min(30, self.count - max(1, start_count))))
        self.until = now + delay

    def reset(self) -> None:
        self.count = 0
        self.until = 0.0

    def delay(self, now: float, *, pending: int, bypass_pending: int) -> float:
        """返回剩余退避秒数；待处理消息达到阈值时绕过。"""
        if pending >= max(0, bypass_pending):
            return 0.0
        return max(0.0, self.until - now)


_switch_cache: OrderedDict[str, bool | None] = OrderedDict()


async def proactive_switch(tenant: str) -> bool | None:
    """读取群级开关；None 表示跟随全局。每个租户每个进程只查一次库。"""
    if tenant in _switch_cache:
        _switch_cache.move_to_end(tenant)
        return _switch_cache[tenant]
    if not memory_ready():
        return None
    try:
        state = await chat_repository.get_session_state(tenant)
    except Exception:
        logger.opt(exception=True).debug("[Chat] 读取主动接话开关失败")
        return None
    value = None if state is None else state.proactive_enabled
    _switch_cache[tenant] = value
    while len(_switch_cache) > MAX_SWITCH_TENANTS:
        _switch_cache.popitem(last=False)
    return value


async def set_proactive_switch(tenant: str, enabled: bool) -> None:
    """持久化群级开关并更新缓存。"""
    await chat_repository.set_proactive_enabled(tenant, enabled)
    _switch_cache[tenant] = enabled
    _switch_cache.move_to_end(tenant)
    while len(_switch_cache) > MAX_SWITCH_TENANTS:
        _switch_cache.popitem(last=False)


@dataclass(frozen=True, slots=True)
class ReplyLikelihoodInput:
    """估计 reply 概率所需的对话快照。"""

    mention_bot: bool
    at_other: bool
    has_question_mark: bool
    placeholder_only: bool
    recent_self_ratio: float
    recent_message_count: int
    seconds_since_bot_message: float
    pending_count: int


def summarize_pending_texts(texts: Sequence[str], *, at_other: bool = False) -> tuple[bool, bool, bool]:
    """结合显式提及标记得到 (at_other, has_question_mark, placeholder_only)。"""
    stripped = [text.strip() for text in texts if text.strip()]
    has_question = any("?" in text or "？" in text for text in stripped)
    placeholder_only = bool(stripped) and all(text.startswith(MEDIA_PLACEHOLDER_PREFIXES) for text in stripped)
    return at_other, has_question, placeholder_only


def estimate_reply_probability(snapshot: ReplyLikelihoodInput) -> float:
    """估计这批消息进入 Planner 后会调用 reply 的概率。"""
    logit = (
        _INTERCEPT
        + _WEIGHT_MENTION_BOT * float(snapshot.mention_bot)
        + _WEIGHT_AT_OTHER * float(snapshot.at_other)
        + _WEIGHT_QUESTION_MARK * float(snapshot.has_question_mark)
        + _WEIGHT_PLACEHOLDER_ONLY * float(snapshot.placeholder_only)
        + _WEIGHT_RECENT_SELF_RATIO * min(1.0, max(0.0, snapshot.recent_self_ratio))
        + _WEIGHT_RECENT_MESSAGE_COUNT * max(0, snapshot.recent_message_count)
        + _WEIGHT_LOG_SECONDS_SINCE_BOT * log1p(max(0.0, snapshot.seconds_since_bot_message))
        + _WEIGHT_PENDING_COUNT * min(MAX_PENDING_COUNT, max(0, snapshot.pending_count))
    )
    return 1.0 / (1.0 + exp(-logit))


def _static_threshold(keep_ratio: float) -> float:
    """按离线统计表线性插值出保留比例对应的概率阈值。"""
    for (low_ratio, low_threshold), (high_ratio, high_threshold) in zip(
        _STATIC_KEEP_RATIO_THRESHOLDS, _STATIC_KEEP_RATIO_THRESHOLDS[1:]
    ):
        if keep_ratio <= high_ratio:
            progress = (keep_ratio - low_ratio) / (high_ratio - low_ratio)
            return low_threshold + (high_threshold - low_threshold) * progress
    return 0.0


@dataclass(frozen=True, slots=True)
class DynamicGateDecision:
    """动态门控的一次判定。"""

    should_trigger: bool
    probability: float
    threshold: float
    expected_replies: float
    target_replies: float
    actual_replies: int
    keep_ratio: float


class DynamicReplyGate:
    """按小时窗口把实际回复数拉回「预计回复数 × 回复频率」。"""

    def __init__(self, window_seconds: float = REPLY_WINDOW_SECONDS) -> None:
        self._window_seconds = window_seconds
        self._proactive_scores: deque[tuple[float, float]] = deque()
        self._forced_expectations: deque[tuple[float, float]] = deque()
        self._reply_times: deque[float] = deque()
        self._counted_message_ids: set[str] = set()
        self._counted_message_order: deque[str] = deque()
        self._open_round_started_at: float | None = None

    def take_uncounted_message_ids(self, message_ids: Sequence[str]) -> list[str]:
        """返回尚未计入预计回复数的消息 ID，并把它们标记为已计入。"""
        new_ids: list[str] = []
        for message_id in message_ids:
            if not message_id or message_id in self._counted_message_ids:
                continue
            new_ids.append(message_id)
            self._counted_message_ids.add(message_id)
            self._counted_message_order.append(message_id)
        while len(self._counted_message_order) > MAX_COUNTED_MESSAGE_IDS:
            self._counted_message_ids.discard(self._counted_message_order.popleft())
        return new_ids

    def is_round_open(self, now: float) -> bool:
        """当前是否仍处在一个尚未结束的虚拟轮次内。"""
        return self._open_round_started_at is not None and now - self._open_round_started_at < VIRTUAL_ROUND_SECONDS

    def record_proactive_demand(self, probability: float, now: float) -> None:
        """同一虚拟轮次只计一次预计回复数，后来的消息覆盖本轮概率。"""
        if self.is_round_open(now) and self._proactive_scores:
            round_started_at, _ = self._proactive_scores[-1]
            self._proactive_scores[-1] = (round_started_at, probability)
            return
        self._open_round_started_at = now
        self._proactive_scores.append((now, probability))

    def close_round(self) -> None:
        """Planner 已实际运行，之后到达的消息属于下一个虚拟轮次。"""
        self._open_round_started_at = None

    def record_forced_turn(self, now: float) -> None:
        """记录一次 @ 或人格名强制触发预计带来的回复数。"""
        self.close_round()
        self._forced_expectations.append((now, FORCED_TURN_EXPECTED_REPLIES))

    def record_reply(self, now: float) -> None:
        """记录一次实际发出的群回复。"""
        self._reply_times.append(now)

    def _prune(self, now: float) -> None:
        expire_before = now - self._window_seconds
        for timed_values in (self._proactive_scores, self._forced_expectations):
            while timed_values and timed_values[0][0] < expire_before:
                timed_values.popleft()
        while self._reply_times and self._reply_times[0] < expire_before:
            self._reply_times.popleft()

    def stats(self, now: float) -> dict[str, Any]:
        """窗口内预计、目标和实际回复数；不改变门控状态。"""
        self._prune(now)
        expected_proactive = sum(score for _, score in self._proactive_scores)
        expected_forced = sum(value for _, value in self._forced_expectations)
        return {
            "window_seconds": self._window_seconds,
            "scored_rounds": len(self._proactive_scores),
            "expected_proactive": expected_proactive,
            "expected_forced": expected_forced,
            "expected_replies": expected_proactive + expected_forced,
            "actual_replies": len(self._reply_times),
        }

    def evaluate(self, probability: float, frequency: float, now: float) -> DynamicGateDecision:
        """判断当前这批待处理消息是否放行进入 Planner。"""
        self._prune(now)
        proactive_scores = [score for _, score in self._proactive_scores]
        expected_proactive = sum(proactive_scores)
        expected_forced = sum(value for _, value in self._forced_expectations)
        expected_total = expected_proactive + expected_forced
        actual_replies = len(self._reply_times)
        normalized_frequency = min(1.0, max(0.0, frequency))
        target_replies = normalized_frequency * expected_total

        if normalized_frequency >= 1.0:
            keep_ratio = 1.0
        elif expected_proactive <= 0:
            keep_ratio = normalized_frequency
        else:
            feedforward = (target_replies - expected_forced) / expected_proactive
            feedback = min(1.0, max(0.0, 1.0 + (target_replies - actual_replies) / max(1.0, target_replies)))
            keep_ratio = min(1.0, max(0.0, feedforward * feedback))

        threshold = self._resolve_threshold(proactive_scores, expected_proactive, keep_ratio)
        return DynamicGateDecision(
            should_trigger=probability >= threshold,
            probability=probability,
            threshold=threshold,
            expected_replies=expected_total,
            target_replies=target_replies,
            actual_replies=actual_replies,
            keep_ratio=keep_ratio,
        )

    @staticmethod
    def _resolve_threshold(scores: Sequence[float], expected_proactive: float, keep_ratio: float) -> float:
        if keep_ratio >= 1.0:
            return 0.0
        if keep_ratio <= 0.0:
            return 1.0
        if len(scores) < MIN_WINDOW_SCORE_COUNT:
            return _static_threshold(keep_ratio)
        budget = keep_ratio * expected_proactive
        accumulated = 0.0
        for score in sorted(scores, reverse=True):
            accumulated += score
            if accumulated >= budget:
                return score
        return 0.0


@dataclass(frozen=True, slots=True)
class GateObservation:
    """一次普通群消息的门控观察结果。"""

    occurred_at: float
    probability: float
    threshold: float
    would_trigger: bool
    expected_replies: float
    target_replies: float
    actual_replies: int
    keep_ratio: float
    features: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return {
            "occurred_at": self.occurred_at,
            "probability": self.probability,
            "threshold": self.threshold,
            "would_trigger": self.would_trigger,
            "expected_replies": self.expected_replies,
            "target_replies": self.target_replies,
            "actual_replies": self.actual_replies,
            "keep_ratio": self.keep_ratio,
            "features": self.features,
        }


def _distribution(observations: Sequence[GateObservation]) -> list[dict[str, Any]]:
    """按 0.1 分桶统计观察到的概率分布。"""
    buckets = [0] * 10
    for observation in observations:
        buckets[min(9, max(0, int(observation.probability * 10)))] += 1
    return [
        {"bucket": f"{index / 10:.1f}-{(index + 1) / 10:.1f}", "count": count} for index, count in enumerate(buckets)
    ]


class ReplyGate:
    """单个租户的门控状态与观察记录；全部在内存。"""

    def __init__(self, tenant: str = "", clock: Callable[[], float] = time.time) -> None:
        self._tenant = tenant
        self._clock = clock
        self._gate = DynamicReplyGate()
        self._events: deque[tuple[float, bool]] = deque(maxlen=MAX_RECENT_EVENTS)
        self._observations: deque[GateObservation] = deque(maxlen=MAX_OBSERVATIONS)
        self._round_texts: list[str] = []
        self._round_has_at_bot = False
        self._round_at_other = False
        self._pending = 0
        self._backoff = IdleBackoff()

    def observe_message(
        self,
        message_ids: Sequence[str],
        texts: Sequence[str],
        *,
        has_at_bot: bool = False,
        at_other: bool = False,
        now: float | None = None,
    ) -> GateObservation:
        """记录一条普通群消息；返回本轮若启用门控时的判定。"""
        current = self._clock() if now is None else now
        clean = [str(text or "") for text in texts] or [""]
        new_ids = self._gate.take_uncounted_message_ids([str(item) for item in message_ids if item])
        if not new_ids:
            # 重复投递不重复计入供需和观察，但仍返回当前判定供路由复用
            snapshot, probability = self._likelihood(clean, has_at_bot, at_other, current, len(clean))
            return self._record(snapshot, probability, current, record=False)
        self._note_external(current, len(clean))
        self._pending += len(clean)
        if not self._gate.is_round_open(current):
            self._round_texts = []
            self._round_has_at_bot = False
            self._round_at_other = False
        self._round_texts.extend(clean)
        self._round_has_at_bot = self._round_has_at_bot or has_at_bot
        self._round_at_other = self._round_at_other or at_other
        _, round_probability = self._likelihood(
            self._round_texts, self._round_has_at_bot, self._round_at_other, current, len(self._round_texts)
        )
        self._gate.record_proactive_demand(round_probability, current)
        snapshot, probability = self._likelihood(clean, has_at_bot, at_other, current, len(clean))
        return self._record(snapshot, probability, current)

    def record_forced_turn(self, message_ids: Sequence[str], *, now: float | None = None) -> None:
        """记录 @、人格名等强制触发；同一批消息只计一次。"""
        current = self._clock() if now is None else now
        new_ids = self._gate.take_uncounted_message_ids([str(item) for item in message_ids if item])
        if not new_ids:
            return
        self._note_external(current, len(new_ids))
        self._gate.record_forced_turn(current)
        self._pending = 0
        self._backoff.reset()

    def record_reply(self, *, now: float | None = None) -> None:
        """记录一次实际发出的群回复。"""
        current = self._clock() if now is None else now
        self._events.append((current, True))
        self._gate.record_reply(current)
        self._pending = 0
        self._backoff.reset()

    def record_idle(self, *, now: float | None = None) -> None:
        """记录一次主动轮没有回复，驱动 no_action_backoff。"""
        cfg = reply_timing_config()
        current = self._clock() if now is None else now
        self._backoff.record_idle(
            current,
            base=_setting_int(cfg, "no_action_backoff_base_seconds"),
            cap=_setting_int(cfg, "no_action_backoff_cap_seconds"),
            start_count=_setting_int(cfg, "no_action_backoff_start_count", 1),
        )

    def backoff_delay(self, *, now: float | None = None, pending_count: int | None = None) -> float:
        """当前退避剩余秒数；待处理消息达到阈值时返回 0。"""
        cfg = reply_timing_config()
        current = self._clock() if now is None else now
        return self._backoff.delay(
            current,
            pending=self._pending if pending_count is None else pending_count,
            bypass_pending=_setting_int(cfg, "no_action_backoff_bypass_pending_count"),
        )

    def mark_triggered(self) -> None:
        """门控已放行，结束当前虚拟轮次并清空待处理计数。"""
        self._gate.close_round()
        self._pending = 0

    def reset_pending(self) -> None:
        """未实际启用门控时清空待处理计数，避免之后误触发退避绕过。"""
        self._pending = 0

    def _note_external(self, now: float, count: int) -> None:
        for _ in range(max(0, count)):
            self._events.append((now, False))

    def _recent(self, now: float) -> tuple[int, int, float]:
        """最近 5 分钟的自身发言数、总消息数和距上次发言秒数。"""
        cutoff = now - RECENT_WINDOW_SECONDS
        while self._events and self._events[0][0] < cutoff:
            self._events.popleft()
        self_count = 0
        total = 0
        last_self: float | None = None
        for occurred_at, is_self in self._events:
            total += 1
            if is_self:
                self_count += 1
                last_self = occurred_at
        seconds = DEFAULT_SECONDS_SINCE_BOT_MESSAGE if last_self is None else max(0.0, now - last_self)
        return self_count, total, seconds

    def _likelihood(
        self, texts: Sequence[str], has_at_bot: bool, at_other: bool, now: float, pending_count: int
    ) -> tuple[ReplyLikelihoodInput, float]:
        _, has_question, placeholder_only = summarize_pending_texts(texts, at_other=at_other)
        self_count, recent_total, seconds = self._recent(now)
        snapshot = ReplyLikelihoodInput(
            mention_bot=has_at_bot,
            at_other=at_other,
            has_question_mark=has_question,
            placeholder_only=placeholder_only,
            recent_self_ratio=self_count / max(1, recent_total),
            recent_message_count=recent_total,
            seconds_since_bot_message=seconds,
            pending_count=pending_count,
        )
        return snapshot, estimate_reply_probability(snapshot)

    def _record(
        self, snapshot: ReplyLikelihoodInput, probability: float, now: float, *, record: bool = True
    ) -> GateObservation:
        decision = self._gate.evaluate(probability, talk_value_for(self._tenant), now)
        observation = GateObservation(
            occurred_at=now,
            probability=probability,
            threshold=decision.threshold,
            would_trigger=decision.should_trigger,
            expected_replies=decision.expected_replies,
            target_replies=decision.target_replies,
            actual_replies=decision.actual_replies,
            keep_ratio=decision.keep_ratio,
            features={
                "mention_bot": snapshot.mention_bot,
                "at_other": snapshot.at_other,
                "has_question_mark": snapshot.has_question_mark,
                "placeholder_only": snapshot.placeholder_only,
                "recent_self_ratio": snapshot.recent_self_ratio,
                "recent_message_count": snapshot.recent_message_count,
                "seconds_since_bot_message": snapshot.seconds_since_bot_message,
                "pending_count": snapshot.pending_count,
            },
        )
        if record:
            self._observations.append(observation)
        return observation

    def snapshot(self, limit: int = MAX_OBSERVATIONS) -> dict[str, Any]:
        """返回窗口统计、最近观察和概率分布；不创建新状态。"""
        size = max(1, min(int(limit), MAX_OBSERVATIONS))
        observations = list(self._observations)[-size:]
        pending = self._pending
        now = self._clock()
        return {
            "window": self._gate.stats(now),
            "observations": [observation.as_dict() for observation in observations],
            "distribution": _distribution(observations),
            "backoff": {"pending": pending, "delay_seconds": self.backoff_delay(now=now)},
        }


class ReplyGateRegistry:
    """按租户保存门控观察状态，超出上限时淘汰最久未访问的租户。"""

    def __init__(self) -> None:
        self._gates: OrderedDict[str, ReplyGate] = OrderedDict()

    def get(self, tenant: str) -> ReplyGate:
        gate = self._gates.get(tenant)
        if gate is None:
            gate = ReplyGate(tenant)
            self._gates[tenant] = gate
        else:
            self._gates.move_to_end(tenant)
        while len(self._gates) > MAX_TENANTS:
            self._gates.popitem(last=False)
        return gate

    def peek(self, tenant: str) -> ReplyGate | None:
        """只读访问；没有观察记录时返回 None，避免控制台凭空造状态。"""
        return self._gates.get(tenant)

    def clear(self, tenant: str) -> None:
        self._gates.pop(tenant, None)


default_reply_gate_registry = ReplyGateRegistry()
