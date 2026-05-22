"""nonebot-plugin-fxbot 启动编排。"""

from __future__ import annotations

from importlib import import_module

from nonebot import load_plugins, logger

from .config import get_manager as get_config_manager
from .db import init_database
from .utils.paths import built_in_plugins_dir

_initialized = False
_database_ready = False


def is_database_ready() -> bool:
    """返回数据库是否已完成初始化。"""
    return _database_ready


def _import_startup_module(module_name: str) -> None:
    """导入启动阶段必须注册 matcher 或钩子的模块。"""
    import_module(f"{__package__}.{module_name}")


async def init() -> None:
    """初始化配置、数据库、系统 matcher 和内置子插件。"""
    global _database_ready, _initialized

    if _initialized:
        return

    logger.info("[FxBot] 开始初始化")
    get_config_manager().bootstrap()

    _import_startup_module("membership.models")
    _import_startup_module("adapter.uninfo")
    _import_startup_module("plugins.memes.recorder")

    try:
        await init_database()
        _database_ready = True
    except Exception:
        _database_ready = False
        logger.opt(exception=True).error("[FxBot] 数据库初始化失败，会员门禁后续必须按 fail-closed 处理")

    _import_startup_module("membership.gate")
    _import_startup_module("membership.commands")
    _import_startup_module("system_control")
    _import_startup_module("chat.router")

    try:
        from .membership.tasks import setup_membership_tasks

        setup_membership_tasks()
    except Exception:
        logger.opt(exception=True).warning("[FxBot] 会员定时任务注册失败，核心功能继续启动")

    try:
        from .console.server import mount_console
    except ImportError:
        mount_console = None

    if mount_console is not None:
        try:
            mount_console()
        except Exception:
            logger.opt(exception=True).warning("[FxBot] 控制台挂载失败，核心功能继续启动")

    load_plugins(str(built_in_plugins_dir()))
    _initialized = True
    logger.info("[FxBot] 初始化完成")
