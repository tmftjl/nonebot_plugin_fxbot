"""AI 人格文件存储。"""

from __future__ import annotations

import os
import json
from pathlib import Path
from dataclasses import dataclass

from nonebot import logger

from ..utils.paths import config_dir

DEFAULT_PERSONA = "小鱼"
_DEFAULT_PERSONA_TEXT = "你是一个友好、耐心且乐于助人的 AI 助手。请保持回答简洁清晰，并具备同理心。"


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


def effective_persona_name(session_id: str, configured: str | None = None) -> str:
    """返回会话实际使用的人格名：绑定优先，其次全局配置，最后默认。"""
    return str(get_bound_persona(session_id) or configured or "").strip() or DEFAULT_PERSONA


def _ensure_default_persona(dir_path: Path) -> None:
    """确保默认人格存在。"""
    default_file = dir_path / f"{DEFAULT_PERSONA}.md"
    if default_file.exists():
        return
    default_file.write_text(_DEFAULT_PERSONA_TEXT, encoding="utf-8")


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
    if DEFAULT_PERSONA not in personas:
        personas[DEFAULT_PERSONA] = PersonaItem(key=DEFAULT_PERSONA, details="")
    return personas


def get_persona_text(name: str | None = None) -> str:
    """读取指定人格内容，未知名称回退默认人格。"""
    persona_name = str(name or "").strip() or DEFAULT_PERSONA
    personas = load_personas()
    persona = personas.get(persona_name) or personas.get(DEFAULT_PERSONA)
    return persona.details if persona else ""


def save_persona_text(name: str, text: str, *, overwrite: bool = True) -> Path:
    """保存人格内容；overwrite=False 时同名人格已存在会抛出 FileExistsError。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("人格名称不能为空")
    if any(ch in key for ch in '/\\:*?"<>|\x00') or key in {".", ".."}:
        raise ValueError('人格名称不能包含 / \\ : * ? " < > |')
    content = str(text or "").strip()
    if not content:
        raise ValueError("人格描述不能为空")
    if "\x00" in content:
        raise ValueError("人格描述包含非法字符")
    dir_path = get_personas_dir()
    _ensure_default_persona(dir_path)
    if not overwrite and any((dir_path / f"{key}{ext}").exists() for ext in (".md", ".txt")):
        raise FileExistsError(key)
    path = dir_path / f"{key}.md"
    path.write_text(content, encoding="utf-8")
    return path


def delete_persona(name: str) -> int:
    """删除人格文件，返回删除数量。"""
    key = str(name or "").strip()
    if not key:
        raise ValueError("人格名称不能为空")
    if key == DEFAULT_PERSONA:
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


def _bindings_path() -> Path:
    """返回会话人格绑定的存储路径。"""
    return config_dir("ai_chat") / "bindings.json"


def load_persona_bindings() -> dict[str, str]:
    """读取会话到人格的绑定。"""
    try:
        raw = json.loads(_bindings_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.opt(exception=True).warning("[AI Chat] 读取人格绑定失败")
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value).strip() for key, value in raw.items() if str(value).strip()}


def get_bound_persona(session_id: str) -> str:
    """读取会话绑定的人格名。"""
    return load_persona_bindings().get(str(session_id), "")


def set_bound_persona(session_id: str, name: str | None) -> None:
    """写入或清除会话绑定的人格名。"""
    bindings = load_persona_bindings()
    key = str(session_id)
    if name:
        bindings[key] = str(name)
    else:
        bindings.pop(key, None)
    path = _bindings_path()
    temp = path.with_name(f"{path.name}.tmp")
    temp.write_text(json.dumps(bindings, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)
