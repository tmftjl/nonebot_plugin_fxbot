"""P5-2 主动接话门控：概率、动态阈值、观察记录与路由接线。"""

from __future__ import annotations

import math
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.chat import router, service as service_module, message_adapter
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior import reply_gate as reply_gate_module
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender as sender_module
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import PendingBatch, DebounceRegistry
from src.plugins.nonebot_plugin_fxbot.chat.behavior.reply_gate import (
    ReplyGate,
    DynamicReplyGate,
    ReplyGateRegistry,
    ReplyLikelihoodInput,
    talk_value_for,
    talk_value_override,
    summarize_pending_texts,
    estimate_reply_probability,
)
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot12 import OneBotV12MessageAdapter

from .support import RouterUnitTests


class _Gate:
    """记录路由调用，不实现门控。"""

    def __init__(self) -> None:
        self.observed: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.forced: list[tuple[object, ...]] = []
        self.replies = 0

    def observe_message(self, *args: object, **kwargs: object) -> None:
        self.observed.append((args, kwargs))

    def record_forced_turn(self, *args: object, **kwargs: object) -> None:
        self.forced.append(args)

    def record_reply(self, *args: object, **kwargs: object) -> None:
        self.replies += 1

    def reset_pending(self) -> None:
        return None


class _Registry:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate

    def get(self, _tenant: str) -> _Gate:
        return self.gate


def make_bot() -> SimpleNamespace:
    """构造不启动平台连接的 Bot。"""
    return SimpleNamespace(self_id="bot", adapter=SimpleNamespace(get_name=lambda: "test"))


def make_event(**values: object) -> SimpleNamespace:
    fields: dict[str, object] = {"user_id": "user", "group_id": "group", "message": [], "text": "你好"}
    fields.update(values)
    event = SimpleNamespace(**fields)
    event.get_message = lambda: event.message
    event.get_plaintext = lambda: event.text
    return event


def _request(**values: object) -> ChatRequest:
    fields: dict[str, object] = {
        "session_id": "tenant",
        "user_id": "user",
        "group_id": "group",
        "text": "你好",
        "turn_id": "turn",
        "dedupe_key": "msg:1",
        "trigger_kind": "mention",
        "message_id": "100",
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


class LikelihoodTests(unittest.TestCase):
    """概率函数与文本摘要。"""

    def test_text_summary(self) -> None:
        self.assertEqual(summarize_pending_texts(["[图片]"]), (False, False, True))
        self.assertEqual(summarize_pending_texts(["[图片]", "在吗？"]), (False, True, False))
        for text in ("@bob 你好", "bob@example.com", "普通@文本"):
            with self.subTest(text=text):
                self.assertFalse(summarize_pending_texts([text])[0])
        self.assertEqual(summarize_pending_texts(["你好"], at_other=True), (True, False, False))
        self.assertEqual(summarize_pending_texts([]), (False, False, False))

    def test_probability_matches_published_formula(self) -> None:
        snapshot = ReplyLikelihoodInput(
            mention_bot=False,
            at_other=False,
            has_question_mark=True,
            placeholder_only=False,
            recent_self_ratio=0.0,
            recent_message_count=3,
            seconds_since_bot_message=100.0,
            pending_count=2,
        )
        logit = -1.2005 + 0.4807 - 0.0367 * 3 + 0.0571 * math.log1p(100) + 0.1012 * 2
        self.assertAlmostEqual(estimate_reply_probability(snapshot), 1 / (1 + math.exp(-logit)))

    def test_placeholder_only_is_less_likely(self) -> None:
        base = ReplyLikelihoodInput(False, False, False, False, 0.0, 1, 3600.0, 1)
        placeholder = ReplyLikelihoodInput(False, False, False, True, 0.0, 1, 3600.0, 1)
        self.assertLess(estimate_reply_probability(placeholder), estimate_reply_probability(base))


class MentionFlagTests(unittest.TestCase):
    """结构化提及标记与门控特征。"""

    def test_structured_mentions(self) -> None:
        bot = make_bot()
        bot.self_info = SimpleNamespace(id="openid")
        for targets, text, group_id, to_me, expected in (
            ([], "bob@example.com", "group", False, (False, False)),
            ([], "普通@文本", "group", False, (False, False)),
            (["other"], "你好", "group", False, (False, True)),
            (["bot"], "你好", "group", False, (True, False)),
            (["openid"], "你好", "group", False, (True, False)),
            (["all"], "你好", "group", False, (False, False)),
            (["bot", "other", "all"], "你好", "group", False, (True, True)),
            ([], "你好", "group", True, (True, False)),
            (["other"], "你好", None, False, (False, False)),
        ):
            with self.subTest(targets=targets, text=text, group_id=group_id, to_me=to_me):
                event = make_event(
                    message=[SimpleNamespace(type="mention", data={"user_id": target}) for target in targets],
                    text=text,
                    group_id=group_id,
                    to_me=to_me,
                )
                with patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()):
                    has_at_bot, at_other = message_adapter.mention_flags(bot, event)
                self.assertEqual((has_at_bot, at_other), expected)
                gate = ReplyGate()
                observation = gate.observe_message(["m1"], [text], has_at_bot=has_at_bot, at_other=at_other)
                self.assertEqual(observation.features["mention_bot"], expected[0])
                self.assertEqual(observation.features["at_other"], expected[1])
                duplicate = gate.observe_message(["m1"], [text], has_at_bot=has_at_bot, at_other=at_other)
                self.assertEqual(duplicate.features["at_other"], expected[1])

    def test_adapter_failure_has_no_mentions(self) -> None:
        with patch.object(message_adapter, "get_platform_adapter", side_effect=RuntimeError):
            self.assertEqual(message_adapter.mention_flags(make_bot(), make_event(to_me=True)), (False, False))
        adapter = OneBotV12MessageAdapter()
        with (
            patch.object(message_adapter, "get_platform_adapter", return_value=adapter),
            patch.object(adapter, "mention_targets", side_effect=RuntimeError),
        ):
            self.assertEqual(message_adapter.mention_flags(make_bot(), make_event(to_me=True)), (False, False))


