"""模型调用统计：分钟桶累加、七天保留与失败口径。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import voice
from src.plugins.nonebot_plugin_fxbot.utils.tz import now, to_naive_utc
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate, expression_vector
from src.plugins.nonebot_plugin_fxbot.chat.providers import usage
from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host import llm
from src.plugins.nonebot_plugin_fxbot.chat.providers.usage import (
    ModelTaskUsage,
    usage_repository,
    get_model_task_usage,
    track_model_task_usage,
)


class _Provider:
    """只提供统计需要的模型名与 Provider 名。"""

    provider_id = "main"

    def get_model(self) -> str:
        return "gpt-test"


async def _prepare_db() -> None:
    await base_models.init_database()
    await migrate.ensure_schema()
    memory_state.mark_ready()


class UsageRepositoryTests(unittest.IsolatedAsyncioTestCase):
    """分钟桶在临时库上的累加与保留。"""

    async def asyncSetUp(self) -> None:
        await _prepare_db()
        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(delete(ModelTaskUsage))
            await session.commit()

    async def test_same_minute_buckets_accumulate(self) -> None:
        await usage_repository.record("reply", "m1", "m1", "main", 1.5, True)
        await usage_repository.record("reply", "m1", "m1", "main", 0.5, False)

        rows = await usage_repository.aggregate(24)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["request_count"], 2)
        self.assertEqual(rows[0]["failure_count"], 1)
        self.assertAlmostEqual(rows[0]["success_duration"], 1.5)

    async def test_state_is_grouped_by_task_and_model(self) -> None:
        await usage_repository.record("reply", "m1", "m1", "main", 1.0, True)
        await usage_repository.record("reply", "m1", "m1", "main", 1.0, True)
        await usage_repository.record("planner", "m2", "m2", "backup", 2.0, False)

        result = await get_model_task_usage(24)

        self.assertEqual(result["request_count"], 3)
        tasks = {item["task_name"]: item for item in result["tasks"]}
        self.assertEqual(tasks["reply"]["avg_duration"], 1.0)
        self.assertEqual(tasks["reply"]["failure_rate"], 0.0)
        self.assertEqual(tasks["planner"]["failure_rate"], 1.0)
        self.assertIsNone(tasks["planner"]["avg_duration"])

    async def test_rows_older_than_retention_are_removed(self) -> None:
        old_bucket = to_naive_utc(now()) - timedelta(days=usage.RETENTION_DAYS + 1)
        async with base_models.async_maker() as session:  # type: ignore[misc]
            session.add(
                ModelTaskUsage(
                    bucket_time=old_bucket.replace(second=0, microsecond=0),
                    task_name="reply",
                    model_name="m1",
                    model_identifier="m1",
                    provider_name="main",
                    request_count=1,
                )
            )
            await session.commit()

        await usage_repository.record("reply", "m1", "m1", "main", 1.0, True)

        rows = await usage_repository.aggregate(usage.RETENTION_DAYS * 24)
        self.assertEqual([row["request_count"] for row in rows], [1])


class CapabilityUsageTests(unittest.IsolatedAsyncioTestCase):
    """真实能力调用边界记录成功、异常、超时和取消。"""

    async def asyncSetUp(self) -> None:
        await _prepare_db()
        async with base_models.async_maker() as session:
            await session.execute(delete(ModelTaskUsage))
            await session.commit()
        self.provider = SimpleNamespace(
            provider_id="capability",
            model_name="test-model",
            get_model=lambda: "test-model",
            config={"timeout": 0.02},
            transcribe=AsyncMock(return_value="text"),
            synthesize=AsyncMock(return_value=b"audio"),
            embed=AsyncMock(return_value=[[1.0]]),
            embed_image=AsyncMock(return_value=[1.0]),
        )
        for name in ("get_stt_provider", "get_tts_provider", "get_embedding_provider"):
            self.enterContext(patch.object(voice.provider_manager, name, AsyncMock(return_value=self.provider)))
        self.enterContext(patch.object(voice, "voice_routing_name", return_value="capability"))
        self.enterContext(patch.object(voice, "tts_routing_name", return_value="capability"))
        self.enterContext(patch.object(llm, "embedding_provider_name", return_value="capability"))
        self.record = self.enterContext(patch.object(usage_repository, "record", wraps=usage_repository.record))

    def _calls(self) -> tuple:
        """列出五个 FxBot 自有能力入口及预期返回值。"""
        return (
            ("voice", lambda: voice.transcribe(b"audio"), "text"),
            ("tts", lambda: voice.synthesize("text"), b"audio"),
            ("embedding", lambda: expression_vector._embed(self.provider, ["text"]), [[1.0]]),
            (
                "embedding",
                lambda: llm.embed_texts(["text"]),
                [
                    llm.EmbeddingResult(
                        embedding=[1.0],
                        model_name="test-model",
                        model_identifier="test-model",
                        api_provider="capability",
                    )
                ],
            ),
            (
                "embedding",
                lambda: llm.EmbeddingServiceClient().embed_image(b"image"),
                llm.EmbeddingResult(
                    embedding=[1.0], model_name="test-model", model_identifier="test-model", api_provider="capability"
                ),
            ),
        )

    def _set_side_effect(self, side_effect) -> None:
        """为所有真实 Provider 方法设置同一故障行为。"""
        for name in ("transcribe", "synthesize", "embed", "embed_image"):
            getattr(self.provider, name).side_effect = side_effect

    async def _assert_usage(self, succeeded: bool) -> None:
        """每个入口只计一次，并检查数据库聚合后的任务键。"""
        self.assertEqual(self.record.await_count, 5)
        for recorded, (task, _, _) in zip(self.record.await_args_list, self._calls()):
            self.assertEqual(recorded.args[:4], (task, "test-model", "test-model", "capability"))
            self.assertEqual(recorded.args[5], succeeded)
        result = await get_model_task_usage()
        self.assertEqual(result["request_count"], 5)
        tasks = {item["task_name"]: item for item in result["tasks"]}
        self.assertEqual(set(tasks), {"voice", "tts", "embedding"})
        for task, count in (("voice", 1), ("tts", 1), ("embedding", 3)):
            self.assertEqual(tasks[task]["request_count"], count)
            self.assertEqual(tasks[task]["failure_count"], 0 if succeeded else count)

    async def test_success_is_recorded(self) -> None:
        for task, call, expected in self._calls():
            with self.subTest(task=task, call=call):
                self.assertEqual(await call(), expected)
        await self._assert_usage(True)

    async def test_exception_is_recorded(self) -> None:
        self._set_side_effect(RuntimeError("provider failed"))
        for task, call, _ in self._calls():
            with self.subTest(task=task, call=call):
                if task in {"voice", "tts"}:
                    self.assertIsNone(await call())
                else:
                    with self.assertRaises(RuntimeError):
                        await call()
        await self._assert_usage(False)

    async def test_timeout_is_recorded(self) -> None:
        async def slow(*args):
            await asyncio.sleep(60)

        self._set_side_effect(slow)
        for task, call, _ in self._calls():
            with self.subTest(task=task, call=call):
                if task in {"voice", "tts"}:
                    self.assertIsNone(await call())
                else:
                    with self.assertRaises(TimeoutError):
                        await asyncio.wait_for(call(), timeout=0.02)
        await self._assert_usage(False)

    async def test_cancellation_is_recorded(self) -> None:
        entered = asyncio.Event()

        async def slow(*args):
            entered.set()
            await asyncio.sleep(60)

        self.provider.config["timeout"] = 60
        self._set_side_effect(slow)
        for task, call, _ in self._calls():
            with self.subTest(task=task, call=call):
                entered.clear()
                pending = asyncio.create_task(call())
                await asyncio.wait_for(entered.wait(), timeout=1)
                pending.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await pending
        await self._assert_usage(False)

    async def test_record_failure_does_not_change_results_or_errors(self) -> None:
        self.record.side_effect = RuntimeError("database failed")
        for task, call, expected in self._calls():
            with self.subTest(task=task, call=call):
                self.assertEqual(await call(), expected)
        self._set_side_effect(ValueError("provider failed"))
        for task, call, _ in self._calls():
            with self.subTest(task=task, call=call):
                if task in {"voice", "tts"}:
                    self.assertIsNone(await call())
                else:
                    with self.assertRaisesRegex(ValueError, "provider failed"):
                        await call()

    async def test_expression_batches_are_counted_per_attempt(self) -> None:
        self.provider.embed.side_effect = [[[1.0]], RuntimeError("second batch failed")]
        with patch.object(expression_vector, "EMBED_BATCH_SIZE", 1), self.assertRaises(RuntimeError):
            await expression_vector._embed(self.provider, ["first", "second", "third"])
        self.assertEqual(self.record.await_count, 2)
        self.assertEqual([call.args[5] for call in self.record.await_args_list], [True, False])
        result = await get_model_task_usage()
        self.assertEqual(result["request_count"], 2)
        self.assertEqual(result["tasks"][0]["failure_count"], 1)


class TrackUsageTests(unittest.IsolatedAsyncioTestCase):
    """调用边界只记录实际尝试，成功与失败分别计数。"""

    async def test_success_and_failure_are_recorded(self) -> None:
        record = AsyncMock()
        with patch.object(usage, "usage_repository", record=record):
            async with track_model_task_usage("reply", _Provider()):
                pass
            with self.assertRaises(RuntimeError):
                async with track_model_task_usage("reply", _Provider()):
                    raise RuntimeError("boom")

        first, second = record.await_args_list
        self.assertEqual(first.args[:4], ("reply", "gpt-test", "gpt-test", "main"))
        self.assertTrue(first.args[5])
        self.assertFalse(second.args[5])
        self.assertGreaterEqual(second.args[4], 0.0)

    async def test_recording_failure_does_not_break_the_call(self) -> None:
        with patch.object(usage, "usage_repository", record=AsyncMock(side_effect=RuntimeError("db"))):
            async with track_model_task_usage("reply", _Provider()):
                pass
