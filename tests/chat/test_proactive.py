"""P5-3 主动接话启用：配置、退避、路由、命令与服务限制。"""

from __future__ import annotations

import copy
import asyncio
import unittest
from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import router, service as service_module, commands, message_adapter
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior import debounce, reply_gate
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender as sender_module
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import FORCED_FLUSH_SECONDS, PendingBatch
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config
from src.plugins.nonebot_plugin_fxbot.chat.behavior.reply_gate import (
    ReplyGate,
    IdleBackoff,
    talk_value_for,
    proactive_switch,
    set_proactive_switch,
)
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot12 import OneBotV12MessageAdapter

from .support import RouterUnitTests


def _pick(overrides: dict[str, object]) -> dict[str, object]:
    value = copy.deepcopy(SYSTEM_DEFAULTS["chat"]["reply_timing"])
    value.update(overrides)
    return value


class _Config:
    def __init__(self, reply_timing: dict[str, object]) -> None:
        self.reply_timing = reply_timing

    def get_system(self) -> dict[str, object]:
        return {"chat": {"reply_timing": self.reply_timing}}


def _patch_gate_config(reply_timing: dict[str, object]):
    return patch.object(reply_gate, "get_config_manager", return_value=_Config(reply_timing))


def _make_bot() -> SimpleNamespace:
    return SimpleNamespace(self_id="bot", adapter=SimpleNamespace(get_name=lambda: "test"))


def _make_event(text: str = "你好", *, group_id: str | None = "g1", message_id: str = "m1") -> SimpleNamespace:
    event = SimpleNamespace(user_id="user", group_id=group_id, message=[], text=text, message_id=message_id)
    event.get_message = lambda: event.message
    event.get_plaintext = lambda: event.text
    return event


def _request(trigger_kind: str = "proactive", *, text: str = "你好", message_id: str = "100") -> ChatRequest:
    return ChatRequest(
        session_id="tenant",
        user_id="user",
        group_id="g1",
        text=text,
        turn_id="turn",
        dedupe_key=f"msg:{message_id}",
        trigger_kind=trigger_kind,  # type: ignore[arg-type]
        message_id=message_id,
    )


class ConfigValidationTests(unittest.TestCase):
    """chat.reply_timing 里的主动接话默认值与显式校验。"""

    def test_defaults_validate(self) -> None:
        validate_system_config({"chat": copy.deepcopy(SYSTEM_DEFAULTS["chat"])})
        reply_timing = SYSTEM_DEFAULTS["chat"]["reply_timing"]
        self.assertEqual(reply_timing["talk_value"], 1.0)
        self.assertNotIn("enabled", reply_timing)
        self.assertNotIn("gate_mode", reply_timing)
        self.assertNotIn("proactive", SYSTEM_DEFAULTS["chat"])

    def test_invalid_values_are_rejected(self) -> None:
        cases = (
            {"talk_value": 1.5},
            {"enable_talk_value_rules": "yes"},
            {"talk_value_rules": [{"time": "09:00-25:00", "value": 1.0}]},
            {"talk_value_rules": [{"time": "09:00-18:00", "value": 2}]},
            {"no_action_backoff_start_count": 0},
            {"no_action_backoff_base_seconds": -1},
        )
        for override in cases:
            with self.subTest(override=override):
                chat = copy.deepcopy(SYSTEM_DEFAULTS["chat"])
                chat["reply_timing"] = _pick(override)
                with self.assertRaises(ValueError):
                    validate_system_config({"chat": chat})

    def test_reply_timing_must_be_object(self) -> None:
        chat = copy.deepcopy(SYSTEM_DEFAULTS["chat"])
        chat["reply_timing"] = None
        with self.assertRaises(ValueError):
            validate_system_config({"chat": chat})


