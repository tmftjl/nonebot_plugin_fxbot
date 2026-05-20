"""NoneBot 进程控制命令。"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from nonebot import get_driver, logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from .permission import PermLevel, PermScene
from .plugin import Plugin
from .utils.compat import extract_message_target, send_text_to_target
from .utils.paths import data_dir

P = Plugin("system", category="system", display_name="系统命令")

_RESTART_FLAG_FILE = data_dir("system") / "restart_flag.json"
_PROC_CMDLINE = "/proc/self/cmdline"

restart_cmd = P.on_regex(
    r"^#重启$",
    name="system_restart",
    display_name="重启",
    priority=1,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    log=True,
)

shutdown_cmd = P.on_regex(
    r"^#关机$",
    name="system_shutdown",
    display_name="关机",
    priority=1,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    log=True,
)


def _save_restart_info(bot: Bot, event: Event) -> None:
    """保存重启后通知目标。"""
    try:
        restart_info: dict[str, Any] = {
            "bot_id": str(bot.self_id),
            **extract_message_target(event),
        }
        _RESTART_FLAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        _RESTART_FLAG_FILE.write_text(
            json.dumps(restart_info, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"[system_control] 已保存重启信息: {restart_info}")
    except Exception as exc:
        logger.error(f"[system_control] 保存重启信息失败: {exc}")


def _current_process_argv() -> list[str]:
    """获取可用于 execv 的当前进程启动参数。"""
    original_argv = getattr(sys, "orig_argv", None)
    if isinstance(original_argv, list) and len(original_argv) >= 2:
        argv = [str(item) for item in original_argv]
        argv[0] = sys.executable
        if argv[1:] != sys.argv:
            return argv

    try:
        with open(_PROC_CMDLINE, "rb") as file:
            raw_parts = [part for part in file.read().split(b"\0") if part]
        parts = [part.decode(errors="surrogateescape") for part in raw_parts]
        if parts:
            parts[0] = sys.executable
            return parts
    except Exception:
        pass

    if sys.argv[:1] == ["-c"]:
        raise RuntimeError("当前进程由 python -c 启动，且无法读取 /proc/self/cmdline，不能还原重启命令")
    return [sys.executable] + sys.argv


async def _execute_restart() -> None:
    """执行进程重启。"""
    logger.critical("[system_control] 执行 NoneBot 进程重启")
    try:
        argv = _current_process_argv()
        logger.info(f"[system_control] 重启命令: {' '.join(argv)}")
        os.execv(argv[0], argv)
    except Exception as exc:
        logger.error(f"[system_control] Bot 重启失败: {exc}")


async def _execute_shutdown() -> None:
    """执行进程关闭。"""
    logger.critical("[system_control] 执行 NoneBot 进程关闭")
    try:
        get_driver().exit()
        logger.info("[system_control] Bot 已优雅退出")
    except Exception as exc:
        logger.error(f"[system_control] Bot 关闭失败，强制退出: {exc}")
        sys.exit(0)


@restart_cmd.handle()
async def _handle_restart(matcher: Matcher, bot: Bot, event: Event) -> None:
    """处理重启命令。"""
    logger.warning(f"[system_control] 超级用户触发 Bot 重启命令: {event.get_user_id()}")
    try:
        await matcher.send("收到重启指令，正在重启 NoneBot...")
    except Exception as exc:
        logger.error(f"[system_control] 发送重启提示失败: {exc}")
    _save_restart_info(bot, event)
    await asyncio.sleep(0.5)
    await _execute_restart()


@shutdown_cmd.handle()
async def _handle_shutdown(matcher: Matcher, event: Event) -> None:
    """处理关机命令。"""
    logger.warning(f"[system_control] 超级用户触发 Bot 关闭命令: {event.get_user_id()}")
    try:
        await matcher.send("收到关机指令，NoneBot 即将关闭...")
    except Exception as exc:
        logger.error(f"[system_control] 发送关闭提示失败: {exc}")
    await asyncio.sleep(0.5)
    await _execute_shutdown()


@get_driver().on_bot_connect
async def _check_restart_flag(bot: Bot) -> None:
    """Bot 连接时发送重启成功通知。"""
    if not _RESTART_FLAG_FILE.exists():
        return
    try:
        restart_info = json.loads(_RESTART_FLAG_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.error(f"[system_control] 读取重启标记失败: {exc}")
        _RESTART_FLAG_FILE.unlink(missing_ok=True)
        return

    if str(restart_info.get("bot_id") or "") != str(bot.self_id):
        return

    try:
        await asyncio.sleep(1)
        await send_text_to_target(bot, restart_info, "✅ NoneBot 重启成功")
        logger.info("[system_control] 重启成功通知已发送")
    except Exception as exc:
        logger.error(f"[system_control] 发送重启成功通知失败: {exc}")
    finally:
        _RESTART_FLAG_FILE.unlink(missing_ok=True)
