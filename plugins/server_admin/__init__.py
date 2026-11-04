"""主人私聊的独立服务器会话及本机 shell 运维工具。"""

from __future__ import annotations

import re
from typing import Any

from nonebot import logger, get_driver
from nonebot.rule import Rule
from nonebot.plugin import PluginMetadata
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from . import store
from .access import actor_base, owner_allowed, plugin_enabled, execution_allowed
from ...plugin import Plugin
from .executor import audit, executor
from ...adapter import event_user_id, event_plain_text
from ...chat.hooks import register_chat_hook
from ...chat.tools import ToolError, ToolContext, ToolRuntime, tool
from ...permission import PermLevel, PermScene, is_superuser
from ...chat.session_hooks import SessionPolicy, register_session_policy, register_session_resolver

__plugin_meta__ = PluginMetadata(
    name="服务器运维",
    description="主人私聊的独立服务器运维会话，执行 Bot 进程权限内的 shell 命令。",
    usage="#服务器模式 / #聊天模式 / #确认 [编号] / #取消",
    type="application",
)

P = Plugin("server_admin", display_name="服务器运维", enabled=True, level=PermLevel.SUPERUSER, scene=PermScene.PRIVATE)
_COMMAND = re.compile(r"^[#＃](服务器模式|聊天模式|确认|取消)(?:\s+([0-9a-f]{12}))?\s*$")


def _resolve(bot, event, base: str) -> str | None:
    """模式开启后固定派生会话，即使撤权或插件停用也不退回普通聊天泄露历史。"""
    if ":private:" not in base:
        return None
    return store.server_session(base) if store.enabled(base) else None


def _policy(session_id: str) -> SessionPolicy | None:
    """服务器域永久禁止 Focus 与表达、黑话跨会话共享。"""
    return SessionPolicy(allow_focus=False, allow_sharing=False) if store.is_server_session(session_id) else None


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
    """shell 工具只在服务器会话可发现；服务器会话不继承其他有副作用的业务工具。"""
    name = details["tool_name"]
    if name == "server_exec":
        return bool(_guard(context, {})) if store.is_server_session(context.session_id) else False
    if store.is_server_session(context.session_id):
        return name in {"tool_search", "read_tool_result", "query_memory", "query_person_profile"}
    return None


def _prompt(context) -> str | None:
    """提示工作环境和工具协议，不覆盖用户通过 #人格 设置的绑定。"""
    if store.is_server_session(context.session_id):
        return (
            "这是独立的服务器运维私聊。需要执行时通过 tool_search 找 server_exec；"
            "先提交 command，返回 job_id 后用同一工具查询。pending 必须等待用户 #确认；"
            "不得自行确认或重复提交任务。命令使用 Bot 当前运行环境与权限，不一定是宿主机。"
        )
    return None


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
    """仅拦截主人私聊命令，未获权用户不会切换模式或确认任务。"""
    matched = _COMMAND.fullmatch(event_plain_text(event).strip())
    if matched is None or not owner_allowed(bot, event):
        return False
    if matched[1] in {"确认", "取消"}:
        return store.enabled(actor_base(bot, event))
    return True


mode_cmd = P.on_regex(
    _COMMAND.pattern,
    name="mode",
    display_name="服务器模式与命令确认",
    level=PermLevel.SUPERUSER,
    scene=PermScene.PRIVATE,
    rule=Rule(_rule),
    priority=3,
    block=True,
)


def _describe(result: dict[str, Any]) -> str:
    """确认前展示完整命令与工作目录，不截断待审批的实际内容。"""
    if result["status"] == "pending":
        return (
            f"命令待确认（120 秒）：{result['job_id']}\n目录：{result['cwd']}\n命令：\n{result['command']}\n"
            "命令具有当前 Bot 进程的系统权限，可能修改或删除数据。\n"
            f"发送 #确认 {result['job_id']} 执行，#取消 放弃。"
        )
    return f"任务 {result['job_id']}：{result['status']}。请让 AI 使用 job_id 查询结果。"


async def handle_mode(bot: Bot, event: Event) -> str:
    """执行私聊模式命令，返回预定义提示，运行时异常由入口记录后静默结束。"""
    if not owner_allowed(bot, event):
        return ""
    matched = _COMMAND.fullmatch(event_plain_text(event).strip())
    if matched is None:
        return ""
    action, identifier = matched[1], matched[2] or ""
    base = actor_base(bot, event)
    session_id = store.server_session(base)
    if action == "服务器模式":
        store.set_mode(base, True)
        audit("mode_enabled", session_id, user_id=str(event_user_id(event)))
        return "服务器模式已开启。后续私聊使用独立人格、历史和会话记忆。命令以 Bot 进程权限执行，并非安全沙箱。"
    if action == "聊天模式":
        store.set_mode(base, False)
        executor.cancel(session_id)
        audit("mode_disabled", session_id)
        return "已退出服务器模式，待确认及运行任务已取消。后续消息回到普通私聊；服务器历史保留。"
    if not execution_allowed(session_id, bot, event):
        return ""
    if action == "取消":
        return "已取消当前服务器会话的命令。" if executor.cancel(session_id) else "没有待确认或运行的命令。"
    try:
        return _describe(executor.confirm(session_id, identifier))
    except ValueError:
        return "没有匹配的待确认命令，或确认已过期。"


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
        "主人服务器私聊中执行任意本机 shell 命令。提交 command 得到 job_id；用 job_id 查询，"
        "或 cancel=true 取消。危险命令需用户私聊 #确认，不能由 AI 确认。"
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
        result = executor.submit(context.session_id, command, cwd, authorize)
        if result["status"] == "pending":
            # 确认必须让真人先看到精确执行内容，不能只依赖模型转述。
            if runtime.matcher is None:
                executor.cancel(context.session_id)
                raise ToolError("当前缺少私聊确认通道")
            try:
                await runtime.matcher.send(_describe(result))
                executor.presented(context.session_id, result["job_id"])
            except BaseException:
                executor.cancel(context.session_id)
                raise
        return result
    except (ValueError, FileNotFoundError, NotADirectoryError):
        raise ToolError("参数无效、目录不存在或已有待处理任务；请先查询当前任务", code="server_admin_invalid") from None
    except PermissionError:
        raise ToolError("执行权限不足或确认已失效", code="server_admin_denied") from None


async def _shutdown() -> None:
    """先停止进程，再注销 hook，关闭失败不影响其余清理。"""
    try:
        await executor.stop()
    finally:
        for unregister in reversed(_unregister):
            unregister()


get_driver().on_shutdown(_shutdown)
