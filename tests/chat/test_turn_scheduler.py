"""MaiBot 调度顺序、防抖、退避和会话隔离回归。"""

from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.behavior import turn_scheduler as scheduler_module


class TurnSchedulerTests(unittest.IsolatedAsyncioTestCase):
    """隔离模型发送，只验证消息进入 Planner 的真实顺序。"""

    async def asyncSetUp(self) -> None:
        self.scheduler = scheduler_module.NormalChatScheduler()
        self.addAsyncCleanup(self.scheduler.stop)
        self.service = SimpleNamespace(
            queue_message=AsyncMock(return_value=False), is_current=lambda *args: True, process_and_send=AsyncMock()
        )
        self.gate = SimpleNamespace(
            backoff_delay=Mock(return_value=0),
            observe_message=Mock(return_value=SimpleNamespace(would_trigger=True)),
            mark_triggered=Mock(),
            record_forced_turn=Mock(),
        )
        self.enterContext(patch.object(scheduler_module.default_reply_gate_registry, "get", return_value=self.gate))
        self.enterContext(patch.object(scheduler_module, "proactive_switch", AsyncMock(return_value=True)))
        self.enterContext(patch.object(scheduler_module, "talk_value_for", return_value=1))
        self.quiet = asyncio.Event()

        async def wait(batch):
            await self.quiet.wait()

        self.enterContext(patch.object(scheduler_module.PendingBatch, "wait", wait))

    def request(self, tenant="tenant-a", kind="proactive") -> ChatRequest:
        return ChatRequest(tenant, "user", "group", "在聊天", uuid4().hex, uuid4().hex, kind, accepted_epoch=0)

    async def drain(self) -> None:
        for _ in range(20):
            await asyncio.sleep(0)
            tasks = [state.task for state in self.scheduler.chats.values() if state.task is not None]
            if not tasks:
                return
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=2)

    async def test_gate_runs_after_quiet_and_sees_whole_batch(self) -> None:
        first, second = self.request(), self.request()
        await self.scheduler.submit(self.service, first, object())
        await self.scheduler.submit(self.service, second, object())
        await asyncio.sleep(0)
        self.gate.observe_message.assert_not_called()
        self.quiet.set()
        await self.drain()
        self.assertEqual(self.gate.observe_message.call_args.args[0], [first.dedupe_key, second.dedupe_key])
        self.service.process_and_send.assert_awaited_once()

    async def test_forced_turn_skips_backoff_and_dynamic_gate(self) -> None:
        await self.scheduler.submit(self.service, self.request(kind="mention"), object())
        self.quiet.set()
        await self.drain()
        self.gate.backoff_delay.assert_not_called()
        self.gate.observe_message.assert_not_called()
        self.gate.record_forced_turn.assert_called_once()

    async def test_zero_frequency_consumes_without_model_even_for_mention(self) -> None:
        with patch.object(scheduler_module, "talk_value_for", return_value=0):
            await self.scheduler.submit(self.service, self.request(kind="mention"), object())
            self.quiet.set()
            await self.drain()
        self.service.process_and_send.assert_not_awaited()

    async def test_denied_batch_retains_pending_until_next_message(self) -> None:
        self.gate.observe_message.return_value = SimpleNamespace(would_trigger=False)
        first = self.request()
        await self.scheduler.submit(self.service, first, object())
        self.quiet.set()
        for _ in range(4):
            await asyncio.sleep(0)
        self.assertEqual(self.scheduler.chats[first.session_id].pending, [first])
        self.gate.observe_message.return_value = SimpleNamespace(would_trigger=True)
        second = self.request()
        await self.scheduler.submit(self.service, second, object())
        await self.drain()
        self.assertEqual(len(self.gate.observe_message.call_args.args[0]), 2)
        self.service.process_and_send.assert_awaited_once()

    async def test_tenants_do_not_share_pending_messages(self) -> None:
        await self.scheduler.submit(self.service, self.request("tenant-a"), object())
        await self.scheduler.submit(self.service, self.request("tenant-b"), object())
        self.quiet.set()
        await self.drain()
        self.assertEqual(
            {call.args[0].session_id for call in self.service.process_and_send.await_args_list},
            {"tenant-a", "tenant-b"},
        )

    async def test_clear_cancels_waiting_turn(self) -> None:
        await self.scheduler.submit(self.service, self.request(), object())
        task = self.scheduler.chats["tenant-a"].task
        self.scheduler.clear("tenant-a")
        await asyncio.gather(task, return_exceptions=True)
        self.quiet.set()
        self.service.process_and_send.assert_not_awaited()
