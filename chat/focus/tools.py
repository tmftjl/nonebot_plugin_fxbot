"""Focus 工具声明与路由：模型显式指定 session_id，执行到对应聊天。"""

from __future__ import annotations

import copy
import json
import asyncio
from typing import TYPE_CHECKING, Any

from nonebot import logger

from ..tools import ToolSpec, ToolContext, execute_tool, tool_visible, default_registry, prepare_tool_result_content
from ..replyer.generator import TOOL_TIMEOUT, allowed_tools

if TYPE_CHECKING:
    from .group_runtime import FocusGroupRuntime

REPLY_TOOL = "reply"
WAIT_TOOL = "wait"
STOP_TOOL = "stop"

_SESSION_ARG = {
    "type": "string",
    "description": "目标聊天的真实 session_id；必须从共享组聊天列表或消息来源中选择。",
}
_REPLY_DESCRIPTION = "在指定聊天里生成并发送一条回复。回复正文由该聊天自己的回复模型生成，这里只给回复方向。"
_WAIT_DESCRIPTION = "让整个共享组暂停一段时间，期间任何聊天收到 @ 会提前唤醒。"
_STOP_DESCRIPTION = "结束本轮共享思考，不再调用工具或发送消息。"


def _definition(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """构造 OpenAI function 工具声明。"""
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


def _control_definitions(session_ids: list[str]) -> list[dict[str, Any]]:
    """共享组自身的控制工具。"""
    return [
        _definition(
            REPLY_TOOL,
            _REPLY_DESCRIPTION,
            {
                "session_id": {**_SESSION_ARG, "enum": session_ids},
                "reply_reference": {
                    "type": "string",
                    "description": "本次回复的上下文关键信息与回复方向；不是回复正文。",
                },
            },
            ["session_id", "reply_reference"],
        ),
        _definition(WAIT_TOOL, _WAIT_DESCRIPTION, {"seconds": {"type": "integer", "minimum": 0}}, ["seconds"]),
        _definition(STOP_TOOL, _STOP_DESCRIPTION, {}, []),
    ]


class FocusToolRouter:
    """合并各会话的可用工具，并保留按 session_id 的权限差异。"""

    def __init__(self, group: FocusGroupRuntime) -> None:
        self.group = group
        self.variants: dict[str, dict[str, ToolSpec]] = {}

    async def refresh(self) -> list[dict[str, Any]]:
        """按成员当前权限重建工具表。"""
        variants: dict[str, dict[str, ToolSpec]] = {}
        for session_id, member in list(self.group.members.items()):
            if not self.group.is_member_valid(member):
                continue
            names, _ = await allowed_tools(member.runtime, proactive=False, session_id=session_id)
            member.allowed_names = names
            for name in names:
                spec = default_registry.get(name)
                if spec is not None and tool_visible(spec, session_id):
                    variants.setdefault(name, {})[session_id] = spec
        self.variants = variants
        session_ids = sorted(self.group.members)
        definitions = _control_definitions(session_ids)
        for name, by_session in sorted(variants.items()):
            spec = next(iter(by_session.values()))
            parameters = copy.deepcopy(spec.parameters or {"type": "object", "properties": {}})
            properties = parameters.setdefault("properties", {})
            properties["session_id"] = {**_SESSION_ARG, "enum": sorted(by_session)}
            parameters["required"] = list(dict.fromkeys([*parameters.get("required", []), "session_id"]))
            definitions.append(_definition(name, spec.description, properties, list(parameters["required"])))
        return definitions

    async def invoke(self, name: str, arguments: dict[str, Any], source_session: str = "") -> tuple[str, bool]:
        """执行一次工具调用，返回 (结果文本, 是否暂停本轮)；特权工具只认本轮来源会话。"""
        session_id = str(arguments.get("session_id") or "")
        if name == WAIT_TOOL:
            return self.group.control_wait(arguments, session_id)
        if name == STOP_TOOL:
            return self.group.control_stop()
        member = self.group.members.get(session_id)
        if member is None or not self.group.is_member_valid(member):
            return _failure(f"session_id={session_id} 不属于当前有效共享组，不能执行工具。"), False
        if name == REPLY_TOOL:
            instruction = str(arguments.get("reply_reference") or "").strip()
            if not instruction:
                return _failure("reply_reference 不能为空。"), False
            return await self.group.reply(member, instruction)
        if name not in member.allowed_names:
            return _failure(f"工具 {name} 在目标聊天不可用。"), False
        spec = default_registry.get(name)
        if spec is not None and spec.permission and source_session != session_id:
            return _failure("特权操作只能由目标聊天本轮的请求发起，请在该聊天里重新请求。"), False
        native = {key: value for key, value in arguments.items() if key != "session_id"}
        context = ToolContext(
            user_id=member.request.user_id,
            group_id=member.request.group_id,
            session_id=session_id,
            platform=member.request.platform,
            proactive=False,
            metadata={"allowed_tools": sorted(member.allowed_names)},
        )
        try:
            result = await asyncio.wait_for(
                execute_tool(name, native, context, member.runtime, allowed_names=member.allowed_names),
                timeout=TOOL_TIMEOUT,
            )
        except asyncio.TimeoutError:
            result = {"ok": False, "error": "工具执行超时"}
        except Exception:
            logger.opt(exception=True).warning(f"[ChatFocus] 工具调用失败: {name}")
            result = {"ok": False, "error": "工具执行失败"}
        content = await prepare_tool_result_content(
            result, session_id=session_id, tool_name=name, arguments=native, timeout=TOOL_TIMEOUT
        )
        return content, False


def _failure(reason: str) -> str:
    """失败结果也用工具结果格式交给模型。"""
    return json.dumps({"ok": False, "error": reason}, ensure_ascii=False)
