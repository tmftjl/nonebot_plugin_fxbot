"""P5-4 情绪、熟悉度与行为样本接入 Planner。"""

from __future__ import annotations

import copy
import unittest
from uuid import uuid4
from typing import Any
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import mood as mood_module, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.types import MentionRef, ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory import migrate, repository as repository_module
from src.plugins.nonebot_plugin_fxbot.chat.planner import Plan, build_prompt
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import (
    ChatState,
    ChatMessage,
    ChatBehavior,
    ChatBehaviorSample,
    utc_now_naive,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config


class _FakeConfig:
    """只提供 chat 配置；mood 缺省时视为启用。"""

    def __init__(self, mood: dict[str, Any] | None = None, **chat: Any) -> None:
        self.chat = {"mood": mood if mood is not None else {"enabled": True, "decay_per_day": 0.1}, **chat}

    def get_system(self) -> dict[str, Any]:
        return {"chat": self.chat}


class _Provider:
    """规划与回复共用同一假 Provider。"""

    provider_id = "fake"

    def get_model(self) -> str:
        return "fake-model"


def _request(**overrides: Any) -> ChatRequest:
    values: dict[str, Any] = {
        "session_id": "group:1",
        "user_id": "10001",
        "group_id": "1",
        "text": "在吗",
        "turn_id": f"turn-{uuid4().hex}",
        "dedupe_key": f"msg-{uuid4().hex}",
        "trigger_kind": "mention",
    }
    values.update(overrides)
    return ChatRequest(**values)


class MoodLineTests(unittest.TestCase):
    """状态行与场景标签的纯函数行为。"""

    def test_mood_thresholds(self) -> None:
        user = ChatState(tenant="t", subject_id="u")
        cases = (
            (0.3, "心情不错"),
            (0.29, "心情平静"),
            (-0.3, "心情低落"),
            (-0.29, "心情平静"),
        )
        for valence, expected in cases:
            with self.subTest(valence=valence):
                mood = ChatState(tenant="t", subject_id="", valence=valence)
                self.assertEqual(mood_module.mood_line(mood, user), f"现在{expected}；对当前用户熟悉度陌生。")

    def test_familiarity_thresholds(self) -> None:
        cases = ((0.8, "很熟"), (0.79, "熟悉"), (0.5, "熟悉"), (0.49, "一般"), (0.2, "一般"), (0.19, "陌生"))
        for familiarity, expected in cases:
            with self.subTest(familiarity=familiarity):
                user = ChatState(tenant="t", subject_id="u", familiarity=familiarity)
                self.assertEqual(mood_module.mood_line(None, user), f"现在心情平静；对当前用户熟悉度{expected}。")

    def test_missing_rows_use_neutral_defaults(self) -> None:
        line = mood_module.mood_line(None, None)
        self.assertEqual(line, "现在心情平静；对当前用户熟悉度陌生。")
        self.assertLessEqual(len(line), 50)

    def test_scene_tags(self) -> None:
        request = _request(text="这是什么？", images=["img"], quoted_text="引用", batched=True)
        request.mentions = [MentionRef(user_id="20001", name="小红")]
        self.assertEqual(
            mood_module.scene_tags(request), ["mention", "question", "image", "quote", "mention_other", "batched"]
        )
        plain = _request(text="在吗", trigger_kind="private", group_id=None)
        self.assertEqual(mood_module.scene_tags(plain), ["private"])

    def test_behavior_line(self) -> None:
        rows = [
            ChatBehavior(
                tenant="t",
                scene_hash="h1",
                scene_tags='["mention","question"]',
                action="text_reply",
                confidence=0.75,
                count=8,
            ),
            ChatBehavior(
                tenant="t", scene_hash="h2", scene_tags="not-json", action="no_reply", confidence=0.0, count=2
            ),
        ]
        line = mood_module.behavior_line(rows)
        self.assertIn("mention+question → text_reply（8 次，续聊率 75%）", line)
        self.assertIn("一般 → no_reply（2 次，续聊率 0%）", line)
        self.assertTrue(line.startswith("行为参考："))


class PlannerHintTests(unittest.IsolatedAsyncioTestCase):
    """提示读取、降级与行为样本记录。"""

    async def test_planner_hints_include_mood_and_behavior(self) -> None:
        request = _request()
        mood = ChatState(tenant=request.session_id, subject_id="", valence=0.4)
        user = ChatState(tenant=request.session_id, subject_id=request.user_id, familiarity=0.6)
        row = ChatBehavior(
            tenant=request.session_id,
            scene_hash="h",
            scene_tags='["mention","question"]',
            action="text_reply",
            confidence=0.75,
            count=8,
        )
        with (
            patch.object(mood_module, "mood_enabled", return_value=True),
            patch.object(mood_module.chat_repository, "get_states", new=AsyncMock(return_value=(mood, user))),
            patch.object(mood_module.chat_repository, "behavior_hints", new=AsyncMock(return_value=([row], 80))),
        ):
            hint = await mood_module.planner_hints(request)
        self.assertIn("心情不错", hint)
        self.assertIn("熟悉度熟悉", hint)
        self.assertIn("mention+question → text_reply", hint)

    async def test_planner_hints_disabled_skips_reads(self) -> None:
        get_states = AsyncMock()
        with (
            patch.object(mood_module, "mood_enabled", return_value=False),
            patch.object(mood_module.chat_repository, "get_states", new=get_states),
        ):
            self.assertEqual(await mood_module.planner_hints(_request()), "")
        get_states.assert_not_awaited()

    async def test_planner_hints_degrade_to_empty(self) -> None:
        with (
            patch.object(mood_module, "mood_enabled", return_value=True),
            patch.object(mood_module.chat_repository, "get_states", new=AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            self.assertEqual(await mood_module.planner_hints(_request()), "")

    async def test_record_action_and_touch_interaction(self) -> None:
        request = _request()
        record = AsyncMock()
        touch = AsyncMock()
        with (
            patch.object(mood_module.chat_repository, "record_behavior_sample", new=record),
            patch.object(mood_module.chat_repository, "touch_interaction", new=touch),
        ):
            await mood_module.record_action(request, "text_reply")
            await mood_module.touch_interaction(request)
        record.assert_awaited_once_with(
            request.session_id, request.turn_id, request.trigger_kind, ["mention"], "text_reply"
        )
        touch.assert_awaited_once_with(request.session_id, request.user_id)

    async def test_record_failures_are_swallowed(self) -> None:
        with (
            patch.object(
                mood_module.chat_repository, "record_behavior_sample", new=AsyncMock(side_effect=RuntimeError("x"))
            ),
            patch.object(
                mood_module.chat_repository, "touch_interaction", new=AsyncMock(side_effect=RuntimeError("x"))
            ),
        ):
            await mood_module.record_action(_request(), "no_reply")
            await mood_module.touch_interaction(_request())

    async def test_behavior_ticks(self) -> None:
        with patch.object(
            mood_module.chat_repository, "attribute_behavior_samples", new=AsyncMock(return_value=3)
        ) as attribute:
            self.assertEqual(await mood_module.behavior_attribute_tick(), 3)
        attribute.assert_awaited_once()
        with patch.object(
            mood_module.chat_repository, "attribute_behavior_samples", new=AsyncMock(side_effect=RuntimeError("x"))
        ):
            self.assertEqual(await mood_module.behavior_attribute_tick(), 0)

        aggregate = AsyncMock(side_effect=[1, RuntimeError("boom")])
        with (
            patch.object(mood_module.chat_repository, "behavior_tenants", new=AsyncMock(return_value=["a", "b"])),
            patch.object(mood_module.chat_repository, "aggregate_behavior", new=aggregate),
        ):
            self.assertEqual(await mood_module.behavior_aggregate_tick(), 1)
        self.assertEqual(aggregate.await_count, 2)

        with patch.object(
            mood_module.chat_repository, "behavior_tenants", new=AsyncMock(side_effect=RuntimeError("x"))
        ):
            self.assertEqual(await mood_module.behavior_aggregate_tick(), 0)


class PromptInjectionTests(unittest.TestCase):
    """Planner 提示只在有内容时追加。"""

    def test_build_prompt_includes_hint(self) -> None:
        messages = [{"role": "user", "content": "hi"}]
        prompt = build_prompt(messages, ["search_knowledge"], "现在心情不错；对当前用户熟悉度一般。")
        self.assertEqual(len(prompt), 2)
        self.assertIn("当前状态与历史行为", prompt[-1]["content"])
        self.assertIn("现在心情不错", prompt[-1]["content"])
        self.assertNotIn("当前状态与历史行为", build_prompt(messages, [], "")[-1]["content"])


class ServiceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """服务层把提示传给 Planner，并维护熟悉度。"""

    async def test_maybe_plan_injects_hint_and_records_action(self) -> None:
        service = ChatService(ChatSessionStore())
        request = _request()
        with (
            patch.object(
                service_module,
                "planner_config",
                return_value={"enabled": True, "mode": "complex", "wait_seconds": 60},
            ),
            patch.object(service_module, "planner_model_name", return_value=""),
            patch.object(service_module, "planner_hints", new=AsyncMock(return_value="hint-line")) as hints,
            patch.object(
                service_module, "request_plan", new=AsyncMock(return_value=Plan("text_reply"))
            ) as request_plan,
            patch.object(service_module, "record_action", new=AsyncMock()) as record_action,
            patch.object(service_module, "record_plan_sample", new=AsyncMock()),
        ):
            plan = await service._maybe_plan(request, [{"role": "user", "content": "hi"}], [], _Provider())
        self.assertIsNotNone(plan)
        self.assertEqual(plan.action, "text_reply")
        hints.assert_awaited_once_with(request)
        self.assertEqual(request_plan.await_args.kwargs["hint"], "hint-line")
        record_action.assert_awaited_once_with(request, "text_reply")

    async def test_maybe_plan_simple_mode_skips_hints(self) -> None:
        service = ChatService(ChatSessionStore())
        with (
            patch.object(service_module, "planner_config", return_value={"enabled": True, "mode": "simple"}),
            patch.object(service_module, "planner_hints", new=AsyncMock()) as hints,
        ):
            self.assertIsNone(await service._maybe_plan(_request(), [], [], _Provider()))
        hints.assert_not_awaited()

    async def _process_locked(self, request: ChatRequest) -> tuple[ChatResponse, AsyncMock]:
        service = ChatService(ChatSessionStore())
        with (
            patch.object(service_module, "get_config_manager", return_value=_FakeConfig(max_history=10)),
            patch.object(service, "_hydrate", new=AsyncMock()),
            patch.object(service, "_process", new=AsyncMock(return_value=ChatResponse(text="ok"))),
            patch.object(service_module, "touch_interaction", new=AsyncMock()) as touch,
        ):
            response = await service._process_locked(request, None)
        return response, touch

    async def test_process_locked_touches_interaction(self) -> None:
        response, touch = await self._process_locked(_request())
        self.assertEqual(response.text, "ok")
        touch.assert_awaited_once()

    async def test_process_locked_skips_touch_for_proactive(self) -> None:
        response, touch = await self._process_locked(_request(trigger_kind="proactive", proactive=True))
        self.assertEqual(response.text, "ok")
        touch.assert_not_awaited()


class MoodConfigTests(unittest.TestCase):
    """情绪配置的默认值与校验。"""

    def test_defaults(self) -> None:
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["mood"], {"enabled": True, "decay_per_day": 0.1})

    def test_valid_values(self) -> None:
        chat = copy.deepcopy(SYSTEM_DEFAULTS["chat"])
        chat["mood"] = {"enabled": False, "decay_per_day": 0.5}
        validate_system_config({"chat": chat})

    def test_invalid_values(self) -> None:
        for mood in ({"enabled": 1}, {"decay_per_day": True}, {"decay_per_day": "0.5"}, {"decay_per_day": 1.5}, []):
            chat = copy.deepcopy(SYSTEM_DEFAULTS["chat"])
            chat["mood"] = mood
            with self.subTest(mood=mood), self.assertRaises(ValueError):
                validate_system_config({"chat": chat})

    def test_decay_reads_are_clamped(self) -> None:
        cases = ((2.0, 1.0), (-1.0, 0.0), (True, 0.1), ("bad", 0.1))
        for value, expected in cases:
            with (
                self.subTest(value=value),
                patch.object(
                    repository_module, "get_config_manager", return_value=_FakeConfig({"decay_per_day": value})
                ),
            ):
                self.assertEqual(repository_module._mood_decay_per_day(), expected)


class BehaviorConsoleTests(unittest.IsolatedAsyncioTestCase):
    """控制台查看与清空行为聚合。"""

    async def test_behavior_console_payload_and_clear(self) -> None:
        row = ChatBehavior(
            tenant="tenant",
            scene_hash="h",
            scene_tags='["mention","question"]',
            action="text_reply",
            confidence=0.5,
            count=2,
            last_seen=utc_now_naive(),
        )
        with patch.object(
            chat_routes.chat_repository, "behavior_hints", new=AsyncMock(return_value=([row], 3))
        ) as hints:
            result = await chat_routes.get_behavior("tenant", 10)
        hints.assert_awaited_once_with("tenant", limit=10, minimum=0)
        self.assertEqual(result["attributed"], 3)
        self.assertEqual(result["minimum"], repository_module.BEHAVIOR_MIN_SAMPLES)
        self.assertEqual(result["items"][0]["scene_tags"], ["mention", "question"])
        self.assertEqual(result["items"][0]["action"], "text_reply")

        with patch.object(chat_routes.chat_repository, "clear_behavior", new=AsyncMock()) as clear:
            self.assertEqual(await chat_routes.delete_behavior("tenant"), {"deleted": True})
        clear.assert_awaited_once_with("tenant")

        with self.assertRaises(HTTPException):
            await chat_routes.get_behavior("  ")
        with (
            patch.object(chat_routes.chat_repository, "behavior_hints", new=AsyncMock(side_effect=RuntimeError)),
            self.assertRaises(HTTPException) as caught,
        ):
            await chat_routes.get_behavior("tenant")
        self.assertEqual(caught.exception.status_code, 503)


class _RepositoryCase(unittest.IsolatedAsyncioTestCase):
    """临时数据库上的状态与行为样本。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        self.tenant = f"test:{uuid4().hex}"
        patcher = patch.object(
            repository_module, "get_config_manager", return_value=_FakeConfig({"enabled": True, "decay_per_day": 0.1})
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    async def _message(self, ingested_at: Any) -> None:
        await chat_repository.append_message(
            ChatMessage(
                tenant=self.tenant,
                sender_id="20001",
                sender_name="小红",
                content="继续聊",
                dedupe_key=f"msg:{uuid4().hex}",
                ingested_at=ingested_at,
            )
        )

    async def test_touch_interaction_accumulates_and_caps(self) -> None:
        for _ in range(60):
            await chat_repository.touch_interaction(self.tenant, "10001")
        mood, user = await chat_repository.get_states(self.tenant, "10001")
        self.assertIsNotNone(user)
        self.assertAlmostEqual(user.familiarity, 1.0)
        self.assertIsNotNone(user.last_interaction_at)
        self.assertIsNone(mood)

    async def test_daily_decay_applies_before_touch(self) -> None:
        now = utc_now_naive()
        await chat_repository.touch_interaction(self.tenant, "10001", now=now - timedelta(days=2))
        await chat_repository.touch_interaction(self.tenant, "10001", now=now)
        _, user = await chat_repository.get_states(self.tenant, "10001")
        self.assertAlmostEqual(user.familiarity, 0.02 * (0.99**2) + 0.02, places=6)

    async def test_emotion_first_value_and_ema(self) -> None:
        await chat_repository.apply_emotion(self.tenant, 0.5, 0.7)
        mood, _ = await chat_repository.get_states(self.tenant, "")
        self.assertAlmostEqual(mood.valence, 0.5)
        self.assertAlmostEqual(mood.arousal, 0.7)

        await chat_repository.apply_emotion(self.tenant, -0.5, 0.5)
        mood, _ = await chat_repository.get_states(self.tenant, "")
        self.assertAlmostEqual(mood.valence, 0.2)
        self.assertAlmostEqual(mood.arousal, 0.64)

    async def test_emotion_is_clamped(self) -> None:
        await chat_repository.apply_emotion(self.tenant, 2.0, -1.0)
        mood, _ = await chat_repository.get_states(self.tenant, "")
        self.assertAlmostEqual(mood.valence, 1.0)
        self.assertAlmostEqual(mood.arousal, 0.0)

    async def test_disabled_mood_writes_nothing(self) -> None:
        with patch.object(repository_module, "get_config_manager", return_value=_FakeConfig({"enabled": False})):
            await chat_repository.apply_emotion(self.tenant, 0.5, 0.5)
            await chat_repository.touch_interaction(self.tenant, "10001")
            mood, user = await chat_repository.get_states(self.tenant, "10001")
        self.assertIsNone(mood)
        self.assertIsNone(user)
        self.assertEqual(await ChatState.select_rows(tenant=self.tenant), [])

    async def test_record_behavior_sample_is_idempotent(self) -> None:
        now = utc_now_naive()
        for _ in range(2):
            await chat_repository.record_behavior_sample(
                self.tenant, "turn", "mention", ["mention"], "text_reply", now=now
            )
        rows = await ChatBehaviorSample.select_rows(tenant=self.tenant)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].outcome, "pending")
        self.assertEqual(rows[0].observed_until, now + timedelta(minutes=10))

    async def test_attribute_continued_and_ignored(self) -> None:
        base = utc_now_naive() - timedelta(minutes=30)
        await chat_repository.record_behavior_sample(
            self.tenant, "turn-a", "mention", ["mention", "question"], "text_reply", now=base
        )
        await chat_repository.record_behavior_sample(
            self.tenant, "turn-b", "mention", ["mention"], "no_reply", now=base - timedelta(minutes=60)
        )
        await self._message(base + timedelta(minutes=5))

        await chat_repository.attribute_behavior_samples(now=base + timedelta(minutes=20))

        rows = await ChatBehaviorSample.select_rows(tenant=self.tenant)
        self.assertEqual({row.turn_id: row.outcome for row in rows}, {"turn-a": "continued", "turn-b": "ignored"})
        self.assertTrue(all(row.attributed_at is not None for row in rows))

    async def test_aggregate_and_hints(self) -> None:
        base = utc_now_naive() - timedelta(minutes=60)
        await chat_repository.record_behavior_sample(
            self.tenant, "t1", "mention", ["mention", "question"], "text_reply", now=base
        )
        await chat_repository.record_behavior_sample(
            self.tenant, "t2", "mention", ["mention", "question"], "text_reply", now=base - timedelta(minutes=20)
        )
        await chat_repository.record_behavior_sample(
            self.tenant, "t3", "mention", ["mention"], "no_reply", now=base - timedelta(minutes=40)
        )
        await self._message(base + timedelta(minutes=5))
        await self._message(base - timedelta(minutes=35))
        await chat_repository.attribute_behavior_samples(now=base + timedelta(minutes=20))

        self.assertIn(self.tenant, await chat_repository.behavior_tenants())
        self.assertEqual(await chat_repository.aggregate_behavior(self.tenant), 2)
        aggregated = await ChatBehavior.select_rows(tenant=self.tenant)
        self.assertEqual({row.action: row.count for row in aggregated}, {"text_reply": 2, "no_reply": 1})

        rows, total = await chat_repository.behavior_hints(self.tenant, minimum=3)
        self.assertEqual((len(rows), total), (2, 3))
        confidences = {row.action: row.confidence for row in rows}
        self.assertAlmostEqual(confidences["text_reply"], 0.5)
        self.assertAlmostEqual(confidences["no_reply"], 1.0)

        self.assertEqual(await chat_repository.behavior_hints(self.tenant), ([], 3))

        await chat_repository.clear_behavior(self.tenant)
        self.assertEqual(await ChatBehaviorSample.select_rows(tenant=self.tenant), [])
        self.assertEqual(await ChatBehavior.select_rows(tenant=self.tenant), [])
        self.assertEqual(await chat_repository.behavior_hints(self.tenant, minimum=0), ([], 0))


if __name__ == "__main__":
    unittest.main()
