"""P1.5 公平调度：失败租户跳过、退避让路与最久未运行优先。"""

from __future__ import annotations

import unittest
from uuid import uuid4
from typing import Any
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.memory.config import DEFAULTS
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage, utc_now_naive
from src.plugins.nonebot_plugin_fxbot.chat.memory.pipeline import _backfill_due, process_tenant, memory_job_tick
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import TenantJobState, chat_repository


def _cfg(**overrides: Any) -> dict[str, Any]:
    """构造每轮只处理一个租户的调度配置。"""
    values = {
        **DEFAULTS,
        "episode_min_messages": 3,
        "episode_idle_minutes": 0,
        "max_tenants_per_tick": 1,
    }
    values.update(overrides)
    return values


class _Provider:
    """返回空抽取结果，只用于确认哪些租户被调用。"""

    def __init__(self) -> None:
        self.calls = 0
        self.prompts: list[Any] = []

    async def chat_json(self, prompt: Any, schema: Any, *, model: str | None = None) -> dict[str, Any]:
        self.calls += 1
        self.prompts.append(prompt)
        return {}


class SchedulingTests(unittest.IsolatedAsyncioTestCase):
    """调度快照与候选顺序。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.prefix = f"test:{uuid4().hex}"
        self.tenants: list[str] = []
        self.message_seq = 0

    async def _tenant(self, name: str) -> str:
        tenant = f"{self.prefix}:{name}"
        self.tenants.append(tenant)
        await chat_repository.set_learn_enabled(tenant, True)
        await chat_repository.finish_backfill(tenant)
        return tenant

    async def _seed(self, tenant: str, count: int = 3) -> list[int]:
        start = self.message_seq
        self.message_seq += count
        return [
            await chat_repository.append_message(
                ChatMessage(
                    tenant=tenant,
                    sender_id="30001",
                    sender_name="小明",
                    content=f"本群聊天内容{start + index}",
                    dedupe_key=f"msg:{uuid4().hex}",
                    occurred_at=utc_now_naive(),
                )
            )
            for index in range(count)
        ]

    async def _tick(self, provider: _Provider, cfg: dict[str, Any], now: Any) -> int:
        """只把本测试的租户交给调度，避免全局测试库里的历史租户干扰。"""
        snapshot = await chat_repository.scheduling_snapshot()
        candidates = [row for row in snapshot if row.tenant in self.tenants]
        with patch.object(
            chat_repository,
            "scheduling_snapshot",
            new=AsyncMock(return_value=candidates),
        ):
            return await memory_job_tick(provider=provider, now=now, cfg=cfg)

    async def test_failed_tenant_is_skipped_so_others_keep_running(self) -> None:
        alpha = await self._tenant("alpha")
        beta = await self._tenant("beta")
        gamma = await self._tenant("gamma")
        for tenant in (alpha, beta, gamma):
            await self._seed(tenant)
        for _ in range(5):
            await chat_repository.mark_batch_failed(alpha, 1, 2, job="memory", error="bad model")

        cfg = _cfg()
        now = utc_now_naive()
        first = _Provider()
        second = _Provider()
        self.assertEqual(await self._tick(first, cfg, now), 1)
        self.assertEqual(await self._tick(second, cfg, now), 1)
        self.assertEqual(first.calls + second.calls, 2)

        failed = await chat_repository.get_cursor(alpha)
        self.assertEqual((failed.cursor_seq, failed.fail_count), (0, 5))
        finished = []
        for tenant in (beta, gamma):
            cursor = await chat_repository.get_cursor(tenant)
            if cursor is not None and cursor.cursor_seq > 0:
                finished.append(tenant)
        self.assertEqual(len(finished), 2)
        self.assertEqual(await self._tick(_Provider(), cfg, now), 0)

    async def test_backoff_tenant_lets_pending_tenant_run_first(self) -> None:
        alpha = await self._tenant("alpha")
        beta = await self._tenant("beta")
        await self._seed(alpha)
        await self._seed(beta)
        await chat_repository.mark_batch_failed(alpha, 1, 2, job="memory", error="temporary")

        cfg = _cfg()
        now = utc_now_naive()
        provider = _Provider()
        self.assertEqual(await self._tick(provider, cfg, now), 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual((await chat_repository.get_cursor(alpha)).cursor_seq, 0)
        self.assertGreater((await chat_repository.get_cursor(beta)).cursor_seq, 0)

        later = now + timedelta(minutes=2)
        retry = _Provider()
        self.assertEqual(await self._tick(retry, cfg, later), 1)
        self.assertEqual(retry.calls, 1)
        cursor = await chat_repository.get_cursor(alpha)
        self.assertGreater(cursor.cursor_seq, 0)
        self.assertEqual(cursor.fail_count, 0)

    async def test_least_recently_run_tenant_gets_the_slot(self) -> None:
        alpha = await self._tenant("alpha")
        beta = await self._tenant("beta")
        alpha_ids = await self._seed(alpha)
        await self._seed(beta)
        cfg = _cfg()
        self.assertTrue(await process_tenant(alpha, provider=_Provider(), cfg=cfg))
        alpha_cursor = await chat_repository.get_cursor(alpha)
        self.assertEqual(alpha_cursor.cursor_seq, alpha_ids[-1])
        await self._seed(alpha)

        provider = _Provider()
        self.assertEqual(await self._tick(provider, cfg, utc_now_naive()), 1)
        self.assertEqual(provider.calls, 1)
        self.assertEqual((await chat_repository.get_cursor(alpha)).cursor_seq, alpha_ids[-1])
        self.assertGreater((await chat_repository.get_cursor(beta)).cursor_seq, 0)


class BackfillDueTests(unittest.TestCase):
    """回填门槛按消息条数判断，而不是按全局自增主键。"""

    def test_backfill_waits_until_minimum_message_count(self) -> None:
        def state(count: int) -> TenantJobState:
            return TenantJobState(
                tenant="test:count",
                last_message_id=99999,
                message_count=count,
                learn_enabled=True,
                memory=None,
                backfill=None,
                backfilled_at=None,
            )

        now = utc_now_naive()
        self.assertFalse(_backfill_due(state(2), now, _cfg()))
        self.assertTrue(_backfill_due(state(3), now, _cfg()))


if __name__ == "__main__":
    unittest.main()