class DynamicGateTests(unittest.TestCase):
    """动态阈值与窗口反馈。"""

    def test_frequency_one_always_passes(self) -> None:
        gate = DynamicReplyGate()
        gate.record_proactive_demand(0.2, now=100)
        decision = gate.evaluate(0.05, 1.0, now=100)
        self.assertTrue(decision.should_trigger)
        self.assertEqual((decision.threshold, decision.keep_ratio), (0.0, 1.0))

    def test_frequency_zero_always_blocks(self) -> None:
        gate = DynamicReplyGate()
        gate.record_proactive_demand(0.9, now=100)
        decision = gate.evaluate(0.99, 0.0, now=100)
        self.assertFalse(decision.should_trigger)
        self.assertEqual((decision.threshold, decision.keep_ratio), (1.0, 0.0))

    def test_static_threshold_before_enough_scores(self) -> None:
        self.assertAlmostEqual(DynamicReplyGate._resolve_threshold([], 0.0, 0.5), 0.336)
        self.assertAlmostEqual(DynamicReplyGate._resolve_threshold([0.4], 0.4, 0.5), 0.336)

    def test_threshold_from_calibrated_scores(self) -> None:
        scores = [0.9] * 10 + [0.1] * 10
        self.assertAlmostEqual(DynamicReplyGate._resolve_threshold(scores, 10.0, 0.5), 0.9)
        self.assertAlmostEqual(DynamicReplyGate._resolve_threshold(scores, 10.0, 0.95), 0.1)

    def test_feedback_only_tightens(self) -> None:
        gate = DynamicReplyGate()
        gate.record_proactive_demand(1.0, now=100)
        relaxed = gate.evaluate(0.5, 0.6, now=100)
        self.assertAlmostEqual(relaxed.keep_ratio, 0.6)
        for offset in range(3):
            gate.record_reply(now=101 + offset)
        tightened = gate.evaluate(0.5, 0.6, now=105)
        self.assertEqual(tightened.keep_ratio, 0.0)
        self.assertFalse(tightened.should_trigger)

    def test_round_only_counted_once(self) -> None:
        gate = DynamicReplyGate()
        gate.record_proactive_demand(0.2, now=100)
        gate.record_proactive_demand(0.8, now=120)
        self.assertEqual(gate.stats(now=120)["scored_rounds"], 1)
        self.assertAlmostEqual(gate.stats(now=120)["expected_proactive"], 0.8)
        gate.record_proactive_demand(0.3, now=150)
        self.assertEqual(gate.stats(now=150)["scored_rounds"], 2)

    def test_forced_turn_closes_round_and_expires(self) -> None:
        gate = DynamicReplyGate()
        gate.record_proactive_demand(0.4, now=100)
        gate.record_forced_turn(now=101)
        gate.record_proactive_demand(0.2, now=102)
        stats = gate.stats(now=102)
        self.assertEqual(stats["scored_rounds"], 2)
        self.assertAlmostEqual(stats["expected_forced"], 0.91)
        expired = gate.stats(now=102 + 3601)
        self.assertEqual(expired["expected_replies"], 0.0)

    def test_message_ids_are_counted_once(self) -> None:
        gate = DynamicReplyGate()
        self.assertEqual(gate.take_uncounted_message_ids(["a", "b", "a", ""]), ["a", "b"])
        self.assertEqual(gate.take_uncounted_message_ids(["a", "c"]), ["c"])


