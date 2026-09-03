"""图库安装和更新命令。"""

from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

from nonebot.adapters import Event
from nonebot.matcher import Matcher

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from .config import POKE_DIR, RES_DIR, load_cfg

P = Plugin(
    "cultured",
    display_name="图库",
    enabled=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)

_updating_gallery = False

update_cmd = P.on_regex(
    r"^[#＃]?(?:cultured|图库)(?:安装|(?:强制)?更新)",
    name="update_gallery",
    display_name="更新图库",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.ALL,
)


async def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """在线程中执行 git 命令。"""

    def _run() -> tuple[int, str, str]:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr

    return await asyncio.to_thread(_run)


def _event_text(event: Event) -> str:
    """提取事件消息文本。"""
    try:
        return str(event.get_message())
    except Exception:
        return ""


@update_cmd.handle()
async def _handle_update(matcher: Matcher, event: Event) -> None:
    """安装或更新图库仓库。"""
    global _updating_gallery

    if _updating_gallery:
        await matcher.finish("已有图库更新任务正在进行中")

    _updating_gallery = True
    try:
        force = "强制" in _event_text(event)
        repo = str(load_cfg()["poke_repo"]).strip()
        if not repo:
            await matcher.finish("未配置图库仓库地址")

        if POKE_DIR.exists() and (POKE_DIR / ".git").exists():
            await matcher.send("开始更新图库，请稍候")
            args = ["git", "pull", "--rebase"]
            if force:
                code, out, err = await _run_git(
                    ["git", "reset", "--hard", "origin/main"], POKE_DIR
                )
                if code != 0:
                    await matcher.finish(f"图库强制更新失败：{(err or out).strip()}")
            code, out, err = await _run_git(args, POKE_DIR)
            if code != 0:
                await matcher.finish(f"图库更新失败：{(err or out).strip()}")
            if "Already up to date" in out or "已经是最新" in out:
                await matcher.finish("图库已经是最新版本")
            match = re.search(r"(\d+) files changed", out)
            suffix = f"，更新 {match.group(1)} 个文件" if match else ""
            await matcher.finish(f"图库更新完成{suffix}")

        if POKE_DIR.exists():
            await matcher.finish("图库目录已存在但不是 Git 仓库，请手动处理后再安装")

        await matcher.send("开始安装图库，请稍候")
        RES_DIR.mkdir(parents=True, exist_ok=True)
        code, out, err = await _run_git(
            ["git", "clone", "--depth=1", repo, "poke"], RES_DIR
        )
        if code != 0:
            await matcher.finish(f"图库安装失败：{(err or out).strip()}")
        await matcher.finish("图库安装完成")
    finally:
        _updating_gallery = False
