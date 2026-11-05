"""服务器模式的持久状态、基础会话身份和人格播种升级。"""

from __future__ import annotations

import hashlib
from pathlib import Path

from ...chat import personas
from .config import persona_text
from ...config import get_manager
from ...chat.bindings import PERSONA, get_binding, set_binding

MARKER = "#server"
PERSONA_NAME = "运维"


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


def _sync_persona() -> None:
    """每次激活同步「运维」人格：控制台填写优先，否则使用插件内置版本。"""
    seed = persona_text() or Path(__file__).with_name("persona.md").read_text(encoding="utf-8")
    path = personas.get_personas_dir() / f"{PERSONA_NAME}.md"
    current = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    if _digest(seed) != _digest(current):
        personas.save_persona_text(PERSONA_NAME, seed, overwrite=True)


def set_mode(session_id: str, active: bool) -> None:
    """先同步人格再原子保存模式；用户绑定只补缺，不覆盖手动选择。"""
    base = base_session(session_id)
    derived = server_session(base)
    data = _load()
    entry = dict(data["sessions"].get(base, {}))
    if active:
        entry.pop("initialized", None)
        _sync_persona()
        if not get_binding(PERSONA, derived):
            set_binding(PERSONA, derived, PERSONA_NAME)
    entry["enabled"] = active
    data["sessions"][base] = entry
    _proxy().save(data)
