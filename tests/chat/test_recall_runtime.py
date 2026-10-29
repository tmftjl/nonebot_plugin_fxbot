"""撤回后的运行时失效：内存历史、防抖批次、Focus 时间线、摘要与在途轮次。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import context_summary as summary_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import lifecycle as lifecycle_module
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.focus.context import FocusEntry, FocusCheckpointStore, restore_entries
from src.plugins.nonebot_plugin_fxbot.chat.replyer.prompt import assemble_messages
from src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce import DebounceRegistry
from src.plugins.nonebot_plugin_fxbot.chat.providers.entities import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime import FocusGroupRuntime

TENANT = "test:recall-runtime"


def _request(**values: object) -> ChatRequest:
    fields: dict[str, object] = {
        "session_id": TENANT,
        "user_id": "u1",
        "group_id": "g1",
        "text": "你好",
        "turn_id": "turn-1",
        "dedupe_key": "msg:1",
        "trigger_kind": "mention",
        "message_id": "1",
        "user_name": "Alice",
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


class SessionDiscardTests(unittest.TestCase):
    """内存历史按平台消息 ID 丢弃，私有标记不发给模型。"""

    def test_discard_removes_history_and_waiting_messages(self) -> None:
        store = ChatSessionStore(max_messages=2)
        for index, message_id in enumerate(("1", "2", "3"), start=1):
            store.append(TENANT, {"role": "user", "content": f"第{index}条", "_source_ids": [message_id]})

        self.assertTrue(store.discard_message(TENANT, "1"))
        self.assertFalse(store.discard_message(TENANT, "1"))
        self.assertTrue(store.discard_message(TENANT, "2"))
        self.assertEqual([entry["content"] for entry in store.get(TENANT)], ["第3条"])
        self.assertFalse(store.discard_message(TENANT, "missing"))

    def test_private_keys_are_not_sent_to_model(self) -> None:
        history = [{"role": "user", "content": "背景", "_source_ids": ["7"]}]
        messages = assemble_messages(_request(), history, "", "", "现在")
        self.assertNotIn("_source_ids", str(messages))
        self.assertIn("背景", str(messages))


class DebounceDiscardTests(unittest.TestCase):
    """撤回消息从待合并批次里去掉；触发消息被撤回时整批取消。"""

    def setUp(self) -> None:
        patcher = patch("src.plugins.nonebot_plugin_fxbot.chat.behavior.debounce._mode", return_value="natural")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_context_message_is_removed(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request(message_id="1"))
        batch.accept(_request(message_id="2"), trigger=False)

        self.assertTrue(registry.discard_message(TENANT, "2"))
        self.assertEqual([request.message_id for request in batch.requests], ["1"])
        self.assertFalse(registry.discard_message(TENANT, "9"))

    def test_trigger_message_cancels_whole_batch(self) -> None:
        registry = DebounceRegistry()
        batch = registry.create(_request(message_id="1"))

        self.assertTrue(registry.discard_message(TENANT, "1"))
        self.assertTrue(batch.cancelled)
        self.assertIsNone(registry.get(TENANT))


class FocusDiscardTests(unittest.IsolatedAsyncioTestCase):
    """Focus 输入与时间线条目按消息删除，来源标记可落盘恢复。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.store = FocusCheckpointStore("scope", root=Path(self.directory.name))
        self.runtime = FocusGroupRuntime("scope", store=self.store)

    async def test_pending_input_is_removed(self) -> None:
        self.runtime._queue_input(TENANT, _request(message_id="5"), False)

        self.assertTrue(await self.runtime.discard_message(TENANT, "5"))
        self.assertEqual(self.runtime._pending.get(TENANT), [])
        self.assertTrue(self.runtime._invalidated)

    async def test_timeline_entry_is_removed(self) -> None:
        self.runtime.entries.append(
            FocusEntry([{"role": "user", "content": "已考虑"}], {TENANT}, 1, sources={TENANT: ["6"]})
        )

        self.assertTrue(await self.runtime.discard_message(TENANT, "6"))
        self.assertEqual(self.runtime.entries, [])
        self.assertFalse(await self.runtime.discard_message(TENANT, "6"))

    def test_sources_survive_checkpoint(self) -> None:
        entry = FocusEntry([{"role": "user", "content": "x"}], {TENANT}, 3, sources={TENANT: ["9"]})
        self.store.save([entry], {})

        restored = restore_entries(self.store.load())
        self.assertEqual(restored[0].sources, {TENANT: ["9"]})


