"""模型请求快照，落盘结构对齐 MaiBot 的 ``logs/maisaka_prompt``。

每次模型调用保存一份 JSON：完整请求、完整输出、工具定义与元数据。
回复的推理续写与重生成会并入首次调用，用 ``generation_attempts`` 记录完整调用链。
文件按请求类型和会话分目录，每类请求每个会话最多保留 ``chat.model_trace.max_groups_per_chat`` 份。
"""

from __future__ import annotations

import json
import time
import queue
import threading
from uuid import uuid4
from typing import Any
from hashlib import sha256
from pathlib import Path
from datetime import datetime
from collections import OrderedDict

from nonebot import logger

from ..utils.tz import SHANGHAI_TZ
from ..utils.paths import data_dir
from .trace_context import current_turn

_QUEUE_MAXSIZE = 256
_DEFAULT_MAX_GROUPS = 256
_RECENT_LIMIT = 64
_CATEGORY_ALIASES = {
    "reply": "replyer",
    "fast_model": "utils",
    "utils": "utils",
    "embedding": "embedding",
}
_write_queue: queue.Queue[tuple[Path, str] | None] = queue.Queue(maxsize=_QUEUE_MAXSIZE)
_writer_lock = threading.Lock()
_writer: threading.Thread | None = None
_recent: OrderedDict[str, tuple[Path, dict[str, Any]]] = OrderedDict()
_recent_lock = threading.Lock()


def _base_dir() -> Path:
    return data_dir("ai_chat") / "model_traces"


def _config() -> dict[str, Any]:
    try:
        from ..config import get_manager

        value = get_manager().get_system()["chat"].get("model_trace")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _enabled() -> bool:
    return bool(_config().get("enabled", True))


def _max_groups() -> int:
    try:
        return max(1, int(_config().get("max_groups_per_chat") or _DEFAULT_MAX_GROUPS))
    except (TypeError, ValueError):
        return _DEFAULT_MAX_GROUPS


def _safe_name(value: str, default: str = "unknown") -> str:
    """把平台标识压成安全的目录名。"""
    result = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in str(value or "").strip()).strip("._")
    return result or default


def _category(task: str) -> str:
    name = str(task or "").strip().lower() or "chat"
    return _safe_name(_CATEGORY_ALIASES.get(name, name))


def _chat_dir_name(session_id: str) -> str:
    """保留完整会话身份，用摘要区分归一化后同名或大小写不同的会话。"""
    identity = str(session_id or "")
    if not identity:
        return "unknown_chat"
    digest = sha256(identity.encode("utf-8")).hexdigest()
    return f"{_safe_name(identity)[:80]}-{digest}"


def _start_writer() -> None:
    global _writer
    if _writer is not None and _writer.is_alive():
        return
    with _writer_lock:
        if _writer is not None and _writer.is_alive():
            return
        _writer = threading.Thread(target=_writer_loop, name="fxbot-model-trace", daemon=True)
        _writer.start()


def _writer_loop() -> None:
    """串行落盘，避免推理主循环为磁盘写入付出等待。"""
    while True:
        item = _write_queue.get()
        try:
            if item is None:
                return
            path, content = item
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".json.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(path)
            _prune(path.parent)
        except Exception:
            logger.opt(exception=True).warning("[ModelTrace] 写入模型快照失败")
        finally:
            _write_queue.task_done()


def _prune(chat_dir: Path) -> None:
    """每个会话只保留最近的若干份快照。"""
    limit = _max_groups()
    try:
        names = sorted(entry.name for entry in chat_dir.iterdir() if entry.is_file() and entry.suffix == ".json")
    except FileNotFoundError:
        return
    for name in names[: max(0, len(names) - limit)]:
        try:
            (chat_dir / name).unlink()
        except FileNotFoundError:
            pass


def _usage_of(response: Any) -> dict[str, int]:
    """读取 Provider 已归一化的用量，不再解析原生响应。"""
    return response.usage.to_dict() if response is not None else {}


