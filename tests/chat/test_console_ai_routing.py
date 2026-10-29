"""控制台任务模型分配的读取与写回。"""

from __future__ import annotations

import copy
import unittest
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.console.routes import ai_providers


class _Chat:
    """提供任务分配接口所需的 chat 配置。"""

    def __init__(self) -> None:
        self.saved: list[dict] = []
        self.chat = {
            "provider": "main",
            "providers": {
                "main": {"provider_type": "chat", "type": "openai", "model": "gpt-4o"},
                "backup": {"provider_type": "chat", "type": "deepseek", "model": "deepseek-chat"},
                "embed": {"provider_type": "embedding", "type": "openai", "model": "text-embedding-3-small"},
                "whisper": {"provider_type": "stt", "type": "openai", "model": "whisper-1"},
            },
            "model_routing": {"reply": "backup"},
        }

    def get_chat(self) -> dict:
        return copy.deepcopy(self.chat)

    def save_chat(self, chat: dict) -> None:
        self.saved.append(copy.deepcopy(chat))
        self.chat = copy.deepcopy(chat)


class ConsoleAiRoutingTests(unittest.IsolatedAsyncioTestCase):
    def _patched(self, store: _Chat):
        return (
            patch.object(ai_providers, "_chat_config", side_effect=store.get_chat),
            patch.object(ai_providers, "_save_chat", side_effect=store.save_chat),
            patch.object(ai_providers, "_type_labels", return_value={}),
        )

    async def test_get_routing_reports_tasks_and_chat_providers(self) -> None:
        store = _Chat()
        chat_patch, save_patch, label_patch = self._patched(store)
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.get_model_routing()
        data = result["data"]
        tasks = {item["task"] for item in data["tasks"]}
        self.assertTrue({"extract", "style"}.isdisjoint(tasks))
        self.assertEqual([item["name"] for item in data["providers"]], ["backup", "embed", "main", "whisper"])
        self.assertEqual(data["default_provider"], "main")
        self.assertNotIn("fallback_providers", data)
        reply = next(item for item in data["tasks"] if item["task"] == "reply")
        self.assertEqual((reply["provider"], reply["model"], reply["configured"]), ("backup", "deepseek-chat", True))
        voice = next(item for item in data["tasks"] if item["task"] == "voice")
        self.assertEqual((voice["provider_type"], voice["provider"]), ("stt", ""))
        for task in ("memory", "utils", "learner", "fast_model", "emoji"):
            self.assertEqual(next(item for item in data["tasks"] if item["task"] == task)["provider_type"], "chat")
        for task in ("embedding", "image_embedding"):
            self.assertEqual(next(item for item in data["tasks"] if item["task"] == task)["provider_type"], "embedding")

    async def test_update_routing_voice_requires_stt_provider(self) -> None:
        store = _Chat()
        chat_patch, save_patch, label_patch = self._patched(store)
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.update_model_routing("voice", {"provider": "whisper"})
        self.assertTrue(result["success"])
        self.assertEqual(store.chat["model_routing"]["voice"], "whisper")

        chat_patch, save_patch, label_patch = self._patched(store)
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.update_model_routing("voice", {"provider": "main"})
        self.assertEqual(result, {"success": False, "message": "语音任务只能分配语音识别 Provider"})
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.update_model_routing("reply", {"provider": "whisper"})
        self.assertEqual(result, {"success": False, "message": "只有对话 Provider 可以分配给任务"})

    async def test_update_routing_embedding_requires_embedding_provider(self) -> None:
        store = _Chat()
        chat_patch, save_patch, label_patch = self._patched(store)
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.update_model_routing("embedding", {"provider": "embed"})
        self.assertTrue(result["success"])
        self.assertEqual(store.chat["model_routing"]["embedding"], "embed")

        chat_patch, save_patch, label_patch = self._patched(store)
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.update_model_routing("image_embedding", {"provider": "main"})
        self.assertEqual(result, {"success": False, "message": "嵌入任务只能分配嵌入 Provider"})
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.update_model_routing("reply", {"provider": "embed"})
        self.assertEqual(result, {"success": False, "message": "只有对话 Provider 可以分配给任务"})

    async def test_update_routing_persists_and_clears(self) -> None:
        store = _Chat()
        chat_patch, save_patch, label_patch = self._patched(store)
        with chat_patch, save_patch, label_patch:
            result = await ai_providers.update_model_routing("planner", {"provider": "main"})
        self.assertTrue(result["success"])
        self.assertEqual(store.chat["model_routing"]["planner"], "main")
        self.assertEqual(store.saved[0]["model_routing"]["planner"], "main")

        with chat_patch, save_patch, label_patch:
            await ai_providers.update_model_routing("reply", {"provider": ""})
        self.assertEqual(store.chat["model_routing"]["reply"], "")

    async def test_update_routing_rejects_unknown_values(self) -> None:
        store = _Chat()
        cases = [
            ("unknown", "main", "未知的模型任务"),
            ("extract", "main", "未知的模型任务"),
            ("style", "main", "未知的模型任务"),
            ("reply", "missing", "Provider 不存在"),
            ("reply", "embed", "只有对话 Provider 可以分配给任务"),
        ]
        for task, provider, message in cases:
            chat_patch, save_patch, label_patch = self._patched(store)
            with self.subTest(task=task, provider=provider), chat_patch, save_patch, label_patch:
                result = await ai_providers.update_model_routing(task, {"provider": provider})
                self.assertEqual(result, {"success": False, "message": message})
        self.assertEqual(store.saved, [])
