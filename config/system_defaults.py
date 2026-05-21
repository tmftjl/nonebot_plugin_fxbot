"""全局默认配置。"""

from __future__ import annotations

SYSTEM_DEFAULTS = {
    "membership": {
        "enabled": True,
        "cache_ttl_seconds": 60,
        "expire_notice_days": [7, 3, 1],
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
    },
    "chat": {
        "enabled": True,
        "command_prefixes": ["#", "/", "."],
        "group_requires_mention": True,
        "provider": "",
        "providers": {},
        "max_history": 20,
        "max_tool_rounds": 3,
    },
    "permission": {
        "bot_admins": [],
    },
}
