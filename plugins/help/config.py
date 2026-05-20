"""帮助图配置解析。"""

from __future__ import annotations

import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

RES_DIR = Path(__file__).parent / "resources"
CFG_DIR = RES_DIR / "help_config"
CFG_MAP_FILE = CFG_DIR / "command_map.json"


def _available_configs() -> dict[str, str]:
    """返回可用帮助配置映射。"""
    mapping: dict[str, str] = {}
    if not CFG_DIR.exists():
        return mapping
    for path in CFG_DIR.iterdir():
        if path.is_file() and path.suffix.lower() == ".json":
            mapping[path.stem.lower()] = path.name
    return mapping


def _default_cmd_map() -> dict[str, str]:
    """内置命令到配置文件的映射。"""
    return {
        "help": "help.json",
        "默认": "help.json",
        "群管": "help.json",
        "帮助": "help.json",
        "菜单": "help.json",
        "功能": "help.json",
        "admin": "help.json",
        "fun": "fun.json",
        "娱乐": "fun.json",
        "娱乐帮助": "fun.json",
        "yx": "fun.json",
        "game": "fun.json",
        "games": "fun.json",
    }


def _load_cmd_map() -> dict[str, str]:
    """读取帮助图别名映射。"""
    mapping: dict[str, str] = {}
    try:
        if CFG_MAP_FILE.exists():
            raw = json.loads(CFG_MAP_FILE.read_text(encoding="utf-8-sig"))
            if isinstance(raw, dict):
                for key, value in raw.items():
                    if isinstance(key, str) and isinstance(value, str):
                        mapping[key.strip().lower()] = value.strip()
    except Exception:
        mapping = {}
    return mapping or _default_cmd_map()


def resolve_help_config(user_input: str | None) -> str | None:
    """根据用户输入解析帮助图配置文件。"""
    if not user_input or not str(user_input).strip():
        return None

    key = str(user_input).strip().lower()
    cmd_map = _load_cmd_map()
    if key in cmd_map:
        return cmd_map[key]

    if key.endswith(".json") and (CFG_DIR / key).exists():
        return key

    if "*" in key:
        matched = [cmd_map[item] for item in cmd_map if fnmatch(item, key)]
        unique = list({item for item in matched})
        if len(unique) == 1:
            return unique[0]
    return None


def help_config_filename(config: str | None) -> str:
    """转换为实际 JSON 文件名。"""
    if not config or not str(config).strip():
        return "help.json"
    name = str(config).strip().lower()
    if name.endswith(".json"):
        return name
    mapped = _load_cmd_map().get(name)
    if mapped:
        return mapped
    return _available_configs().get(name, "help.json")


def load_help_config(config: str | None) -> dict[str, Any]:
    """读取帮助图 JSON 配置。"""
    file = CFG_DIR / help_config_filename(config)
    if not file.exists():
        return {}
    data = json.loads(file.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}
