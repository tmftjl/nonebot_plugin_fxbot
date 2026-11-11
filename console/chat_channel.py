"""WebUI 独立聊天渠道，复用正常对话与可见历史，不模拟真实平台身份。"""

from __future__ import annotations

import json
import asyncio
from uuid import UUID, uuid4
from typing import Any
from dataclasses import dataclass
from collections.abc import AsyncIterator

from nonebot import logger

from ..chat.hooks import ChatHookContext, guard, notify
from ..chat.tools import ToolRuntime
from ..chat.types import ChatRequest, speaker_label
from ..chat.service import ChatService, chat_service
from ..chat.bindings import PERSONA, PROVIDER, get_binding, set_binding
from ..chat.personas import load_personas, effective_persona_name
from ..chat.providers import provider_manager
from ..chat.extensions import ChatExtension, SessionPolicy, SystemPromptSection
from ..chat.work_schedule import can_reply

PREFIX = "webui:console:private:"
CHANNEL_TOOLS = frozenset(
    {
        "reply",
        "wait",
        "stop",
        "set_reply_options",
        "tool_search",
        "read_tool_result",
        "web_search",
        "web_fetch",
        "query_memory",
        "query_person_profile",
    }
)
MAX_ACTIVE_TURNS = 16
TURN_TIMEOUT = 300.0
REJECTION_MESSAGES = {
    "unavailable": "聊天服务正在关闭",
    "busy": "当前会话仍在处理中，请先停止或稍后重试",
    "capacity": "当前对话较多，请稍后重试",
    "empty": "请输入消息内容",
    "disabled": "AI 已关闭或当前不在允许回复的时间内",
    "persona": "所选人格已不可用，请重新选择",
    "provider": "所选模型已不可用，请重新选择",
    "ingress": "消息未被接收策略放行",
    "persona_locked": "请新建或清空会话后切换人格",
    "duplicate": "这条消息已经接收，请刷新查看历史",
    "clearing": "当前会话正在清空，请稍后重试",
    "stale": "会话已清空，请重新发送",
}
_extension = ChatExtension("console_chat")


def session_id(conversation: UUID) -> str:
    """只从经过校验的 UUID 派生本渠道租户，不能注入真实聊天目标。"""
    return PREFIX + conversation.hex


def _policy(tenant: str) -> SessionPolicy | None:
    """网页即时显示回复，不加入共享 Focus 或模拟打字，但保留正常规划和记忆。"""
    if tenant.startswith(PREFIX):
        return SessionPolicy(allow_focus=False, allow_debounce=False, allow_typing=False, allow_typo=False)
    return None


def _prompt(context: ChatHookContext) -> SystemPromptSection | None:
    """声明真实渠道能力，不把网页身份伪装成 QQ 用户或机器人账号。"""
    if context.session_id.startswith(PREFIX):
        return SystemPromptSection(
            "当前在 WebUI 独立私聊会话中与控制台用户交谈。仅支持文字收发；"
            "没有连接真实平台机器人，不支持发送附件、语音、表情或执行群管理操作。"
        )
    return None


_extension.on("session_policy", _policy)
_extension.on("system_prompt", _prompt)


class ChannelRejected(ValueError):
    """使用预定义提示说明可预期的渠道拒绝。"""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass
class ChannelTurn:
    """一轮请求的真实身份、有限传输队列与受管理生成任务。"""

    request: ChatRequest
    queue: asyncio.Queue[dict[str, Any]]
    task: asyncio.Task[None] | None = None
    cancelled: bool = False


class _WebSender:
    """将实际发送的文字交给网页响应队列，核心随后记录可见历史。"""

    def __init__(self, turn: ChannelTurn, service: ChatService) -> None:
        self.turn = turn
        self.service = service

    async def send(self, payload: Any) -> dict[str, str]:
        """队列接收失败或轮次失效时不能报告发送成功。"""
        request = self.turn.request
        if self.turn.cancelled:
            raise asyncio.CancelledError
        if not isinstance(payload, str):
            raise TypeError("网页渠道只支持文字")
        if not self.service.is_current(request.session_id, request.accepted_epoch):
            raise RuntimeError("网页轮次已失效")
        receipt = uuid4().hex
        await self.turn.queue.put({"type": "message", "id": receipt, "content": payload})
        return {"message_id": receipt}