class TalkValueTests(unittest.TestCase):
    """talk_value 的默认值与分时段规则。"""

    def test_hot_reload_and_rules(self) -> None:
        rules = [
            {"tenant": "", "time": "00:00-08:59", "value": 0.8},
            {"tenant": "", "time": "09:00-18:59", "value": 1.0},
        ]
        proactive = _pick({"talk_value": 0.5, "enable_talk_value_rules": True, "talk_value_rules": rules})
        with _patch_gate_config(proactive):
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)), 0.8)
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 8, 59, tzinfo=timezone.utc)), 0.8)
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 9, 0, tzinfo=timezone.utc)), 1.0)
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)), 0.5)
            proactive["talk_value"] = 0.2
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)), 0.2)

    def test_rules_disabled_uses_default(self) -> None:
        proactive = _pick({"talk_value": 0.25, "enable_talk_value_rules": False})
        with _patch_gate_config(proactive):
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)), 0.25)

    def test_tenant_rule_precedes_global(self) -> None:
        rules = [
            {"tenant": "", "time": "00:00-23:59", "value": 1.0},
            {"tenant": "t", "time": "00:00-23:59", "value": 0.3},
        ]
        with _patch_gate_config(_pick({"enable_talk_value_rules": True, "talk_value_rules": rules})):
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)), 0.3)
            self.assertAlmostEqual(talk_value_for("other", at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)), 1.0)

    def test_cross_midnight_rule(self) -> None:
        rules = [{"tenant": "", "time": "22:00-02:00", "value": 0.4}]
        with _patch_gate_config(_pick({"talk_value": 0.9, "enable_talk_value_rules": True, "talk_value_rules": rules})):
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 23, 30, tzinfo=timezone.utc)), 0.4)
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 1, 30, tzinfo=timezone.utc)), 0.4)
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)), 0.9)

    def test_empty_or_star_time_covers_all_day(self) -> None:
        for time_range in ("", "*", "  "):
            rules = [{"tenant": "", "time": time_range, "value": 0.4}]
            config = _pick({"talk_value": 0.9, "enable_talk_value_rules": True, "talk_value_rules": rules})
            with _patch_gate_config(config):
                for hour in (0, 9, 23):
                    at = datetime(2026, 1, 1, hour, 0, tzinfo=timezone.utc)
                    self.assertAlmostEqual(talk_value_for("t", at=at), 0.4)

    def test_time_range_precedes_all_day_rule(self) -> None:
        rules = [
            {"tenant": "", "time": "", "value": 0.4},
            {"tenant": "", "time": "09:00-18:59", "value": 1.0},
        ]
        with _patch_gate_config(_pick({"talk_value": 0.9, "enable_talk_value_rules": True, "talk_value_rules": rules})):
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)), 1.0)
            self.assertAlmostEqual(talk_value_for("t", at=datetime(2026, 1, 1, 20, 0, tzinfo=timezone.utc)), 0.4)

    def test_all_day_time_is_valid(self) -> None:
        chat = copy.deepcopy(SYSTEM_DEFAULTS["chat"])
        chat["reply_timing"] = _pick(
            {"talk_value_rules": [{"tenant": "", "time": "", "value": 0.5}, {"tenant": "", "time": "*", "value": 0.5}]}
        )
        validate_system_config({"chat": chat})


class IdleBackoffTests(unittest.TestCase):
    """连续空闲轮次的指数退避。"""

    def test_delay_grows_and_caps(self) -> None:
        backoff = IdleBackoff()
        backoff.record_idle(100, base=15, cap=300, start_count=2)
        self.assertEqual(backoff.delay(101, pending=0, bypass_pending=6), 0.0)
        backoff.record_idle(115, base=15, cap=300, start_count=2)
        self.assertEqual(backoff.delay(115, pending=0, bypass_pending=6), 15.0)
        backoff.record_idle(130, base=15, cap=300, start_count=2)
        self.assertEqual(backoff.delay(130, pending=0, bypass_pending=6), 30.0)
        for index in range(10):
            backoff.record_idle(200 + index, base=15, cap=300, start_count=2)
        self.assertLessEqual(backoff.delay(300, pending=0, bypass_pending=6), 300.0)

    def test_pending_bypass_and_reset(self) -> None:
        backoff = IdleBackoff()
        backoff.record_idle(100, base=15, cap=300, start_count=2)
        backoff.record_idle(100, base=15, cap=300, start_count=2)
        self.assertGreater(backoff.delay(100, pending=0, bypass_pending=6), 0.0)
        self.assertEqual(backoff.delay(100, pending=6, bypass_pending=6), 0.0)
        backoff.reset()
        self.assertEqual(backoff.delay(100, pending=0, bypass_pending=6), 0.0)


