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
        "schedule_time": "12:00",
        "batch_delay_seconds": 0,
        "contact_info": "",
    },
    "console": {
        "enabled": True,
        "mount_path": "/fxbot",
        "token": "",
        "stats_api_url": "http://127.0.0.1:8000",
    },
    "message": {
        "ignored_mention_bot_ids": [],
        "qq_group_requires_mention": False,
    },
    "chat": {
        "enabled": False,
        "command_prefixes": ["#", "/", "."],
        "group_requires_mention": False,
        "provider": "",
        "providers": {},
        "max_history": 20,
        "max_tool_rounds": 3,
    },
    "permission": {
        "bot_admins": [],
    },
}