class ReplyGateTests(unittest.TestCase):
    """单个租户的观察记录。"""

    def setUp(self) -> None:
        self.now = 1000.0
        self.gate = ReplyGate(clock=lambda: self.now)

    def test_observation_carries_probability_and_features(self) -> None:
        observation = self.gate.observe_message(["m1"], ["在吗？"], now=1000)
        self.assertGreater(observation.probability, 0.0)
        self.assertLessEqual(observation.probability, 1.0)
        self.assertTrue(observation.features["has_question_mark"])
        self.assertEqual(observation.features["pending_count"], 1)
        self.assertEqual(observation.features["recent_message_count"], 1)
        snapshot = self.gate.snapshot()
        self.assertEqual(len(snapshot["observations"]), 1)
        self.assertEqual(snapshot["window"]["scored_rounds"], 1)
        self.assertEqual(sum(bucket["count"] for bucket in snapshot["distribution"]), 1)

    def test_recent_self_ratio_and_seconds_since_bot(self) -> None:
        self.gate.record_reply(now=900)
        observation = self.gate.observe_message(["m1"], ["你好"], now=1000)
        self.assertAlmostEqual(observation.features["recent_self_ratio"], 0.5)
        self.assertAlmostEqual(observation.features["seconds_since_bot_message"], 100.0)
        self.assertEqual(observation.features["recent_message_count"], 2)

    def test_stale_events_do_not_affect_features(self) -> None:
        self.gate.record_reply(now=100)
        observation = self.gate.observe_message(["m1"], ["你好"], now=1000)
        self.assertEqual(observation.features["recent_self_ratio"], 0.0)
        self.assertEqual(observation.features["seconds_since_bot_message"], 900.0)

    def test_last_reply_time_survives_window_and_event_capacity(self) -> None:
        """上次发言不随活跃度窗口或事件容量淘汰。"""
        self.gate.record_reply(now=100)
        self.assertEqual(self.gate._recent(400)[2], 300.0)
        self.assertEqual(self.gate._recent(401)[2], 301.0)
        self.assertEqual(self.gate._recent(5000)[2], 4900.0)
        self.gate.record_reply(now=5000)
        for index in range(2100):
            self.gate.record_message(f"m{index}", now=5001)
        self.assertEqual(self.gate._recent(5002)[2], 2.0)

    def test_growing_batch_counts_each_message_once(self) -> None:
        """积压批次重评不重复累计活跃度、pending 或轮次文本。"""
        for size in range(1, 21):
            observation = self.gate.observe_message(
                [f"m{index}" for index in range(size)], ["你好"] * size, now=1000 + size
            )
            self.assertEqual(observation.features["recent_message_count"], size)
            self.assertEqual(observation.features["pending_count"], size)
            self.assertEqual(self.gate._pending, size)
            self.assertEqual(len(self.gate._round_texts), size)
        self.assertAlmostEqual(observation.probability, 0.635778956, places=7)
        self.assertAlmostEqual(self.gate._gate.stats(1020)["expected_proactive"], observation.probability)

    def test_ingress_and_repeated_evaluation_do_not_refresh_old_events(self) -> None:
        """预先记录的接收时间不会被评分刷新，乱序到达也能正确过期。"""
        self.gate.record_message("new", now=1400)
        self.gate.record_message("old", now=1000)
        observation = self.gate.observe_message(["old", "new"], ["旧消息", "新消息"], now=1400)
        self.assertEqual(observation.features["recent_message_count"], 1)
        duplicate = self.gate.observe_message(["old", "new"], ["旧消息", "新消息"], now=1401)
        self.assertEqual(duplicate.features["recent_message_count"], 1)

    def test_new_round_only_includes_new_message_texts(self) -> None:
        """跨轮次的旧积压只参与放行评分，不参与本轮预计回复数。"""
        self.gate.observe_message(["old"], ["旧问题？"], now=1000)
        observation = self.gate.observe_message(["old", "new"], ["旧问题？", "新消息"], now=1400)
        self.assertEqual(self.gate._round_texts, ["新消息"])
        self.assertTrue(observation.features["has_question_mark"])
        self.assertEqual(observation.features["recent_message_count"], 1)

    def test_duplicate_ids_within_batch_are_counted_once(self) -> None:
        """同一批内重复 ID 也只对应一条消息。"""
        observation = self.gate.observe_message(["m1", "m1"], ["你好", "你好"], now=1000)
        self.assertEqual(observation.features["recent_message_count"], 1)
        self.assertEqual(observation.features["pending_count"], 1)
        self.assertEqual(self.gate._round_texts, ["你好"])

    def test_ingress_does_not_consume_forced_demand_identity(self) -> None:
        """入站活跃度和强制预计回复数各自去重，互不吞计数。"""
        self.gate.record_message("m1", now=1000)
        self.gate.record_forced_turn(["m1"], now=1001)
        self.gate.record_forced_turn(["m1"], now=1002)
        self.assertEqual(self.gate._recent(1002)[1], 1)
        self.assertAlmostEqual(self.gate._gate.stats(1002)["expected_forced"], 0.91)

    def test_duplicate_message_does_not_add_demand(self) -> None:
        first = self.gate.observe_message(["m1"], ["你好"], now=1000)
        self.gate.observe_message(["m1"], ["你好"], now=1005)
        window = self.gate.snapshot()["window"]
        self.assertEqual(window["scored_rounds"], 1)
        self.assertAlmostEqual(window["expected_proactive"], first.probability)

    def test_round_preserves_and_resets_other_mentions(self) -> None:
        """轮次累计保留他人提及，新轮次不继承该标记。"""
        self.gate.observe_message(["m1"], ["你好"], at_other=True, now=1000)
        with patch.object(self.gate, "_likelihood", wraps=self.gate._likelihood) as likelihood:
            self.gate.observe_message(["m2"], ["在吗"], now=1001)
            self.assertTrue(likelihood.call_args_list[0].args[2])
            self.assertFalse(likelihood.call_args_list[1].args[2])
            self.gate.mark_triggered()
            self.gate.observe_message(["m3"], ["你好"], now=1002)
            self.assertFalse(likelihood.call_args_list[2].args[2])

    def test_forced_turn_is_counted_once(self) -> None:
        self.gate.record_forced_turn(["m1"], now=1000)
        self.gate.record_forced_turn(["m1"], now=1001)
        window = self.gate.snapshot()["window"]
        self.assertAlmostEqual(window["expected_forced"], 0.91)
        self.assertEqual(window["actual_replies"], 0)

    def test_observations_are_bounded(self) -> None:
        for index in range(250):
            self.gate.observe_message([f"m{index}"], ["你好"], now=1000 + index)
        self.assertEqual(len(self.gate.snapshot()["observations"]), 200)