class ReplyGateStateTests(unittest.TestCase):
    """门控的待处理计数与退避状态。"""

    def setUp(self) -> None:
        self.gate = ReplyGate("tenant", clock=lambda: 1000.0)

    def test_pending_resets_on_trigger(self) -> None:
        self.gate.observe_message(["a"], ["你好"], now=1000)
        self.gate.observe_message(["b"], ["在吗"], now=1001)
        self.assertEqual(self.gate.snapshot()["backoff"]["pending"], 2)
        self.gate.mark_triggered()
        self.assertEqual(self.gate.snapshot()["backoff"]["pending"], 0)

    def test_idle_backoff_and_forced_reset(self) -> None:
        with _patch_gate_config(_pick({})):
            self.gate.record_idle(now=1000)
            self.assertEqual(self.gate.backoff_delay(now=1000), 0.0)
            self.gate.record_idle(now=1000)
            self.assertGreater(self.gate.backoff_delay(now=1000), 0.0)
            self.gate.record_forced_turn(["a"], now=1001)
            self.assertEqual(self.gate.backoff_delay(now=1001), 0.0)


class SwitchTests(unittest.IsolatedAsyncioTestCase):
    """群级开关只查一次库，写入后立即刷新缓存。"""

    def setUp(self) -> None:
        reply_gate._switch_cache.clear()

    async def test_read_once_and_set_updates_cache(self) -> None:
        state = SimpleNamespace(proactive_enabled=True)
        get = AsyncMock(side_effect=lambda tenant: state if tenant == "cache-read" else None)
        with (
            patch.object(reply_gate, "memory_ready", return_value=True),
            patch.object(reply_gate.chat_repository, "get_session_state", new=get),
        ):
            self.assertTrue(await proactive_switch("cache-read"))
            self.assertTrue(await proactive_switch("cache-read"))
            get.assert_awaited_once()
            self.assertIsNone(await proactive_switch("cache-missing"))
            self.assertEqual(get.await_count, 2)

    async def test_set_writes_and_caches(self) -> None:
        save = AsyncMock()
        with patch.object(reply_gate.chat_repository, "set_proactive_enabled", new=save):
            await set_proactive_switch("cache-set", False)
            self.assertFalse(await proactive_switch("cache-set"))
            save.assert_awaited_once()

    async def test_unready_database_does_not_cache(self) -> None:
        with patch.object(reply_gate, "memory_ready", return_value=False):
            self.assertIsNone(await proactive_switch("cache-unready"))


class _Observation:
    def __init__(self, would_trigger: bool) -> None:
        self.would_trigger = would_trigger


class _Gate:
    def __init__(self, would_trigger: bool = True, delay: float = 0.0) -> None:
        self.observation = _Observation(would_trigger)
        self.delay = delay
        self.observed: list[tuple[tuple[object, ...], dict[str, object]]] = []
        self.forced: list[tuple[object, ...]] = []
        self.triggered = 0
        self.pending_resets = 0
        self.idle = 0

    def observe_message(self, *args: object, **kwargs: object) -> _Observation:
        self.observed.append((args, kwargs))
        return self.observation

    def backoff_delay(self) -> float:
        return self.delay

    def mark_triggered(self) -> None:
        self.triggered += 1

    def reset_pending(self) -> None:
        self.pending_resets += 1

    def record_forced_turn(self, *args: object) -> None:
        self.forced.append(args)

    def record_idle(self) -> None:
        self.idle += 1


