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

from ..adapter import extract_message_target, send_forward_texts, send_text_to_target
from ..permission import PermLevel, PermScene
from ..utils.paths import data_dir, package_root
from .registry import P

_RESTART_FLAG_FILE = data_dir("system") / "restart_flag.json"
_PROC_CMDLINE = "/proc/self/cmdline"
_UPDATE_LOG_PREFIX = "[系统命令·更新并重启]"
_GIT_UP_TO_DATE_MARKERS = ("Already up to date", "Already up-to-date", "已经是最新")

restart_cmd = P.on_regex(
    r"^[#＃]重启$",
    name="system_restart",
    display_name="重启",
    priority=1,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    log=True,
)

shutdown_cmd = P.on_regex(
    r"^[#＃]关机$",
    name="system_shutdown",
    display_name="关机",
    priority=1,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    log=True,
)

update_cmd = P.on_regex(
    r"^[#＃]更新$",
    name="system_update",
    display_name="更新并重启",
    priority=1,
    block=True,
    level=PermLevel.SUPERUSER,
    scene=PermScene.ALL,
    log=True,
)


def _save_restart_info(bot: Bot, event: Event, success_message: str = "✅ FxBot 重启成功") -> None:
    """保存重启后通知目标。"""
    try:
        restart_info: dict[str, Any] = {
            "bot_id": str(bot.self_id),
            "success_message": success_message,
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
        raise RuntimeError(
            "当前进程由 python -c 启动，且无法读取 /proc/self/cmdline，不能还原重启命令"
        )
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


def _truncate_output(text: str, limit: int = 1200) -> str:
    """截断命令输出，避免消息过长。"""
    clean_text = str(text or "").strip()
    if len(clean_text) <= limit:
        return clean_text
    return clean_text[-limit:]


def _git_already_up_to_date(output: str) -> bool:
    """判断 git 输出是否表示无更新。"""
    normalized = str(output or "").lower()
    return any(marker.lower() in normalized for marker in _GIT_UP_TO_DATE_MARKERS)


def _build_update_report(ok: bool, output: str, logs: list[str] | None = None) -> list[str]:
    """构造更新结果转发摘要，不暴露 git 文件变更列表。"""
    if ok:
        if _git_already_up_to_date(output):
            return [
                "✅ FxBot 本次无更新内容！",
            ]
        return [
            "✅ FxBot 更新完成！",
            *(logs or ["未读取到本次提交日志"]),
        ]

    if str(output or "").strip():
        return [
            "❌ FxBot 更新失败！",
            "git pull 返回错误，完整信息已记录到后台日志。",
        ]
    return [
        "❌ FxBot 更新失败！",
        "git pull 未返回错误信息，请查看后台日志。",
    ]


async def _run_git(args: list[str]) -> tuple[int, str]:
    """执行 git 命令并返回输出。"""
    process = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(package_root()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    output = "\n".join(
        part.decode("utf-8", errors="replace").strip() for part in (stdout, stderr) if part
    ).strip()
    return process.returncode, output


async def _git_head() -> str:
    """读取当前 HEAD。"""
    code, output = await _run_git(["rev-parse", "HEAD"])
    return output.strip() if code == 0 else ""


async def _git_log_messages(before: str, after: str) -> list[str]:
    """读取本次更新新增提交信息。"""
    if not before or not after or before == after:
        return []
    code, output = await _run_git(
        [
            "log",
            "--reverse",
            "--pretty=format:%B%x1e",
            f"{before}..{after}",
        ]
    )
    if code != 0 or not output:
        return []
    return [item.strip() for item in output.split("\x1e") if item.strip()]


async def _send_update_report(matcher: Matcher, bot: Bot, event: Event, lines: list[str]) -> None:
    """优先用合并转发发送更新结果，失败时退回普通文本。"""
    if await send_forward_texts(bot, event, lines, nickname="小助手"):
        return
    await matcher.send("\n".join(lines))


async def _git_pull_plugin() -> tuple[bool, str, list[str]]:
    """在当前插件目录执行 git pull。"""
    before = await _git_head()
    process = await asyncio.create_subprocess_exec(
        "git",
        "pull",
        "--ff-only",
        cwd=str(package_root()),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=180)
    except asyncio.TimeoutError:
        process.kill()
        await process.communicate()
        return False, "git pull 超时", []

    output = "\n".join(
        part.decode("utf-8", errors="replace").strip() for part in (stdout, stderr) if part
    ).strip()
    if process.returncode == 0:
        after = await _git_head()
        logs = await _git_log_messages(before, after)
        return True, output or "Already up to date.", logs
    return False, output or f"git pull 失败，退出码 {process.returncode}", []


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


@update_cmd.handle()
async def _handle_update(matcher: Matcher, bot: Bot, event: Event) -> None:
    """处理更新并重启命令。"""
    logger.warning(f"{_UPDATE_LOG_PREFIX} 超级用户触发更新命令: {event.get_user_id()}")
    await matcher.send("🔔 正在尝试更新 FxBot，请稍等片刻。")
    ok, output, logs = await _git_pull_plugin()
    if not ok:
        logger.error(f"{_UPDATE_LOG_PREFIX} git pull 失败: {_truncate_output(output)}")
        await _send_update_report(matcher, bot, event, _build_update_report(False, output, logs))
        await matcher.finish()

    try:
        status = "无更新" if _git_already_up_to_date(output) else "已更新"
        logger.info(f"{_UPDATE_LOG_PREFIX} git pull 完成: {status}")
        await _send_update_report(matcher, bot, event, _build_update_report(True, output, logs))
    except Exception as exc:
        logger.error(f"{_UPDATE_LOG_PREFIX} 发送更新提示失败: {exc}")
    _save_restart_info(bot, event, "✅ FxBot 更新重启成功")
    await asyncio.sleep(0.5)
    await _execute_restart()


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
        message = str(restart_info.get("success_message") or "✅ FxBot 重启成功")
        await send_text_to_target(bot, restart_info, message)
        logger.info("[system_control] 重启成功通知已发送")
    except Exception as exc:
        logger.error(f"[system_control] 发送重启成功通知失败: {exc}")
    finally:
        _RESTART_FLAG_FILE.unlink(missing_ok=True)


# 导入状态命令和监控 hooks，使 system_control 成为系统控制统一入口。
from . import bot_status as bot_status
