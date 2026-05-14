"""运行时路径辅助函数。"""

from __future__ import annotations

import os
from pathlib import Path


def package_root() -> Path:
    return Path(__file__).resolve().parents[1]


def data_dir(name: str | None = None) -> Path:
    root = Path(os.getenv("FXBOT_DATA_DIR", package_root() / "data"))
    root.mkdir(parents=True, exist_ok=True)
    if name is None:
        return root
    child = root / name
    child.mkdir(parents=True, exist_ok=True)
    return child


def config_dir(name: str | None = None) -> Path:
    root = Path(os.getenv("FXBOT_CONFIG_DIR", data_dir("config")))
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