class _Registry:
    def __init__(self, gate: _Gate) -> None:
        self.gate = gate

    def get(self, _tenant: str) -> _Gate:
        return self.gate

    def peek(self, _tenant: str) -> _Gate:
        return self.gate


def _chat_cfg() -> dict[str, object]:
    return {
        "enabled": True,
        "max_context_size": 10,
        "group_allowlist": [],
        "private_enabled": False,
        "command_prefixes": ["/", "#"],
        "persona": "",
    }


class RouterEnforcementTests(RouterUnitTests):
    """普通群消息的路由只采样一次，通过后绕过 _match_context。"""

    def _patches(self, gate: _Gate, *, switch: bool | None = True) -> tuple[object, ...]:
        return (
            patch.object(router, "proactive_switch", new=AsyncMock(return_value=switch)),
            patch.object(router, "default_reply_gate_registry", _Registry(gate)),
            patch.object(router, "default_debounce_registry", debounce.DebounceRegistry()),
            patch.object(router, "_chat_cfg", return_value=_chat_cfg()),
            patch.object(router, "should_process_fxbot_message", return_value=True),
            patch.object(router, "_persona_trigger", return_value=""),
            patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
            patch.object(router, "_has_image", return_value=False),
        )

    async def test_gate_decision_is_deferred_until_scheduler(self) -> None:
        gate = _Gate()
        patches = self._patches(gate)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            event = _make_event()
            self.assertTrue(await router._context_rule(_make_bot(), event))
            self.assertFalse(getattr(event, router._PROACTIVE_ATTR, False))
            self.assertFalse(await router._ai_fallback_rule(_make_bot(), event))
        self.assertEqual(gate.observed, [])
        self.assertEqual(gate.triggered, 0)

    async def test_gate_fail_keeps_context_history(self) -> None:
        gate = _Gate(would_trigger=False)
        patches = self._patches(gate)
        with patches[0], patches[1], patches[2], patches[3], patches[4], patches[5], patches[6], patches[7]:
            event = _make_event()
            self.assertTrue(await router._context_rule(_make_bot(), event))
            self.assertFalse(getattr(event, router._PROACTIVE_ATTR, False))
            self.assertFalse(await router._ai_fallback_rule(_make_bot(), event))
        self.assertEqual(gate.triggered, 0)

    async def test_unset_switch_uses_default_enabled_and_off_blocks(self) -> None:
        for switch in (None, False):
            with self.subTest(switch=switch):
                gate = _Gate()
                patches = self._patches(gate, switch=switch)
                with (
                    patches[0],
                    patches[1],
                    patches[2],
                    patches[3],
                    patches[4],
                    patches[5],
                    patches[6],
                    patches[7],
                ):
                    event = _make_event()
                    self.assertTrue(await router._context_rule(_make_bot(), event))
                    self.assertFalse(getattr(event, router._PROACTIVE_ATTR, False))
                self.assertEqual(gate.triggered, 0)

    async def test_group_switch_off_blocks_and_backoff_blocks(self) -> None:
        for gate, switch in ((_Gate(), False), (_Gate(delay=10), True)):
            with self.subTest(switch=switch):
                patches = self._patches(gate, switch=switch)
                with (
                    patches[0],
                    patches[1],
                    patches[2],
                    patches[3],
                    patches[4],
                    patches[5],
                    patches[6],
                    patches[7],
                ):
                    self.assertTrue(await router._context_rule(_make_bot(), _make_event()))
                self.assertEqual(gate.triggered, 0)

    async def test_handle_uses_proactive_trigger_without_forced_expectation(self) -> None:
        gate = _Gate()
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            queue_message=AsyncMock(return_value=False),
            record_group_message=AsyncMock(),
            process_and_send=AsyncMock(return_value=False),
            ensure_hydrated=AsyncMock(),
        )
        request = _request("proactive")
        adapt = AsyncMock(return_value=request)
        with (
            patch.object(router, "chat_service", service),
            patch.object(router, "default_reply_gate_registry", _Registry(gate)),
            patch.object(router, "default_debounce_registry", debounce.DebounceRegistry()),
            patch.object(router, "adapt_message_event", new=adapt),
            patch.object(debounce.PendingBatch, "wait", new=AsyncMock()),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
            patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
        ):
            event = _make_event()
            setattr(event, router._PROACTIVE_ATTR, True)
            await router._handle_ai_fallback(_make_bot(), event, SimpleNamespace(finish=AsyncMock()))
        self.assertEqual(adapt.await_args.kwargs["trigger_kind"], "proactive")
        self.assertEqual(gate.forced, [])


