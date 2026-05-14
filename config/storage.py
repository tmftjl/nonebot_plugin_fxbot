"""JSON 配置存储。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nonebot import logger

from ..utils.paths import config_dir


class ConfigStorage:
    """运行时配置文件的 UTF-8 JSON 存储封装。"""

    def __init__(self, namespace: str, filename: str = "config.json") -> None:
        self.namespace = namespace
        self.filename = filename
        self._path: Path | None = None

    @property
    def path(self) -> Path:
        if self._path is None:
            self._path = config_dir(self.namespace) / self.filename
        return self._path

    def exists(self) -> bool:
        return self.path.exists()

    def read(self) -> dict[str, Any]:
        if not self.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.warning(f"[Config] Invalid JSON in {self.path}: {exc}")
            return {}
        return data if isinstance(data, dict) else {}

    def write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def mtime(self) -> float:
        try:
            return self.path.stat().st_mtime
        except OSError:
            return 0.0
