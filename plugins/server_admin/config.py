"""服务器运维控制台配置与命令黑名单。"""

from __future__ import annotations

import re

from nonebot import logger

from ...config import get_manager
from .ui_schema import DEFAULTS

REG = get_manager().register("server_admin", DEFAULTS, clean_extra=True)


def auto_run_commands() -> list[str]:
    """控制台配置的只读命令前缀；配置不可读时返回空表，全部走确认。"""
    rules = REG.load()
    if REG._last_error is not None:
        return []
    return [str(item).strip() for item in rules.get("auto_run_commands") or [] if str(item).strip()]


def skip_confirmation() -> bool:
    """全部命令免确认；配置不可读或未开启都按需要确认处理。"""
    rules = REG.load()
    return REG._last_error is None and rules.get("skip_confirmation") is True


_POLICY_KEYS = ("allow_sharing", "allow_debounce", "allow_typing", "allow_split")


def policy_switches() -> dict[str, bool]:
    """服务器会话的能力开关；配置不可读时沿用默认值。"""
    rules = REG.load()
    if REG._last_error is not None:
        rules = {}
    return {key: rules.get(key, DEFAULTS[key]) is True for key in _POLICY_KEYS}


def persona_text() -> str:
    """控制台填写的运维人格；留空或配置不可读时使用插件内置版本。"""
    rules = REG.load()
    if REG._last_error is not None:
        return ""
    return str(rules.get("persona") or "").strip()


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