class RegistryTests(unittest.TestCase):
    """租户状态隔离与清理。"""

    def test_tenant_isolation_and_clear(self) -> None:
        registry = ReplyGateRegistry()
        self.assertIsNone(registry.peek("a"))
        self.assertIs(registry.get("a"), registry.get("a"))
        self.assertIsNot(registry.get("a"), registry.get("b"))
        registry.get("a").observe_message(["m"], ["你好"])
        self.assertEqual(len(registry.peek("a").snapshot()["observations"]), 1)  # type: ignore[union-attr]
        registry.clear("a")
        self.assertIsNone(registry.peek("a"))


class RouterWiringTests(RouterUnitTests):
    """路由在普通消息和强制触发上的观察点。"""

    async def test_proactive_gate_passes_mention_flags(self) -> None:
        """路由向门控传递结构化提及，私聊不标记他人提及。"""
        for group_id, expected in (("group", True), (None, False)):
            with self.subTest(group_id=group_id):
                gate = _Gate()
                event = make_event(
                    group_id=group_id,
                    message=[SimpleNamespace(type="mention", data={"user_id": "other"})],
                )
                with (
                    patch.object(router, "default_reply_gate_registry", _Registry(gate)),
                    patch.object(router, "proactive_switch", new=AsyncMock(return_value=False)),
                    patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
                ):
                    self.assertFalse(await router._proactive_gate(make_bot(), event, text="你好", has_image=False))
                self.assertEqual(gate.observed[0][1], {"has_at_bot": False, "at_other": expected})

    async def test_context_message_is_observed_without_reply(self) -> None:
        gate = _Gate()
        service = SimpleNamespace(session_store=ChatSessionStore(), record_group_message=AsyncMock())
        cfg = {
            "enabled": True,
            "max_context_size": 10,
            "group_allowlist": [],
            "private_enabled": False,
            "persona": "",
        }
        with (
            patch.object(router, "proactive_switch", AsyncMock(return_value=False)),
            patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
            patch.object(router, "chat_service", service),
            patch.object(router, "default_reply_gate_registry", _Registry(gate)),
            patch.object(router, "default_debounce_registry", DebounceRegistry()),
            patch.object(router, "collect_mentions", new=AsyncMock(return_value=[])),
            patch.object(router, "_chat_cfg", return_value=cfg),
            patch.object(router, "should_process_fxbot_message", return_value=True),
            patch.object(router, "_persona_trigger", return_value=""),
        ):
            event = make_event(message_id="message")
            self.assertTrue(await router._context_rule(make_bot(), event))
            await router._handle_context(make_bot(), event)
        self.assertEqual(gate.observed, [])
        service.record_group_message.assert_awaited_once()

    async def test_group_trigger_records_forced_turn_but_private_does_not(self) -> None:
        for group_id, kind, expected in (("group", "mention", 1), (None, "private", 0)):
            with self.subTest(kind=kind):
                gate = _Gate()
                service = SimpleNamespace(
                    session_store=ChatSessionStore(),
                    queue_message=AsyncMock(return_value=False),
                    record_group_message=AsyncMock(),
                    process_and_send=AsyncMock(return_value=False),
                    ensure_hydrated=AsyncMock(),
                )
                request = _request(group_id=group_id, trigger_kind=kind)
                with (
                    patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
                    patch.object(router, "chat_service", service),
                    patch.object(router, "adapt_message_event", new=AsyncMock(return_value=request)),
                    patch.object(router, "default_reply_gate_registry", _Registry(gate)),
                    patch.object(router, "default_debounce_registry", DebounceRegistry()),
                    patch.object(PendingBatch, "wait", new=AsyncMock()),
                    patch.object(router.default_runtime_factory, "create", return_value=object()),
                ):
                    event = make_event(group_id=group_id, to_me=group_id is not None, message_id="message")
                    await router._handle_ai_fallback(make_bot(), event, SimpleNamespace(finish=AsyncMock()))
                self.assertEqual(len(gate.forced), 0)
                self.assertEqual(request.trigger_kind, kind)