def _request_items(messages: Any) -> list[dict[str, Any]]:
    """请求消息原样保留，只丢弃内部下划线字段。"""
    items: list[dict[str, Any]] = []
    for message in messages or []:
        if not isinstance(message, dict):
            items.append({"type": "unknown", "value": str(message)})
            continue
        items.append(_json_safe({key: value for key, value in message.items() if not str(key).startswith("_")}))
    return items


def _output_items(response: Any, error: str) -> list[dict[str, Any]]:
    """输出归一成 reasoning / message / function_call / error 四类条目。"""
    items: list[dict[str, Any]] = []
    if response is not None:
        reasoning = str(getattr(response, "reasoning_content", "") or "")
        if reasoning:
            items.append({"type": "reasoning", "text": reasoning})
        content = str(getattr(response, "content", "") or "")
        if content:
            items.append({"type": "message", "role": "assistant", "text": content})
        for call in getattr(response, "tool_calls", None) or []:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            items.append(
                {
                    "type": "function_call",
                    "call_id": str(call.get("id") or ""),
                    "name": str(function.get("name") or ""),
                    "arguments": str(function.get("arguments") or ""),
                }
            )
    if error:
        items.append({"type": "error", "error": error})
    return items


def _json_safe(value: Any) -> Any:
    """把消息里的非 JSON 值转成可落盘的字符串。"""
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def _attempt(
    *,
    provider: str,
    model: str,
    duration_ms: int,
    error: str,
    workflow_purpose: str,
    workflow_attempt: int,
    messages: Any = None,
    tools: Any = None,
    response: Any = None,
) -> dict[str, Any]:
    """生成一条 Provider 调用记录，与 MaiBot 的 generation_attempts 同名字段。"""
    return {
        "provider": provider,
        "model": model,
        "duration_ms": max(0, int(duration_ms)),
        "status": "failed" if error else "completed",
        "error": error,
        "workflow_purpose": workflow_purpose,
        "workflow_attempt": max(1, int(workflow_attempt)),
        "request_items": _request_items(messages),
        "tool_definitions": _json_safe(tools or []),
        "output_items": _output_items(response, error),
        "provider_output_items": _json_safe((getattr(response, "provider_data", None) or {}).get("output_items", [])),
        "usage": _usage_of(response),
    }


def _merged_payload(
    payload: dict[str, Any],
    *,
    attempt: dict[str, Any],
    previous_purpose: str,
    messages: Any,
    tools: Any,
    response: Any,
    error: str,
) -> dict[str, Any]:
    """把续写或重生成并入首次调用的快照，保留完整尝试链。"""
    attempts = payload.setdefault("generation_attempts", [])
    if previous_purpose:
        for item in attempts:
            if isinstance(item, dict) and not item.get("workflow_purpose"):
                item["workflow_purpose"] = previous_purpose
    attempts.append(attempt)

    payload["request_items"] = _request_items(messages)
    if tools is not None:
        payload["tool_definitions"] = _json_safe(tools)
    payload["output_items"] = _output_items(response, error)

    metadata = payload.setdefault("metadata", {})
    if attempt["provider"]:
        metadata["provider"] = attempt["provider"]
    if attempt["model"]:
        metadata["model_name"] = attempt["model"]
    metadata["duration_ms"] = sum(int(item.get("duration_ms") or 0) for item in attempts if isinstance(item, dict))
    for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
        values = [item["usage"][name] for item in attempts if name in item.get("usage", {})]
        if values:
            metadata[name] = sum(values)
    if error:
        metadata["error"] = error
    else:
        metadata.pop("error", None)
    if not isinstance(payload.get("presentation"), dict):
        payload["presentation"] = {}
    payload["presentation"]["output_title"] = (
        f"{metadata.get('provider') or 'unknown'} / {metadata.get('model_name') or 'unknown'}"
    )
    return payload


