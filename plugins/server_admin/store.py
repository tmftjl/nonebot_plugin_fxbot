"""服务器模式的持久状态、基础会话身份和首次人格绑定。"""

from __future__ import annotations

from pathlib import Path

from ...config import get_manager
from ...chat.bindings import PERSONA, get_binding, set_binding
from ...chat.personas import save_persona_text

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


def set_mode(session_id: str, active: bool) -> None:
    """先初始化人格再原子保存模式，退出后保留首次初始化标记。"""
    base = base_session(session_id)
    derived = server_session(base)
    data = _load()
    entry = dict(data["sessions"].get(base, {}))
    if active and not entry.get("initialized"):
        text = Path(__file__).with_name("persona.md").read_text(encoding="utf-8")
        try:
            save_persona_text(PERSONA_NAME, text, overwrite=False)
        except FileExistsError:
            pass
        if not get_binding(PERSONA, derived):
            set_binding(PERSONA, derived, PERSONA_NAME)
        entry["initialized"] = True
    entry["enabled"] = active
    data["sessions"][base] = entry
    _proxy().save(data)