class _Config:
    """只提供发送路径需要的配置。"""

    def get_system(self) -> dict[str, object]:
        return {
            "chat": {
                "max_context_size": 10,
                "behavior": {"group_quote_frequency": "never", "private_quote_frequency": "never"},
            }
        }


class _Matcher:
    async def send(self, _payload: object) -> None:
        return None


class ServiceReplyTests(unittest.IsolatedAsyncioTestCase):
    """只有群聊实际发出回复时才计入窗口。"""

    async def _send(self, group_id: str | None) -> _Gate:
        gate = _Gate()
        request = _request(group_id=group_id)
        response = ChatResponse(text="回复", epoch=0, metadata={"reply_parts": ["回复"]})
        with (
            patch.object(service_module, "get_config_manager", return_value=_Config()),
            patch.object(sender_module, "decide_quote", return_value=(False, 0.0)),
            patch.object(sender_module, "default_reply_gate_registry", _Registry(gate)),
            patch.object(sender_module, "typing_time", return_value=0.0),
            patch.object(service_module.memory_state, "is_ready", return_value=False),
        ):
            sent = await ChatService(ChatSessionStore())._send_parts(
                request, response, service_module.ToolRuntime(), _Matcher()
            )
        self.assertTrue(sent)
        return gate

    async def test_group_reply_is_counted(self) -> None:
        self.assertEqual((await self._send("group")).replies, 1)

    async def test_private_reply_is_not_counted(self) -> None:
        self.assertEqual((await self._send(None)).replies, 0)

    async def test_group_reply_is_counted_without_history(self) -> None:
        """关闭历史只影响存储，不影响实际回复反馈。"""
        with patch.object(sender_module, "history_enabled", return_value=False):
            self.assertEqual((await self._send("group")).replies, 1)


