"""服务器模式的持久状态、基础会话身份和人格播种升级。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ...chat import personas
from ...config import get_manager
from ...chat.bindings import PERSONA, get_binding, set_binding

MARKER = "#server"
PERSONA_NAME = "运维"
# 已发布过的「运维」人格种子；内容未改过时允许被新版本覆盖。
_LEGACY_SEEDS = frozenset(
    {
        "090fcd4333c4ad1e91f574b795017e25d3b41bbb608cff2c977d419d049f2cc1",
        "bfe126c39bffae37d647760ed39e6b5612cfa66e61d0128a0d38671930fa163c",
        "0c457ce6a43b64f99d319119bc40cb408f32c5396b624ed45d0067a392a61ca6",
    }
)


def base_session(session_id: str) -> str:
    """只移除命名空间末尾的服务器标记，保留真实平台、类型和用户号。"""
    namespace, kind, target = session_id.rsplit(":", 2)
    return f"{namespace.removesuffix(MARKER)}:{kind}:{target}"


def server_session(session_id: str) -> str:
    """从基础私聊派生独立服务器会话。"""
    namespace, kind, target = base_session(session_id).rsplit(":", 2)
    if kind != "private" or not target:
        raise ValueError("服务器模式只适用于私聊")
    return f"{namespace}{MARKER}:{kind}:{target}"


def is_server_session(session_id: str) -> bool:
    """严格识别私聊命名空间标记，不使用任意子串匹配。"""
    parts = str(session_id).rsplit(":", 2)
    return len(parts) == 3 and parts[0].endswith(MARKER) and parts[1] == "private" and bool(parts[2])


def _proxy():
    """动态会话状态使用统一配置管理器保存，保留任意会话键。"""
    return get_manager().register("server_admin", {"sessions": {}}, filename="mode.json")


def _load() -> dict:
    """状态解析失败不沿用旧的开启值。"""
    proxy = _proxy()
    value = proxy.load()
    if proxy._last_error is not None or not isinstance(value.get("sessions"), dict):
        raise RuntimeError("服务器模式状态不可用")
    return value


def enabled(session_id: str) -> bool:
    """读取基础会话的持久开启状态。"""
    entry = _load()["sessions"].get(base_session(session_id), {})
    return isinstance(entry, dict) and entry.get("enabled") is True


def _digest(text: str) -> str:
    """统一换行和首尾空白后计算人格内容指纹。"""
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _sync_persona(entry: dict) -> None:
    """播种或升级「运维」人格；用户改过的内容不覆盖。"""
    seed = Path(__file__).with_name("persona.md").read_text(encoding="utf-8")
    seed_hash = _digest(seed)
    path = personas.get_personas_dir() / f"{PERSONA_NAME}.md"
    current = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    current_hash = _digest(current)
    if seed_hash != current_hash and (
        not current or current_hash == entry.get("persona_hash") or current_hash in _LEGACY_SEEDS
    ):
        personas.save_persona_text(PERSONA_NAME, seed, overwrite=True)
    entry["persona_hash"] = seed_hash


def set_mode(session_id: str, active: bool) -> None:
    """先同步人格再原子保存模式；用户绑定只补缺，不覆盖手动选择。"""
    base = base_session(session_id)
    derived = server_session(base)
    data = _load()
    entry = dict(data["sessions"].get(base, {}))
    if active:
        entry.pop("initialized", None)
        _sync_persona(entry)
        if not get_binding(PERSONA, derived):
            set_binding(PERSONA, derived, PERSONA_NAME)
    entry["enabled"] = active
    data["sessions"][base] = entry
    _proxy().save(data)
