"""模型请求快照的结构、落盘、清理与读取边界。"""

from __future__ import annotations

import json
import shutil
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import model_trace
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.trace_context import bind_turn, unbind_turn
from src.plugins.nonebot_plugin_fxbot.chat.providers.usage import usage_repository
from src.plugins.nonebot_plugin_fxbot.chat.providers.routing import TaskProvider


def _cleanup(session_id: str) -> None:
    """只清理本次测试会话的快照目录。"""
    root = model_trace.trace_root()
    for category_dir in root.iterdir() if root.is_dir() else []:
        if category_dir.is_dir():
            shutil.rmtree(category_dir / model_trace._chat_dir_name(session_id), ignore_errors=True)


def _snapshot_kwargs(kwargs: dict) -> dict:
    """从调用记录里取 build_snapshot 认识的字段。"""
    names = {
        "task",
        "provider",
        "model",
        "messages",
        "tools",
        "response",
        "duration_ms",
        "error",
        "workflow_purpose",
        "workflow_attempt",
    }
    return {key: value for key, value in kwargs.items() if key in names}


class SnapshotStructureTests(unittest.TestCase):
    """快照字段与输出归一。"""

    def test_build_snapshot_normalizes_output_items(self) -> None:
        response = LLMResponse(
            content="正文",
            reasoning_content="先想一想",
            tool_calls=[{"id": "call-1", "function": {"name": "search", "arguments": '{"q": "x"}'}}],
            raw_response=SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=4, total_tokens=14)),
        )
        payload = model_trace.build_snapshot(
            task="reply",
            session_id="qq:bot:group:1",
            turn_id="t1",
            provider="p",
            model="m",
            messages=[{"role": "user", "content": "hi", "_internal": "drop"}],
            tools=[{"type": "function", "function": {"name": "search"}}],
            response=response,
            duration_ms=25,
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["request"]["kind"], "reply")
        self.assertEqual(payload["metadata"]["total_tokens"], 14)
        self.assertEqual(payload["metadata"]["turn_id"], "t1")
        self.assertEqual(payload["request_items"], [{"role": "user", "content": "hi"}])
        self.assertEqual(
            [item["type"] for item in payload["output_items"]],
            ["reasoning", "message", "function_call"],
        )
        self.assertEqual(payload["generation_attempts"][0]["status"], "completed")

    def test_failed_snapshot_marks_attempt(self) -> None:
        payload = model_trace.build_snapshot(
            task="planner",
            session_id="qq:bot:group:1",
            turn_id="t2",
            provider="p",
            model="m",
            messages=[],
            tools=None,
            response=None,
            duration_ms=5,
            error="TimeoutError: boom",
        )
        self.assertEqual(payload["output_items"], [{"type": "error", "error": "TimeoutError: boom"}])
        self.assertEqual(payload["generation_attempts"][0]["status"], "failed")


