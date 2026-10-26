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
            "extract_interval_seconds": 300,
            "max_tenants_per_tick": 3,
            "algorithm_version": 1,
            "episode_idle_minutes": 20,
            "episode_max_messages": 80,
            "episode_max_hours": 2,
            "episode_min_messages": 10,
            "profile_fact_threshold": 20,
            "history_retention_days": 30,
            "episode_retention_days": 180,
            "fact_retention_days": 180,
            "max_facts_per_tenant": 2000,
            "max_history_chars": 12000,
            "max_input_chars_per_batch": 6000,
            "backfill_messages": 500,
            "backfill_batch": 40,
            "inject_profile_chars": 300,
            "inject_facts_chars": 400,
            "inject_episodes_chars": 300,
            "inject_style_chars": 150,
            "inject_mood_chars": 50,
            "inject_jargon_chars": 100,
            "inject_expressions_chars": 200,
            "inject_total_chars": 1500,
            "background_concurrency": 2,
            "max_model_calls_per_hour": 60,
            "max_images_per_hour": 30,
            "payload_mode": "whitelist",
        },
        "privacy": {
            "image_description_learn": False,
            "style_learn": False,
            "expression_learn": False,
            "group_opt_out_command": True,
        },
        "model_routing": {
            "reply": "",
            "extract": "",
            "profile": "",
            "style": "",
            "planner": "",
            "vision": "",
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
    for key, default in SYSTEM_DEFAULTS["chat"]["memory"].items():
        if key not in memory:
            continue
        if isinstance(default, bool):
            if not isinstance(memory[key], bool):
                raise ValueError(f"chat.memory.{key} 必须是布尔值")
        elif isinstance(default, int):
            if isinstance(memory[key], bool) or not isinstance(memory[key], int) or memory[key] < 0:
                raise ValueError(f"chat.memory.{key} 必须是非负整数")
    total = memory.get("inject_total_chars", SYSTEM_DEFAULTS["chat"]["memory"]["inject_total_chars"])
    fixed = memory.get("inject_profile_chars", 0) + memory.get("inject_facts_chars", 0)
    if isinstance(total, int) and isinstance(fixed, int) and total < fixed:
        raise ValueError("chat.memory.inject_total_chars 不能小于画像与事实预算之和")

    privacy = chat.get("privacy", {})
    if not isinstance(privacy, dict):
        raise ValueError("chat.privacy 必须是对象")
    for key, default in SYSTEM_DEFAULTS["chat"]["privacy"].items():
        if isinstance(default, bool) and key in privacy and not isinstance(privacy[key], bool):
            raise ValueError(f"chat.privacy.{key} 必须是布尔值")

    routing = chat.get("model_routing", {})
    if not isinstance(routing, dict):
        raise ValueError("chat.model_routing 必须是对象")
    if any(not isinstance(value, str) for value in routing.values()):
        raise ValueError("chat.model_routing 的值必须是字符串")