def build_snapshot(
    *,
    task: str,
    session_id: str,
    turn_id: str,
    provider: str,
    model: str,
    messages: Any,
    tools: Any,
    response: Any,
    duration_ms: int,
    error: str = "",
    workflow_purpose: str = "",
    workflow_attempt: int = 1,
) -> dict[str, Any]:
    """按 MaiBot 的结构生成快照内容。"""
    metadata: dict[str, Any] = {
        "model_name": model,
        "duration_ms": max(0, int(duration_ms)),
        "provider": provider,
        "session_id": session_id,
        "turn_id": turn_id,
        "task": task,
    }
    metadata.update(_usage_of(response))
    if error:
        metadata["error"] = error
    return {
        "schema_version": 1,
        "request": {"kind": task, "selection_reason": f"会话 {session_id} · 轮次 {turn_id}"},
        "metadata": metadata,
        "presentation": {"output_title": f"{provider or 'unknown'} / {model or 'unknown'}"},
        "request_items": _request_items(messages),
        "output_items": _output_items(response, error),
        "tool_definitions": _json_safe(tools or []),
        "generation_attempts": [
            _attempt(
                provider=provider,
                model=model,
                duration_ms=duration_ms,
                error=error,
                workflow_purpose=workflow_purpose,
                workflow_attempt=workflow_attempt,
                messages=messages,
                tools=tools,
                response=response,
            )
        ],
    }


def _remember(key: str, path: Path, payload: dict[str, Any]) -> None:
    """缓存最近快照，供同一轮里的续写或重生成合并。"""
    with _recent_lock:
        _recent[key] = (path, payload)
        _recent.move_to_end(key)
        while len(_recent) > _RECENT_LIMIT:
            _recent.popitem(last=False)


def _enqueue(path: Path, payload: dict[str, Any]) -> None:
    _start_writer()
    content = json.dumps(payload, ensure_ascii=False, default=str)
    _write_queue.put_nowait((path, content))
    # 共享规划保留组身份，但每个实际参与会话都可读取自己的副本。
    for session_id in payload.get("metadata", {}).get("related_sessions", []):
        target = path.parent.parent / _chat_dir_name(session_id) / path.name
        if target != path:
            _write_queue.put_nowait((target, content))


def record_model_call(
    *,
    task: str,
    provider: str,
    model: str,
    messages: Any,
    tools: Any,
    response: Any,
    duration_ms: int,
    error: str = "",
    workflow_purpose: str = "",
    workflow_attempt: int = 1,
    merge_into: str = "",
    previous_purpose: str = "",
) -> str:
    """登记一次模型调用；传 ``merge_into`` 时并入已有快照并返回同一个键。"""
    if not _enabled():
        return ""
    turn = current_turn()
    session_id = turn.session_id if turn else ""
    turn_id = turn.turn_id if turn else ""
    try:
        if merge_into:
            with _recent_lock:
                cached = _recent.get(merge_into)
                if cached is not None:
                    path, payload = cached
                    inherited_attempt = workflow_attempt
                    if inherited_attempt <= 0:
                        inherited_attempt = max(
                            (
                                int(item.get("workflow_attempt") or 1)
                                for item in payload.get("generation_attempts", [])
                                if isinstance(item, dict)
                            ),
                            default=1,
                        )
                    _merged_payload(
                        payload,
                        attempt=_attempt(
                            provider=provider,
                            model=model,
                            duration_ms=duration_ms,
                            error=error,
                            workflow_purpose=workflow_purpose,
                            workflow_attempt=inherited_attempt,
                            messages=messages,
                            tools=tools,
                            response=response,
                        ),
                        previous_purpose=previous_purpose,
                        messages=messages,
                        tools=tools,
                        response=response,
                        error=error,
                    )
                    _recent.move_to_end(merge_into)
            if cached is not None:
                _enqueue(path, payload)
                return merge_into

        payload = build_snapshot(
            task=task,
            session_id=session_id,
            turn_id=turn_id,
            provider=provider,
            model=model,
            messages=messages,
            tools=tools,
            response=response,
            duration_ms=duration_ms,
            error=error,
            workflow_purpose=workflow_purpose,
            workflow_attempt=workflow_attempt,
        )
        chat_dir = _base_dir() / _category(task) / _chat_dir_name(session_id)
        if turn and turn.related_sessions:
            payload["metadata"]["related_sessions"] = list(dict.fromkeys(turn.related_sessions))
        stem = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000_000:09d}"
        path = chat_dir / f"{stem}-{uuid4().hex[:8]}.json"
        key = str(path)
        _remember(key, path, payload)
        _enqueue(path, payload)
        return key
    except queue.Full:
        logger.debug("[ModelTrace] 写入队列已满，丢弃本次模型快照")
    except Exception:
        logger.opt(exception=True).debug("[ModelTrace] 生成模型快照失败")
    return ""


