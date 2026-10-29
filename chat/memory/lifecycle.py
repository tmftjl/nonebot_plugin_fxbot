"""撤回失效的业务入口。"""

from __future__ import annotations

from nonebot import logger

from .state import is_ready
from ..session import default_session_store
from .repository import chat_repository
from ..context_summary import forget_summarized
from ..behavior.debounce import default_debounce_registry


async def handle_recall(tenant: str, platform_message_id: str, *, operator: str = "") -> dict[str, int] | None:
    """处理撤回：数据库与运行时上下文一起失效，基于旧内容的在途回复不再发出。"""
    if not is_ready():
        raise RuntimeError("聊天存储暂不可用")
    result = await chat_repository.invalidate_message(tenant, platform_message_id, operator=operator)
    if await _discard_runtime(tenant, platform_message_id):
        default_session_store.invalidate(tenant)
        logger.debug(f"[Chat] 撤回后运行时上下文已失效: session={tenant} message={platform_message_id}")
    return result


async def _discard_runtime(tenant: str, platform_message_id: str) -> bool:
    """从内存历史、待合并批次、Focus 时间线与摘要里移除这条消息；返回是否有变化。"""
    dropped = default_session_store.discard_message(tenant, platform_message_id)
    dropped = default_debounce_registry.discard_message(tenant, platform_message_id) or dropped
    dropped = forget_summarized(tenant, platform_message_id) or dropped
    try:
        from ..focus import focus_mode_manager

        dropped = await focus_mode_manager.discard_message(tenant, platform_message_id) or dropped
    except Exception:
        logger.opt(exception=True).warning("[Chat] 撤回时清理 Focus 上下文失败")
    return dropped
