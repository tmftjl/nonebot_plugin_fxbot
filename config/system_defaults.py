"""全局默认配置。"""

from __future__ import annotations

import re
from typing import Any

_TIME_RANGE = re.compile(r"^([01]?\d|2[0-3]):[0-5]\d-([01]?\d|2[0-3]):[0-5]\d$")

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
        "jargon": {"enabled": True, "inject_max": 5},
        "expression": {"enabled": True, "learn": True, "llm_select": False, "sample_size": 50, "inject_top_k": 5},
        "proactive": {
            "enabled": False,
            "gate_mode": "observe",
            "talk_value": 1.0,
            "enable_talk_value_rules": False,
            "talk_value_rules": [
                {"tenant": "", "time": "00:00-08:59", "value": 0.8},
                {"tenant": "", "time": "09:00-18:59", "value": 1.0},
            ],
            "group_allowlist": [],
            "no_action_backoff_base_seconds": 15,
            "no_action_backoff_cap_seconds": 300,
            "no_action_backoff_start_count": 2,
            "no_action_backoff_bypass_pending_count": 6,
        },
        "planner": {"enabled": False, "mode": "simple", "max_rounds": 1, "wait_seconds": 60},
        "mood": {"enabled": True, "decay_per_day": 0.1},
        "behavior": {
            "anti_repeat": True,
            "anti_repeat_threshold": 85,
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

    jargon = chat.get("jargon", {})
    if not isinstance(jargon, dict):
        raise ValueError("chat.jargon 必须是对象")
    if "enabled" in jargon and not isinstance(jargon["enabled"], bool):
        raise ValueError("chat.jargon.enabled 必须是布尔值")
    inject_max = jargon.get("inject_max", SYSTEM_DEFAULTS["chat"]["jargon"]["inject_max"])
    if isinstance(inject_max, bool) or not isinstance(inject_max, int) or not 0 <= inject_max <= 20:
        raise ValueError("chat.jargon.inject_max 必须是 0 到 20 的整数")

    expression = chat.get("expression", {})
    if not isinstance(expression, dict):
        raise ValueError("chat.expression 必须是对象")
    for key in ("enabled", "learn", "llm_select"):
        if key in expression and not isinstance(expression[key], bool):
            raise ValueError(f"chat.expression.{key} 必须是布尔值")
    sample_size = expression.get("sample_size", SYSTEM_DEFAULTS["chat"]["expression"]["sample_size"])
    if isinstance(sample_size, bool) or not isinstance(sample_size, int) or not 1 <= sample_size <= 200:
        raise ValueError("chat.expression.sample_size 必须是 1 到 200 的整数")
    top_k = expression.get("inject_top_k", SYSTEM_DEFAULTS["chat"]["expression"]["inject_top_k"])
    if isinstance(top_k, bool) or not isinstance(top_k, int) or not 1 <= top_k <= 20:
        raise ValueError("chat.expression.inject_top_k 必须是 1 到 20 的整数")

    planner = chat.get("planner", {})
    if not isinstance(planner, dict):
        raise ValueError("chat.planner 必须是对象")
    for key in ("enabled",):
        if key in planner and not isinstance(planner[key], bool):
            raise ValueError(f"chat.planner.{key} 必须是布尔值")
    mode = planner.get("mode", SYSTEM_DEFAULTS["chat"]["planner"]["mode"])
    if mode not in {"simple", "complex"}:
        raise ValueError("chat.planner.mode 只能是 simple 或 complex")
    rounds = planner.get("max_rounds", SYSTEM_DEFAULTS["chat"]["planner"]["max_rounds"])
    if isinstance(rounds, bool) or not isinstance(rounds, int) or rounds != 1:
        raise ValueError("chat.planner.max_rounds 只能是 1")
    wait_seconds = planner.get("wait_seconds", SYSTEM_DEFAULTS["chat"]["planner"]["wait_seconds"])
    if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, int) or not 30 <= wait_seconds <= 120:
        raise ValueError("chat.planner.wait_seconds 必须是 30 到 120 的整数")

    mood = chat.get("mood", {})
    if not isinstance(mood, dict):
        raise ValueError("chat.mood 必须是对象")
    if "enabled" in mood and not isinstance(mood["enabled"], bool):
        raise ValueError("chat.mood.enabled 必须是布尔值")
    decay = mood.get("decay_per_day", SYSTEM_DEFAULTS["chat"]["mood"]["decay_per_day"])
    if isinstance(decay, bool) or not isinstance(decay, (int, float)) or not 0 <= decay <= 1:
        raise ValueError("chat.mood.decay_per_day 必须是 0 到 1 之间的数字")

    proactive = chat.get("proactive", {})
    if not isinstance(proactive, dict):
        raise ValueError("chat.proactive 必须是对象")
    for key in ("enabled", "enable_talk_value_rules"):
        if key in proactive and not isinstance(proactive[key], bool):
            raise ValueError(f"chat.proactive.{key} 必须是布尔值")
    gate_mode = proactive.get("gate_mode", SYSTEM_DEFAULTS["chat"]["proactive"]["gate_mode"])
    if gate_mode not in {"observe", "enforce"}:
        raise ValueError("chat.proactive.gate_mode 只能是 observe 或 enforce")
    talk_value = proactive.get("talk_value", SYSTEM_DEFAULTS["chat"]["proactive"]["talk_value"])
    if isinstance(talk_value, bool) or not isinstance(talk_value, (int, float)) or not 0 <= talk_value <= 1:
        raise ValueError("chat.proactive.talk_value 必须是 0 到 1 的数字")
    allowlist = proactive.get("group_allowlist", SYSTEM_DEFAULTS["chat"]["proactive"]["group_allowlist"])
    if not isinstance(allowlist, (list, tuple, set)) or any(not isinstance(item, str) for item in allowlist):
        raise ValueError("chat.proactive.group_allowlist 必须是字符串数组")
    rules = proactive.get("talk_value_rules", SYSTEM_DEFAULTS["chat"]["proactive"]["talk_value_rules"])
    if not isinstance(rules, list):
        raise ValueError("chat.proactive.talk_value_rules 必须是数组")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("chat.proactive.talk_value_rules 的每一项必须是对象")
        if not isinstance(rule.get("tenant", ""), str):
            raise ValueError("chat.proactive.talk_value_rules.tenant 必须是字符串")
        if not _TIME_RANGE.fullmatch(str(rule.get("time") or "")):
            raise ValueError("chat.proactive.talk_value_rules.time 必须形如 09:00-18:59")
        value = rule.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("chat.proactive.talk_value_rules.value 必须是 0 到 1 的数字")
    for key, minimum in (
        ("no_action_backoff_base_seconds", 0),
        ("no_action_backoff_cap_seconds", 0),
        ("no_action_backoff_start_count", 1),
        ("no_action_backoff_bypass_pending_count", 0),
    ):
        value = proactive.get(key, SYSTEM_DEFAULTS["chat"]["proactive"][key])
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"chat.proactive.{key} 必须是不小于 {minimum} 的整数")

    behavior = chat.get("behavior", {})
    if not isinstance(behavior, dict):
        raise ValueError("chat.behavior 必须是对象")
    if "anti_repeat" in behavior and not isinstance(behavior["anti_repeat"], bool):
        raise ValueError("chat.behavior.anti_repeat 必须是布尔值")
    threshold = behavior.get("anti_repeat_threshold", 85)
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 0 <= threshold <= 100:
        raise ValueError("chat.behavior.anti_repeat_threshold 必须是 0 到 100 的整数")

    routing = chat.get("model_routing", {})
    if not isinstance(routing, dict):
        raise ValueError("chat.model_routing 必须是对象")
    if any(not isinstance(value, str) for value in routing.values()):
        raise ValueError("chat.model_routing 的值必须是字符串")
