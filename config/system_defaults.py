"""全局默认配置。"""

from __future__ import annotations

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
        "web_search": {
            "base_url": "https://api.deepseek.com",
            "api_key": "",
        },
    },
}
