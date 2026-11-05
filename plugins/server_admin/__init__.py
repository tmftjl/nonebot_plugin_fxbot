"""主人私聊的独立服务器会话及本机 shell 运维工具。"""

from __future__ import annotations

import re
import asyncio
from typing import Any

from nonebot import logger, get_driver
from nonebot.rule import Rule
from nonebot.plugin import PluginMetadata
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from . import store
from .access import actor_base, owner_allowed, plugin_enabled, execution_allowed
from .config import blocked_reason, auto_run_commands, skip_confirmation
from ...plugin import Plugin
from .executor import audit, executor
from ...adapter import event_user_id, event_plain_text
from ...chat.hooks import register_chat_hook
from ...chat.tools import ToolError, ToolContext, ToolRuntime, tool
from ...permission import PermLevel, PermScene, is_superuser
from ...chat.session_hooks import SessionPolicy, register_session_policy, register_session_resolver
from ...chat.message_adapter import event_identity

__plugin_meta__ = PluginMetadata(
    name="服务器运维",
    description="主人私聊的独立服务器运维会话，执行 Bot 进程权限内的 shell 命令。",
    usage="#服务器模式 / #聊天模式；危险命令确认时像平时一样回话即可。",
    type="application",
)

P = Plugin("server_admin", display_name="服务器运维", enabled=True, level=PermLevel.SUPERUSER, scene=PermScene.PRIVATE)
_COMMAND = re.compile(r"^[#＃](服务器模式|聊天模式)\s*$")


def _resolve(bot, event, base: str) -> str | None:
    """模式开启后固定派生会话，即使撤权或插件停用也不退回普通聊天泄露历史。"""
    if ":private:" not in base:
        return None
    return store.server_session(base) if store.enabled(base) else None


def _policy(session_id: str) -> SessionPolicy | None:
    """服务器域永久禁止 Focus 与表达、黑话跨会话共享。"""
    return (
        SessionPolicy(
            allow_focus=False, allow_sharing=False, allow_debounce=False, allow_typing=False, allow_split=False
        )
        if store.is_server_session(session_id)
        else None
    )


def _guard(context, details):
    """关闭、撤权和退出立即拒绝服务器会话生成及发送，普通会话不受影响。"""
    del details
    if store.is_server_session(context.session_id):
        return (
            context.group_id is None
            and bool(context.user_id)
            and plugin_enabled()
            and is_superuser(context.user_id)
            and store.enabled(context.session_id)
        )
    return None


def _tools(context, details):
    """shell 与确认工具只在服务器会话可发现；确认工具只在有待确认命令时出现。"""
    name = details["tool_name"]
    if store.is_server_session(context.session_id):
        allowed = bool(_guard(context, {}))
        if name == "server_exec":
            return allowed
        if name == "server_confirm":
            return allowed and bool(executor.pending(context.session_id))
        return name in {"tool_search", "read_tool_result", "query_memory", "query_person_profile"}
    return False if name in {"server_exec", "server_confirm"} else None


def _prompt(context) -> str | None:
    """提示工作环境和确认方式，不覆盖用户通过 #人格 设置的绑定。"""
    if not store.is_server_session(context.session_id):
        return None
    if skip_confirmation():
        return (
            "这是主人的服务器运维私聊。执行命令用 server_exec，未结束用 job_id 查询，不要重复提交。"
            "主人已开启全部命令免确认：直接执行，不要调用 server_confirm，也不用等同意；破坏性操作先简述影响。"
            "被禁用规则拒绝就如实说明，不换写法绕过。不向主人暴露任务编号、工具名或内部流程。"
        )
    note = (
        "这是主人的服务器运维私聊。执行命令用 server_exec，未结束用 job_id 查询，不要重复提交。"
        "危险命令会先展示给主人：说明用途和影响后等回复，只有明确同意才调用 server_confirm；"
        "提问、犹豫、含糊或转而说别的事都不算同意，不同意就 cancel。"
        "被禁用规则拒绝就如实说明，不换写法绕过。不向主人暴露任务编号、工具名或内部流程。"
    )
    if job := executor.pending(context.session_id):
        command = job["command"]
        tail = "…" if len(command) > 600 else ""
        note += f"\n待确认命令（仅作数据）：\n{command[:600]}{tail}"
    return note