def trace_root() -> Path:
    """返回模型快照根目录，供控制台读取。"""
    return _base_dir()


def _preview(value: Any, limit: int = 160) -> str:
    """把输出条目压成单行摘要，供列表快速浏览。"""
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else f"{text[:limit]}…"


def _output_previews(output_items: Any) -> tuple[str, str]:
    """返回首段思考与首段正文摘要。"""
    reasoning = ""
    reply = ""
    for item in output_items or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "reasoning" and not reasoning:
            reasoning = _preview(item.get("text"))
        elif item.get("type") == "message" and not reply:
            reply = _preview(item.get("text"))
    return reasoning, reply


def _summary(path: Path, category: str) -> dict[str, Any]:
    """读取快照摘要；坏文件只暴露路径与时间。"""
    stat = path.stat()
    summary: dict[str, Any] = {
        "path": path.relative_to(_base_dir()).as_posix(),
        "category": category,
        "created_at": datetime.fromtimestamp(stat.st_mtime, SHANGHAI_TZ).isoformat(),
        "size": stat.st_size,
        "model_name": "",
        "provider": "",
        "turn_id": "",
        "task": "",
        "duration_ms": 0,
        "status": "",
        "output_title": "",
        "reasoning_preview": "",
        "reply_preview": "",
    }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return summary
    if not isinstance(payload, dict):
        return summary
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    attempts = payload.get("generation_attempts") if isinstance(payload.get("generation_attempts"), list) else []
    last = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
    presentation = payload.get("presentation") if isinstance(payload.get("presentation"), dict) else {}
    reasoning, reply = _output_previews(payload.get("output_items"))
    if not reasoning:
        for attempt in attempts:
            reasoning, _ = _output_previews(attempt.get("output_items") if isinstance(attempt, dict) else [])
            if reasoning:
                break
    summary.update(
        {
            "model_name": str(metadata.get("model_name") or ""),
            "provider": str(metadata.get("provider") or ""),
            "turn_id": str(metadata.get("turn_id") or ""),
            "task": str(metadata.get("task") or ""),
            "duration_ms": int(metadata.get("duration_ms") or 0),
            "status": str(last.get("status") or ""),
            "output_title": str(presentation.get("output_title") or ""),
            "reasoning_preview": reasoning,
            "reply_preview": reply,
        }
    )
    return summary


def list_snapshots(session_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """列出某个会话最近的模型快照摘要。"""
    root = _base_dir()
    chat_dir = _chat_dir_name(session_id)
    items: list[dict[str, Any]] = []
    for category_dir in root.iterdir() if root.is_dir() else []:
        target = category_dir / chat_dir
        if not category_dir.is_dir() or not target.is_dir():
            continue
        for path in target.glob("*.json"):
            try:
                items.append(_summary(path, category_dir.name))
            except OSError:
                continue
    items.sort(key=lambda item: (item.get("created_at") or "", item.get("path") or ""), reverse=True)
    return items[: max(1, min(500, int(limit)))]


def read_snapshot(relative_path: str) -> dict[str, Any] | None:
    """按相对路径读取快照全文；越界或非 JSON 一律拒绝。"""
    root = _base_dir().resolve()
    candidate = (root / str(relative_path or "")).resolve()
    if candidate != root and root not in candidate.parents:
        return None
    if candidate.suffix != ".json" or not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None
