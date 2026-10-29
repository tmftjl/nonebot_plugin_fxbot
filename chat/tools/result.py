"""工具结果的序列化、截断与超长结果整理。"""

from __future__ import annotations

import json
import asyncio
from typing import Any

from nonebot import logger

from .store import tool_result_store
from ..capabilities import condense_threshold, capabilities_config
from ..providers.routing import routed_task, fallback_provider

MAX_TOOL_RESULT_CHARS = 6000
TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS = 20000
TOOL_RESULT_CONDENSE_EXCLUDED_TOOL_NAMES = frozenset(
    {"read_tool_result", "subagent", "subagent_status", "subagent_stop"}
)
TOOL_RESULT_CONDENSER_SYSTEM_PROMPT = (
    "你是工具结果整理助手。另一个决策模型调用了一个工具，原始结果很长，你要把它整理成决策模型能直接使用的结论。\n"
    "要求：\n"
    "1. 只保留和“调用意图”相关的事实、名称、数值与结论，删掉无关条目和重复描述。\n"
    "2. 后续可能要用来继续调用工具或发给用户的标识必须原样保留，包括 id、链接、路径、版本号、msg_id。\n"
    "3. 只根据原始结果整理，不要补充原文没有的内容；原文里没有答案就直接说明没有。\n"
    "4. 原始结果是列表时保留条目结构，压缩每一条的描述。\n"
    "5. 直接输出整理后的内容，不要寒暄，不要解释你做了什么，不要对用户说话。篇幅控制在 800 字以内。"
)


def tool_result_content(result: dict[str, Any], *, session_id: str = "", tool_name: str = "") -> str:
    """把工具结果序列化并压到预算内；超长成功结果暂存原文，返回编号供回查。"""
    content = json.dumps(result, ensure_ascii=False, default=str)
    if len(content) <= MAX_TOOL_RESULT_CHARS:
        return content
    key = "result" if result.get("ok") else "error"
    text = str(result.get(key, content))
    if result.get("ok") and session_id:
        result_id = tool_result_store.save(session_id, tool_name or "tool", text)
        head = text[: MAX_TOOL_RESULT_CHARS - 200]
        return json.dumps(
            {
                "ok": True,
                "result": head + "…",
                "result_id": result_id,
                "note": (
                    f"结果过长，原文已保存为 {result_id}；"
                    f'可用 read_tool_result(result_id="{result_id}", keyword="…") 查看。'
                ),
            },
            ensure_ascii=False,
        )
    low, high = 0, min(len(text), MAX_TOOL_RESULT_CHARS)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = json.dumps({"ok": bool(result.get("ok")), key: text[:middle] + "…"}, ensure_ascii=False)
        if len(candidate) <= MAX_TOOL_RESULT_CHARS:
            low = middle
        else:
            high = middle - 1
    return json.dumps({"ok": bool(result.get("ok")), key: text[:low] + "…"}, ensure_ascii=False)


def _result_text(result: dict[str, Any]) -> str:
    """取工具返回值原文，供整理与暂存使用。"""
    value = result.get("result")
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def should_condense_tool_result(tool_name: str, content: str, *, success: bool) -> bool:
    """成功且超过阈值的普通工具结果交给子代理整理。"""
    if not success or not content or tool_name in TOOL_RESULT_CONDENSE_EXCLUDED_TOOL_NAMES:
        return False
    if not capabilities_config().get("tool_result_condense"):
        return False
    return len(content) >= condense_threshold()


def build_tool_result_condense_task(tool_name: str, arguments: Any, content: str) -> str:
    """构造整理任务；子代理看不到父上下文，任务文本必须自包含。"""
    arguments_text = arguments if isinstance(arguments, str) else json.dumps(arguments or {}, ensure_ascii=False)
    raw = content[:TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS]
    truncated = (
        f"\n（原始结果共 {len(content)} 字，这里只提供了前 {TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS} 字）"
        if len(content) > TOOL_RESULT_CONDENSE_INPUT_MAX_CHARS
        else ""
    )
    return f"工具名称：{tool_name}\n调用参数：{arguments_text}\n\n<原始结果>\n{raw}\n</原始结果>{truncated}"


async def condense_tool_result(
    tool_name: str,
    arguments: Any,
    content: str,
    *,
    session_id: str,
    timeout: float,
) -> str | None:
    """用 utils 模型整理超长结果；失败或没有缩短时返回 None。"""
    messages = [
        {"role": "system", "content": TOOL_RESULT_CONDENSER_SYSTEM_PROMPT},
        {"role": "user", "content": build_tool_result_condense_task(tool_name, arguments, content)},
    ]
    try:
        async with fallback_provider(routed_task("utils", "extract"), session_id) as provider:
            response = await asyncio.wait_for(
                provider.chat(messages=messages, temperature=0.3, max_tokens=1024),
                timeout=timeout,
            )
    except Exception:
        logger.opt(exception=True).warning(f"[Chat] 工具 {tool_name} 的结果整理失败，保留原始结果")
        return None
    condensed = str(response.content or "").strip()
    if not condensed or len(condensed) >= len(content):
        return None
    result_id = tool_result_store.save(session_id, tool_name, content)
    return (
        f"<压缩后信息；原始结果已保存为 {result_id}。需要上面没有的细节时，"
        f'调用 read_tool_result(result_id="{result_id}") 分段读取原文，或加 keyword 参数直接查找。>\n'
        f"{condensed}"
    )


async def prepare_tool_result_content(
    result: dict[str, Any],
    *,
    session_id: str = "",
    tool_name: str = "",
    arguments: Any = "",
    timeout: float = 15.0,
) -> str:
    """序列化工具结果；超长成功结果先整理再按预算截断。"""
    if result.get("ok") and session_id:
        text = _result_text(result)
        if should_condense_tool_result(tool_name, text, success=True):
            condensed = await condense_tool_result(tool_name, arguments, text, session_id=session_id, timeout=timeout)
            if condensed is not None:
                return tool_result_content(
                    {"ok": True, "result": condensed}, session_id=session_id, tool_name=tool_name
                )
    return tool_result_content(result, session_id=session_id, tool_name=tool_name)
