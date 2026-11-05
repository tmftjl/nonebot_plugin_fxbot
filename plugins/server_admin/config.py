"""服务器运维控制台配置与命令黑名单。"""

from __future__ import annotations

import re

from nonebot import logger

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("server_admin", DEFAULTS, clean_extra=True)


def blocked_reason(command: str) -> str:
    """命中禁用规则时返回该规则；配置读不出来时按拒绝处理。"""
    rules = REG.load()
    if REG._last_error is not None:
        return "命令黑名单配置不可用"
    for pattern in rules.get("blocked_commands") or []:
        rule = str(pattern).strip()
        if not rule:
            continue
        try:
            if re.search(rule, command, re.IGNORECASE | re.MULTILINE):
                return rule
        except re.error:
            logger.warning(f"[ServerAdmin] 无效的命令黑名单规则已忽略: {rule}")
    return ""
