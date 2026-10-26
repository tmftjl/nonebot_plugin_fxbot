"""遗忘、画像查看与撤回失效的业务入口。"""

from __future__ import annotations

from .state import is_ready
from .repository import chat_repository


def _require_ready() -> None:
    """记忆不可用时直接失败，不能伪报删除成功。"""
    if not is_ready():
        raise RuntimeError("记忆功能暂不可用")


async def forget_subject(tenant: str, subject_id: str, *, reason: str = "user") -> int:
    """删除主体全部长期记忆并写禁止重建标记。"""
    _require_ready()
    return await chat_repository.forget_subject(tenant, subject_id, reason=reason)


async def forget_fact(tenant: str, subject_id: str, keyword: str, *, reason: str = "user") -> list[tuple[str, str]]:
    """删除匹配关键词的事实，返回被删除的 (key, value)。"""
    _require_ready()
    return await chat_repository.forget_fact(tenant, subject_id, keyword, reason=reason)


async def profile_view(tenant: str, subject_id: str) -> str:
    """返回画像与事实的可读文本。"""
    _require_ready()
    profile = await chat_repository.get_profile(tenant, subject_id)
    facts = await chat_repository.get_facts(tenant, subject_id, limit=20)
    lines: list[str] = []
    if profile is not None and not profile.stale:
        name = profile.display_name or subject_id
        summary = profile.manual_override or profile.summary
        lines.append(f"{name}的画像：{summary or '暂无'}")
    elif profile is not None:
        lines.append("画像正在重建，暂不可用")
    else:
        lines.append("还没有可用的长期画像")
    if facts:
        lines.append("已知事实：")
        lines.extend(f"- {fact.key.split('.', 1)[-1]}：{fact.value}" for fact in facts)
    return "\n".join(lines)


async def handle_recall(tenant: str, platform_message_id: str, *, operator: str = "") -> dict[str, int] | None:
    """处理撤回；消息不存在时返回 None。"""
    _require_ready()
    return await chat_repository.invalidate_message(tenant, platform_message_id, operator=operator)
