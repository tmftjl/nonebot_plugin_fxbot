"""运行时 JSON 权限配置存储。"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

from ..utils.paths import built_in_plugins_dir, config_dir

from .types import perm_entry_default


def scan_plugins_for_permissions() -> dict[str, Any]:
    """扫描内置子插件，生成权限配置初始结构。"""
    result: dict[str, Any] = {"top": perm_entry_default(), "sub_plugins": {}}
    sub_map = result["sub_plugins"]
    base = built_in_plugins_dir()

    if not base.exists():
        return result

    for plugin_dir in base.iterdir():
        if not plugin_dir.is_dir() or not (plugin_dir / "__init__.py").exists():
            continue
        plugin_name = plugin_dir.name
        node = sub_map.setdefault(plugin_name, {"top": perm_entry_default(), "commands": {}})
        commands = node.setdefault("commands", {})
        for file_path in plugin_dir.rglob("*.py"):
            _scan_file_for_commands(file_path, commands)
    return result


def _scan_file_for_commands(file_path: Path, commands: dict[str, Any]) -> None:
    """扫描单个 Python 文件中 P.on_* 和 P.permission_cmd 的命令名。"""
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8").lstrip("\ufeff"))
    except Exception:
        return

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or not isinstance(func.value, ast.Name):
            continue
        if func.value.id != "P":
            continue

        if func.attr == "permission_cmd":
            if node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                commands.setdefault(node.args[0].value, perm_entry_default())
            continue

        if func.attr == "on" or func.attr.startswith("on_"):
            for kw in node.keywords:
                if kw.arg == "name" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                    commands.setdefault(kw.value.value, perm_entry_default())


class PermissionStorage:
    """权限配置文件存储。"""

    def __init__(self) -> None:
        self._path = config_dir() / "permissions.json"
        self._data: dict[str, Any] = {}
        self._loaded = False
        self._dirty = False

    @property
    def path(self) -> Path:
        """权限配置文件路径。"""
        return self._path

    def ensure(self) -> None:
        """确保权限配置文件存在。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if not self._path.exists():
            self._path.write_text(
                json.dumps(scan_plugins_for_permissions(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    def load(self) -> dict[str, Any]:
        """加载权限配置。"""
        self.ensure()
        if not self._loaded:
            self.reload()
        return self._data

    def reload(self) -> None:
        """从磁盘重新加载权限配置。"""
        data = json.loads(self._path.read_text(encoding="utf-8"))
        self._data = data if isinstance(data, dict) else {}
        self._loaded = True
        self._dirty = False

    def save(self, data: dict[str, Any]) -> None:
        """保存权限配置。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        self._data = data
        self._loaded = True
        self._dirty = False

    def mark_dirty(self, data: dict[str, Any]) -> None:
        """标记权限配置为待写入。"""
        self._data = data
        self._loaded = True
        self._dirty = True

    def flush(self) -> None:
        """写入待保存的权限配置。"""
        if self._dirty and self._loaded:
            self.save(self._data)


_global_storage = PermissionStorage()


def get_storage() -> PermissionStorage:
    """获取全局权限存储。"""
    return _global_storage
