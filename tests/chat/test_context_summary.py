"""上下文压缩、会话窗口挤出与规划打断。"""

from __future__ import annotations

import json
import asyncio
import unittest
from typing import Any
from functools import partial
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module, context_summary as summary_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.replyer.prompt import assemble_messages
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)
from src.plugins.nonebot_plugin_fxbot.chat.providers.entities import LLMResponse

TENANT = "test:context-summary"
_STATE = {
    "topics": ["周末去哪"],
    "background": ["小明(ID:1) 喜欢爬山"],
    "progress": ["已经约好周六出发"],
    "pending": ["等小红确认"],
    "agreements": [],
}


def _request() -> ChatRequest:
    """构造一条私聊触发。"""
    return ChatRequest(
        session_id=TENANT,
        user_id="40001",
        group_id=None,
        text="在吗",
        turn_id="turn-summary",
        dedupe_key="msg:summary",
        trigger_kind="private",
    )


class _Lease:
    def __init__(self, provider: object) -> None:
        self.provider = provider

    async def __aenter__(self) -> object:
        return self.provider

    async def __aexit__(self, *exc: object) -> None:
        return None


class _Provider:
    def __init__(self, content: str = "") -> None:
        self.content = content
        self.calls: list[list[dict[str, Any]]] = []

    async def chat(self, messages: list[dict[str, Any]], **_kwargs: Any) -> LLMResponse:
        self.calls.append(messages)
        return LLMResponse(content=self.content)


class SummaryConfigTests(unittest.TestCase):
    """默认值、校验与字符上限。"""

    def test_defaults(self) -> None:
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["planner"]["context_summary_max_chars"], 6000)
        self.assertFalse(SYSTEM_DEFAULTS["chat"]["planner"]["passive_replyer"])
        self.assertFalse(SYSTEM_DEFAULTS["chat"]["reply_timing"]["planner_message_steering"])

    def test_validation(self) -> None:
        validate_system_config({"chat": {"planner": {"context_summary_max_chars": 4000}}})
        with self.assertRaisesRegex(ValueError, "context_summary_max_chars"):
            validate_system_config({"chat": {"planner": {"context_summary_max_chars": 999}}})
        with self.assertRaisesRegex(ValueError, "passive_replyer"):
            validate_system_config({"chat": {"planner": {"passive_replyer": "yes"}}})
        with self.assertRaisesRegex(ValueError, "planner_message_steering"):
            validate_system_config({"chat": {"reply_timing": {"planner_message_steering": "yes"}}})

    def test_max_chars_is_clamped(self) -> None:
        for raw, expected in ((100, 1000), (99999, 8000), ("bad", 6000), (True, 6000)):
            with self.subTest(raw=raw):
                with patch.object(summary_module, "planner_config", return_value={"context_summary_max_chars": raw}):
                    self.assertEqual(summary_module.summary_max_chars(), expected)


class SummaryTextTests(unittest.TestCase):
    """压缩请求与摘要渲染。"""

    def test_task_carries_previous_and_messages(self) -> None:
        messages = [
            {"role": "user", "content": "小明(ID:1): 周六爬山吗"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "web_search"}}]},
            {"role": "tool", "content": "不该出现的结果"},
        ]
        snapshot = summary_module.PlannerRequestSnapshot([], [], "test", {}, "test")
        prompt = summary_module.build_snapshot_messages(snapshot, "旧摘要", messages, [], 3000)
        body = prompt[-1]["content"]
        self.assertIn("旧摘要", body)
        self.assertIn("周六爬山吗", body)
        self.assertIn("web_search", body)
        self.assertIn("不该出现的结果", body)
        self.assertIn("3000", body)

    def test_render_keeps_categories_and_dedupes(self) -> None:
        text = summary_module.render_state({**_STATE, "topics": ["周末去哪", "周末去哪", " "]}, 6000)
        self.assertIn("当前话题:\n- 周末去哪", text)
        self.assertIn("未完事项:\n- 等小红确认", text)
        self.assertNotIn("对话约定", text)

    def test_render_rejects_bad_shapes(self) -> None:
        bad_entry = {key: [] for key in summary_module.STATE_LABELS}
        bad_entry["topics"] = [1]
        for data in ({}, {**_STATE, "topics": "x"}, bad_entry):
            with self.subTest(data=data):
                with self.assertRaises(ValueError):
                    summary_module.render_state(data, 6000)
        with self.assertRaisesRegex(ValueError, "超过字符预算"):
            summary_module.render_state(_STATE, 10)

    def test_parse_rejects_fences_and_non_state_fields(self) -> None:
        text = "```json\n" + json.dumps(_STATE, ensure_ascii=False) + "\n```"
        with self.assertRaises(ValueError):
            summary_module.parse_state(text, 6000)
        self.assertIn("当前话题", summary_module.parse_state(json.dumps(_STATE), 6000))
        with self.assertRaises(ValueError):
            summary_module.parse_state(json.dumps({**_STATE, "tool_calls": []}), 6000)
        with self.assertRaises(ValueError):
            summary_module.parse_state("没有 JSON", 6000)


