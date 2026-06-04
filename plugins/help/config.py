"""帮助图配置解析。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RES_DIR = Path(__file__).parent / "resources"
CFG_DIR = RES_DIR / "help_config"
PLUGIN_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class HelpConfigRef:
    """帮助配置引用。"""

    key: str
    path: Path


def _read_json(path: Path) -> dict[str, Any]:
    """读取 JSON 配置。"""
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return data if isinstance(data, dict) else {}


def _builtin_config() -> HelpConfigRef:
    """返回最基础帮助配置。"""
    return HelpConfigRef("help", CFG_DIR / "help.json")


def _plugin_configs() -> dict[str, HelpConfigRef]:
    """读取各子插件自带帮助配置。"""
    mapping: dict[str, HelpConfigRef] = {}
    if not PLUGIN_DIR.exists():
        return mapping
    for path in PLUGIN_DIR.iterdir():
        if path.name == "help" or not path.is_dir():
            continue
        config_path = path / "help.json"
        if not config_path.is_file():
            continue
        try:
            data = _read_json(config_path)
        except Exception:
            continue
        key = str(data.get("key") or path.name).strip().lower()
        ref = HelpConfigRef(key or path.name.lower(), config_path)
        if key:
            mapping[key] = ref
        for alias in data.get("aliases") or []:
            if isinstance(alias, str) and alias.strip():
                mapping[alias.strip().lower()] = ref
    return mapping


def default_help_config() -> HelpConfigRef:
    """返回默认帮助配置。"""
    return _builtin_config()


def resolve_help_config(user_input: str | None) -> HelpConfigRef | None:
    """根据用户输入解析帮助图配置。"""
    keyword = str(user_input or "").strip().lower()
    if not keyword:
        return _builtin_config()
    return _plugin_configs().get(keyword)


def qq_variant(ref: HelpConfigRef) -> HelpConfigRef | None:
    """返回 QQ 官方适配器帮助配置变体。"""
    candidate = ref.path.with_name(f"{ref.path.stem}_qq{ref.path.suffix}")
    if candidate.is_file():
        return HelpConfigRef(f"{ref.key}_qq", candidate)
    return None


def load_help_config(ref: HelpConfigRef | None) -> dict[str, Any]:
    """读取帮助图 JSON 配置。"""
    target = ref or _builtin_config()
    if not target.path.exists():
        return {}
    data = _read_json(target.path)
    data["_config_key"] = target.key
    data["_config_path"] = str(target.path)
    data["_base_dir"] = str(target.path.parent)
    return data
