"""MaiBot 调度顺序、防抖、退避和会话隔离回归。"""

from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.behavior import turn_scheduler as scheduler_module
from src.plugins.nonebot_plugin_fxbot.chat.behavior.reply_gate import ReplyGate


class TurnSchedulerTests(unittest.IsolatedAsyncioTestCase):
    """隔离模型发送，只验证消息进入 Planner 的真实顺序。"""

    async def asyncSetUp(self) -> None:
        self.scheduler = scheduler_module.NormalChatScheduler()
        self.addAsyncCleanup(self.scheduler.stop)
        self.service = SimpleNamespace(
            queue_message=AsyncMock(return_value=False), is_current=lambda *args: True, process_and_send=AsyncMock()
        )
        self.gate = SimpleNamespace(
            record_message=Mock(),
            backoff_delay=Mock(return_value=0),
            observe_message=Mock(return_value=SimpleNamespace(would_trigger=True)),
            mark_triggered=Mock(),
            reset_pending=Mock(),
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

    async def test_active_planner_records_ingress_without_evaluating_gate(self) -> None:
        """运行及等待中的 Planner 续接仍记录活跃度，且不重复运行门控。"""
        self.service.queue_message.return_value = True
        for kind in ("proactive", "mention", "persona"):
            request = self.request(kind=kind)
            request.metadata["received_at"] = "2026-11-04T00:00:00"
            await self.scheduler.submit(self.service, request, object())
            self.gate.record_message.assert_called_with(request.dedupe_key, now=1793750400.0)
        self.assertEqual(self.gate.record_message.call_count, 3)
        self.assertEqual(self.gate.record_forced_turn.call_count, 2)
        self.gate.observe_message.assert_not_called()
        self.assertEqual(self.scheduler.chats, {})

    async def test_real_gate_does_not_recount_scheduler_retries(self) -> None:
        """真实门控与调度器组合验证，不用放行结果替身掩盖重复计数。"""
        gate = ReplyGate()
        with (
            patch.object(scheduler_module.default_reply_gate_registry, "get", return_value=gate),
            patch.object(
                gate._gate,
                "evaluate",
                return_value=SimpleNamespace(
                    should_trigger=False,
                    threshold=1.0,
                    expected_replies=0.0,
                    target_replies=0.0,
                    actual_replies=0,
                    keep_ratio=0.0,
                ),
            ),
        ):
            self.quiet.set()
            for size in range(1, 4):
                await self.scheduler.submit(self.service, self.request(), object())
                for _ in range(12):
                    await asyncio.sleep(0)
                observation = gate._observations[-1]
                self.assertEqual(observation.features["recent_message_count"], size)
                self.assertEqual(observation.features["pending_count"], size)
                self.assertEqual(len(gate._round_texts), size)

    async def test_pending_capacity_keeps_latest_messages(self) -> None:
        """积压容量与防抖批次独立封顶，最终只合并保留的消息。"""
        requests = [self.request() for _ in range(scheduler_module.MAX_SCHEDULED_MESSAGES + 5)]
        for request in requests:
            await self.scheduler.submit(self.service, request, object())
        state = self.scheduler.chats[requests[-1].session_id]
        self.assertEqual(state.pending, requests[5:])
        self.assertEqual(len(state.received_at), scheduler_module.MAX_SCHEDULED_MESSAGES)
        self.quiet.set()
        await self.drain()
        ids = self.gate.observe_message.call_args.args[0]
        self.assertEqual(ids, [request.dedupe_key for request in requests[5:]])
        merged = self.service.process_and_send.await_args.args[0]
        self.assertEqual(set(merged.metadata["source_senders"]), set(ids))

    async def test_denied_pending_expires_without_new_messages(self) -> None:
        """门控拒绝后也设置到期唤醒，避免空闲会话永久保留积压。"""
        self.gate.observe_message.return_value = SimpleNamespace(would_trigger=False)
        with patch.object(scheduler_module, "PENDING_TTL_SECONDS", 0.05):
            request = self.request()
            await self.scheduler.submit(self.service, request, object())
            self.quiet.set()
            task = self.scheduler.chats[request.session_id].task
            await asyncio.wait_for(task, timeout=2)
        self.assertNotIn(request.session_id, self.scheduler.chats)
        self.gate.reset_pending.assert_called_once()
        self.service.process_and_send.assert_not_awaited()

    async def test_pending_expires_during_long_backoff(self) -> None:
        """退避长于积压有效期时，按积压到期时刻唤醒并释放会话。"""
        self.gate.backoff_delay.return_value = 300
        with patch.object(scheduler_module, "PENDING_TTL_SECONDS", 0.05):
            request = self.request()
            await self.scheduler.submit(self.service, request, object())
            self.quiet.set()
            await asyncio.wait_for(self.scheduler.chats[request.session_id].task, timeout=2)
        self.assertNotIn(request.session_id, self.scheduler.chats)
        self.gate.observe_message.assert_not_called()

    async def test_expired_pending_is_not_merged_into_new_trigger(self) -> None:
        """过期消息仍可留在历史，但不能作为待回复目标带进新轮次。"""
        old, latest = self.request(), self.request(kind="mention")
        with patch.object(scheduler_module.time, "time", return_value=1000):
            await self.scheduler.submit(self.service, old, object())
        with patch.object(scheduler_module.time, "time", return_value=1400):
            await self.scheduler.submit(self.service, latest, object())
            self.assertEqual(self.scheduler.chats[old.session_id].pending, [latest])
            self.quiet.set()
            await self.drain()
        self.gate.record_forced_turn.assert_called_once_with([latest.dedupe_key])

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
