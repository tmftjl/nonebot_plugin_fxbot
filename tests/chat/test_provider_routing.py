"""任务级 Provider 路由与调用边界回退。"""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from contextlib import asynccontextmanager
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat.providers import routing
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS


class _FakeConfigManager:
    def __init__(self, chat: dict[str, Any]) -> None:
        self.chat = chat

    def get_system(self) -> dict[str, Any]:
        return {"chat": self.chat}


class _Provider:
    """记录调用次数；可配置每次调用抛出的异常。"""

    def __init__(self, name: str, error: BaseException | None = None) -> None:
        self.provider_id = name
        self.error = error
        self.calls = 0

    async def chat(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return f"reply:{self.provider_id}"

    async def chat_json(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return {"provider": self.provider_id}


class _StatusError(RuntimeError):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


def _lease_factory(providers: dict[str, _Provider], used: list[str]):
    @asynccontextmanager
    async def _lease(name: str = ""):
        used.append(name)
        yield providers[name or "default"]

    return _lease


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    """任务与会话选择单一模型，请求失败不切换其他 Provider。"""

    def test_task_and_session_binding_select_one_provider(self) -> None:
        config = _FakeConfigManager({"model_routing": {"reply": "primary"}})
        with (
            patch.object(routing, "get_config_manager", return_value=config),
            patch.object(routing, "get_binding", return_value="bound"),
            patch.object(routing.provider_manager, "list_providers_by_type", return_value=["primary", "bound"]),
        ):
            self.assertEqual(routing.candidate_providers("reply", "own"), ["primary"])
            self.assertEqual(routing.candidate_providers("utils", "own"), ["bound"])

    async def test_failed_request_does_not_lease_a_different_provider(self) -> None:
        used = []
        providers = {"primary": _Provider("primary", asyncio.TimeoutError())}
        with patch.object(routing.provider_manager, "lease", _lease_factory(providers, used)):
            async with routing.TaskProvider(["primary"], task="reply") as provider:
                with self.assertRaises(asyncio.TimeoutError):
                    await provider.chat(messages=[{"role": "user", "content": "你好"}])
        self.assertEqual(used, ["primary"])
        self.assertEqual(providers["primary"].calls, 1)

    async def test_json_reports_selected_provider(self) -> None:
        used = []
        providers = {"primary": _Provider("primary")}
        with patch.object(routing.provider_manager, "lease", _lease_factory(providers, used)):
            async with routing.TaskProvider(["primary"], task="utils") as provider:
                result = await provider.chat_json(messages=[{"role": "user", "content": "你好"}])
                self.assertEqual(provider.name, "primary")
        self.assertEqual(result, {"provider": "primary"})
        self.assertEqual(used, ["primary"])


class RoutedTaskTests(unittest.TestCase):
    """显式任务分配优先，否则使用上游规定的备用任务。"""

    def test_configured_task_wins(self) -> None:
        chat = {"model_routing": {"learner": " fast ", "utils": "deep"}}
        with patch.object(routing, "get_config_manager", return_value=_FakeConfigManager(chat)):
            self.assertEqual(routing.routed_task("learner", "utils"), "learner")

    def test_falls_back_when_unset_or_invalid(self) -> None:
        with patch.object(routing, "get_config_manager", return_value=_FakeConfigManager({"model_routing": {}})):
            self.assertEqual(routing.routed_task("emoji", "planner"), "planner")
        with patch.object(
            routing, "get_config_manager", return_value=_FakeConfigManager({"model_routing": {"emoji": " "}})
        ):
            self.assertEqual(routing.routed_task("emoji", "planner"), "planner")
        with patch.object(routing, "get_config_manager", return_value=_FakeConfigManager({"model_routing": []})):
            self.assertEqual(routing.routed_task("emoji", "planner"), "planner")
        with patch.object(routing, "get_config_manager", side_effect=RuntimeError):
            self.assertEqual(routing.routed_task("emoji", "planner"), "planner")

    def test_without_fallback_returns_task(self) -> None:
        with patch.object(routing, "get_config_manager", side_effect=RuntimeError):
            self.assertEqual(routing.routed_task("replyer"), "replyer")

    def test_defaults_include_ported_tasks(self) -> None:
        model_routing = SYSTEM_DEFAULTS["chat"]["model_routing"]
        self.assertTrue({"extract", "style"}.isdisjoint(model_routing))
        for task in (
            "memory",
            "utils",
            "learner",
            "fast_model",
            "emoji",
            "voice",
            "embedding",
            "image_embedding",
            "web_search",
        ):
            self.assertEqual(model_routing[task], "", task)

    def test_profile_routing_key_is_gone(self) -> None:
        self.assertNotIn("profile", SYSTEM_DEFAULTS["chat"]["model_routing"])
