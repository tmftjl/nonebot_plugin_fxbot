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
        "batch_delay_seconds": 5,
    },
    "system": {
        "token": "",
        "ignored_mention_bot_ids": [],
        "bot_admins": [],
    },
    "chat": {
        "enabled": False,
        "command_prefixes": ["#", "/", "."],
        "provider": "",
        "providers": {},
        "max_history": 20,
        "max_tool_rounds": 3,
    },
}