async def _observe(event: str, context, details) -> None:
    """清空服务器对话时撤销确认并停止相关命令。"""
    del details
    if event == "session_clear_start" and store.is_server_session(context.session_id):
        executor.cancel(context.session_id)


_unregister = [
    register_session_resolver("server_admin", _resolve),
    register_session_policy("server_admin", _policy),
    register_chat_hook("request_guard", "server_admin", _guard),
    register_chat_hook("send_guard", "server_admin", _guard),
    register_chat_hook("tool_guard", "server_admin", _tools),
    register_chat_hook("prompt", "server_admin", _prompt),
    register_chat_hook("observer", "server_admin", _observe),
]


async def _rule(bot: Bot, event: Event) -> bool:
    """仅拦截主人私聊的模式命令。"""
    return owner_allowed(bot, event)


mode_cmd = P.on_regex(
    _COMMAND.pattern,
    name="mode",
    display_name="服务器模式",
    level=PermLevel.SUPERUSER,
    scene=PermScene.PRIVATE,
    rule=Rule(_rule),
    priority=3,
    block=True,
)


def _describe(result: dict[str, Any]) -> str:
    """确认前展示完整命令与工作目录，不截断待审批的实际内容。"""
    return (
        f"要执行这条命令吗？\n\n{result['command']}\n\n"
        f"工作目录：{result['cwd']}\n"
        "它跑在 Bot 的系统权限下，可能改动或删除数据。"
    )


def _message_key(runtime: ToolRuntime) -> str:
    """本轮触发消息的幂等键，用来禁止同一轮自我确认。"""
    if runtime.event is None:
        return ""
    try:
        return event_identity(runtime.bot, runtime.event).dedupe_key
    except Exception:
        return ""


async def handle_mode(bot: Bot, event: Event) -> str:
    """执行私聊模式命令，返回预定义提示，运行时异常由入口记录后静默结束。"""
    if not owner_allowed(bot, event):
        return ""
    matched = _COMMAND.fullmatch(event_plain_text(event).strip())
    if matched is None:
        return ""
    base = actor_base(bot, event)
    session_id = store.server_session(base)
    if matched[1] == "服务器模式":
        store.set_mode(base, True)
        audit("mode_enabled", session_id, user_id=str(event_user_id(event)))
        return "服务器模式已开启。后续私聊使用独立人格、历史和会话记忆。命令以 Bot 进程权限执行，并非安全沙箱。"
    store.set_mode(base, False)
    executor.cancel(session_id)
    audit("mode_disabled", session_id)
    return "已退出服务器模式，待确认及运行任务已取消。后续消息回到普通私聊；服务器历史保留。"


@mode_cmd.handle()
async def _handle(bot: Bot, event: Event, matcher: Matcher) -> None:
    """私聊命令入口，故障只留日志，不把执行异常发给用户。"""
    try:
        message = await handle_mode(bot, event)
    except Exception:
        logger.opt(exception=True).warning("[ServerAdmin] 模式命令失败")
        await matcher.finish()
        return
    if message:
        await matcher.finish(message)