class TriggerOrderTests(RouterUnitTests):
    """序号按到达顺序分配：适配慢的旧消息不能拿到更新的序号。"""

    async def test_slow_adaptation_does_not_take_the_newer_seq(self) -> None:
        store = ChatSessionStore()
        seen: dict[str, int] = {}
        first_started = asyncio.Event()
        release = asyncio.Event()

        async def process_and_send(request: ChatRequest, _runtime: object) -> bool:
            seen[str(request.message_id)] = request.trigger_seq
            return False

        async def adapt(bot: object, event: SimpleNamespace, *, trigger_kind: str) -> ChatRequest:
            if event.message_id == "m1":
                first_started.set()
                await release.wait()
            identity = message_adapter.event_identity(bot, event)
            return ChatRequest(
                session_id=identity.session_id,
                user_id="user",
                group_id=None,
                text="你好",
                turn_id=f"turn-{event.message_id}",
                dedupe_key=identity.dedupe_key,
                trigger_kind=trigger_kind,  # type: ignore[arg-type]
                message_id=event.message_id,
            )

        service = SimpleNamespace(
            session_store=store,
            ensure_hydrated=AsyncMock(),
            queue_message=AsyncMock(return_value=False),
            record_group_message=AsyncMock(),
            process_and_send=AsyncMock(side_effect=process_and_send),
        )
        matcher = SimpleNamespace(finish=AsyncMock())
        with (
            patch.object(router, "chat_service", service),
            patch.object(router, "default_reply_gate_registry", _Registry(_Gate())),
            patch.object(router, "default_debounce_registry", debounce.DebounceRegistry()),
            patch.object(router, "adapt_message_event", new=adapt),
            patch.object(debounce.PendingBatch, "wait", new=AsyncMock()),
            patch.object(router.default_runtime_factory, "create", return_value=object()),
            patch.object(message_adapter, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
        ):
            slow = asyncio.create_task(
                router._handle_ai_fallback(_make_bot(), _make_event(group_id=None, message_id="m1"), matcher)
            )
            await asyncio.wait_for(first_started.wait(), 1.0)
            await router._handle_ai_fallback(_make_bot(), _make_event(group_id=None, message_id="m2"), matcher)
            release.set()
            await asyncio.wait_for(slow, 1.0)
        self.assertEqual(seen, {"m1": 1, "m2": 2})


class DebounceProactiveTests(unittest.TestCase):
    """主动批次与 @ 的合并规则。"""

    def _batch(
        self, trigger_kind: str = "proactive", *, started: float = 100.0, deadline: float = 102.0
    ) -> PendingBatch:
        request = _request(trigger_kind)
        request.proactive = trigger_kind == "proactive"
        return PendingBatch(
            tenant="tenant",
            owner_id="user",
            requests=[request],
            started=started,
            deadline=deadline,
            trigger_seq=1,
            forced=True,
            forced_kind="" if trigger_kind == "proactive" else trigger_kind,
            has_proactive=request.proactive,
        )

    def test_mention_joins_proactive_batch_and_flushes_quickly(self) -> None:
        batch = self._batch()
        mention = _request("mention")
        mention.user_id = "other"
        with patch.object(debounce, "_now", return_value=100.0):
            self.assertTrue(batch.accept(mention, trigger=True))
        self.assertLessEqual(batch.deadline, 100.0 + FORCED_FLUSH_SECONDS)
        merged = batch.merge()
        self.assertEqual(merged.trigger_kind, "mention")
        # 有人 @ 时按普通轮回复，不能沿用主动轮的工具限制与「不发言就结束」
        self.assertFalse(merged.proactive)

    def test_proactive_joins_existing_batch_without_extending(self) -> None:
        batch = self._batch("mention", started=100.0, deadline=101.0)
        proactive = _request("proactive")
        with patch.object(debounce, "_now", return_value=100.5):
            self.assertTrue(batch.accept(proactive, trigger=False, extend=False))
        self.assertEqual(batch.deadline, 101.0)
        merged = batch.merge()
        self.assertEqual(merged.trigger_kind, "mention")
        self.assertFalse(merged.proactive)

    def test_proactive_only_batch_keeps_kind(self) -> None:
        batch = self._batch()
        context = _request("mention")
        context.proactive = False
        with patch.object(debounce, "_now", return_value=100.0):
            batch.accept(context, trigger=False)
        merged = batch.merge()
        self.assertEqual(merged.trigger_kind, "proactive")
        self.assertTrue(merged.proactive)


class ServiceRestrictionTests(unittest.IsolatedAsyncioTestCase):
    """主动轮的引用与工具限制。"""

    async def test_proactive_does_not_quote(self) -> None:
        request = _request("proactive")
        request.message_id = "100"
        runtime = SimpleNamespace(bot=object())
        self.assertEqual(sender_module.first_payload(None, request, runtime, "回复", []), "回复")
        request.proactive = False
        request.trigger_kind = "mention"
        built = object()
        with (
            patch.object(sender_module, "decide_quote", return_value=(True, 1.0)),
            patch.object(sender_module, "supports_reply", return_value=True),
            patch.object(sender_module, "build_message", return_value=built),
            patch.object(sender_module, "build_reply_segment", return_value=object()),
            patch.object(sender_module, "build_message_segment", return_value=object()),
        ):
            self.assertIs(sender_module.first_payload(None, request, runtime, "回复", []), built)

    async def test_proactive_tools_are_read_only(self) -> None:
        specs = [
            SimpleNamespace(name="web_search"),
            SimpleNamespace(name="like"),
            SimpleNamespace(name="mute_member"),
        ]
        with (
            patch.object(generator.default_registry, "list", return_value=specs),
            patch.object(
                generator.default_registry,
                "to_openai_tools",
                return_value=[{"function": {"name": spec.name}} for spec in specs],
            ),
            patch.object(generator, "tool_allowed", new=AsyncMock(return_value=True)),
            patch.object(generator, "web_runtime", return_value=SimpleNamespace(enabled=True)),
        ):
            names, tools = await generator.allowed_tools(SimpleNamespace(), proactive=True)
        self.assertEqual(names, {"web_search"})
        self.assertEqual([tool["function"]["name"] for tool in tools], ["web_search"])

    async def test_empty_proactive_turn_records_idle(self) -> None:
        service = ChatService(ChatSessionStore())
        service._process_locked = AsyncMock(return_value=ChatResponse(text="", epoch=0))  # type: ignore[method-assign]
        gate = _Gate()
        with patch.object(service_module, "default_reply_gate_registry", _Registry(gate)):
            self.assertFalse(await service.process_and_send(_request("proactive"), None))
        self.assertEqual(gate.idle, 1)

    async def test_empty_normal_turn_does_not_record_idle(self) -> None:
        service = ChatService(ChatSessionStore())
        service._process_locked = AsyncMock(return_value=ChatResponse(text="", epoch=0))  # type: ignore[method-assign]
        gate = _Gate()
        with patch.object(service_module, "default_reply_gate_registry", _Registry(gate)):
            self.assertFalse(await service.process_and_send(_request("mention"), None))
        self.assertEqual(gate.idle, 0)


class _Finished(Exception):
    pass


class _FinishMatcher:
    def __init__(self) -> None:
        self.messages: list[str] = []

    async def finish(self, message: str | None = None) -> None:
        self.messages.append(str(message or ""))
        raise _Finished


class CommandTests(unittest.IsolatedAsyncioTestCase):
    """#主动 查询与开关权限。"""

    def test_parse(self) -> None:
        self.assertEqual(commands._proactive_argument("#主动"), "")
        self.assertEqual(commands._proactive_argument("/主动 开"), "开")
        self.assertIsNone(commands._proactive_argument("#人格"))

    async def test_private_set_is_rejected(self) -> None:
        matcher = _FinishMatcher()
        with patch.object(commands, "session_id_for_event", return_value="tenant"):
            with self.assertRaises(_Finished):
                await commands._handle_proactive(_make_bot(), _make_event("#主动 开", group_id=None), matcher)  # type: ignore[arg-type]
        self.assertIn("只用于群聊", matcher.messages[-1])

    async def test_group_set_requires_manage_and_persists(self) -> None:
        matcher = _FinishMatcher()
        setter = AsyncMock()
        event = _make_event("#主动 开")
        with (
            patch.object(commands, "session_id_for_event", return_value="tenant"),
            patch.object(commands, "_can_manage", new=AsyncMock(return_value=False)),
        ):
            with self.assertRaises(_Finished):
                await commands._handle_proactive(_make_bot(), event, matcher)  # type: ignore[arg-type]
        self.assertIn("群主或管理员", matcher.messages[-1])

        with (
            patch.object(commands, "session_id_for_event", return_value="tenant"),
            patch.object(commands, "_can_manage", new=AsyncMock(return_value=True)),
            patch.object(commands, "set_proactive_switch", new=setter),
        ):
            with self.assertRaises(_Finished):
                await commands._handle_proactive(_make_bot(), event, matcher)  # type: ignore[arg-type]
        setter.assert_awaited_once_with("tenant", True)
        self.assertIn("已开启", matcher.messages[-1])

    async def test_query_reports_effective_state(self) -> None:
        matcher = _FinishMatcher()
        gate = SimpleNamespace(
            snapshot=lambda _limit: {"window": {"expected_replies": 1.5, "actual_replies": 1, "scored_rounds": 3}}
        )
        registry = SimpleNamespace(peek=lambda _tenant: gate)
        with (
            patch.object(commands, "session_id_for_event", return_value="tenant"),
            patch.object(commands, "proactive_switch", new=AsyncMock(return_value=True)),
            patch.object(commands, "talk_value_for", return_value=0.8),
            patch.object(commands, "default_reply_gate_registry", registry),
        ):
            with self.assertRaises(_Finished):
                await commands._handle_proactive(_make_bot(), _make_event("#主动"), matcher)  # type: ignore[arg-type]
        message = matcher.messages[-1]
        self.assertIn("本群主动接话：开启", message)
        self.assertIn("talk_value：0.80", message)
        self.assertIn("预计 1.50", message)


class ConsoleProactiveTests(unittest.IsolatedAsyncioTestCase):
    """控制台门控接口附带当前开关。"""

    async def test_payload_includes_settings(self) -> None:
        with (
            patch.object(chat_routes, "proactive_switch", new=AsyncMock(return_value=True)),
            patch.object(chat_routes, "talk_value_for", return_value=0.6),
            patch.object(chat_routes.default_reply_gate_registry, "peek", return_value=None),
        ):
            payload = await chat_routes.get_reply_gate("onebot:bot:group:g1", 10)
        self.assertTrue(payload["proactive"]["switch"])
        self.assertAlmostEqual(payload["proactive"]["talk_value"], 0.6)
        self.assertEqual(payload["backoff"]["pending"], 0)


if __name__ == "__main__":
    unittest.main()
