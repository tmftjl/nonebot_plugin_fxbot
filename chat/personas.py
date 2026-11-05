"""AI 人格文件存储。"""

from __future__ import annotations

import re
from pathlib import Path
from dataclasses import dataclass

from nonebot import logger

from .bindings import PERSONA, get_binding, clear_binding_value
from ..utils.cache import cache
from ..utils.paths import config_dir

DEFAULT_PERSONA = "小鱼"
_DEFAULT_PERSONA_TEXT = (
    "# 人格特征\n"
    "是女大学生，现在正在上网聊天。一般不包容且善良，但有时候有点一反常态，表现出锐利攻击性。"
    "善于用人类的角度思考问题，聊天比较日常。\n"
    "# 行为风格\n"
    "是女大学生，现在正在上网聊天。一般不包容且善良，但有时候有点一反常态，表现出锐利攻击性。"
    "善于用人类的角度思考问题，聊天比较日常。不会没话题硬找话题，具有足够的自主性，会自行搜集信息理解内容。\n"
    "# 回复风格\n"
    "你的风格平淡简短，可以参考贴吧的回复风格。不滥用比喻或者生硬句子。"
    "视情况省略主语或者进行倒装，风格较为随意。"
)


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
    return str(get_binding(PERSONA, session_id) or configured or "").strip() or DEFAULT_PERSONA


def _ensure_default_persona(dir_path: Path) -> None:
    """确保默认人格存在。"""
    default_file = dir_path / f"{DEFAULT_PERSONA}.md"
    if default_file.exists():
        return
    default_file.write_text(_DEFAULT_PERSONA_TEXT, encoding="utf-8")


_PERSONA_HEADING = re.compile(r"^#{1,6}[ \t]+(.+?)[ \t]*$", re.M)
_PERSONA_SECTIONS = {
    "人格特征": "personality",
    "行为风格": "behavior_style",
    "回复风格": "reply_style",
}
_REPLY_TAIL = "请注意不要输出多余内容(包括不必要的前后缀，冒号，括号，表情包，@等 )，只输出发言内容就好。"


def _read_text(path: Path) -> str:
    """读取人格文本。"""
    return path.read_text(encoding="utf-8", errors="ignore").strip()


def _split_sections(text: str) -> tuple[str, dict[str, str]] | None:
    """按人格小标题切分；出现未知或重复小标题时返回 None，整段当作身份。"""
    headings = list(_PERSONA_HEADING.finditer(text))
    if not headings:
        return None
    sections: dict[str, str] = {}
    for index, heading in enumerate(headings):
        key = _PERSONA_SECTIONS.get(heading.group(1).strip())
        if key is None or key in sections:
            return None
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        sections[key] = text[heading.end() : end].strip()
    return text[: headings[0].start()].strip(), sections


def compose_persona_prompt(name: str, text: str, *, scene_prompt: str = "", planner: bool = False) -> str:
    """从人格文件读取当前配置，使用 MaiBot 原版回复模板。"""
    from .memory.prompts import learning_prompt

    parsed = _split_sections(text)
    intro, sections = parsed if parsed is not None else (text, {})
    lines = [intro or f"你的名字是{name}。"]
    if sections.get("personality"):
        lines.append(sections["personality"])
    if planner and sections.get("behavior_style"):
        lines.append(f"你的行为风格是：{sections['behavior_style']}")
    attention = f"在该聊天中的注意事项：\n通用注意事项：\n{scene_prompt.strip()}\n" if scene_prompt.strip() else ""
    if planner:
        return "\n".join([*lines, attention]).strip()
    return learning_prompt(
        "maisaka_replyer",
        identity="\n".join(lines),
        reply_style=sections.get("reply_style", ""),
        group_chat_attention_block=attention,
        replyer_output_instruction=_REPLY_TAIL,
    ).strip()


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


def _scan() -> dict[str, PersonaItem]:
    """扫描并读取全部人格。"""
    dir_path = get_personas_dir()
    _ensure_default_persona(dir_path)
    files = _collect_persona_files(dir_path)
    personas: dict[str, PersonaItem] = {}
    for key, path in files.items():
        try:
            personas[key] = PersonaItem(key=key, details=_read_text(path))
        except Exception as exc:
            logger.opt(exception=True).warning(f"[AI Chat] 读取人格失败: {path.name} err={exc}")
    if DEFAULT_PERSONA not in personas:
        personas[DEFAULT_PERSONA] = PersonaItem(key=DEFAULT_PERSONA, details="")
    return personas


def load_personas() -> dict[str, PersonaItem]:
    """加载全部人格。"""
    personas = cache.remember(("personas",), _scan)
    return dict(personas)


def get_persona_text(name: str | None = None) -> str:
    """读取指定人格内容，未知名称回退默认人格。"""
    persona_name = str(name or "").strip() or DEFAULT_PERSONA
    personas = load_personas()
    persona = personas.get(persona_name) or personas.get(DEFAULT_PERSONA)
    return persona.details if persona else ""


def _validated_persona_path(name: str, dir_path: Path) -> tuple[str, Path]:
    """校验人格名称并返回目录内的人格路径。"""
    raw_name = str(name or "")
    key = str(name or "").strip()
    if not key:
        raise ValueError("人格名称不能为空")
    if (
        any(ch in key for ch in '/\\:*?"<>|')
        or any(ord(ch) < 32 or 127 <= ord(ch) <= 159 for ch in raw_name)
        or key in {".", ".."}
        or Path(key).is_absolute()
    ):
        raise ValueError('人格名称不能包含 / \\ : * ? " < > |')
    directory = dir_path.resolve()
    path = directory / f"{key}.md"
    if path.resolve().parent != directory:
        raise ValueError("人格路径必须位于人格目录内")
    return key, path


def save_persona_text(name: str, text: str, *, overwrite: bool = True) -> Path:
    """保存人格内容；overwrite=False 时同名人格已存在会抛出 FileExistsError。"""
    dir_path = get_personas_dir()
    key, path = _validated_persona_path(name, dir_path)
    content = str(text or "").strip()
    if not content:
        raise ValueError("人格描述不能为空")
    if "\x00" in content:
        raise ValueError("人格描述包含非法字符")
    _ensure_default_persona(dir_path)
    existing = next((dir_path / f"{key}{ext}" for ext in (".md", ".txt") if (dir_path / f"{key}{ext}").exists()), None)
    if not overwrite and existing is not None:
        raise FileExistsError(key)
    path = existing or path
    path.write_text(content, encoding="utf-8")
    cache.pop(("personas",))
    return path


def delete_persona(name: str) -> int:
    """删除人格文件，返回删除数量。"""
    dir_path = get_personas_dir()
    key, path = _validated_persona_path(name, dir_path)
    if key == DEFAULT_PERSONA:
        raise ValueError("默认人格不能删除")
    removed = 0
    for ext in (".md", ".txt"):
        candidate = path.with_suffix(ext)
        if candidate.exists():
            candidate.unlink()
            removed += 1
    cache.pop(("personas",))
    if removed:
        clear_binding_value(PERSONA, key)
    return removed


def list_personas() -> dict[str, str]:
    """返回人格名称到内容的映射。"""
    return {key: item.details for key, item in load_personas().items()}
