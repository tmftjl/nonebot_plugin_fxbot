"""远行商人订阅状态存储。"""

from __future__ import annotations

import json
from typing import Any

from nonebot import logger

from ...utils.paths import data_dir

DATA_FILE = data_dir("rocom_merchant") / "state.json"


def _default() -> dict[str, Any]:
    return {"last_signature": "", "subscriptions": []}


def load_state() -> dict[str, Any]:
    """加载订阅状态。"""
    if not DATA_FILE.exists():
        return _default()
    try:
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("last_signature", "")
            data.setdefault("subscriptions", [])
            return data
    except Exception:
        logger.opt(exception=True).warning("[rocom_merchant] 订阅状态加载失败")
    return _default()


def save_state(data: dict[str, Any]) -> None:
    """保存订阅状态。"""
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_subscriptions() -> list[dict[str, Any]]:
    """获取所有订阅。"""
    subs = load_state().get("subscriptions", [])
    return subs if isinstance(subs, list) else []


def upsert_subscription(group_key: str, target: dict[str, Any], keywords: list[str], operator_id: str) -> None:
    """新增或更新群订阅。"""
    state = load_state()
    subs = [item for item in get_subscriptions() if item.get("group_key") != group_key]
    subs.append(
        {
            "group_key": group_key,
            "target": target,
            "keywords": keywords,
            "operator_id": operator_id,
        }
    )
    state["subscriptions"] = subs
    save_state(state)


def remove_subscription(group_key: str) -> bool:
    """删除群订阅。"""
    state = load_state()
    old = get_subscriptions()
    new = [item for item in old if item.get("group_key") != group_key]
    state["subscriptions"] = new
    save_state(state)
    return len(new) != len(old)


def get_subscription(group_key: str) -> dict[str, Any] | None:
    """获取单个群订阅。"""
    for item in get_subscriptions():
        if item.get("group_key") == group_key:
            return item
    return None


def get_last_signature() -> str:
    """获取最后推送签名。"""
    return str(load_state().get("last_signature") or "")


def set_last_signature(signature: str) -> None:
    """保存最后推送签名。"""
    state = load_state()
    state["last_signature"] = signature
    save_state(state)
