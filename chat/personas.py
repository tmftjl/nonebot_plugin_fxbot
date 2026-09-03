"""AI 人格文件存储。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nonebot import logger

from ..utils.paths import config_dir


@dataclass(frozen=True)
class PersonaItem:
    """人格条目。"""

    key: str
    details: str


def get_personas_dir() -> Path:
    """返回人格配置目录。"""
    path = config_dir("ai_chat") / "personas"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _ensure_default_persona(dir_path: Path) -> None:
    """确保默认人格存在。"""
    default_file = dir_path / "default.md"
    if default_file.exists():
        return
    default_file.write_text(
        "你是一个友好、耐心且乐于助人的 AI 助手。请保持回答简洁清晰，并具备同理心。",
        encoding="utf-8",
    )


def _read_text(path: Path) -> str:
    """读取人格文本。"""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _collect_persona_files(dir_path: Path) -> dict[str, Path]:
    """收集人格文件。"""
    result: dict[str, Path] = {}
    for path in sorted(dir_path.iterdir()):
        if not path.is_file() or path.suffix.lower() not in {".md", ".txt"}:
            continue
        key = path.stem.strip()
        if not key or key in result:
            continue
        result[key] = path
    return result


def load_personas() -> dict[str, PersonaItem]:
    """加载全部人格。"""
    dir_path = get_personas_dir()
    _ensure_default_persona(dir_path)
    personas: dict[str, PersonaItem] = {}
    for key, path in _collect_persona_files(dir_path).items():
        try:
            personas[key] = PersonaItem(key=key, details=_read_text(path))
        except Exception as exc:
            logger.opt(exception=True).warning(f"[AI Chat] 读取人格失败: {path.name} err={exc}")
    if "default" not in personas:
        personas["default"] = PersonaItem(key="default", details="")
    return personas


def get_persona_text(name: str | None = None) -> str:
    """读取指定人格内容。"""
    persona_name = str(name or "").strip() or "default"
    personas = load_personas()
    persona = personas.get(persona_name) or personas.get("default")
    return persona.details if persona else ""


def save_persona_text(name: str, text: str) -> Path:
    """保存人格内容。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("人格名称不能为空")
    if any(ch in key for ch in '/\\:*?"<>|') or key in {".", ".."}:
        raise ValueError("人格名称非法")
    dir_path = get_personas_dir()
    _ensure_default_persona(dir_path)
    path = dir_path / f"{key}.md"
    path.write_text(str(text or "").strip(), encoding="utf-8")
    return path


def delete_persona(name: str) -> int:
    """删除人格文件，返回删除数量。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("人格名称不能为空")
    if key == "default":
        raise ValueError("默认人格不能删除")
    dir_path = get_personas_dir()
    removed = 0
    for ext in (".md", ".txt"):
        path = dir_path / f"{key}{ext}"
        if path.exists():
            path.unlink()
            removed += 1
    return removed


def list_personas() -> dict[str, str]:
    """返回人格名称到内容的映射。"""
    return {key: item.details for key, item in load_personas().items()}
