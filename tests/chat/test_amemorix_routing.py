"""A_Memorix 宿主任务路由：任务键与嵌入 Provider 选择。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.providers import routing
from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host import llm
from src.plugins.nonebot_plugin_fxbot.chat.amemorix._host.person_db import person_scope


class _ConfigManager:
    def __init__(self, chat: dict) -> None:
        self.chat = chat

    def get_system(self) -> dict:
        return {"chat": self.chat}


def _chat(routing_map: dict | None = None, providers: dict | None = None) -> dict:
    return {
        "provider": "main",
        "providers": providers
        or {
            "main": {"provider_type": "chat", "model": "m1"},
            "mem": {"provider_type": "chat", "model": "m2"},
        },
        "model_routing": routing_map or {},
    }


class HostRouteTests(unittest.TestCase):
    """按照上游学习与记忆任务关系选择可用模型。"""

    def test_source_task_fallbacks(self) -> None:
        chat = _chat()
        with patch.object(routing, "get_config_manager", return_value=_ConfigManager(chat)):
            for task, route in (
                ("memory", "utils"),
                ("utils", "utils"),
                ("learner", "utils"),
                ("emoji", "vision"),
                ("replyer", "reply"),
                ("unknown", "utils"),
            ):
                self.assertEqual(llm.resolve_route(task), route, task)

    def test_task_keys_win(self) -> None:
        chat = _chat({"memory": "mem", "utils": "aux", "learner": "learn", "emoji": "emo"})
        with patch.object(routing, "get_config_manager", return_value=_ConfigManager(chat)):
            for task, route in (
                ("memory", "memory"),
                ("utils", "utils"),
                ("learner", "learner"),
                ("emoji", "emoji"),
            ):
                self.assertEqual(llm.resolve_route(task), route, task)

    def test_available_models_follow_routing(self) -> None:
        chat = _chat({"memory": "mem"})
        with (
            patch.object(llm, "_chat_config", return_value=chat),
            patch.object(routing, "get_config_manager", return_value=_ConfigManager(chat)),
        ):
            models = llm.get_available_models()
        self.assertEqual(models["memory"].model_list, ["m2"])
        self.assertEqual(models["utils"].model_list, ["m1"])
        self.assertEqual(models["emoji"].model_list, ["m1"])

    def test_no_chat_provider_means_no_models(self) -> None:
        with patch.object(llm, "_chat_config", return_value=_chat(providers={"embed": {"provider_type": "embedding"}})):
            self.assertEqual(llm.get_available_models(), {})


class HostEmbeddingTests(unittest.IsolatedAsyncioTestCase):
    """embedding / image_embedding 各自选择嵌入 Provider。"""

    providers = {
        "main": {"provider_type": "chat", "model": "m1"},
        "text1": {"provider_type": "embedding", "model": "e1"},
        "text2": {"provider_type": "embedding", "model": "e2"},
    }

    def test_uses_task_provider_then_first(self) -> None:
        cases = (
            ({}, "text1"),
            ({"embedding": "text2"}, "text2"),
            ({"embedding": "main"}, "text1"),
        )
        for routing_map, expected in cases:
            chat = _chat(routing_map, self.providers)
            with self.subTest(routing_map=routing_map), patch.object(llm, "_chat_config", return_value=chat):
                self.assertEqual(llm.embedding_provider_name(), expected)

    def test_no_embedding_provider_raises(self) -> None:
        chat = _chat(providers={"main": {"provider_type": "chat"}})
        with patch.object(llm, "_chat_config", return_value=chat), self.assertRaisesRegex(RuntimeError, "未配置嵌入"):
            llm.embedding_provider_name()

    async def test_image_embedding_requires_provider_support(self) -> None:
        chat = _chat({"image_embedding": "text2"}, self.providers)
        plain = type("Plain", (), {"model_name": "e2"})()
        with (
            patch.object(llm, "_chat_config", return_value=chat),
            patch.object(llm.provider_manager, "get_embedding_provider", AsyncMock(return_value=plain)),
            self.assertRaisesRegex(RuntimeError, "不支持图片嵌入"),
        ):
            await llm.EmbeddingServiceClient().embed_image(b"img")

    async def test_image_embedding_returns_vector(self) -> None:
        class _ImageProvider:
            model_name = "clip"

            async def embed_image(self, data: bytes) -> list[float]:
                return [1.0, 0.0]

        chat = _chat({"image_embedding": "text2"}, self.providers)
        with (
            patch.object(llm, "_chat_config", return_value=chat),
            patch.object(
                llm.provider_manager, "get_embedding_provider", AsyncMock(return_value=_ImageProvider())
            ) as getter,
        ):
            result = await llm.EmbeddingServiceClient().embed_image(b"img")
        getter.assert_awaited_once_with("text2")
        self.assertEqual(result.embedding, [1.0, 0.0])
        self.assertEqual((result.model_name, result.api_provider), ("clip", "text2"))

    async def test_embed_texts_uses_routed_provider(self) -> None:
        class _TextProvider:
            model_name = "e2"

            async def embed(self, texts: list[str]) -> list[list[float]]:
                return [[float(len(text))] for text in texts]

        chat = _chat({"embedding": "text2"}, self.providers)
        with (
            patch.object(llm, "_chat_config", return_value=chat),
            patch.object(llm.provider_manager, "get_embedding_provider", AsyncMock(return_value=_TextProvider())),
        ):
            results = await llm.embed_texts(["ab", "c"])
        self.assertEqual([item.embedding for item in results], [[2.0], [1.0]])


class HostSessionBindingTests(unittest.IsolatedAsyncioTestCase):
    """文本生成把会话 ID 带进 Provider 选择，后台任务也能跟随 # provider。"""

    def _lease(self, seen: list[tuple[str, str]]):
        class _Lease:
            async def __aenter__(self):
                return SimpleNamespace(name="bound", chat=AsyncMock(return_value=SimpleNamespace(content="ok")))

            async def __aexit__(self, *exc):
                return False

        def factory(task: str, session_id: str = ""):
            seen.append((task, session_id))
            return _Lease()

        return factory

    async def test_request_session_is_forwarded(self) -> None:
        seen: list[tuple[str, str]] = []
        with patch.object(llm, "fallback_provider", self._lease(seen)):
            await llm.generate(llm.LLMServiceRequest(task_name="memory", prompt="hi", session_id="conversation"))
        self.assertEqual([session for _, session in seen], ["conversation"])

    async def test_person_scope_supplies_missing_session(self) -> None:
        seen: list[tuple[str, str]] = []
        with patch.object(llm, "fallback_provider", self._lease(seen)), person_scope("group-session"):
            await llm.LLMServiceClient(task_name="utils").generate_response("hi")
        self.assertEqual([session for _, session in seen], ["group-session"])

    async def test_explicit_session_wins_over_scope(self) -> None:
        seen: list[tuple[str, str]] = []
        with patch.object(llm, "fallback_provider", self._lease(seen)), person_scope("scope-session"):
            await llm.LLMServiceClient(task_name="utils", session_id="explicit").generate_response("hi")
        self.assertEqual([session for _, session in seen], ["explicit"])

    async def test_heuristic_impression_uses_tenant(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.amemorix import inject

        context = inject._RecallContext(tenant="group-session", platform="onebot11", group_id="1", user_id="2")
        with patch.object(inject, "LLMServiceClient") as client_cls:
            client_cls.return_value.generate_response = AsyncMock(return_value=SimpleNamespace(response="印象"))
            self.assertEqual(await inject.HeuristicRecall()._impression(context), "印象")
        self.assertEqual(client_cls.call_args.kwargs["session_id"], "group-session")


if __name__ == "__main__":
    unittest.main()