class CompactTests(unittest.IsolatedAsyncioTestCase):
    """压缩成功写入摘要，失败保留上一份。"""

    def setUp(self) -> None:
        summary_module.clear_summary(TENANT)
        self.snapshot = summary_module.PlannerRequestSnapshot(
            messages=[{"role": "system", "content": "原始 Planner 人格"}],
            tools=[{"type": "function", "function": {"name": "reply"}}],
            provider_id="successful-planner",
            provider_config={"model": "snapshot-model"},
            model="snapshot-model",
        )
        self.compact = partial(summary_module.compact_messages, planner_request=self.snapshot)

    async def test_success_updates_summary(self) -> None:
        provider = _Provider(json.dumps(_STATE, ensure_ascii=False))
        with (
            patch.object(summary_module.provider_manager, "lease_snapshot", lambda *a, **k: _Lease(provider)),
            patch.object(summary_module, "planner_config", return_value={"context_summary_max_chars": 6000}),
        ):
            summary = await self.compact(TENANT, [{"role": "user", "content": "小明(ID:1): 周六爬山吗"}])
        self.assertIn("当前话题", summary)
        self.assertEqual(summary_module.current_summary(TENANT), summary)
        self.assertIn("周六爬山吗", provider.calls[0][-1]["content"])

    async def test_failure_keeps_previous(self) -> None:
        summary_module._summaries[TENANT] = "上一份摘要"
        broken = _Provider("不是 JSON")
        with (
            patch.object(summary_module.provider_manager, "lease_snapshot", lambda *a, **k: _Lease(broken)),
            patch.object(summary_module, "planner_config", return_value={"context_summary_max_chars": 6000}),
            patch.object(summary_module, "logger"),
        ):
            self.assertEqual(
                await self.compact(TENANT, [{"role": "user", "content": "旧"}]),
                "上一份摘要",
            )
            self.assertEqual(
                await self.compact(TENANT, [{"role": "user", "content": "旧"}]),
                "上一份摘要",
            )

    async def test_empty_batch_keeps_previous(self) -> None:
        summary_module._summaries[TENANT] = "上一份摘要"
        self.assertEqual(await summary_module.compact_messages(TENANT, []), "上一份摘要")

    async def test_clear_session_during_compaction_discards_late_result(self) -> None:
        for content in (json.dumps(_STATE, ensure_ascii=False), "不是 JSON"):
            for replacement in ("", "清空后新摘要"):
                with self.subTest(content=content, replacement=replacement):
                    started = asyncio.Event()
                    release = asyncio.Event()
                    summary_module._summaries[TENANT] = "上一份摘要"
                    summary_module._summary_sources[TENANT] = {"previous-message"}
                    service = service_module.ChatService(ChatSessionStore())

                    async def delayed_chat(**_kwargs: Any) -> LLMResponse:
                        started.set()
                        await release.wait()
                        return LLMResponse(content=content)

                    provider = _Provider()
                    with (
                        patch.object(provider, "chat", delayed_chat),
                        patch.object(
                            summary_module.provider_manager, "lease_snapshot", lambda *a, **k: _Lease(provider)
                        ),
                        patch.object(
                            summary_module, "planner_config", return_value={"context_summary_max_chars": 6000}
                        ),
                        patch.object(summary_module, "logger"),
                        patch.object(service_module.memory_state, "is_ready", return_value=False),
                        patch(
                            "src.plugins.nonebot_plugin_fxbot.chat.focus.focus_mode_manager.clear_context",
                            AsyncMock(),
                        ),
                    ):
                        task = asyncio.create_task(
                            self.compact(
                                TENANT, [{"role": "user", "content": "旧消息", "_source_ids": ["old-message"]}]
                            )
                        )
                        try:
                            await asyncio.wait_for(started.wait(), timeout=5)
                            await service.clear_session(TENANT)
                            self.assertEqual(summary_module.current_summary(TENANT), "")
                            self.assertNotIn(TENANT, summary_module._summary_sources)
                            if replacement:
                                summary_module._summaries[TENANT] = replacement
                                summary_module._summary_sources[TENANT] = {"new-message"}
                        finally:
                            release.set()
                            result = await asyncio.wait_for(task, timeout=5)

                    self.assertEqual(result, replacement)
                    self.assertEqual(summary_module.current_summary(TENANT), replacement)
                    self.assertEqual(
                        summary_module._summary_sources.get(TENANT, set()),
                        {"new-message"} if replacement else set(),
                    )

    def test_clear(self) -> None:
        summary_module._summaries[TENANT] = "摘要"
        summary_module.clear_summary(TENANT)
        self.assertEqual(summary_module.current_summary(TENANT), "")


