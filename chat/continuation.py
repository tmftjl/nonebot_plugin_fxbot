"""插件发起后续对话的受控入口，目标仅从真实对话捕获。"""

from __future__ import annotations

import time
import asyncio
from math import isfinite
from uuid import uuid4
from typing import TYPE_CHECKING, Any
from collections import OrderedDict
from dataclasses import dataclass
from collections.abc import Callable

from nonebot import logger

from .hooks import ChatHookContext, hook_context
from .tools import ToolRuntime
from .types import ChatRequest
from .access import continuation_allowed
from .stages import execute_stage
from ..adapter import bind_bot, build_message, build_message_segment, extract_message_target, send_message_to_target

if TYPE_CHECKING:
    from .service import ChatService

MAX_DELAY_SECONDS = 3600.0
MAX_BINDINGS = 1024
MAX_TASKS = 256


@dataclass(frozen=True)
class _Binding:
    """核心捕获的真实目标与轮次，不允许插件自行指定收件人。"""

    context: ChatHookContext
    service: ChatService
    bot: Any
    target: dict[str, Any]
    created: float


class _ReplyTarget:
    """每次实际发送前重新检查连接、会员和轮次有效性。"""

    def __init__(self, binding: _Binding, valid: Callable[[], bool]) -> None:
        self.binding = binding
        self.valid = valid

    async def send(self, payload: Any) -> Any:
        """通过平台适配层发送，不保留已失效的 matcher。"""
        if not self.valid() or not await _eligible(self.binding) or not self.valid():
            raise RuntimeError("后续对话目标已失效")
        bot = self.binding.bot
        bind_bot(bot)
        if isinstance(payload, str):
            payload = build_message(bot, build_message_segment(bot, "text", payload))
        return await send_message_to_target(bot, self.binding.target, payload)

    async def finish(self, *args: Any, **kwargs: Any) -> None:
        """后台回复没有 matcher 生命周期需要结束。"""
        return None


async def _eligible(binding: _Binding) -> bool:
    """将调度绑定交给共享准入策略，发送与生成使用同一检查。"""
    return await continuation_allowed(binding.context, binding.bot)


class ReplyScheduler:
    """按插件与任务名管理后续对话，清空、卸载和退出可统一取消。"""

    def __init__(self) -> None:
        self.bindings: OrderedDict[str, _Binding] = OrderedDict()
        self.tasks: dict[tuple[str, str, str], asyncio.Task] = {}
        self.stopping = False

    def remember(self, service: ChatService, request: ChatRequest, runtime: ToolRuntime | None) -> None:
        """仅真实入站轮次可建立目标绑定，不让后续对话递归建立新起点。"""
        if self.stopping or request.metadata.get("_extension_owner") or getattr(runtime, "event", None) is None:
            return
        if getattr(runtime, "bot", None) is None:
            return
        try:
            binding = _Binding(
                hook_context(request),
                service,
                runtime.bot,
                dict(extract_message_target(runtime.event)),
                time.monotonic(),
            )
        except Exception:
            logger.opt(exception=True).warning("[ChatExtension] 捕获后续对话目标失败")
            return
        self.bindings[request.session_id] = binding
        self.bindings.move_to_end(request.session_id)
        cutoff = time.monotonic() - MAX_DELAY_SECONDS
        while self.bindings and (
            len(self.bindings) > MAX_BINDINGS or next(iter(self.bindings.values())).created < cutoff
        ):
            self.bindings.popitem(last=False)

    def schedule(
        self,
        owner: str,
        key: str,
        context: ChatHookContext,
        instruction: str,
        *,
        delay: float = 0,
        source: str | None = None,
    ) -> asyncio.Task[str]:
        """按真实轮次安排一次后续回复，同名任务替换；返回任务结果为已发送正文。"""
        if (
            not isinstance(owner, str)
            or not owner.strip()
            or not isinstance(key, str)
            or not key.strip()
            or not isinstance(instruction, str)
            or not instruction.strip()
            or isinstance(delay, bool)
            or not isinstance(delay, (int, float))
            or not isfinite(delay)
            or not 0 <= delay <= MAX_DELAY_SECONDS
        ):
            raise ValueError("后续对话参数无效")
        if self.stopping:
            raise RuntimeError("聊天扩展正在关闭")
        binding = self.bindings.get(context.session_id)
        if binding is None or (binding.context.turn_id, binding.context.user_id, binding.context.epoch) != (
            context.turn_id,
            context.user_id,
            context.epoch,
        ):
            raise ValueError("没有对应真实轮次的后续对话目标")
        # 后续请求只认核心捕获的身份，插件提供的快照不用于构造目标或群聊类型。
        context = binding.context
        task_key = (owner, context.session_id, key)
        previous = self.tasks.get(task_key)
        if previous is asyncio.current_task():
            raise ValueError("后续对话不能替换自身任务")
        if previous is None and len(self.tasks) >= MAX_TASKS:
            raise RuntimeError("后续对话任务数量已达上限")
        if previous is not None:
            previous.cancel()
        activity = binding.service.session_store.activity(context.session_id)

        def valid() -> bool:
            """新消息、新轮次、清空、卸载或过期均使旧任务失效。"""
            return (
                not self.stopping
                and self.tasks.get(task_key) is task
                and self.bindings.get(context.session_id) is binding
                and time.monotonic() - binding.created <= MAX_DELAY_SECONDS
                and binding.service.is_current(context.session_id, context.epoch)
                and binding.service.session_store.activity(context.session_id) == activity
            )

        async def run() -> str:
            try:
                await asyncio.sleep(delay)
                if not valid() or not await _eligible(binding) or not valid():
                    return ""
                bind_bot(binding.bot)
                token = uuid4().hex
                request = ChatRequest(
                    context.session_id,
                    "",
                    context.group_id,
                    "",
                    f"extension-{token}",
                    f"extension:{token}",
                    "proactive",
                    proactive=True,
                    platform=context.platform,
                    accepted_epoch=context.epoch,
                    metadata={"_extension_owner": source or owner, "continuation": True},
                )
                runtime = ToolRuntime(bot=binding.bot, matcher=_ReplyTarget(binding, valid), allowed_tools=frozenset())

                async def reply() -> str:
                    if not valid():
                        return ""
                    return await binding.service.focus_reply(request, runtime, instruction, is_valid=valid)

                return await execute_stage(
                    "scheduled_reply",
                    hook_context(request, phase="reply"),
                    reply,
                    rejected="",
                    owner=owner,
                    key=key,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.opt(exception=True).warning("[ChatExtension] 后续对话失败")
                return ""
            finally:
                if self.tasks.get(task_key) is asyncio.current_task():
                    self.tasks.pop(task_key, None)

        task = asyncio.create_task(run(), name=f"chat-extension:{owner}:{key}")
        self.tasks[task_key] = task
        return task

    def cancel(self, *, owner: str | None = None, session_id: str | None = None) -> None:
        """立即撤销任务身份后取消，发送前检查也会随即拒绝。"""
        for key, task in list(self.tasks.items()):
            if (owner is None or key[0] == owner) and (session_id is None or key[1] == session_id):
                self.tasks.pop(key, None)
                if task is not asyncio.current_task():
                    task.cancel()
        if session_id is not None:
            self.bindings.pop(session_id, None)

    async def stop(self) -> None:
        """关闭时先撤销目标和任务，再等待取消完成。"""
        self.stopping = True
        tasks = list(self.tasks.values())
        self.cancel()
        self.bindings.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


reply_scheduler = ReplyScheduler()
