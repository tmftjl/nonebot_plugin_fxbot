"""引用回复的触发规则与连发计数。

公式来自 MaiBot（commit 062d45c，`src/maisaka/quote/{counter,rules}.py`）。
"""

from __future__ import annotations

import math
import time
import random
from typing import Any
from collections import OrderedDict
from dataclasses import dataclass

from ...config import get_manager as get_config_manager
from ...utils.cache import cache

QUOTE_FREQUENCIES = frozenset({"never", "occasionally", "often"})
_DOMAIN = "chat:quote"
_WINDOW = 100


def normalize_quote_frequency(value: object) -> str:
    """只接受三档频率，非法值回退 occasionally。"""
    frequency = str(value or "").strip().lower()
    return frequency if frequency in QUOTE_FREQUENCIES else "occasionally"


def quote_frequency(is_group: bool) -> str:
    """按群聊或私聊读取当前引用频率。"""
    try:
        behavior = get_config_manager().get_system()["chat"].get("behavior") or {}
    except Exception:
        behavior = {}
    key = "group_quote_frequency" if is_group else "private_quote_frequency"
    return normalize_quote_frequency(behavior.get(key))


@dataclass(frozen=True)
class QuoteContext:
    """发送前快照；target_distance 为 None 表示目标已离开最近窗口。"""

    target_distance: int | None
    previous_sender_id: str
    previous_seconds: float
    previous_burst: int


class QuoteCounter:
    """按会话记录消息序号与连发状态，只保留最近 100 条 ID。"""

    def __init__(self) -> None:
        self._positions: OrderedDict[str, int] = OrderedDict()
        self._sequence = 0
        self._sender_id = ""
        self._previous_time = 0.0
        self._burst = 0

    def record(self, message_id: str, sender_id: str) -> None:
        """登记一条收发消息；同一 ID 只计一次。"""
        if not message_id or message_id in self._positions:
            return
        self._sequence += 1
        self._positions[message_id] = self._sequence
        if len(self._positions) > _WINDOW:
            self._positions.popitem(last=False)
        self._burst = min(self._burst + 1, 16) if sender_id == self._sender_id else 1
        self._sender_id = sender_id
        self._previous_time = time.monotonic()

    def snapshot(self, message_id: str) -> QuoteContext | None:
        """读取目标距离与上一条消息状态；目标未登记时返回 None。"""
        position = self._positions.get(message_id)
        if position is None or not self._sender_id:
            return None
        return QuoteContext(
            target_distance=self._sequence - position,
            previous_sender_id=self._sender_id,
            previous_seconds=max(0.0, time.monotonic() - self._previous_time),
            previous_burst=self._burst,
        )


def quote_counter(tenant: str) -> QuoteCounter:
    """按租户取计数器。"""
    key = (_DOMAIN, tenant)
    counter = cache.get(key)
    if counter is None:
        counter = QuoteCounter()
        cache.set(key, counter)
    return counter


def forget_counter(tenant: str) -> None:
    """清空会话时一并丢掉计数。"""
    cache.pop((_DOMAIN, tenant), None)


def quote_probability(
    *,
    text_length: int,
    previous_sender_self: bool,
    previous_seconds: float,
    frequency: str,
    target_distance: int,
) -> float:
    """群聊八类基础概率乘以频率档位和目标距离系数。"""
    if target_distance < 0:
        raise ValueError("引用目标距离不能为负数")
    if frequency == "never":
        return 0.0
    if frequency not in ("occasionally", "often"):
        raise ValueError(f"未知的群聊引用频率：{frequency}")
    if text_length == 0:
        if previous_sender_self:
            probability = 0.005913471588878362 if previous_seconds <= 3 else 0.013812173277286514
        else:
            probability = 0.025472825783009516 if previous_seconds <= 60 else 0.0108627046866078
    elif previous_sender_self:
        probability = 0.011765162067321502 if previous_seconds <= 3 else 0.07760369512696548
    else:
        probability = 0.20934408508581265 if text_length <= 50 else 0.08614950202149084
    if target_distance == 0:
        distance_multiplier = 0.5
    elif target_distance <= 2:
        distance_multiplier = 1.0
    elif target_distance <= 5:
        distance_multiplier = 1.1
    else:
        distance_multiplier = 1.5
    return min(1.0, probability * (2 if frequency == "often" else 1) * distance_multiplier)


def private_quote_probability(
    *,
    text_length: int,
    previous_sender_self: bool,
    previous_seconds: float,
    previous_burst: int,
    target_distance: int,
    frequency: str,
) -> float:
    """私聊固定公式；往前第几条最多按第 32 条计算。"""
    if target_distance < 0:
        raise ValueError("引用目标距离不能为负数")
    if frequency == "never":
        return 0.0
    if frequency not in ("occasionally", "often"):
        raise ValueError(f"未知的私聊引用频率：{frequency}")
    z = -4.176576611204103
    if previous_sender_self:
        z += -2.0390866101745555 if previous_seconds <= 3 else 0.407259409803155
    else:
        z += 0.6811570734750046 * math.log2(max(1, min(previous_burst, 16)))
    if text_length == 0:
        z -= 3.4527659205203576
    z += 0.521800209819322 * math.log2(min(target_distance + 1, 32))
    probability = 1 / (1 + math.exp(-z))
    return min(1.0, probability * (2 if frequency == "often" else 1))


def decide_quote(
    *,
    context: QuoteContext | None,
    bot_user_id: str,
    text: str,
    frequency: str,
    is_group: bool,
) -> tuple[bool, float]:
    """窗口内按公式抽签；窗口外目标直接引用；never 档优先。"""
    if frequency == "never":
        return False, 0.0
    if frequency not in ("occasionally", "often"):
        raise ValueError(f"未知的引用频率：{frequency}")
    if context is None or context.target_distance is None:
        return True, 1.0
    common: dict[str, Any] = {
        "text_length": len(text.strip()),
        "previous_sender_self": context.previous_sender_id == bot_user_id,
        "previous_seconds": context.previous_seconds,
        "target_distance": context.target_distance,
        "frequency": frequency,
    }
    probability = (
        quote_probability(**common)
        if is_group
        else private_quote_probability(previous_burst=context.previous_burst, **common)
    )
    return random.random() < probability, probability
