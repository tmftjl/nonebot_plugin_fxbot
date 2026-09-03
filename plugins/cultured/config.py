"""Cultured 图库配置和资源索引。"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from nonebot import logger

from ...config import get_manager
from ...utils.paths import data_dir
from .ui_schema import DEFAULTS

PLUGIN_DIR = Path(__file__).parent
COMMANDS_PATH = PLUGIN_DIR / "commands.json"
RES_DIR = data_dir("resources") / "cultured"
POKE_DIR = RES_DIR / "poke"

REG = get_manager().register("cultured", DEFAULTS, clean_extra=True)


def ensure_dirs() -> None:
    """确保图库资源目录存在。"""
    RES_DIR.mkdir(parents=True, exist_ok=True)


def load_cfg() -> dict[str, Any]:
    """读取图库配置。"""
    ensure_dirs()
    return REG.load()


def face_list() -> list[str]:
    """返回已安装的本地图库名称。"""
    ensure_dirs()
    if not POKE_DIR.exists():
        return []
    try:
        return sorted(
            {
                path.name
                for path in POKE_DIR.iterdir()
                if path.is_dir() and path.name != ".git"
            }
        )
    except Exception:
        logger.opt(exception=True).warning("[Cultured] 读取图库目录失败")
        return []


def random_local_image(face: str) -> Path | None:
    """从指定本地图库随机选择一张图片。"""
    directory = POKE_DIR / face
    if not directory.is_dir():
        return None
    try:
        files = [path for path in directory.iterdir() if path.is_file()]
    except Exception:
        logger.opt(exception=True).warning(f"[Cultured] 读取图库 {face} 失败")
        return None
    return random.choice(files) if files else None


def load_default_commands() -> list[dict[str, Any]]:
    """加载内置 API 图库命令。"""
    try:
        data = json.loads(COMMANDS_PATH.read_text(encoding="utf-8"))
    except Exception:
        logger.opt(exception=True).warning("[Cultured] 加载内置图库命令失败")
        return []
    commands = data.get("api_commands", [])
    return commands if isinstance(commands, list) else []


def load_all_commands() -> list[dict[str, Any]]:
    """合并内置和用户自定义 API 图库命令。"""
    commands = list(load_default_commands())
    custom = load_cfg()["custom_commands"]
    if isinstance(custom, dict):
        for name, item in custom.items():
            if isinstance(item, dict):
                commands.append({"name": str(name), **item})
    return commands