class GateConsoleTests(unittest.IsolatedAsyncioTestCase):
    """控制台读取内存观察。"""

    async def test_empty_and_populated_payload(self) -> None:
        empty = await chat_routes.get_reply_gate("gate-empty-tenant", 100)
        self.assertFalse(empty["calibrated"])
        self.assertEqual(empty["observations"], [])
        self.assertEqual(empty["window"]["scored_rounds"], 0)

        gate = ReplyGate(clock=lambda: 5000.0)
        gate.observe_message(["m1"], ["在吗？"], now=4000)
        with patch.object(chat_routes.default_reply_gate_registry, "peek", return_value=gate):
            payload = await chat_routes.get_reply_gate("tenant", 100)
        self.assertFalse(payload["calibrated"])
        self.assertEqual(len(payload["observations"]), 1)
        self.assertTrue(payload["observations"][0]["occurred_at"].endswith("+08:00"))
        self.assertEqual(sum(bucket["count"] for bucket in payload["distribution"]), 1)

    async def test_tenant_is_required(self) -> None:
        with self.assertRaises(HTTPException):
            await chat_routes.get_reply_gate("  ", 100)


class TalkValueOverrideTests(unittest.TestCase):
    """会话级回复频率覆盖优先级与容错。"""

    def test_override_takes_precedence(self) -> None:
        with (
            patch.object(reply_gate_module, "get_binding", return_value="0.25"),
            patch.object(
                reply_gate_module,
                "reply_timing_config",
                return_value={"talk_value": 0.9, "enable_talk_value_rules": False},
            ),
        ):
            self.assertAlmostEqual(talk_value_for("tenant"), 0.25)

    def test_invalid_override_falls_back_to_global(self) -> None:
        cfg = {"talk_value": 0.6, "enable_talk_value_rules": False}
        for raw in ("", "bad"):
            with self.subTest(raw=raw):
                with (
                    patch.object(reply_gate_module, "get_binding", return_value=raw),
                    patch.object(reply_gate_module, "reply_timing_config", return_value=cfg),
                ):
                    self.assertIsNone(talk_value_override("tenant"))
                    self.assertAlmostEqual(talk_value_for("tenant"), 0.6)

    def test_override_is_clamped(self) -> None:
        with patch.object(reply_gate_module, "get_binding", return_value="1.5"):
            self.assertEqual(talk_value_override("tenant"), 1.0)
        with patch.object(reply_gate_module, "get_binding", return_value="-0.2"):
            self.assertEqual(talk_value_override("tenant"), 0.0)