class SnapshotStorageTests(unittest.TestCase):
    """真实落盘、每会话保留数、列表与读取。"""

    def setUp(self) -> None:
        self.session = "qq:bot:group:987654"
        self.addCleanup(_cleanup, self.session)
        self.enterContext(
            patch.object(model_trace, "_config", return_value={"enabled": True, "max_groups_per_chat": 2})
        )

    def _record(self, index: int) -> None:
        model_trace.record_model_call(
            task="reply",
            provider="p",
            model="m",
            messages=[{"role": "user", "content": f"hi {index}"}],
            tools=[],
            response=LLMResponse(content=f"ok {index}", reasoning_content="think"),
            duration_ms=index,
        )

    def test_record_keeps_latest_snapshots_and_reads_back(self) -> None:
        token = bind_turn(self.session, "turn-1")
        try:
            for index in range(3):
                self._record(index)
            model_trace._write_queue.join()
        finally:
            unbind_turn(token)

        items = model_trace.list_snapshots(self.session, 10)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["category"], "replyer")
        self.assertEqual(items[0]["turn_id"], "turn-1")
        self.assertEqual(items[0]["status"], "completed")
        self.assertEqual(items[0]["reasoning_preview"], "think")
        self.assertEqual(items[0]["reply_preview"], "ok 2")

        snapshot = model_trace.read_snapshot(items[0]["path"])
        assert snapshot is not None
        self.assertEqual(snapshot["request_items"][0]["content"], "hi 2")
        self.assertEqual(snapshot["output_items"][0]["type"], "reasoning")

    def test_read_snapshot_rejects_outside_or_non_json(self) -> None:
        self.assertIsNone(model_trace.read_snapshot("../escape.json"))
        self.assertIsNone(model_trace.read_snapshot("replyer/qq_group_987654/missing.json"))
        self.assertIsNone(model_trace.read_snapshot("replyer/qq_group_987654/note.txt"))

    def test_disabled_records_nothing(self) -> None:
        with patch.object(model_trace, "_config", return_value={"enabled": False}):
            self._record(0)
        model_trace._write_queue.join()
        self.assertEqual(model_trace.list_snapshots(self.session, 10), [])

    def test_sessions_have_separate_lists_and_retention(self) -> None:
        sessions = [self.session, "qq:other_bot:group:987654", "qq:bot:custom:group:987654"]
        for session in sessions:
            self.addCleanup(_cleanup, session)
            token = bind_turn(session, session)
            try:
                self._record(0)
            finally:
                unbind_turn(token)
        token = bind_turn(self.session, "latest")
        try:
            for index in range(3):
                self._record(index)
        finally:
            unbind_turn(token)
        model_trace._write_queue.join()
        self.assertEqual(len(model_trace.list_snapshots(self.session)), 2)
        for session in sessions[1:]:
            items = model_trace.list_snapshots(session)
            self.assertEqual(len(items), 1)
            snapshot = model_trace.read_snapshot(items[0]["path"])
            self.assertEqual(snapshot["metadata"]["session_id"], session)

    def test_continuation_merges_into_initial_snapshot(self) -> None:
        token = bind_turn(self.session, "turn-merge")
        try:
            key = model_trace.record_model_call(
                task="reply",
                provider="first",
                model="m1",
                messages=[{"role": "user", "content": "hi"}],
                tools=[],
                response=LLMResponse(reasoning_content="思考"),
                duration_ms=10,
            )
            model_trace.record_model_call(
                task="reply",
                provider="second",
                model="m2",
                messages=[{"role": "user", "content": "hi"}, {"type": "reasoning"}],
                tools=None,
                response=LLMResponse(content="正文"),
                duration_ms=20,
                workflow_purpose="replyer.reasoning_continuation",
                previous_purpose="replyer.reasoning_initial",
                workflow_attempt=0,
                merge_into=key,
            )
            model_trace._write_queue.join()
        finally:
            unbind_turn(token)

        items = model_trace.list_snapshots(self.session, 10)
        self.assertEqual(len(items), 1)
        snapshot = model_trace.read_snapshot(items[0]["path"])
        assert snapshot is not None
        self.assertEqual(
            [attempt["workflow_purpose"] for attempt in snapshot["generation_attempts"]],
            ["replyer.reasoning_initial", "replyer.reasoning_continuation"],
        )
        self.assertEqual([attempt["workflow_attempt"] for attempt in snapshot["generation_attempts"]], [1, 1])
        self.assertEqual(snapshot["metadata"]["model_name"], "m2")
        self.assertEqual(snapshot["metadata"]["duration_ms"], 30)
        self.assertEqual(snapshot["request_items"][-1], {"type": "reasoning"})
        self.assertEqual(snapshot["output_items"][0]["text"], "正文")

        self.assertEqual(snapshot["generation_attempts"][0]["output_items"][0]["text"], "思考")
        self.assertEqual(snapshot["generation_attempts"][1]["output_items"][0]["text"], "正文")
        self.assertEqual(items[0]["reasoning_preview"], "思考")

    def test_focus_snapshots_keep_scope_identity_and_member_isolation(self) -> None:
        scope = "group:trace-test"
        members = (self.session, "qq:other_bot:group:987655")
        for identity in (scope, *members):
            self.addCleanup(_cleanup, identity)
        token = bind_turn(scope, "focus-turn", related_sessions=members)
        try:
            self._record(0)
        finally:
            unbind_turn(token)
        model_trace._write_queue.join()
        for member in members:
            items = model_trace.list_snapshots(member)
            self.assertEqual(len(items), 1)
            snapshot = model_trace.read_snapshot(items[0]["path"])
            self.assertEqual(snapshot["metadata"]["session_id"], scope)
            self.assertEqual(snapshot["metadata"]["related_sessions"], list(members))
        self.assertEqual(model_trace.list_snapshots("qq:unrelated:group:987655"), [])

    def test_merged_attempts_preserve_failures_and_sum_usage(self) -> None:
        token = bind_turn(self.session, "turn-chain")
        try:
            key = ""
            for index, (response, error) in enumerate(
                [
                    (
                        LLMResponse(
                            reasoning_content="首次思考",
                            raw_response=SimpleNamespace(
                                usage=SimpleNamespace(input_tokens=10, output_tokens=2, total_tokens=12)
                            ),
                        ),
                        "",
                    ),
                    (None, "TimeoutError"),
                    (
                        LLMResponse(
                            content="正文",
                            raw_response=SimpleNamespace(
                                usage=SimpleNamespace(input_tokens=20, output_tokens=3, total_tokens=23)
                            ),
                        ),
                        "",
                    ),
                ]
            ):
                key = model_trace.record_model_call(
                    task="reply",
                    provider="p",
                    model="m",
                    messages=[{"role": "user", "content": str(index)}],
                    tools=None,
                    response=response,
                    error=error,
                    duration_ms=10,
                    merge_into=key,
                    workflow_purpose=str(index),
                )
        finally:
            unbind_turn(token)
        model_trace._write_queue.join()
        snapshot = model_trace.read_snapshot(model_trace.list_snapshots(self.session)[0]["path"])
        attempts = snapshot["generation_attempts"]
        self.assertEqual([attempt["status"] for attempt in attempts], ["completed", "failed", "completed"])
        self.assertEqual(attempts[0]["output_items"][0]["text"], "首次思考")
        self.assertEqual(attempts[1]["output_items"][0]["error"], "TimeoutError")
        self.assertEqual(attempts[2]["request_items"][0]["content"], "2")
        self.assertEqual(snapshot["metadata"]["total_tokens"], 35)
        self.assertNotIn("error", snapshot["metadata"])

    def test_sanitized_names_remain_distinct_on_windows(self) -> None:
        sessions = ["qq:bot-a:group:1", "qq:bot/a:group:1", "qq:BOT-A:group:1"]
        names = [model_trace._chat_dir_name(session).casefold() for session in sessions]
        self.assertEqual(len(set(names)), len(sessions))

    def test_unbound_turn_still_records_with_generic_chat_dir(self) -> None:
        model_trace.record_model_call(
            task="utils",
            provider="p",
            model="m",
            messages=[{"role": "user", "content": "x"}],
            tools=None,
            response=LLMResponse(content="y"),
            duration_ms=1,
        )
        model_trace._write_queue.join()
        root = model_trace.trace_root() / "utils" / "unknown_chat"
        files = sorted(root.glob("*.json"))
        self.assertTrue(files)
        self.assertEqual(json.loads(files[-1].read_text(encoding="utf-8"))["metadata"]["session_id"], "")
        for path in files:
            path.unlink()


