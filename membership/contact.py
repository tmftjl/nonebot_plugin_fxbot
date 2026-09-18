"""会员续费联系信息。"""

from __future__ import annotations

from datetime import datetime, timezone

from ..config import get_manager as get_config_manager


def renewal_contact_text() -> str:
    """生成统一的续费联系提示。"""
    contact = str(get_config_manager().get_system()["membership"]["renewal_contact"] or "").strip()
    if contact:
        return f"如需续费请联系{contact}。"
    return "如需续费请联系管理员。"


def format_membership_expiry(value: datetime | None) -> str:
    """按会员日结时间格式化到期时间。"""
    if value is None:
        return "-"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return f"{value.astimezone().strftime('%Y-%m-%d')} 00:00"
