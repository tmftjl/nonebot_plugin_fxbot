"""全局默认配置。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlsplit

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
        "max_context_size": 30,
        "max_private_context_size": 60,
        "message_receive": {
            "image_parse_threshold": 5,
            "ban_words": [],
            "ban_msgs_regex": [],
            "keyword_rules": [],
            "regex_rules": [],
        },
        "jargon": {"learning_list": [], "groups": []},
        "expression": {
            "checked_only": False,
            "self_reflect": True,
            "max_expression_learner": 3,
            "learning_list": [],
            "groups": [],
            "vector": {
                "enabled": False,
                "index_path": "data/expression_selection/expression_vector_index.json",
                "candidate_pool_size": 50,
            },
        },
        "reply_timing": {
            "message_debounce_mode": "natural",
            "talk_value": 1.0,
            "private_reply_paused": False,
            "private_reply_pause_rules": [],
            "mentioned_bot_reply": True,
            "inevitable_at_reply": True,
            "planner_message_steering": False,
            "enable_talk_value_rules": False,
            "talk_value_rules": [
                {"tenant": "", "time": "00:00-08:59", "value": 0.8},
                {"tenant": "", "time": "09:00-18:59", "value": 1.0},
            ],
            "no_action_backoff_base_seconds": 15,
            "no_action_backoff_cap_seconds": 300,
            "no_action_backoff_start_count": 2,
            "no_action_backoff_bypass_pending_count": 6,
        },
        "planner": {
            "mode": "simple",
            "wait_seconds": 60,
            "context_summary_max_chars": 6000,
            "passive_replyer": False,
        },
        "focus": {
            "enabled": False,
            "on_private": False,
            "chat_whitelist": [],
            "groups": [],
        },
        "behavior": {
            "group_quote_frequency": "occasionally",
            "private_quote_frequency": "occasionally",
            "enable_reply_at": True,
            "persona_aliases": [],
            "group_chat_prompt": (
                "下面是聊天中的的内容，聊天中包含文字，图片和表情包等消息。\n"
                "回复尽量简短一些。最好一次对一个话题进行回复，但必须考虑不同群友发言之间的交互，免得啰嗦或者回复内容太乱。\n"
                "根据聊天内容自由发挥，但是要日常不浮夸，不要刻意找话题。\n"
                "不用刻意回复其他人发送的表情包。你可以适当发送表情包表达情绪。"
                "如果有人不喜欢你或者不理你，就不要强行回复，回复前读空气。"
                "不要每个人的消息都回复，适当回复其他话题。可以适当回过头回复之前用户的消息。"
            ),
            "private_chat_prompts": (
                "你正在聊天，下面是正在聊的内容，其中包含聊天记录和聊天中的图片。\n"
                "回复尽量简短一些。请注意把握聊天内容。\n"
                "请考虑对方的发言频率，想法，思考自己何时回复以及回复内容。"
            ),
            "multiple_reply_style": [
                "你的风格平淡但不失讽刺，很简短,很白话。可以参考贴吧，微博的回复风格。",
                "用1-2个字进行回复",
                "用1-2个符号进行回复",
                "言辭凝練古雅，穿插《論語》經句卻不晦澀，以文言短句為基，輔以淺白語意，持長者溫和風範，全用繁體字表達，具先秦儒者談吐韻致。",
                "带点翻译腔，但不要太长",
            ],
            "multiple_probability": 0.0,
            "chat_prompts": [],
        },
        "attention_drift": {
            "enabled": False,
            "drift_level": "scattered",
            "anchor_policy": "balanced",
            "reaction_style": "lively",
        },
        "visual": {
            "image_quality": "normal",
            "max_image_num": 64,
            "max_image_size_mb": 16,
            "wait_image_recognize_max_time": 32,
            "handle_oversized_images": True,
            "oversized_image_handle_method": "compress",
        },
        "voice": {
            "enable_asr": False,
            "enable_tts": False,
        },
        "response_post_process": {
            "enable_response_post_process": True,
            "typing_speed": 1.0,
        },
        "response_splitter": {
            "mode": "rule",
            "enable": True,
            "max_length": 512,
            "max_sentence_num": 8,
            "max_split_num": 3,
            "enable_kaomoji_protection": False,
            "enable_overflow_return_all": False,
        },
        "typo": {
            "enable": True,
            "error_rate": 0.01,
            "min_freq": 9,
            "tone_error_rate": 0.1,
            "word_replace_rate": 0.006,
            "enable_correction_quote": True,
            "correction_quote_probability": 1.0,
        },
        "emoji": {
            "use_new_send_logic": False,
            "emoji_send_num": 25,
            "max_reg_num": 64,
            "do_replace": True,
            "check_interval": 10,
            "steal_emoji": True,
            "max_emoji_size_mb": 5,
            "content_filtration": False,
            "cache_cleanup": {
                "enabled": True,
                "check_interval_hours": 6,
                "emoji_file_retention_days": 30,
                "no_file_record_retention_days": 30,
            },
        },
        "model_routing": {
            "reply": "",
            "extract": "",
            "style": "",
            "planner": "",
            "vision": "",
            "memory": "",
            "utils": "",
            "learner": "",
            "fast_model": "",
            "emoji": "",
            "voice": "",
            "tts": "",
            "embedding": "",
            "image_embedding": "",
            "web_search": "",
        },
        "provider_fallback": [],
        "capabilities": {
            "tool_result_condense": False,
            "tool_result_condense_threshold": 3000,
            "planner_subagent": False,
            "advanced_sub_agent": False,
        },
        "web": {
            "preview_chars": 4000,
            "services": [],
        },
    },
}


def migrate_system_config(raw: dict[str, Any]) -> bool:
    """把旧的 chat.web_search 迁移为 web.services；返回是否有改动。"""
    chat = raw.get("chat")
    if not isinstance(chat, dict) or "web_search" not in chat:
        return False
    legacy = chat.pop("web_search")
    if not isinstance(legacy, dict):
        return True
    api_key = str(legacy.get("api_key") or "").strip()
    if not api_key:
        return True
    web = chat.setdefault("web", {})
    if not isinstance(web, dict):
        return True
    services = web.setdefault("services", [])
    if isinstance(services, list) and not any(
        isinstance(entry, dict) and entry.get("name") == "deepseek" for entry in services
    ):
        services.append(
            {
                "name": "deepseek",
                "adapter": "deepseek",
                "base_url": str(legacy.get("base_url") or ""),
                "api_key": api_key,
                "timeout": 60,
                "search_depth": "basic",
                "extract_depth": "basic",
            }
        )
        routing = chat.setdefault("model_routing", {})
        if isinstance(routing, dict):
            routing["web_search"] = "deepseek"
    return True


def _validate_target_list(items: Any, name: str) -> None:
    """校验一张平台目标表；共享组与 Focus 白名单共用。"""
    if not isinstance(items, list):
        raise ValueError(f"{name} 必须是数组")
    for target in items:
        if not isinstance(target, dict):
            raise ValueError(f"{name} 每项必须是对象")
        for key in ("platform", "item_id"):
            if not isinstance(target.get(key), str) or not target[key].strip():
                raise ValueError(f"{name}.{key} 不能为空")
        if target.get("type") not in {"group", "private"}:
            raise ValueError(f"{name}.type 只能是 group 或 private")


def _validate_learning_config(settings: dict[str, Any], name: str) -> None:
    """校验 learning_list 与共享组目标；格式错误直接拒绝保存。"""
    entries = settings.get("learning_list")
    if entries is not None:
        if not isinstance(entries, list):
            raise ValueError(f"{name}.learning_list 必须是数组")
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError(f"{name}.learning_list 每项必须是对象")
            for key in ("platform", "item_id"):
                if key in entry and not isinstance(entry[key], str):
                    raise ValueError(f"{name}.learning_list.{key} 必须是字符串")
            if entry.get("type", "") not in {"", "group", "private"}:
                raise ValueError(f"{name}.learning_list.type 只能是 group 或 private")
            for key in ("use", "learn"):
                if key in entry and not isinstance(entry[key], bool):
                    raise ValueError(f"{name}.learning_list.{key} 必须是布尔值")

    groups = settings.get("groups")
    if groups is None:
        return
    if not isinstance(groups, list):
        raise ValueError(f"{name}.groups 必须是数组")
    for group in groups:
        targets = group.get("targets") if isinstance(group, dict) else None
        if not isinstance(targets, list):
            raise ValueError(f"{name}.groups 每项必须是包含 targets 数组的对象")
        _validate_target_list(targets, f"{name}.groups.targets")


def validate_system_config(data: dict[str, Any]) -> None:
    """校验对话相关配置；非法值直接抛出，由调用方决定拒绝保存或回退默认。"""
    if not isinstance(data, dict):
        raise ValueError("配置必须是对象")

    chat = data.get("chat", {})
    if not isinstance(chat, dict):
        raise ValueError("chat 必须是对象")

    for key in ("max_context_size", "max_private_context_size"):
        limit = chat.get(key, SYSTEM_DEFAULTS["chat"][key])
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError(f"chat.{key} 必须是非负整数")

    jargon = chat.get("jargon", {})
    if not isinstance(jargon, dict):
        raise ValueError("chat.jargon 必须是对象")
    _validate_learning_config(jargon, "chat.jargon")

    expression = chat.get("expression", {})
    if not isinstance(expression, dict):
        raise ValueError("chat.expression 必须是对象")
    for key in ("checked_only", "self_reflect"):
        if key in expression and not isinstance(expression[key], bool):
            raise ValueError(f"chat.expression.{key} 必须是布尔值")
    learner = expression.get("max_expression_learner", SYSTEM_DEFAULTS["chat"]["expression"]["max_expression_learner"])
    if isinstance(learner, bool) or not isinstance(learner, int) or not 1 <= learner <= 20:
        raise ValueError("chat.expression.max_expression_learner 必须是 1 到 20 的整数")
    _validate_learning_config(expression, "chat.expression")
    if "vector" in expression:
        vector = expression["vector"]
        if not isinstance(vector, dict):
            raise ValueError("chat.expression.vector 必须是对象")
        if "enabled" in vector and not isinstance(vector["enabled"], bool):
            raise ValueError("chat.expression.vector.enabled 必须是布尔值")
        if "index_path" in vector and (not isinstance(vector["index_path"], str) or not vector["index_path"].strip()):
            raise ValueError("chat.expression.vector.index_path 不能为空")
        pool = vector.get("candidate_pool_size", 50)
        if isinstance(pool, bool) or not isinstance(pool, int) or not 1 <= pool <= 50:
            raise ValueError("chat.expression.vector.candidate_pool_size 必须是 1 到 50 的整数")

    planner = chat.get("planner", {})
    if not isinstance(planner, dict):
        raise ValueError("chat.planner 必须是对象")
    mode = planner.get("mode", SYSTEM_DEFAULTS["chat"]["planner"]["mode"])
    if mode not in {"simple", "complex"}:
        raise ValueError("chat.planner.mode 只能是 simple 或 complex")
    wait_seconds = planner.get("wait_seconds", SYSTEM_DEFAULTS["chat"]["planner"]["wait_seconds"])
    if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, int) or not 30 <= wait_seconds <= 120:
        raise ValueError("chat.planner.wait_seconds 必须是 30 到 120 的整数")
    summary_chars = planner.get(
        "context_summary_max_chars", SYSTEM_DEFAULTS["chat"]["planner"]["context_summary_max_chars"]
    )
    if isinstance(summary_chars, bool) or not isinstance(summary_chars, int) or not 1000 <= summary_chars <= 8000:
        raise ValueError("chat.planner.context_summary_max_chars 必须是 1000 到 8000 的整数")
    passive = planner.get("passive_replyer", SYSTEM_DEFAULTS["chat"]["planner"]["passive_replyer"])
    if not isinstance(passive, bool):
        raise ValueError("chat.planner.passive_replyer 必须是布尔值")

    focus = chat.get("focus", {})
    if not isinstance(focus, dict):
        raise ValueError("chat.focus 必须是对象")
    for key in ("enabled", "on_private"):
        if key in focus and not isinstance(focus[key], bool):
            raise ValueError(f"chat.focus.{key} 必须是布尔值")
    if "chat_whitelist" in focus:
        _validate_target_list(focus["chat_whitelist"], "chat.focus.chat_whitelist")
    if "groups" in focus:
        groups = focus["groups"]
        if not isinstance(groups, list):
            raise ValueError("chat.focus.groups 必须是数组")
        for group in groups:
            targets = group.get("targets") if isinstance(group, dict) else None
            if not isinstance(targets, list):
                raise ValueError("chat.focus.groups 每项必须是包含 targets 数组的对象")
            _validate_target_list(targets, "chat.focus.groups.targets")

    typo = chat.get("typo", {})
    if not isinstance(typo, dict):
        raise ValueError("chat.typo 必须是对象")
    for key in ("enable", "enable_correction_quote"):
        if key in typo and not isinstance(typo[key], bool):
            raise ValueError(f"chat.typo.{key} 必须是布尔值")
    for key in ("error_rate", "tone_error_rate", "word_replace_rate", "correction_quote_probability"):
        value = typo.get(key, SYSTEM_DEFAULTS["chat"]["typo"][key])
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError(f"chat.typo.{key} 必须是 0 到 1 之间的数字")
    min_freq = typo.get("min_freq", SYSTEM_DEFAULTS["chat"]["typo"]["min_freq"])
    if isinstance(min_freq, bool) or not isinstance(min_freq, int) or not 0 <= min_freq <= 1000:
        raise ValueError("chat.typo.min_freq 必须是 0 到 1000 之间的整数")

    emoji = chat.get("emoji", {})
    if not isinstance(emoji, dict):
        raise ValueError("chat.emoji 必须是对象")
    for key in ("do_replace", "steal_emoji", "content_filtration", "use_new_send_logic"):
        if key in emoji and not isinstance(emoji[key], bool):
            raise ValueError(f"chat.emoji.{key} 必须是布尔值")
    send_num = emoji.get("emoji_send_num", SYSTEM_DEFAULTS["chat"]["emoji"]["emoji_send_num"])
    if isinstance(send_num, bool) or not isinstance(send_num, int) or not 1 <= send_num <= 64:
        raise ValueError("chat.emoji.emoji_send_num 必须是 1 到 64 的整数")
    max_reg = emoji.get("max_reg_num", SYSTEM_DEFAULTS["chat"]["emoji"]["max_reg_num"])
    if isinstance(max_reg, bool) or not isinstance(max_reg, int) or not 0 <= max_reg <= 10000:
        raise ValueError("chat.emoji.max_reg_num 必须是 0 到 10000 的整数")
    check_interval = emoji.get("check_interval", SYSTEM_DEFAULTS["chat"]["emoji"]["check_interval"])
    if isinstance(check_interval, bool) or not isinstance(check_interval, int) or not 1 <= check_interval <= 1440:
        raise ValueError("chat.emoji.check_interval 必须是 1 到 1440 分钟的整数")
    max_size = emoji.get("max_emoji_size_mb", SYSTEM_DEFAULTS["chat"]["emoji"]["max_emoji_size_mb"])
    if isinstance(max_size, bool) or not isinstance(max_size, (int, float)) or max_size < 0:
        raise ValueError("chat.emoji.max_emoji_size_mb 必须是非负数字")
    cleanup = emoji.get("cache_cleanup", SYSTEM_DEFAULTS["chat"]["emoji"]["cache_cleanup"])
    if not isinstance(cleanup, dict):
        raise ValueError("chat.emoji.cache_cleanup 必须是对象")
    if "enabled" in cleanup and not isinstance(cleanup["enabled"], bool):
        raise ValueError("chat.emoji.cache_cleanup.enabled 必须是布尔值")
    interval = cleanup.get(
        "check_interval_hours", SYSTEM_DEFAULTS["chat"]["emoji"]["cache_cleanup"]["check_interval_hours"]
    )
    if isinstance(interval, bool) or not isinstance(interval, (int, float)) or interval <= 0:
        raise ValueError("chat.emoji.cache_cleanup.check_interval_hours 必须是正数")
    for key in ("emoji_file_retention_days", "no_file_record_retention_days"):
        value = cleanup.get(key, SYSTEM_DEFAULTS["chat"]["emoji"]["cache_cleanup"][key])
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"chat.emoji.cache_cleanup.{key} 必须是正整数")

    message_receive = chat.get("message_receive", {})
    if not isinstance(message_receive, dict):
        raise ValueError("chat.message_receive 必须是对象")
    threshold = message_receive.get(
        "image_parse_threshold", SYSTEM_DEFAULTS["chat"]["message_receive"]["image_parse_threshold"]
    )
    if isinstance(threshold, bool) or not isinstance(threshold, int) or threshold < 0:
        raise ValueError("chat.message_receive.image_parse_threshold 必须是非负整数")
    for key in ("ban_words", "ban_msgs_regex"):
        values = message_receive.get(key, [])
        if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
            raise ValueError(f"chat.message_receive.{key} 必须是字符串数组")
    for pattern in message_receive.get("ban_msgs_regex", []):
        try:
            re.compile(pattern)
        except re.error as exc:
            raise ValueError(f"chat.message_receive.ban_msgs_regex 正则非法: {pattern}") from exc
    for key in ("keyword_rules", "regex_rules"):
        for rule in message_receive.get(key, []):
            if not isinstance(rule, dict):
                raise ValueError(f"chat.message_receive.{key} 的每一项必须是对象")
            for field in ("keywords", "regex"):
                values = rule.get(field, [])
                if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                    raise ValueError(f"chat.message_receive.{key}.{field} 必须是字符串数组")
            if not any(value.strip() for value in rule.get("keywords", []) + rule.get("regex", [])):
                raise ValueError(f"chat.message_receive.{key} 必须至少包含关键词或正则")
            if not isinstance(rule.get("reaction", ""), str):
                raise ValueError(f"chat.message_receive.{key}.reaction 必须是字符串")
            for pattern in rule.get("regex", []):
                try:
                    re.compile(pattern)
                except re.error as exc:
                    raise ValueError(f"chat.message_receive.{key} 正则非法: {pattern}") from exc

    reply_timing = chat.get("reply_timing", {})
    if not isinstance(reply_timing, dict):
        raise ValueError("chat.reply_timing 必须是对象")
    mode = reply_timing.get("message_debounce_mode", SYSTEM_DEFAULTS["chat"]["reply_timing"]["message_debounce_mode"])
    if mode not in {"off", "fast", "natural", "steady"}:
        raise ValueError("chat.reply_timing.message_debounce_mode 只能是 off、fast、natural 或 steady")
    for key in ("private_reply_paused", "mentioned_bot_reply", "inevitable_at_reply", "planner_message_steering"):
        if key in reply_timing and not isinstance(reply_timing[key], bool):
            raise ValueError(f"chat.reply_timing.{key} 必须是布尔值")
    rules = reply_timing.get("private_reply_pause_rules", [])
    if not isinstance(rules, list):
        raise ValueError("chat.reply_timing.private_reply_pause_rules 必须是数组")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("chat.reply_timing.private_reply_pause_rules 的每一项必须是对象")
        if not isinstance(rule.get("platform", ""), str):
            raise ValueError("chat.reply_timing.private_reply_pause_rules.platform 必须是字符串")
        if not str(rule.get("user_id") or "").strip():
            raise ValueError("chat.reply_timing.private_reply_pause_rules.user_id 不能为空")
        if "paused" in rule and not isinstance(rule["paused"], bool):
            raise ValueError("chat.reply_timing.private_reply_pause_rules.paused 必须是布尔值")
    if "enable_talk_value_rules" in reply_timing and not isinstance(reply_timing["enable_talk_value_rules"], bool):
        raise ValueError("chat.reply_timing.enable_talk_value_rules 必须是布尔值")
    talk_value = reply_timing.get("talk_value", SYSTEM_DEFAULTS["chat"]["reply_timing"]["talk_value"])
    if isinstance(talk_value, bool) or not isinstance(talk_value, (int, float)) or not 0 <= talk_value <= 1:
        raise ValueError("chat.reply_timing.talk_value 必须是 0 到 1 的数字")
    rules = reply_timing.get("talk_value_rules", SYSTEM_DEFAULTS["chat"]["reply_timing"]["talk_value_rules"])
    if not isinstance(rules, list):
        raise ValueError("chat.reply_timing.talk_value_rules 必须是数组")
    for rule in rules:
        if not isinstance(rule, dict):
            raise ValueError("chat.reply_timing.talk_value_rules 的每一项必须是对象")
        if not isinstance(rule.get("tenant", ""), str):
            raise ValueError("chat.reply_timing.talk_value_rules.tenant 必须是字符串")
        time_range = str(rule.get("time") or "").strip()
        if time_range not in {"", "*"} and not _TIME_RANGE.fullmatch(time_range):
            raise ValueError("chat.reply_timing.talk_value_rules.time 必须形如 09:00-18:59，留空或 * 表示全天")
        value = rule.get("value")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            raise ValueError("chat.reply_timing.talk_value_rules.value 必须是 0 到 1 的数字")
    for key, minimum in (
        ("no_action_backoff_base_seconds", 0),
        ("no_action_backoff_cap_seconds", 0),
        ("no_action_backoff_start_count", 1),
        ("no_action_backoff_bypass_pending_count", 0),
    ):
        value = reply_timing.get(key, SYSTEM_DEFAULTS["chat"]["reply_timing"][key])
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"chat.reply_timing.{key} 必须是不小于 {minimum} 的整数")

    behavior = chat.get("behavior", {})
    if not isinstance(behavior, dict):
        raise ValueError("chat.behavior 必须是对象")
    if "enable_reply_at" in behavior and not isinstance(behavior["enable_reply_at"], bool):
        raise ValueError("chat.behavior.enable_reply_at 必须是布尔值")
    for key in ("group_chat_prompt", "private_chat_prompts"):
        if key in behavior and not isinstance(behavior[key], str):
            raise ValueError(f"chat.behavior.{key} 必须是字符串")
    for key in ("group_quote_frequency", "private_quote_frequency"):
        if behavior.get(key, "occasionally") not in {"never", "occasionally", "often"}:
            raise ValueError(f"chat.behavior.{key} 只能是 never、occasionally 或 often")
    styles = behavior.get("multiple_reply_style", SYSTEM_DEFAULTS["chat"]["behavior"]["multiple_reply_style"])
    if not isinstance(styles, list) or any(not isinstance(style, str) for style in styles):
        raise ValueError("chat.behavior.multiple_reply_style 必须是字符串数组")
    probability = behavior.get("multiple_probability", SYSTEM_DEFAULTS["chat"]["behavior"]["multiple_probability"])
    if isinstance(probability, bool) or not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
        raise ValueError("chat.behavior.multiple_probability 必须是 0 到 1 的数字")
    aliases = behavior.get("persona_aliases", [])
    if not isinstance(aliases, list) or any(not isinstance(alias, str) for alias in aliases):
        raise ValueError("chat.behavior.persona_aliases 必须是字符串数组")
    prompts = behavior.get("chat_prompts", [])
    if not isinstance(prompts, list) or any(not isinstance(item, str) for item in prompts):
        raise ValueError("chat.behavior.chat_prompts 必须是字符串数组")
    for item in prompts:
        parts = item.split(":", 3)
        if len(parts) != 4 or not all(part.strip() for part in parts[:3]):
            raise ValueError("chat.behavior.chat_prompts 的每一项必须形如 平台:群号或用户ID:group|private:提示词")
        if parts[2].strip() not in {"group", "private"}:
            raise ValueError("chat.behavior.chat_prompts 的第三段只能是 group 或 private")

    drift = chat.get("attention_drift", {})
    if not isinstance(drift, dict):
        raise ValueError("chat.attention_drift 必须是对象")
    if "enabled" in drift and not isinstance(drift["enabled"], bool):
        raise ValueError("chat.attention_drift.enabled 必须是布尔值")
    for key, values in (
        ("drift_level", {"subtle", "active", "scattered", "wild"}),
        ("anchor_policy", {"strict", "balanced", "loose"}),
        ("reaction_style", {"reserved", "natural", "lively"}),
    ):
        value = drift.get(key, SYSTEM_DEFAULTS["chat"]["attention_drift"][key])
        if value not in values:
            raise ValueError(f"chat.attention_drift.{key} 只能是 {'、'.join(sorted(values))}")

    visual = chat.get("visual", {})
    if not isinstance(visual, dict):
        raise ValueError("chat.visual 必须是对象")
    quality = visual.get("image_quality", SYSTEM_DEFAULTS["chat"]["visual"]["image_quality"])
    if quality not in {"normal", "high"}:
        raise ValueError("chat.visual.image_quality 只能是 normal 或 high")
    max_images = visual.get("max_image_num", SYSTEM_DEFAULTS["chat"]["visual"]["max_image_num"])
    if isinstance(max_images, bool) or not isinstance(max_images, int) or max_images < 0:
        raise ValueError("chat.visual.max_image_num 必须是非负整数")
    max_size = visual.get("max_image_size_mb", SYSTEM_DEFAULTS["chat"]["visual"]["max_image_size_mb"])
    if isinstance(max_size, bool) or not isinstance(max_size, (int, float)) or max_size < 0:
        raise ValueError("chat.visual.max_image_size_mb 必须是非负数")
    wait_seconds = visual.get(
        "wait_image_recognize_max_time", SYSTEM_DEFAULTS["chat"]["visual"]["wait_image_recognize_max_time"]
    )
    if isinstance(wait_seconds, bool) or not isinstance(wait_seconds, (int, float)) or wait_seconds < 0:
        raise ValueError("chat.visual.wait_image_recognize_max_time 必须是非负数")
    if "handle_oversized_images" in visual and not isinstance(visual["handle_oversized_images"], bool):
        raise ValueError("chat.visual.handle_oversized_images 必须是布尔值")
    method = visual.get(
        "oversized_image_handle_method", SYSTEM_DEFAULTS["chat"]["visual"]["oversized_image_handle_method"]
    )
    if method not in {"compress", "discard"}:
        raise ValueError("chat.visual.oversized_image_handle_method 只能是 compress 或 discard")

    voice = chat.get("voice", {})
    if not isinstance(voice, dict):
        raise ValueError("chat.voice 必须是对象")
    if "enable_asr" in voice and not isinstance(voice["enable_asr"], bool):
        raise ValueError("chat.voice.enable_asr 必须是布尔值")
    if "enable_tts" in voice and not isinstance(voice["enable_tts"], bool):
        raise ValueError("chat.voice.enable_tts 必须是布尔值")

    post_process = chat.get("response_post_process", {})
    if not isinstance(post_process, dict):
        raise ValueError("chat.response_post_process 必须是对象")
    if "enable_response_post_process" in post_process and not isinstance(
        post_process["enable_response_post_process"], bool
    ):
        raise ValueError("chat.response_post_process.enable_response_post_process 必须是布尔值")
    speed = post_process.get("typing_speed", SYSTEM_DEFAULTS["chat"]["response_post_process"]["typing_speed"])
    if isinstance(speed, bool) or not isinstance(speed, (int, float)) or not 0 <= speed <= 2:
        raise ValueError("chat.response_post_process.typing_speed 必须是 0 到 2 之间的数字")

    splitter = chat.get("response_splitter", {})
    if not isinstance(splitter, dict):
        raise ValueError("chat.response_splitter 必须是对象")
    mode = splitter.get("mode", SYSTEM_DEFAULTS["chat"]["response_splitter"]["mode"])
    if mode not in {"rule", "llm"}:
        raise ValueError("chat.response_splitter.mode 只能是 rule 或 llm")
    for key in ("enable", "enable_kaomoji_protection", "enable_overflow_return_all"):
        if key in splitter and not isinstance(splitter[key], bool):
            raise ValueError(f"chat.response_splitter.{key} 必须是布尔值")
    for key, minimum in (("max_length", 1), ("max_sentence_num", 1), ("max_split_num", 1)):
        value = splitter.get(key, SYSTEM_DEFAULTS["chat"]["response_splitter"][key])
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"chat.response_splitter.{key} 必须是不小于 {minimum} 的整数")

    routing = chat.get("model_routing", {})
    if not isinstance(routing, dict):
        raise ValueError("chat.model_routing 必须是对象")
    if any(not isinstance(value, str) for value in routing.values()):
        raise ValueError("chat.model_routing 的值必须是字符串")
    fallback = chat.get("provider_fallback", [])
    if not isinstance(fallback, list) or any(not isinstance(name, str) for name in fallback):
        raise ValueError("chat.provider_fallback 必须是字符串数组")

    providers = chat.get("providers", {})
    if not isinstance(providers, dict) or any(not isinstance(value, dict) for value in providers.values()):
        raise ValueError("chat.providers 必须是 Provider 配置对象映射")

    def validate_provider(name: Any, field: str, capability: str) -> None:
        """校验非空引用存在且能力匹配，空值保留自动选择语义。"""
        if not isinstance(name, str):
            raise ValueError(f"{field} 必须是字符串")
        if not name:
            return
        if name not in providers:
            raise ValueError(f"{field} 引用了不存在的 Provider：{name}")
        if providers[name].get("provider_type") != capability:
            raise ValueError(f"{field} 必须引用 {capability} 类型的 Provider：{name}")

    validate_provider(chat.get("provider", ""), "chat.provider", "chat")
    for name in fallback:
        validate_provider(name, "chat.provider_fallback", "chat")
    task_capabilities = {"voice": "stt", "tts": "tts", "embedding": "embedding", "image_embedding": "embedding"}
    for task, name in routing.items():
        if task != "web_search":
            validate_provider(name, f"chat.model_routing.{task}", task_capabilities.get(task, "chat"))

    capabilities = chat.get("capabilities", {})
    if not isinstance(capabilities, dict):
        raise ValueError("chat.capabilities 必须是对象")
    for key in ("tool_result_condense", "planner_subagent", "advanced_sub_agent"):
        if not isinstance(capabilities.get(key, False), bool):
            raise ValueError(f"chat.capabilities.{key} 必须是布尔值")
    threshold = capabilities.get(
        "tool_result_condense_threshold", SYSTEM_DEFAULTS["chat"]["capabilities"]["tool_result_condense_threshold"]
    )
    if isinstance(threshold, bool) or not isinstance(threshold, int) or not 500 <= threshold <= 30000:
        raise ValueError("chat.capabilities.tool_result_condense_threshold 必须是 500 到 30000 之间的整数")

    web = chat.get("web", {})
    if not isinstance(web, dict):
        raise ValueError("chat.web 必须是对象")
    preview_chars = web.get("preview_chars", SYSTEM_DEFAULTS["chat"]["web"]["preview_chars"])
    if isinstance(preview_chars, bool) or not isinstance(preview_chars, int) or not 500 <= preview_chars <= 12000:
        raise ValueError("chat.web.preview_chars 必须是 500 到 12000 之间的整数")
    services = web.get("services", [])
    if not isinstance(services, list):
        raise ValueError("chat.web.services 必须是数组")
    names: set[str] = set()
    for entry in services:
        if not isinstance(entry, dict):
            raise ValueError("chat.web.services 每项必须是对象")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip() or name != name.strip():
            raise ValueError("联网服务名称不能为空或包含首尾空格")
        if name in names:
            raise ValueError(f"联网服务名称重复：{name}")
        names.add(name)
        adapter = entry.get("adapter", "deepseek")
        if adapter not in {"deepseek", "tavily", "anysearch", "exa", "firecrawl"}:
            raise ValueError(f"chat.web.services.{name}.adapter 不受支持")
        for key in ("api_key", "base_url"):
            if key in entry and not isinstance(entry[key], str):
                raise ValueError(f"chat.web.services.{name}.{key} 必须是字符串")
        base_url = str(entry.get("base_url") or "")
        if base_url:
            parts = urlsplit(base_url)
            if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password:
                raise ValueError(f"chat.web.services.{name}.base_url 必须是无用户名和密码的 HTTP(S) 地址")
            if parts.query or parts.fragment:
                raise ValueError(f"chat.web.services.{name}.base_url 不能包含查询参数或片段")
        timeout = entry.get("timeout", 60.0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout <= 300:
            raise ValueError(f"chat.web.services.{name}.timeout 必须是 0 到 300 之间的数字")
        if entry.get("search_depth", "basic") not in {"basic", "advanced", "fast", "ultra-fast"}:
            raise ValueError(f"chat.web.services.{name}.search_depth 不受支持")
        if entry.get("extract_depth", "basic") not in {"basic", "advanced"}:
            raise ValueError(f"chat.web.services.{name}.extract_depth 不受支持")
    selected = str(routing.get("web_search") or "")
    if selected and selected not in names:
        raise ValueError(f"chat.model_routing.web_search 引用了不存在的联网服务：{selected}")