class SummaryDiscardTests(unittest.IsolatedAsyncioTestCase):
    """被压缩进摘要的消息撤回后整份摘要作废。"""

    def setUp(self) -> None:
        summary_module.clear_summary(TENANT)

    async def test_summarized_message_clears_summary(self) -> None:
        state = {"topics": ["周末"], "background": [], "progress": [], "pending": [], "agreements": []}

        class _Provider:
            async def chat(self, messages: list[dict[str, object]], **_kwargs: object) -> LLMResponse:
                return LLMResponse(content=json.dumps(state, ensure_ascii=False))

        class _Lease:
            def __init__(self, provider: object) -> None:
                self.provider = provider

            async def __aenter__(self) -> object:
                return self.provider

            async def __aexit__(self, *exc: object) -> None:
                return None

        with (
            patch.object(summary_module, "routed_task", return_value="utils"),
            patch.object(summary_module, "fallback_provider", lambda *a, **k: _Lease(_Provider())),
            patch.object(summary_module, "planner_config", return_value={"context_summary_max_chars": 6000}),
        ):
            summary = await summary_module.compact_messages(
                TENANT, [{"role": "user", "content": "旧消息", "_source_ids": ["11"]}]
            )

        self.assertTrue(summary)
        self.assertTrue(summary_module.forget_summarized(TENANT, "11"))
        self.assertEqual(summary_module.current_summary(TENANT), "")
        self.assertFalse(summary_module.forget_summarized(TENANT, "12"))


class RecallLifecycleTests(unittest.IsolatedAsyncioTestCase):
    """撤回入口把数据库与运行时一起失效，并让在途轮次作废。"""

    async def _recall(self, message_id: str):
        store = ChatSessionStore()
        store.append(TENANT, {"role": "user", "content": "要被撤回", "_source_ids": ["pm-1"]})
        result = {"jargon_batches": 0, "expressions": 0, "rolled_back": 0}
        with (
            patch.object(lifecycle_module, "is_ready", return_value=True),
            patch.object(lifecycle_module, "default_session_store", store),
            patch.object(lifecycle_module, "default_debounce_registry", DebounceRegistry()),
            patch.object(lifecycle_module, "forget_summarized", MagicMock(return_value=False)),
            patch.object(lifecycle_module.chat_repository, "invalidate_message", AsyncMock(return_value=result)),
            patch(
                "src.plugins.nonebot_plugin_fxbot.chat.focus.focus_mode_manager.discard_message",
                AsyncMock(return_value=False),
            ),
        ):
            before = store.epoch(TENANT)
            recalled = await lifecycle_module.handle_recall(TENANT, message_id)
        return store, before, recalled

    async def test_recall_drops_context_and_invalidates_epoch(self) -> None:
        store, before, recalled = await self._recall("pm-1")

        self.assertIsNotNone(recalled)
        self.assertEqual(store.get(TENANT), [])
        self.assertEqual(store.epoch(TENANT), before + 1)

    async def test_unrelated_recall_keeps_epoch(self) -> None:
        store, before, recalled = await self._recall("pm-2")

        self.assertIsNotNone(recalled)
        self.assertEqual(len(store.get(TENANT)), 1)
        self.assertEqual(store.epoch(TENANT), before)


if __name__ == "__main__":
    unittest.main()
