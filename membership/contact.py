"""会员续费联系信息。"""

from __future__ import annotations

from nonebot import get_driver


def renewal_contact_text() -> str:
    """生成统一的续费联系提示。"""
    try:
        superusers = sorted({str(item).strip() for item in get_driver().config.superusers or [] if str(item).strip()})
    except Exception:
        superusers = []
    if superusers:
        return f"如需续费请联系QQ{superusers[0]}。"
    return "如需续费请联系管理员。"
