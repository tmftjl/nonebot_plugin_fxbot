"""nonebot-plugin-fxbot 插件入口。"""

from __future__ import annotations

from nonebot import logger, get_driver
from nonebot.plugin import PluginMetadata

from . import bootstrap

_shutdown_registered = False

__plugin_meta__ = PluginMetadata(
    name="FxBot",
    description="面向群会员、权限控制、AI 对话和内置子插件的 NoneBot2 框架插件。",
    usage="加载插件后自动初始化配置、数据库、门禁、控制台和内置子插件。",
    type="application",
    supported_adapters={"~onebot.v11", "~onebot.v12"},
)


@get_driver().on_startup
async def _fxbot_startup() -> None:
    """在 NoneBot 启动阶段初始化框架底座。"""
    global _shutdown_registered

    try:
        await bootstrap.init()
    finally:
        if not _shutdown_registered:
            get_driver().on_shutdown(_fxbot_shutdown)
            _shutdown_registered = True


async def _fxbot_shutdown() -> None:
    """在 NoneBot 关闭阶段释放共享资源。"""
    from .chat.audit import stop_audit_observers
    from .chat.focus import focus_mode_manager
    from .utils.http import close_shared_async_client
    from .chat.providers import provider_manager
    from .chat.continuation import reply_scheduler
    from .chat.tools.subagent import subagent_tasks
    from .console.chat_channel import console_chat
    from .chat.amemorix.runtime import amemorix_runtime
    from .chat.amemorix.writeback import chat_writeback

    for name, cleanup in (
        ("网页独立会话", console_chat.stop),
        ("插件后续对话", reply_scheduler.stop),
        ("Focus 共享组", focus_mode_manager.stop_all),
        ("后台子代理", subagent_tasks.cancel_all),
        ("聊天记忆回写", chat_writeback.stop),
        ("后台聊天审计", stop_audit_observers),
        ("长期记忆运行时", amemorix_runtime.stop),
        ("模型提供方", provider_manager.reset),
        ("共享 HTTP 客户端", close_shared_async_client),
    ):
        try:
            await cleanup()
        except Exception:
            logger.opt(exception=True).warning(f"[FxBot] 关闭清理失败: {name}")