class SessionConsoleTests(unittest.IsolatedAsyncioTestCase):
    """控制台读取与保存会话设置。"""

    async def test_clear_session_reports_persistence_and_focus_failures(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.focus import focus_mode_manager

        for ready, focus_error, storage_error in (
            (True, False, False),
            (False, False, False),
            (True, True, False),
            (True, False, True),
        ):
            with self.subTest(ready=ready, focus_error=focus_error, storage_error=storage_error):
                service = ChatService(ChatSessionStore())
                clear = AsyncMock(return_value=1)
                if storage_error:
                    clear.side_effect = RuntimeError("storage failed")
                with (
                    patch.object(service_module.memory_state, "is_ready", return_value=ready),
                    patch.object(service_module.history_module, "clear_tenant", clear),
                    patch.object(
                        focus_mode_manager,
                        "clear_context",
                        AsyncMock(side_effect=RuntimeError("focus failed") if focus_error else None),
                    ),
                ):
                    result = await service.clear_session("test:clear-status")
                self.assertEqual(result, ready and not focus_error and not storage_error)

    async def test_session_settings_reports_bound_and_effective(self) -> None:
        def binding(kind: str, _tenant: str) -> str:
            return "p1" if kind == chat_routes.PROVIDER else ""

        with (
            patch.object(
                chat_routes,
                "get_config_manager",
                return_value=SimpleNamespace(get_system=lambda: {"chat": {"provider": "p2", "persona": "global"}}),
            ),
            patch.object(chat_routes.provider_manager, "list_providers_by_type", return_value=["p2", "p1"]),
            patch.object(chat_routes, "get_binding", side_effect=binding),
            patch.object(chat_routes, "load_personas", return_value={"小鱼": object()}),
            patch.object(chat_routes, "effective_persona_name", return_value="小鱼"),
            patch.object(chat_routes, "talk_value_override", return_value=0.3),
            patch.object(chat_routes, "talk_value_for", return_value=0.3),
            patch.object(chat_routes, "proactive_switch", AsyncMock(return_value=True)),
        ):
            payload = await chat_routes._session_settings("tenant")

        self.assertEqual(payload["provider"], {"bound": "p1", "effective": "p1", "options": ["p1", "p2"]})
        self.assertEqual(payload["persona"], {"bound": "", "effective": "小鱼", "options": ["小鱼"]})
        self.assertEqual(payload["talk_value"], {"bound": 0.3, "effective": 0.3})
        self.assertEqual(payload["proactive"], {"switch": True})

    async def test_persona_change_clears_history(self) -> None:
        writes: list[tuple[str, str, str | None]] = []

        def binding(kind: str, _tenant: str) -> str:
            return "old" if kind == chat_routes.PERSONA else ""

        with (
            patch.object(chat_routes, "load_personas", return_value={"old": object(), "new": object()}),
            patch.object(chat_routes.provider_manager, "list_providers_by_type", return_value=["p1"]),
            patch.object(chat_routes, "get_binding", side_effect=binding),
            patch.object(chat_routes, "set_binding", side_effect=lambda *args: writes.append(args)),
            patch.object(
                chat_routes,
                "_session_settings",
                AsyncMock(return_value={"tenant": "tenant"}),
            ),
            patch.object(chat_routes.chat_service, "clear_session", AsyncMock(return_value=True)) as clear,
        ):
            result = await chat_routes.save_session_settings(
                {"tenant": "tenant", "persona": "new", "provider": "p1", "talk_value": 0.4}
            )

        clear.assert_awaited_once_with("tenant")
        self.assertTrue(result["history_cleared"])
        self.assertIn((chat_routes.PERSONA, "tenant", "new"), writes)
        self.assertIn((chat_routes.PROVIDER, "tenant", "p1"), writes)
        self.assertIn((chat_routes.TALK_VALUE, "tenant", "0.4"), writes)

    async def test_persona_clear_failure_does_not_save_bindings(self) -> None:
        for failure in (False, RuntimeError("internal error")):
            with self.subTest(failure=failure):
                clear = AsyncMock(return_value=False)
                if isinstance(failure, Exception):
                    clear.side_effect = failure
                with (
                    patch.object(chat_routes, "load_personas", return_value={"new": object()}),
                    patch.object(chat_routes.provider_manager, "list_providers_by_type", return_value=[]),
                    patch.object(chat_routes, "get_binding", return_value="old"),
                    patch.object(chat_routes, "set_binding") as write,
                    patch.object(chat_routes.chat_service, "clear_session", clear),
                ):
                    with self.assertRaises(HTTPException) as raised:
                        await chat_routes.save_session_settings({"tenant": "tenant", "persona": "new"})
                    self.assertEqual(raised.exception.status_code, 503)
                    self.assertNotIn("internal error", raised.exception.detail)
                    write.assert_not_called()

    async def test_unchanged_persona_keeps_history(self) -> None:
        with (
            patch.object(chat_routes, "load_personas", return_value={"same": object()}),
            patch.object(chat_routes.provider_manager, "list_providers_by_type", return_value=[]),
            patch.object(chat_routes, "get_binding", return_value="same"),
            patch.object(chat_routes, "set_binding"),
            patch.object(chat_routes, "_session_settings", AsyncMock(return_value={"tenant": "tenant"})),
            patch.object(chat_routes.chat_service, "clear_session", AsyncMock()) as clear,
        ):
            result = await chat_routes.save_session_settings({"tenant": "tenant", "persona": "same"})

        clear.assert_not_awaited()
        self.assertFalse(result["history_cleared"])

    async def test_unknown_provider_is_rejected(self) -> None:
        with (
            patch.object(chat_routes, "load_personas", return_value={"same": object()}),
            patch.object(chat_routes.provider_manager, "list_providers_by_type", return_value=["p1"]),
            patch.object(chat_routes, "get_binding", return_value=""),
            patch.object(chat_routes, "set_binding") as write,
        ):
            with self.assertRaises(HTTPException):
                await chat_routes.save_session_settings({"tenant": "tenant", "provider": "missing"})
        write.assert_not_called()


if __name__ == "__main__":
    unittest.main()