class TaskProviderTraceTests(unittest.IsolatedAsyncioTestCase):
    """续写由 TaskProvider 自动并入首次调用的快照。"""

    async def test_continuation_merges_through_task_provider(self) -> None:
        session = "qq:bot:group:555000"
        self.addCleanup(_cleanup, session)
        initial = LLMResponse(
            reasoning_content="思考",
            provider_data={"client_type": "openai_responses", "output_items": [{"type": "reasoning"}]},
        )
        provider = TaskProvider(["p"], task="reply")
        provider._provider = SimpleNamespace(
            provider_id="p",
            get_model=lambda: "m",
            chat=AsyncMock(return_value=initial),
            chat_continuation=AsyncMock(return_value=LLMResponse(content="正文")),
        )
        token = bind_turn(session, "turn-provider")
        try:
            with (
                patch.object(usage_repository, "record", AsyncMock()),
                patch.object(model_trace, "_config", return_value={"enabled": True}),
            ):
                first = await provider.chat(messages=[{"role": "user", "content": "hi"}])
                await provider.chat_continuation(messages=[{"role": "user", "content": "hi"}], previous=first)
        finally:
            unbind_turn(token)
        model_trace._write_queue.join()

        items = model_trace.list_snapshots(session, 10)
        self.assertEqual(len(items), 1)
        snapshot = model_trace.read_snapshot(items[0]["path"])
        assert snapshot is not None
        self.assertEqual(
            [attempt["workflow_purpose"] for attempt in snapshot["generation_attempts"]],
            ["replyer.reasoning_initial", "replyer.reasoning_continuation"],
        )
        self.assertEqual(snapshot["request_items"][-1], {"type": "reasoning"})
        self.assertEqual(snapshot["output_items"][0]["text"], "正文")


