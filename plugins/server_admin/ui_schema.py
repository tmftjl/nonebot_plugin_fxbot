"""服务器运维控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "blocked_commands": [
        r"\brm\s+(?:--?[a-zA-Z][a-zA-Z-]*\s+)*(?:/|\*|/\*|~|\$HOME)(?:\s|$|[;&|])",
        r"--no-preserve-root",
        r"\b(?:mkfs(?:\.\w+)?|mkswap|wipefs|blkdiscard)\b",
        r"\b(?:fdisk|parted|sgdisk)\s+/dev/",
        r"\bdd\b[^\n]*\bof\s*=\s*/dev/(?:sd|hd|vd|nvme|mmcblk|disk)",
        r"(?:>|tee\s+)\s*/dev/(?:sd|hd|vd|nvme|mmcblk|disk)",
        r":\s*\(\s*\)\s*\{[^}]*:\s*\|\s*:",
    ]
}


def get_ui_schema() -> dict[str, Any]:
    """返回服务器运维配置界面 schema。"""
    return {
        "key": "server_admin",
        "title": "服务器运维",
        "cards": [
            {
                "key": "server_admin",
                "title": "命令黑名单",
                "schemas": [
                    {
                        "field": "blocked_commands",
                        "label": "禁止提交的命令规则",
                        "component": "GTags",
                        "default": DEFAULTS["blocked_commands"],
                        "helpMessage": "正则匹配，不区分大小写；命中即拒绝，不进入确认。只防误操作，不是安全边界。",
                    }
                ],
            }
        ],
    }
