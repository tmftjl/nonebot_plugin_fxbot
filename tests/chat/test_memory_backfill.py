"""P1.5 回填：窗口冻结、从新到旧、幂等续跑与实时游标协调。"""

from __future__ import annotations

import re
import unittest
from uuid import uuid4
from typing import Any
from datetime import timedelta

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.memory.config import DEFAULTS
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import (
    ChatMessage,
    ChatJobBatch,
    ChatSessionState,
    utc_now_naive,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.pipeline import process_tenant, process_backfill
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository

U1 = "20001"
U2 = "20002"


def _cfg(**overrides: Any) -> dict[str, Any]:
    """构造小批量回填配置。"""
    values = {
        **DEFAULTS,
        "episode_min_messages": 3,
        "episode_idle_minutes": 0,
        "backfill_messages": 500,
        "backfill_batch": 40,
    }
    values.update(overrides)
    return values


def _prompt_ids(prompt: list[dict[str, Any]]) -> list[int]:
    """从提示的用户消息里取回被抽取的消息主键。"""
    content = str(prompt[-1].get("content") or "")
    return [int(value) for value in re.findall(r"^\[(\d+)\]", content, re.M)]


class _Provider:
    """记录每次提示，并按顺序返回预设结果。"""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.prompts: list[list[dict[str, Any]]] = []

    @property
    def calls(self) -> int:
        """已调用次数。"""
        return len(self.prompts)

    async def chat_json(self, prompt: Any, schema: Any, *, model: str | None = None) -> Any:
        self.prompts.append(list(prompt))
        value = self.payloads[min(self.calls - 1, len(self.payloads) - 1)]
        if isinstance(value, BaseException):
            raise value
        return value


def _payload(message_ids: list[int], *, subject: str = U1) -> dict[str, Any]:
    """返回两条稳定事实，方便检验重复回填不会新增行。"""
    return {
        "topics": [{"title": "回填主题", "keywords": ["回填"], "summary": "一段历史对话"}],
        "facts": [
            {
                "subject_id": subject,
                "subject_name": "小明",
                "key": "name",
                "value": "小明",
                "category": "identity",
                "confidence": 0.9,
                "importance": 0.8,
                "asserted_by": subject,
                "evidence": [message_ids[0]],
            },
            {
                "subject_id": subject,
                "subject_name": "小明",
                "key": "food",
                "value": "拉面",
                "category": "preference",
                "confidence": 0.8,
                "importance": 0.6,
                "asserted_by": subject,
                "evidence": [message_ids[-1]],
            },
        ],
    }


class BackfillTests(unittest.IsolatedAsyncioTestCase):
    """回填窗口、批次顺序和完成标记。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.tenant = f"test:{uuid4().hex}"

    async def _seed(self, content: str, *, sender_id: str = U1) -> int:
        return await chat_repository.append_message(
            ChatMessage(
                tenant=self.tenant,
                sender_id=sender_id,
                sender_name="小明" if sender_id == U1 else "小红",
                content=content,
                dedupe_key=f"msg:{uuid4().hex}",
                occurred_at=utc_now_naive(),
            )
        )

    async def _seed_many(self, count: int, prefix: str = "历史消息") -> list[int]:
        return [await self._seed(f"{prefix}{index}") for index in range(count)]

    async def _enable(self) -> None:
        await chat_repository.set_learn_enabled(self.tenant, True)

    async def test_backfill_freezes_window_and_real_time_does_not_duplicate(self) -> None:
        ids = await self._seed_many(12)
        await self._enable()
        cfg = _cfg(backfill_messages=6, backfill_batch=3)

        self.assertTrue(await chat_repository.ensure_backfill(self.tenant, min_messages=3, max_messages=6))
        memory = await chat_repository.get_cursor(self.tenant)
        backfill = await chat_repository.get_cursor(self.tenant, "backfill")
        self.assertEqual(memory.cursor_seq, ids[-1])
        self.assertEqual(backfill.direction, "backward")
        self.assertEqual(backfill.cursor_seq, ids[-1] + 1)
        self.assertEqual(backfill.stop_seq, ids[-6] - 1)

        first = _Provider(_payload(ids[-3:]))
        now = utc_now_naive()
        self.assertTrue(await process_backfill(self.tenant, provider=first, now=now, cfg=cfg))
        self.assertEqual(_prompt_ids(first.prompts[0]), ids[-3:])

        throttled = _Provider(_payload(ids[-6:-3]))
        self.assertFalse(await process_backfill(self.tenant, provider=throttled, now=now, cfg=cfg))
        self.assertEqual(throttled.calls, 0)

        self.assertTrue(
            await process_backfill(self.tenant, provider=throttled, now=now + timedelta(seconds=11), cfg=cfg)
        )
        self.assertEqual(_prompt_ids(throttled.prompts[0]), ids[-6:-3])

        state = await ChatSessionState.select_rows(tenant=self.tenant)
        self.assertIsNotNone(state[0].backfilled_at)
        batches = await ChatJobBatch.select_rows(tenant=self.tenant, job="backfill")
        self.assertEqual([batch.status for batch in batches], ["done", "done"])

        facts = await chat_repository.get_facts(self.tenant, U1)
        self.assertEqual(len(facts), 2)
        self.assertTrue(all(fact.source == "backfill" for fact in facts))

        idle = _Provider(ValueError("real-time should not run"))
        self.assertFalse(await process_tenant(self.tenant, provider=idle, cfg=cfg))
        self.assertEqual(idle.calls, 0)

        new_ids = await self._seed_many(3, "新消息")
        realtime = _Provider(_payload(new_ids))
        self.assertTrue(await process_tenant(self.tenant, provider=realtime, cfg=cfg))
        self.assertEqual(_prompt_ids(realtime.prompts[0]), new_ids)

    async def test_backfill_stops_at_configured_message_count(self) -> None:
        ids = await self._seed_many(10)
        await self._enable()
        cfg = _cfg(backfill_messages=4, backfill_batch=2)
        await chat_repository.ensure_backfill(self.tenant, min_messages=3, max_messages=4)

        now = utc_now_naive()
        first = _Provider(_payload(ids[-2:]))
        second = _Provider(_payload(ids[-4:-2]))
        self.assertTrue(await process_backfill(self.tenant, provider=first, now=now, cfg=cfg))
        self.assertTrue(await process_backfill(self.tenant, provider=second, now=now + timedelta(seconds=11), cfg=cfg))

        extracted = _prompt_ids(first.prompts[0]) + _prompt_ids(second.prompts[0])
        self.assertEqual(sorted(extracted), ids[-4:])
        state = await ChatSessionState.select_rows(tenant=self.tenant)
        self.assertIsNotNone(state[0].backfilled_at)

    async def test_failed_batch_resumes_same_range_without_duplicate_rows(self) -> None:
        ids = await self._seed_many(6)
        await self._enable()
        cfg = _cfg(backfill_messages=6, backfill_batch=3)
        await chat_repository.ensure_backfill(self.tenant, min_messages=3, max_messages=6)

        failing = _Provider(ValueError("bad json"), ValueError("bad json"))
        now = utc_now_naive()
        self.assertFalse(await process_backfill(self.tenant, provider=failing, now=now, cfg=cfg))
        self.assertEqual(failing.calls, 2)
        cursor = await chat_repository.get_cursor(self.tenant, "backfill")
        self.assertEqual(cursor.cursor_seq, ids[-1] + 1)
        self.assertEqual(cursor.fail_count, 1)
        self.assertIsNotNone(cursor.next_retry_at)
        failed = await ChatJobBatch.select_rows(
            tenant=self.tenant, job="backfill", range_start=ids[-3], range_end=ids[-1]
        )
        self.assertEqual([batch.status for batch in failed], ["failed"])

        waiting = _Provider(_payload(ids[-3:]))
        self.assertFalse(await process_backfill(self.tenant, provider=waiting, now=now, cfg=cfg))
        self.assertEqual(waiting.calls, 0)

        recovery = _Provider(_payload(ids[-3:]))
        retry_at = cursor.next_retry_at + timedelta(seconds=1)
        self.assertTrue(await process_backfill(self.tenant, provider=recovery, now=retry_at, cfg=cfg))
        cursor = await chat_repository.get_cursor(self.tenant, "backfill")
        self.assertEqual((cursor.cursor_seq, cursor.fail_count), (ids[-3], 0))
        recovered = await ChatJobBatch.select_rows(
            tenant=self.tenant, job="backfill", range_start=ids[-3], range_end=ids[-1]
        )
        self.assertEqual([batch.status for batch in recovered], ["done"])

        older = _Provider(_payload(ids[:3]))
        self.assertTrue(
            await process_backfill(
                self.tenant,
                provider=older,
                now=retry_at + timedelta(seconds=11),
                cfg=cfg,
            )
        )
        self.assertEqual(len(await chat_repository.get_facts(self.tenant, U1)), 2)

    async def test_all_noise_history_marks_backfilled_without_model_call(self) -> None:
        for index in range(4):
            await self._seed(f"https://example.com/{index}")
        await self._enable()

        self.assertFalse(await chat_repository.ensure_backfill(self.tenant, min_messages=3, max_messages=5))
        state = await ChatSessionState.select_rows(tenant=self.tenant)
        self.assertIsNotNone(state[0].backfilled_at)
        provider = _Provider(_payload([1]))
        self.assertFalse(await process_backfill(self.tenant, provider=provider, now=utc_now_naive(), cfg=_cfg()))
        self.assertEqual(provider.calls, 0)
        self.assertEqual(await ChatJobBatch.select_rows(tenant=self.tenant), [])


if __name__ == "__main__":
    unittest.main()