class DroppedMessageTests(unittest.TestCase):
    """被窗口挤掉的消息要能取出来压缩。"""

    def test_overflow_is_captured(self) -> None:
        store = ChatSessionStore()
        store.append_many(
            TENANT,
            [{"role": "user", "content": text} for text in ("一", "二", "三", "四")],
            limit=3,
        )
        store.process_cycle(TENANT, 3)
        self.assertEqual(store.take_dropped(TENANT), [])
        self.assertEqual([item["content"] for item in store.get(TENANT)], ["一", "二", "三", "四"])
        self.assertEqual(store.take_dropped(TENANT), [])

    def test_leading_non_user_is_captured(self) -> None:
        store = ChatSessionStore()
        store.append_many(
            TENANT,
            [{"role": "assistant", "content": "孤立回答"}, {"role": "user", "content": "一"}],
            limit=3,
        )
        self.assertEqual([item["content"] for item in store.take_dropped(TENANT)], ["孤立回答"])
        self.assertEqual([item["content"] for item in store.get(TENANT)], ["一"])

    def test_shrinking_limit_captures_overflow(self) -> None:
        store = ChatSessionStore()
        for text in ("一", "二", "三"):
            store.append(TENANT, {"role": "user", "content": text}, limit=5)
        store.trim(TENANT, 2)
        store.process_cycle(TENANT, 2)
        self.assertEqual(store.take_dropped(TENANT), [])

    def test_clear_forgets_dropped(self) -> None:
        store = ChatSessionStore()
        store.append_many(TENANT, [{"role": "user", "content": "一"}], limit=1)
        store.append(TENANT, {"role": "user", "content": "二"}, limit=1)
        store.clear(TENANT)
        self.assertEqual(store.take_dropped(TENANT), [])

    def test_append_without_limit_preserves_session_limit(self) -> None:
        for method in ("append", "append_many"):
            with self.subTest(method=method):
                store = ChatSessionStore()
                messages = [{"role": "user", "content": str(index)} for index in range(40)]
                store.append_many(TENANT, messages, limit=60)
                result = {"role": "assistant", "content": "result"}
                if method == "append":
                    store.append(TENANT, result)
                else:
                    store.append_many(TENANT, [result])
                self.assertEqual(store.get(TENANT), [*messages, result])
                self.assertEqual(store.take_dropped(TENANT), [])

    def test_explicit_limit_replaces_session_limit(self) -> None:
        store = ChatSessionStore()
        messages = [{"role": "user", "content": str(index)} for index in range(40)]
        store.append_many(TENANT, messages, limit=60)
        result = {"role": "assistant", "content": "result"}
        store.append(TENANT, result, limit=20)
        self.assertEqual(store.get(TENANT), [*messages, result])
        store.process_cycle(TENANT, 20)
        self.assertEqual(store.get(TENANT), [*messages[-4:], result])
        self.assertEqual(store.take_dropped(TENANT), messages[:-4])
        store.append(TENANT, result)
        self.assertEqual(store.get(TENANT), [*messages[-4:], result, result])
        self.assertEqual(store.take_dropped(TENANT), [])

    def test_clear_forgets_session_limit(self) -> None:
        store = ChatSessionStore()
        messages = [{"role": "user", "content": str(index)} for index in range(40)]
        store.append_many(TENANT, messages, limit=60)
        store.clear(TENANT)
        store.append_many(TENANT, messages)
        store.process_cycle(TENANT, 20)
        self.assertEqual(store.get(TENANT), messages[-5:])
        self.assertEqual(store.take_dropped(TENANT), messages[:-5])

    def test_default_threshold_and_trim_target(self) -> None:
        store = ChatSessionStore()
        messages = [{"role": "user", "content": str(index)} for index in range(36)]
        store.append_many(TENANT, messages[:35], limit=30)
        self.assertEqual(store.process_cycle(TENANT, 30), [])
        store.append(TENANT, messages[-1], limit=30)
        self.assertEqual(store.process_cycle(TENANT, 30), messages[:30])
        self.assertEqual(store.get(TENANT), messages[-6:])


class AssembleSummaryTests(unittest.TestCase):
    """状态摘要作为独立 system 消息发送。"""

    def test_summary_message(self) -> None:
        messages = assemble_messages(_request(), [], "人格", "", "你好", context_summary="当前话题:\n- 周末")
        system = [message["content"] for message in messages if message["role"] == "system"]
        self.assertEqual(len(system), 2)
        self.assertTrue(system[1].startswith(summary_module.SUMMARY_HEADER))
        self.assertIn("周末", system[1])
        self.assertEqual(assemble_messages(_request(), [], "", "", "你好")[0]["role"], "user")


if __name__ == "__main__":
    unittest.main()
