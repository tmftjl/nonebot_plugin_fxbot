"""统一撤回通知处理。"""

from __future__ import annotations

from nonebot import logger, on_notice
from nonebot.rule import Rule
from nonebot.adapters import Bot, Event

from ..adapter import extract_recall
from .message_adapter import session_id_for_event
from .memory.lifecycle import handle_recall


def _is_recall(bot: Bot, event: Event) -> bool:
    """只放行平台能识别的撤回通知。"""
    try:
        return extract_recall(bot, event) is not None
    except Exception:
        return False


recall_notice = on_notice(rule=Rule(_is_recall), priority=10, block=False)


@recall_notice.handle()
async def _handle_recall(bot: Bot, event: Event) -> None:
    """撤回后标记删除，并丢弃覆盖该消息的学习批次。"""
    try:
        info = extract_recall(bot, event)
    except Exception:
        logger.opt(exception=True).debug("[ChatStorage] 读取撤回信息失败")
        return
    if info is None:
        return
    tenant = session_id_for_event(bot, event)
    try:
        await handle_recall(tenant, info.message_id, operator=info.operator_id)
    except Exception:
        logger.opt(exception=True).warning(f"[ChatStorage] 撤回失效处理失败: message={info.message_id}")
