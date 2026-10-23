"""B 站直播订阅和状态存储。"""

from __future__ import annotations

import json
import time
from copy import deepcopy
from typing import Any

from nonebot import logger

from .client import LiveRoomSnapshot
from ...utils.cache import cache
from ...utils.paths import data_dir

DATA_FILE = data_dir("bilibili_live") / "state.json"
_STATE_KEY = ("bilibili_live", "state")


def _default() -> dict[str, Any]:
    return {"subscriptions": [], "room_states": {}}


def _read() -> dict[str, Any]:
    """从磁盘读取插件状态。"""
    if not DATA_FILE.exists():
        return _default()
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("subscriptions", [])
            data.setdefault("room_states", {})
            return data
    except Exception:
        logger.opt(exception=True).warning("[bilibili_live] 状态文件加载失败")
    return _default()


def load_state() -> dict[str, Any]:
    """加载插件状态，返回可安全修改的副本。"""
    return deepcopy(cache.remember(_STATE_KEY, _read))


def save_state(data: dict[str, Any]) -> None:
    """保存插件状态并同步缓存。"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    cache.set(_STATE_KEY, deepcopy(data))


def get_subscriptions() -> list[dict[str, Any]]:
    """返回全部有效订阅会话。"""
    subscriptions = load_state().get("subscriptions", [])
    return [item for item in subscriptions if isinstance(item, dict)] if isinstance(subscriptions, list) else []


def _key_field(sub_type: str) -> str:
    return "group_key" if sub_type == "group" else "user_key"


def get_subscription(sub_type: str, sub_key: str) -> dict[str, Any] | None:
    """获取指定会话的订阅。"""
    key_field = _key_field(sub_type)
    for item in get_subscriptions():
        if item.get("type") == sub_type and str(item.get(key_field) or "") == sub_key:
            return item
    return None


def add_room(
    sub_type: str,
    sub_key: str,
    target: dict[str, Any],
    room: LiveRoomSnapshot,
    operator_id: str,
) -> bool:
    """向会话添加直播间订阅，返回是否为新订阅。"""
    state = load_state()
    subscriptions = state.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        subscriptions = []

    key_field = _key_field(sub_type)
    entry = next(
        (
            item
            for item in subscriptions
            if isinstance(item, dict) and item.get("type") == sub_type and str(item.get(key_field) or "") == sub_key
        ),
        None,
    )
    if entry is None:
        entry = {
            "type": sub_type,
            key_field: sub_key,
            "target": target,
            "operator_id": operator_id,
            "rooms": {},
        }
        subscriptions.append(entry)
    else:
        entry["target"] = target
        entry["operator_id"] = operator_id

    rooms = entry.get("rooms")
    if not isinstance(rooms, dict):
        rooms = {}
        entry["rooms"] = rooms
    room_key = str(room.room_id)
    created = room_key not in rooms
    rooms[room_key] = room.to_record()
    state["subscriptions"] = subscriptions
    save_state(state)
    return created


def remove_room(sub_type: str, sub_key: str, room_id: int) -> bool:
    """从会话移除直播间订阅。"""
    state = load_state()
    subscriptions = state.get("subscriptions", [])
    if not isinstance(subscriptions, list):
        return False

    key_field = _key_field(sub_type)
    removed = False
    retained: list[dict[str, Any]] = []
    for item in subscriptions:
        if not isinstance(item, dict):
            continue
        is_target = item.get("type") == sub_type and str(item.get(key_field) or "") == sub_key
        if is_target:
            rooms = item.get("rooms")
            if isinstance(rooms, dict) and rooms.pop(str(room_id), None) is not None:
                removed = True
            if rooms:
                retained.append(item)
        else:
            retained.append(item)

    if removed:
        state["subscriptions"] = retained
        save_state(state)
    return removed


def get_room_records() -> list[dict[str, Any]]:
    """汇总所有会话订阅的直播间，按真实房间号去重。"""
    records: dict[str, dict[str, Any]] = {}
    for subscription in get_subscriptions():
        rooms = subscription.get("rooms")
        if not isinstance(rooms, dict):
            continue
        for room_id, room in rooms.items():
            if isinstance(room, dict):
                records.setdefault(str(room_id), room)
    return list(records.values())


def get_room_subscriptions(room_id: int) -> list[dict[str, Any]]:
    """获取订阅指定直播间的全部会话。"""
    room_key = str(room_id)
    result: list[dict[str, Any]] = []
    for subscription in get_subscriptions():
        rooms = subscription.get("rooms")
        if isinstance(rooms, dict) and room_key in rooms:
            result.append(subscription)
    return result


def get_room_state(room_id: int) -> bool | None:
    """读取直播间上次已知的开播状态。"""
    states = load_state().get("room_states", {})
    if not isinstance(states, dict):
        return None
    item = states.get(str(room_id))
    if not isinstance(item, dict) or not isinstance(item.get("is_live"), bool):
        return None
    return item["is_live"]


def set_room_state(room_id: int, is_live: bool) -> None:
    """保存直播间当前开播状态，状态未变化时不重写文件。"""
    state = load_state()
    states = state.get("room_states")
    if not isinstance(states, dict):
        states = {}
        state["room_states"] = states
    current = states.get(str(room_id))
    if isinstance(current, dict) and current.get("is_live") is is_live:
        return
    states[str(room_id)] = {"is_live": is_live, "checked_at": int(time.time())}
    save_state(state)