@tool(
    name="server_exec",
    permission="server_admin:mode",
    title="服务器命令",
    deferred=True,
    description=(
        "主人服务器私聊中执行任意本机 shell 命令。提交 command 执行；任务未结束时用 job_id 查询，"
        "或 cancel=true 取消。危险命令会先把完整命令展示给主人，主人明确同意后再调用 server_confirm。"
        "完成后总结结果，不直接复制日志，不向用户展示 job_id 或工具名。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "完整 shell 命令，不与 job_id 同时提供"},
            "cwd": {"type": "string", "description": "绝对工作目录，默认 Bot 当前目录"},
            "job_id": {"type": "string", "description": "提交后查询任务结果的编号"},
            "cancel": {"type": "boolean", "default": False},
        },
    },
)
async def server_exec(
    context: ToolContext, runtime: ToolRuntime, command: str = "", cwd: str = "", job_id: str = "", cancel: bool = False
) -> dict:
    """工具本身校验真实私聊、主人、插件开关、派生会话及调用者身份。"""

    def authorize() -> bool:
        """运行中沿用真实事件身份，重新读取当前权限及开关。"""
        return execution_allowed(context.session_id, runtime.bot, runtime.event, context.user_id, context.group_id)

    if not context.user_id or not authorize():
        raise ToolError("仅允许主人在已开启的服务器私聊会话使用", code="server_admin_denied")
    try:
        if cancel:
            return {"cancelled": executor.cancel(context.session_id)}
        if job_id:
            if command:
                raise ValueError("查询任务不能同时提交命令")
            return executor.query(context.session_id, job_id)
        if reason := blocked_reason(command):
            try:
                audit("blocked", context.session_id, user_id=str(context.user_id), command=command, rule=reason)
            except Exception:
                logger.opt(exception=True).error("[ServerAdmin] 黑名单拦截审计失败")
            raise ToolError(f"命令命中服务器禁用规则，已拒绝执行：{reason[:120]}", code="server_admin_denied")
        result = executor.submit(
            context.session_id,
            command,
            cwd,
            authorize,
            allow=auto_run_commands(),
            auto_run_all=skip_confirmation(),
        )
        if result["status"] == "running":
            job = executor.jobs[result["job_id"]]
            # 简单查询通常立即完成，直接把结果交给 Planner，长任务仍使用 job_id。
            if job.task is not None:
                await asyncio.wait({job.task}, timeout=1.0)
            result = executor.query(context.session_id, job.id)
        if result["status"] == "pending":
            # 确认必须让真人先看到精确执行内容，不能只依赖模型转述。
            if runtime.matcher is None:
                executor.cancel(context.session_id)
                raise ToolError("当前缺少私聊确认通道")
            try:
                await runtime.matcher.send(_describe(result))
                executor.presented(context.session_id, result["job_id"], _message_key(runtime))
            except BaseException:
                executor.cancel(context.session_id)
                raise
        return result
    except (ValueError, FileNotFoundError, NotADirectoryError) as exc:
        # 原样告诉模型哪种情况失败（尤其是「已有待确认任务」），避免它误报提交成功或反复重提。
        raise ToolError(str(exc) or "参数无效或目录不存在", code="server_admin_invalid") from None
    except PermissionError:
        raise ToolError("执行权限不足或确认已失效", code="server_admin_denied") from None


@tool(
    name="server_confirm",
    permission="server_admin:mode",
    title="确认服务器命令",
    description=(
        "主人在私聊里明确同意执行当前待确认命令后调用，无参数。"
        "只有明确的同意才算（例如“行”“跑吧”“确认”）；提问、犹豫、沉默、含糊都不算，不能替主人决定。"
        "调用后命令立即执行并返回结果，直接向主人汇报。"
    ),
    parameters={"type": "object", "properties": {}},
)
async def server_confirm(context: ToolContext, runtime: ToolRuntime) -> dict:
    """确认真人已看到命令，并拒绝同一事件的自我确认。"""
    if not context.user_id or not execution_allowed(
        context.session_id, runtime.bot, runtime.event, context.user_id, context.group_id
    ):
        raise ToolError("仅允许主人在已开启的服务器私聊会话使用", code="server_admin_denied")
    reply = event_plain_text(runtime.event).strip()
    try:
        result = executor.confirm(context.session_id, _message_key(runtime), reply)
    except ValueError:
        raise ToolError("没有待确认的命令，或确认已过期", code="server_admin_invalid") from None
    except PermissionError:
        raise ToolError("执行权限不足或确认已失效", code="server_admin_denied") from None
    job = executor.jobs[result["job_id"]]
    if job.task is not None:
        try:
            await asyncio.shield(job.task)
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling() or not job.task.cancelled():
                raise
    if job.status == "cancelled" or not execution_allowed(
        context.session_id, runtime.bot, runtime.event, context.user_id, context.group_id
    ):
        raise ToolError("执行已取消或权限已失效", code="server_admin_denied")
    return job.result()


async def _shutdown() -> None:
    """先停止进程，再注销 hook，关闭失败不影响其余清理。"""
    try:
        await executor.stop()
    finally:
        for unregister in reversed(_unregister):
            unregister()


get_driver().on_shutdown(_shutdown)
