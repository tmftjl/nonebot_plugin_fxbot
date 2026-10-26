"""全局默认配置。"""

from __future__ import annotations

from typing import Any

SYSTEM_DEFAULTS = {
    "membership": {
        "enabled": False,
        "free_bot_ids": [],
        "expire_notice_days": 7,
        "expire_prompt_text_prefixes": ["ww"],
        "auto_leave_expired_groups": False,
        "enable_scheduler": True,
        "schedule_time": "00:00",
        "batch_delay_seconds": 5,
        "renewal_contact": "",
    },
    "system": {
        "token": "",
        "ignored_mention_bot_ids": [],
        "bot_admins": [],
    },
    "chat": {
        "enabled": False,
        "command_prefixes": ["gs", "#", "ww"],
        "private_enabled": False,
        "private_allowlist": [],
        "group_allowlist": [],
        "persona": "小鱼",
        "provider": "",
        "providers": {},
        "max_history": 50,
        "image_description": True,
        "enabled_tools": [],
        "memory": {
            "history_enabled": True,
            "learn_enabled": False,
        },
        "web_search": {
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        },
    },
}


def validate_system_config(data: dict[str, Any]) -> None:
    """校验对话相关配置；非法值直接抛出，由调用方决定拒绝保存或回退默认。"""
    if not isinstance(data, dict):
        raise ValueError("配置必须是对象")

    chat = data.get("chat", {})
    if not isinstance(chat, dict):
        raise ValueError("chat 必须是对象")

    max_history = chat.get("max_history", SYSTEM_DEFAULTS["chat"]["max_history"])
    if isinstance(max_history, bool) or not isinstance(max_history, int) or max_history < 0:
        raise ValueError("chat.max_history 必须是非负整数")

    memory = chat.get("memory", {})
    if not isinstance(memory, dict):
        raise ValueError("chat.memory 必须是对象")
    for key in ("history_enabled", "learn_enabled"):
        if key in memory and not isinstance(memory[key], bool):
            raise ValueError(f"chat.memory.{key} 必须是布尔值")
