"""运行时路径辅助函数。"""

from __future__ import annotations

from pathlib import Path

from nonebot import get_driver


def _path_setting(config_name: str, default: Path) -> Path:
    """读取 NoneBot 配置中的路径配置。"""
    value = getattr(get_driver().config, config_name, None)
    return Path(str(value)) if value else default


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_dir(name: str | None = None) -> Path:
    root = _path_setting("fxbot_data_dir", package_root() / "data")
    root.mkdir(parents=True, exist_ok=True)
    if name is None:
        return root
    child = root / name
    child.mkdir(parents=True, exist_ok=True)
    return child


def config_dir(name: str | None = None) -> Path:
    root = _path_setting("fxbot_config_dir", data_dir("config"))
    root.mkdir(parents=True, exist_ok=True)
    if name is None:
        return root
    child = root / name
    child.mkdir(parents=True, exist_ok=True)
    return child


def cache_dir(name: str | None = None) -> Path:
    root = _path_setting("fxbot_cache_dir", data_dir("cache"))
    root.mkdir(parents=True, exist_ok=True)
    if name is None:
        return root
    child = root / name
    child.mkdir(parents=True, exist_ok=True)
    return child


def database_path() -> Path:
    return data_dir("db") / "fxbot.db"


def built_in_plugins_dir() -> Path:
    return package_root() / "plugins"
