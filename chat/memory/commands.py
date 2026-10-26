"""长期记忆命令：画像、忘记与学习开关。"""

from __future__ import annotations

from typing import Any

from nonebot import logger, on_message
from nonebot.rule import Rule
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from .state import is_ready
from .config import tenant_learn_enabled
from ...adapter import normalize_id, event_user_id, event_group_id, event_plain_text
from ..commands import _group_role, _strip_prefix, _is_privileged
from .lifecycle import forget_fact, profile_view, forget_subject
from .repository import chat_repository
from ..message_adapter import collect_mentions, session_id_for_event


def _memory_text(event: Any) -> str:
    """读取去掉命令前缀后的文本。"""
    return _strip_prefix(event_plain_text(event).strip()) or ""


def _profile_rule(bot: Bot, event: Event) -> bool:
    return _memory_text(event).startswith("画像")


def _forget_rule(bot: Bot, event: Event) -> bool:
    text = _memory_text(event)
    return text == "忘记我" or text.startswith("忘记")


def _learn_rule(bot: Bot, event: Event) -> bool:
    return _memory_text(event).startswith("记忆")


profile_cmd = on_message(rule=Rule(_profile_rule), priority=4, block=True)
forget_cmd = on_message(rule=Rule(_forget_rule), priority=4, block=True)
learn_cmd = on_message(rule=Rule(_learn_rule), priority=4, block=True)


async def _is_admin(group_id: str, user_id: str, event: Any) -> bool:
    """群内要求群主/管理员，私聊本人即可。"""
    if not group_id:
        return True
    return _is_privileged(user_id) or await _group_role(group_id, user_id, event) in {"owner", "admin"}


async def _target(event: Any, bot: Any) -> str | None:
    """读取命令里第一个被 @ 的成员。"""
    mentions = await collect_mentions(bot, event, normalize_id(event_group_id(event)), resolve_names=False)
    return mentions[0].user_id if mentions else None


async def _ready_or_finish(matcher: Matcher) -> bool:
    """记忆不可用时明确告知，不装作成功。"""
    if is_ready():
        return True
    await matcher.finish("记忆功能暂不可用，请稍后再试")
    return False


@profile_cmd.handle()
async def _handle_profile(bot: Bot, event: Event, matcher: Matcher) -> None:
    """查看自己或他人画像。"""
    if not await _ready_or_finish(matcher):
        return
    tenant = session_id_for_event(bot, event)
    group_id = normalize_id(event_group_id(event)) or ""
    user_id = normalize_id(event_user_id(event)) or ""
    target = await _target(event, bot)
    if target and target != user_id and not await _is_admin(group_id, user_id, event):
        await matcher.finish("只有群主或管理员可以查看他人画像")
    try:
        text = await profile_view(tenant, target or user_id)
    except Exception:
        logger.opt(exception=True).warning("[ChatMemory] 读取画像失败")
        await matcher.finish("画像读取失败，请稍后再试")
        return
    await matcher.finish(text)


@forget_cmd.handle()
async def _handle_forget(bot: Bot, event: Event, matcher: Matcher) -> None:
    """删除自己的记忆；@ 他人需要管理权限。"""
    if not await _ready_or_finish(matcher):
        return
    text = _memory_text(event)
    tenant = session_id_for_event(bot, event)
    group_id = normalize_id(event_group_id(event)) or ""
    user_id = normalize_id(event_user_id(event)) or ""
    target = await _target(event, bot)
    keyword = text.removeprefix("忘记我").removeprefix("忘记").strip()
    if target and target != user_id and not await _is_admin(group_id, user_id, event):
        await matcher.finish("只有群主或管理员可以删除他人记忆")
    subject = target or user_id
    if text == "忘记我" or (not keyword and target):
        try:
            count = await forget_subject(tenant, subject, reason="user_command")
        except Exception:
            logger.opt(exception=True).warning("[ChatMemory] 删除主体记忆失败")
            await matcher.finish("删除失败，请稍后再试")
            return
        await matcher.finish(f"已忘记相关的长期记忆（{count} 条事实）")
    if not keyword:
        await matcher.finish("用法：#忘记我、#忘记 <关键词> 或 #忘记 @成员")
    try:
        removed = await forget_fact(tenant, subject, keyword, reason="user_command")
    except Exception:
        logger.opt(exception=True).warning("[ChatMemory] 删除事实失败")
        await matcher.finish("删除失败，请稍后再试")
        return
    if not removed:
        await matcher.finish("没有找到匹配的事实")
    listed = "、".join(value for _, value in removed[:5])
    await matcher.finish(f"已忘记 {len(removed)} 条事实：{listed}")


@learn_cmd.handle()
async def _handle_learn(bot: Bot, event: Event, matcher: Matcher) -> None:
    """开启或关闭本会话长期学习。"""
    if not await _ready_or_finish(matcher):
        return
    text = _memory_text(event)
    group_id = normalize_id(event_group_id(event)) or ""
    user_id = normalize_id(event_user_id(event)) or ""
    if not await _is_admin(group_id, user_id, event):
        await matcher.finish("只有群主或管理员可以调整长期学习开关")
    tenant = session_id_for_event(bot, event)
    argument = text.removeprefix("记忆").strip()
    scope = "本群" if group_id else "当前会话"
    if argument in {"开", "开启", "打开"}:
        enabled = True
    elif argument in {"关", "关闭"}:
        enabled = False
    elif not argument:
        state = await chat_repository.get_session_state(tenant)
        status = "开启" if tenant_learn_enabled(state) else "关闭"
        await matcher.finish(f"{scope}长期记忆：{status}")
        return
    else:
        await matcher.finish("用法：#记忆 开 / #记忆 关")
    try:
        await chat_repository.set_learn_enabled(tenant, enabled)
    except Exception:
        logger.opt(exception=True).warning("[ChatMemory] 写入学习开关失败")
        await matcher.finish("设置失败，请稍后再试")
        return
    await matcher.finish(f"{scope}长期记忆已{'开启' if enabled else '关闭'}")