class SnapshotCancellationTests(unittest.IsolatedAsyncioTestCase):
    """取消与超时保留失败快照，同时继续向调用方传播取消。"""

    async def test_timeout_records_failure(self) -> None:
        started = asyncio.Event()

        async def pending(**kwargs):
            started.set()
            await asyncio.Future()

        provider = TaskProvider(["p"], task="reply")
        provider._provider = SimpleNamespace(provider_id="p", get_model=lambda: "m", chat=pending)
        with (
            patch.object(usage_repository, "record", AsyncMock()),
            patch("src.plugins.nonebot_plugin_fxbot.chat.providers.routing.record_model_call") as record,
        ):
            with self.assertRaises(TimeoutError):
                await asyncio.wait_for(provider.chat(messages=[]), timeout=0.05)
            self.assertTrue(started.is_set())
            record.assert_called_once()
            payload = model_trace.build_snapshot(
                session_id="s", turn_id="t", **_snapshot_kwargs(record.call_args.kwargs)
            )
        self.assertEqual(payload["generation_attempts"][0]["status"], "failed")

    async def test_closing_stream_records_failure_and_preserves_partial_output(self) -> None:
        closed = asyncio.Event()

        async def stream(**kwargs):
            try:
                yield LLMResponse(content="部分正文", is_stream_chunk=True)
                await asyncio.Future()
            finally:
                closed.set()

        provider = TaskProvider(["p"], task="reply")
        provider._provider = SimpleNamespace(provider_id="p", get_model=lambda: "m", chat_stream=stream)
        with (
            patch.object(usage_repository, "record", AsyncMock()),
            patch("src.plugins.nonebot_plugin_fxbot.chat.providers.routing.record_model_call") as record,
        ):
            responses = provider.chat_stream(messages=[])
            await anext(responses)
            await responses.aclose()
            self.assertTrue(closed.is_set())
            record.assert_called_once()
            payload = model_trace.build_snapshot(
                session_id="s", turn_id="t", **_snapshot_kwargs(record.call_args.kwargs)
            )
        self.assertEqual(payload["generation_attempts"][0]["status"], "failed")
        self.assertEqual(payload["output_items"][0]["text"], "部分正文")

    async def test_cancelled_calls_are_not_marked_completed(self) -> None:
        for method in ("chat", "chat_json", "chat_continuation", "chat_stream"):
            with self.subTest(method=method):
                provider = TaskProvider(["p"], task="reply")

                async def cancelled_stream(**kwargs):
                    yield LLMResponse(content="部分正文", is_stream_chunk=True)
                    raise asyncio.CancelledError

                provider._provider = SimpleNamespace(
                    provider_id="p",
                    get_model=lambda: "m",
                    chat=AsyncMock(side_effect=asyncio.CancelledError),
                    chat_json=AsyncMock(side_effect=asyncio.CancelledError),
                    chat_continuation=AsyncMock(side_effect=asyncio.CancelledError),
                    chat_stream=cancelled_stream,
                )
                with (
                    patch.object(usage_repository, "record", AsyncMock()),
                    patch("src.plugins.nonebot_plugin_fxbot.chat.providers.routing.record_model_call") as record,
                ):
                    with self.assertRaises(asyncio.CancelledError):
                        if method == "chat_stream":
                            async for _ in provider.chat_stream(messages=[]):
                                pass
                        elif method == "chat_continuation":
                            await provider.chat_continuation(messages=[], previous=LLMResponse())
                        else:
                            await getattr(provider, method)(messages=[])
                    record.assert_called_once()
                    payload = model_trace.build_snapshot(
                        session_id="s", turn_id="t", **_snapshot_kwargs(record.call_args.kwargs)
                    )
                self.assertEqual(payload["generation_attempts"][0]["status"], "failed")
                self.assertIn("CancelledError", payload["metadata"]["error"])
                if method == "chat_stream":
                    self.assertEqual(payload["output_items"][0]["text"], "部分正文")