class ConsoleChatChannel:
    """管理独立会话的收发与取消，历史由现有 ChatService 持久化。"""

    def __init__(self, service: ChatService = chat_service) -> None:
        self.service = service
        self.active: dict[str, ChannelTurn] = {}
        self.clearing: set[str] = set()
        self.stopping = False

    def _available(self, tenant: str) -> None:
        """限制并发并防止清空与新请求交错。"""
        if self.stopping:
            raise ChannelRejected("unavailable")
        if tenant in self.active or tenant in self.clearing:
            raise ChannelRejected("busy")
        if len(self.active) >= MAX_ACTIVE_TURNS:
            raise ChannelRejected("capacity")

    async def snapshot(self, conversation: UUID) -> dict[str, Any]:
        """只返回真实用户与已发送助手正文，内部规划和参考不进入网页历史。"""
        tenant = session_id(conversation)
        await self.service.ensure_hydrated(tenant, is_group=False)
        items = []
        for index, item in enumerate(self.service.session_store.get(tenant)):
            role = item.get("role")
            if (
                role not in {"user", "assistant"}
                or item.get("_planner_internal")
                or item.get("_reference")
                or item.get("tool_calls")
                or item.get("tool_call_id")
                or not isinstance(item.get("content"), str)
                or (role == "assistant" and not item.get("_self_message"))
            ):
                continue
            text = item["content"]
            if role == "user":
                text = text.removeprefix(
                    speaker_label(str(item.get("_sender_id") or ""), str(item.get("_sender_name") or "")) + ": "
                )
            items.append(
                {"id": f"{role}:{index}", "role": role, "content": text, "timestamp": item.get("_timestamp", "")}
            )
        return {
            "conversation": str(conversation),
            "tenant": tenant,
            "items": items[-100:],
            "running": tenant in self.active or tenant in self.clearing,
            "message_id": self.active[tenant].request.message_id if tenant in self.active else None,
            "persona": get_binding(PERSONA, tenant),
            "provider": get_binding(PROVIDER, tenant),
            "effective_persona": effective_persona_name(tenant),
            "can_chat": can_reply(session_id=tenant),
        }

    async def begin(
        self, conversation: UUID, *, text: str, message_id: UUID, nickname: str, persona: str, provider: str
    ) -> ChannelTurn:
        """接收入站并建立受控任务，不借用真实 Bot 或平台 Event。"""
        tenant = session_id(conversation)
        self._available(tenant)
        if not text.strip():
            raise ChannelRejected("empty")
        if not can_reply(session_id=tenant):
            raise ChannelRejected("disabled")
        if persona and persona not in load_personas():
            raise ChannelRejected("persona")
        if provider and provider not in provider_manager.list_providers_by_type("chat"):
            raise ChannelRejected("provider")
        await self.service.ensure_hydrated(tenant, is_group=False)
        self._available(tenant)
        accepted_epoch = self.service.session_store.epoch(tenant)
        context = ChatHookContext(tenant, user_id=conversation.hex, platform="webui", phase="ingress")
        if not await guard("ingress_guard", context, path="reply", text=text):
            await notify("ingress_rejected", context, path="reply")
            raise ChannelRejected("ingress")
        self._available(tenant)
        if not self.service.is_current(tenant, accepted_epoch):
            raise ChannelRejected("stale")
        if persona != get_binding(PERSONA, tenant) and self.service.session_store.get(tenant):
            raise ChannelRejected("persona_locked")
        for kind, value in ((PERSONA, persona), (PROVIDER, provider)):
            if get_binding(kind, tenant) != value:
                set_binding(kind, tenant, value or None)
        store = self.service.session_store
        if not store.mark_seen(tenant, f"webui:{message_id.hex}"):
            raise ChannelRejected("duplicate")
        request = ChatRequest(
            tenant,
            conversation.hex,
            None,
            text,
            uuid4().hex,
            f"webui:{message_id.hex}",
            "private",
            message_id=message_id.hex,
            user_name=nickname,
            platform="webui",
            trigger_seq=store.next_seq(tenant, conversation.hex),
            accepted_epoch=store.epoch(tenant),
            activity_seq=store.activity(tenant),
        )
        turn = ChannelTurn(request, asyncio.Queue(maxsize=32))
        self.active[tenant] = turn
        turn.task = asyncio.create_task(self._run(turn), name=f"console-chat:{conversation.hex}")
        return turn

    async def _run(self, turn: ChannelTurn) -> None:
        """通过正常规划与发送链处理请求，错误只回传固定提示。"""
        request = turn.request
        subscription = ChatExtension(f"console-turn:{request.turn_id}")

        def progress(event: str, context: ChatHookContext, details: Any) -> None:
            """只推送本会话的轻量状态，不暴露模型思考、工具参数或内部异常。"""
            if context.session_id == request.session_id and not turn.cancelled and not turn.queue.full():
                label = (
                    "正在调用工具" if event == "tool_start" else "正在回复" if context.phase == "reply" else "正在思考"
                )
                turn.queue.put_nowait({"type": "status", "content": label})

        subscription.on("observer", progress, events={"request_start", "tool_start"})
        try:
            await notify(
                "ingress_accepted",
                ChatHookContext(request.session_id, user_id=request.user_id, platform="webui", phase="ingress"),
                path="reply",
            )
            await self.service.record_group_message(
                request.session_id,
                request.user_id,
                request.user_name,
                request.text,
                is_group=False,
                dedupe_key=request.dedupe_key,
                platform="webui",
                message_id=request.message_id,
                occurred_at=request.occurred_at,
            )
            request.metadata.update({"history_recorded": True, "history_appended": True})
            await turn.queue.put({"type": "accepted", "id": request.message_id, "content": request.text})
            runtime = ToolRuntime(matcher=_WebSender(turn, self.service), allowed_tools=CHANNEL_TOOLS)
            sent = await asyncio.wait_for(self.service.process_and_send(request, runtime), TURN_TIMEOUT)
            if not sent:
                await turn.queue.put({"type": "status", "content": "本轮没有发送回复"})
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.opt(exception=True).warning("[ConsoleChat] 网页对话处理失败")
            await turn.queue.put({"type": "error", "content": "本轮对话未能完成，请稍后重试"})
        finally:
            subscription.close()
            if not turn.queue.full():
                turn.queue.put_nowait({"type": "done"})
            if self.active.get(request.session_id) is turn:
                self.active.pop(request.session_id)

    async def events(self, turn: ChannelTurn) -> AsyncIterator[str]:
        """响应断开即取消本轮，队列施加背压，不在后台遗留生成任务。"""
        try:
            while turn.task is not None:
                if turn.task.done() and turn.queue.empty():
                    break
                try:
                    event = await asyncio.wait_for(turn.queue.get(), 15)
                except asyncio.TimeoutError:
                    event = {"type": "heartbeat"}
                if turn.cancelled and event["type"] != "done":
                    continue
                yield json.dumps(event, ensure_ascii=False) + "\n"
                if event["type"] == "done":
                    break
        finally:
            await self.release(turn)

    async def release(self, turn: ChannelTurn) -> None:
        """按对象身份幂等释放轮次，响应尚未开始迭代就断开时也能清理。"""
        turn.cancelled = True
        if turn.task is not None:
            if not turn.task.done():
                turn.task.cancel()
            await asyncio.gather(turn.task, return_exceptions=True)
        if self.active.get(turn.request.session_id) is turn:
            self.active.pop(turn.request.session_id)

    async def clear(self, conversation: UUID) -> bool:
        """先取消旧任务再清空正常历史，清理期间拒绝新轮次。"""
        tenant = session_id(conversation)
        if tenant in self.clearing:
            raise ChannelRejected("clearing")
        self.clearing.add(tenant)
        try:
            turn = self.active.get(tenant)
            if turn is not None:
                await self.release(turn)
            return await self.service.clear_session(tenant)
        finally:
            self.clearing.discard(tenant)

    async def cancel(self, conversation: UUID, message_id: UUID) -> bool:
        """只停止指定请求，迟到的浏览器停止操作不能取消后续新请求。"""
        tenant = session_id(conversation)
        turn = self.active.get(tenant)
        if turn is None or turn.request.message_id != message_id.hex or turn.task is None:
            return False
        await self.release(turn)
        return True

    async def stop(self) -> None:
        """框架退出时在模型和共享资源释放前取消网页对话。"""
        self.stopping = True
        await asyncio.gather(*(self.release(turn) for turn in tuple(self.active.values())))


console_chat = ConsoleChatChannel()
