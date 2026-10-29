"""subagent 工具：把自包含任务交给独立上下文的子代理。

子代理拿到任务说明和被授权的工具，自己多轮调用工具，只把结论交还给调用方。
开启 ``capabilities.advanced_sub_agent`` 后改为后台运行：立即返回任务 id，
结论在结束时写入会话，下一轮对话可见；``subagent_status`` 与 ``subagent_stop`` 用于查看和终止。
"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from dataclasses import field, dataclass

from nonebot import logger

from .types import ToolError, ToolContext
from .result import tool_result_content
from .runtime import ToolRuntime
from ..session import default_session_store
from .executor import execute_tool
from .registry import tool, default_registry
from ..capabilities import capabilities_config
from ..providers.routing import fallback_provider
from ..providers.entities import LLMResponse

SUBAGENT_TOOL_NAME = "subagent"
SUBAGENT_STATUS_TOOL_NAME = "subagent_status"
SUBAGENT_STOP_TOOL_NAME = "subagent_stop"
SUBAGENT_TOOL_NAMES = frozenset({SUBAGENT_TOOL_NAME, SUBAGENT_STATUS_TOOL_NAME, SUBAGENT_STOP_TOOL_NAME})
SUBAGENT_FORBIDDEN_TOOL_NAMES = SUBAGENT_TOOL_NAMES
SUBAGENT_MAX_ROUNDS = 5
SUBAGENT_BACKGROUND_MAX_ROUNDS = 20
SUBAGENT_MAX_RUNNING_TASKS = 3
SUBAGENT_FINISHED_TASK_KEEP_COUNT = 20
SUBAGENT_TOOL_TIMEOUT = 15.0

SUBAGENT_SYSTEM_PROMPT = (
    "你是一个被派发任务的子代理。派发者看不到你的工作过程，只会收到你最后一条不带工具调用的文字。\n"
    "工作方式：\n"
    "1. 任务说明就是你知道的全部背景，不要假设任何没有写出来的信息。\n"
    "2. 用提供的工具查清楚需要的事实；互不依赖的调用可以一次并行发出。\n"
    "3. 工具返回无结果或失败时换一种查法，确实查不到就如实说明，不要编造。\n"
    "4. 查清后直接输出结论：先给答案，再列出支撑它的关键事实；派发者后续可能用到的 id、链接、路径、版本号原样保留。\n"
    "5. 你不能向任何人发言，也不能把任务再派发出去。结论控制在 800 字以内。"
)
SUBAGENT_FINAL_ROUND_HINT = (
    "可用的轮数已经用完，这一轮不能再调用工具。请根据已经拿到的结果直接给出结论，没有查清的部分如实说明。"
)


@dataclass
class SubagentTask:
    """一个子代理任务的状态。"""

    task_id: str
    session_id: str
    description: str
    granted_tools: list[str]
    status: str = "running"
    conclusion: str = ""
    error: str = ""
    started_at: float = field(default_factory=time.time)
    finished_at: float = 0.0
    handle: asyncio.Task[Any] | None = None
    discard_result: bool = False


class _TaskManager:
    """登记并管理后台子代理任务。"""

    def __init__(self) -> None:
        self._tasks: dict[str, SubagentTask] = {}
        self._counter = 0
        self._stopping = False

    def running_count(self, session_id: str) -> int:
        return sum(1 for task in self._tasks.values() if task.session_id == session_id and task.status == "running")

    def get(self, task_id: str) -> SubagentTask | None:
        return self._tasks.get(task_id)

    def list_tasks(self, session_id: str) -> list[SubagentTask]:
        return [task for task in self._tasks.values() if task.session_id == session_id]

    def start(
        self,
        context: ToolContext,
        runtime: ToolRuntime,
        *,
        description: str,
        granted_tools: list[str],
        max_rounds: int,
    ) -> SubagentTask:
        if self._stopping:
            raise ToolError("正在关闭，不能启动新的子代理任务")
        self._counter += 1
        task = SubagentTask(
            task_id=f"subagent-{self._counter}",
            session_id=context.session_id,
            description=description,
            granted_tools=list(granted_tools),
        )
        self._tasks[task.task_id] = task
        task.handle = asyncio.create_task(self._run(task, context, runtime, max_rounds))
        self._drop_old_finished()
        return task

    async def _run(self, task: SubagentTask, context: ToolContext, runtime: ToolRuntime, max_rounds: int) -> None:
        try:
            task.conclusion = await _run_sub_agent(context, runtime, task.description, task.granted_tools, max_rounds)
        except asyncio.CancelledError:
            task.status = "stopped"
            task.finished_at = time.time()
            raise
        except Exception as exc:
            if task.discard_result:
                return
            logger.opt(exception=True).warning(f"[Chat] 子代理任务失败: {task.task_id}")
            task.status = "failed"
            task.error = f"{type(exc).__name__}: {exc}"
        else:
            if task.discard_result:
                return
            task.status = "completed" if task.conclusion else "failed"
            if not task.conclusion:
                task.error = "子代理没有给出结论"
        task.finished_at = time.time()
        _deliver(task)

    def stop(self, session_id: str, task_id: str) -> bool:
        task = self._tasks.get(task_id)
        if task is None or task.session_id != session_id or task.status != "running" or task.handle is None:
            return False
        task.handle.cancel()
        return True

    def cancel_session(self, session_id: str) -> None:
        """取消会话任务，并使旧任务的后续回写失效。"""
        for task in self.list_tasks(session_id):
            task.discard_result = True
            if task.status == "running":
                task.status = "stopped"
                task.finished_at = time.time()
            if task.handle is not None and not task.handle.done():
                task.handle.cancel()

    def _drop_old_finished(self) -> None:
        finished = [task for task in self._tasks.values() if task.status != "running"]
        for task in finished[: max(0, len(finished) - SUBAGENT_FINISHED_TASK_KEEP_COUNT)]:
            self._tasks.pop(task.task_id, None)

    async def cancel_all(self) -> None:
        """取消全部后台任务并等待退出，禁止关闭期间回写或启动新任务。"""
        self._stopping = True
        for session_id in {task.session_id for task in self._tasks.values()}:
            self.cancel_session(session_id)
        handles = [task.handle for task in self._tasks.values() if task.handle is not None and not task.handle.done()]
        results = await asyncio.gather(*handles, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.opt(exception=result).warning("[Chat] 后台子代理关闭失败")


subagent_tasks = _TaskManager()


def _deliver(task: SubagentTask) -> None:
    """把后台子代理的结论写回会话，作为下一轮可见的用户消息。"""
    if task.discard_result:
        return
    body = task.conclusion or f"子代理没有给出结论：{task.error}"
    default_session_store.append(
        task.session_id,
        {
            "role": "user",
            "content": f'<subagent_result task_id="{task.task_id}" status="{task.status}">\n{body}\n</subagent_result>',
        },
    )


def _granted_tools(context: ToolContext, requested: Any) -> list[str]:
    """只授权调用方本来就能用、且没有禁用的工具。"""
    names = [str(name).strip() for name in requested] if isinstance(requested, list) else []
    allowed = set(context.metadata.get("allowed_tools") or ())
    return [
        name
        for name in dict.fromkeys(name for name in names if name)
        if name in allowed and name not in SUBAGENT_FORBIDDEN_TOOL_NAMES and default_registry.get(name) is not None
    ]


async def _run_sub_agent(
    context: ToolContext,
    runtime: ToolRuntime,
    task: str,
    granted_tools: list[str],
    max_rounds: int,
) -> str:
    """在独立上下文里多轮运行子代理，直到它给出结论或用完轮数。"""
    history: list[dict[str, Any]] = [
        {"role": "system", "content": SUBAGENT_SYSTEM_PROMPT},
        {"role": "user", "content": task},
    ]
    granted = set(granted_tools)
    tool_definitions = [item for item in default_registry.to_openai_tools() if item["function"]["name"] in granted]
    async with fallback_provider("planner", context.session_id) as provider:
        for round_index in range(max_rounds + 1):
            final_round = round_index >= max_rounds
            if final_round:
                history.append({"role": "user", "content": SUBAGENT_FINAL_ROUND_HINT})
            response: LLMResponse = await provider.chat(
                messages=history, tools=None if final_round else tool_definitions or None
            )
            if final_round or not response.has_tool_calls:
                return str(response.content or "").strip()
            history.append(response.to_message_dict())
            for call in response.tool_calls:
                function = call.get("function", {})
                name = str(function.get("name") or "")
                arguments = function.get("arguments") or "{}"
                if name not in granted:
                    result: dict[str, Any] = {"ok": False, "error": f"工具 {name} 没有授权给你"}
                else:
                    try:
                        result = await asyncio.wait_for(
                            execute_tool(name, arguments, context, runtime, allowed_names=granted),
                            timeout=SUBAGENT_TOOL_TIMEOUT,
                        )
                    except asyncio.TimeoutError:
                        result = {"ok": False, "error": "工具执行超时"}
                history.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.get("id"),
                        "name": name,
                        "content": tool_result_content(result),
                    }
                )
    return ""


@tool(
    name=SUBAGENT_TOOL_NAME,
    description=(
        "把一个自包含的查询或整理任务交给子代理。子代理在独立上下文里工作，可以自己多次调用你授权的工具，"
        "最后只把结论返回给你，中间的原始结果不会进入你的上下文。"
        "开启高级子代理后改为后台运行：立即返回任务 id，结论在结束时写入会话，下一轮对话可见。"
        "适合需要多次查询、交叉比对，或会返回大量原始内容的任务；只需要一次简单查询时直接调用对应工具即可。"
        "子代理看不到当前聊天，也不能发言：发言、等待和群管理操作必须由你自己完成。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "完整、自包含的任务说明。子代理看不到聊天记录，"
                    "要写清楚要查什么、相关的名称或关键词、以及你希望得到什么形式的结论。"
                ),
            },
            "tools": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "授权子代理使用的工具名列表，只填查询、检索、读取类工具；不能包含子代理自身的管理工具。",
            },
        },
        "required": ["task", "tools"],
    },
)
async def subagent_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    task: str = "",
    tools: Any = None,
    **_kwargs: Any,
) -> str | dict[str, Any]:
    """运行子代理并返回结论，或在后台模式下登记任务。"""
    description = str(task or "").strip()
    if not description:
        raise ToolError("subagent 需要提供非空的 task")
    granted = _granted_tools(context, tools)
    if not granted:
        raise ToolError("没有可以授权给子代理的工具：tools 为空、不存在，或调用方没有权限")
    if capabilities_config().get("advanced_sub_agent"):
        if subagent_tasks.running_count(context.session_id) >= SUBAGENT_MAX_RUNNING_TASKS:
            raise ToolError(
                f"已有 {SUBAGENT_MAX_RUNNING_TASKS} 个子代理在后台运行，等它们结束或用 subagent_stop 终止后再派发"
            )
        registered = subagent_tasks.start(
            context,
            runtime,
            description=description,
            granted_tools=granted,
            max_rounds=SUBAGENT_BACKGROUND_MAX_ROUNDS,
        )
        return {
            "task_id": registered.task_id,
            "status": "running",
            "granted_tools": granted,
            "note": "子代理在后台运行；结论结束时写入会话，下一轮对话可见，也可用 subagent_status 查看。",
        }
    return await _run_sub_agent(context, runtime, description, granted, SUBAGENT_MAX_ROUNDS)


@tool(
    name=SUBAGENT_STATUS_TOOL_NAME,
    description=(
        "查看后台子代理任务的状态。不填 task_id 时列出当前会话的全部任务；填了则给出该任务的详情，"
        "已结束的任务会附上结论。"
    ),
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "要查看的任务 id，例如 subagent-1；不填则列出全部任务。"},
        },
    },
)
async def subagent_status_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    task_id: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """返回任务状态；任务详情只包含当前会话自己的任务。"""
    del runtime
    task_id = str(task_id or "").strip()
    if task_id:
        task = subagent_tasks.get(task_id)
        if task is None or task.session_id != context.session_id:
            raise ToolError("子代理任务不存在")
        return {
            "task_id": task.task_id,
            "status": task.status,
            "description": task.description,
            "granted_tools": task.granted_tools,
            "conclusion": task.conclusion,
            "error": task.error,
            "elapsed_seconds": round((task.finished_at or time.time()) - task.started_at, 1),
        }
    return {
        "tasks": [
            {
                "task_id": task.task_id,
                "status": task.status,
                "description": task.description,
                "granted_tools": task.granted_tools,
            }
            for task in subagent_tasks.list_tasks(context.session_id)
        ]
    }


@tool(
    name=SUBAGENT_STOP_TOOL_NAME,
    description="终止一个仍在运行的后台子代理任务。被终止的任务不会再写入结论。",
    parameters={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "minLength": 1, "description": "要终止的任务 id，例如 subagent-1。"},
        },
        "required": ["task_id"],
    },
)
async def subagent_stop_tool(
    context: ToolContext,
    runtime: ToolRuntime,
    task_id: str = "",
    **_kwargs: Any,
) -> dict[str, Any]:
    """终止后台任务。"""
    del runtime
    task_id = str(task_id or "").strip()
    if not task_id or not subagent_tasks.stop(context.session_id, task_id):
        raise ToolError("子代理任务不存在或已经结束")
    return {"task_id": task_id, "status": "stopped"}


__all__ = [
    "SUBAGENT_STATUS_TOOL_NAME",
    "SUBAGENT_STOP_TOOL_NAME",
    "SUBAGENT_TOOL_NAME",
    "SUBAGENT_TOOL_NAMES",
    "subagent_status_tool",
    "subagent_stop_tool",
    "subagent_tasks",
    "subagent_tool",
]
