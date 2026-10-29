"""任务级 Provider 路由与调用边界回退。"""

from __future__ import annotations

import copy
import asyncio
import unittest
from typing import Any
from contextlib import asynccontextmanager
from unittest.mock import patch

from src.plugins.nonebot_plugin_fxbot.chat.providers import routing
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config


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
    """候选顺序、可重试判断与包装器换 Provider 的边界。"""

    def _chat(self, routing_map: dict[str, str], fallback: list[str]) -> dict[str, Any]:
        return {
            "model_routing": routing_map,
            "provider_fallback": fallback,
            "provider": "",
        }

    def test_candidate_order_dedup_and_cap(self) -> None:
        chat = self._chat({"reply": "primary"}, ["backup", "third", "primary"])
        with (
            patch.object(routing, "get_config_manager", return_value=_FakeConfigManager(chat)),
            patch.object(routing, "get_binding", return_value="bound"),
            patch.object(
                routing.provider_manager,
                "list_providers_by_type",
                return_value=["primary", "backup", "third", "bound"],
            ),
        ):
            self.assertEqual(routing.candidate_providers("reply", "tenant"), ["primary", "backup"])
            self.assertEqual(routing.candidate_providers("vision", "tenant"), ["bound", "backup"])

    def test_invalid_configured_names_fall_back_to_default(self) -> None:
        chat = self._chat({"reply": "missing"}, ["also-missing"])
        with (
            patch.object(routing, "get_config_manager", return_value=_FakeConfigManager(chat)),
            patch.object(routing, "get_binding", return_value=""),
            patch.object(routing.provider_manager, "list_providers_by_type", return_value=["primary"]),
        ):
            self.assertEqual(routing.candidate_providers("reply", "tenant"), [""])

    def test_retryable_errors(self) -> None:
        self.assertTrue(routing.is_retryable(asyncio.TimeoutError()))
        self.assertTrue(routing.is_retryable(ConnectionError("reset")))
        self.assertTrue(routing.is_retryable(_StatusError(503)))
        self.assertFalse(routing.is_retryable(_StatusError(400)))
        self.assertFalse(routing.is_retryable(ValueError("bad")))

    async def test_chat_switches_on_retryable_error(self) -> None:
        used: list[str] = []
        providers = {"primary": _Provider("primary", _StatusError(502)), "backup": _Provider("backup")}
        with patch.object(routing.provider_manager, "lease", _lease_factory(providers, used)):
            async with routing.FallbackProvider(["primary", "backup"], task="reply") as pool:
                result = await pool.chat(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(result, "reply:backup")
        self.assertEqual(used, ["primary", "backup"])
        self.assertEqual(pool.name, "backup")

    async def test_chat_does_not_switch_on_client_error(self) -> None:
        used: list[str] = []
        providers = {"primary": _Provider("primary", _StatusError(400)), "backup": _Provider("backup")}
        with patch.object(routing.provider_manager, "lease", _lease_factory(providers, used)):
            async with routing.FallbackProvider(["primary", "backup"]) as pool:
                with self.assertRaises(_StatusError):
                    await pool.chat(messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(used, ["primary"])
        self.assertEqual(providers["backup"].calls, 0)

    async def test_chat_does_not_switch_after_tool_results(self) -> None:
        used: list[str] = []
        providers = {"primary": _Provider("primary", asyncio.TimeoutError()), "backup": _Provider("backup")}
        messages = [
            {"role": "user", "content": "hi"},
            {"role": "tool", "tool_call_id": "1", "content": "{}"},
        ]
        with patch.object(routing.provider_manager, "lease", _lease_factory(providers, used)):
            async with routing.FallbackProvider(["primary", "backup"]) as pool:
                with self.assertRaises(asyncio.TimeoutError):
                    await pool.chat(messages=messages)
        self.assertEqual(used, ["primary"])

    async def test_chat_json_switches_and_reports_provider(self) -> None:
        used: list[str] = []
        providers = {"primary": _Provider("primary", asyncio.TimeoutError()), "backup": _Provider("backup")}
        chat = self._chat({"extract": "primary"}, ["backup"])
        with (
            patch.object(routing, "get_config_manager", return_value=_FakeConfigManager(chat)),
            patch.object(routing, "get_binding", return_value=""),
            patch.object(routing.provider_manager, "list_providers_by_type", return_value=["primary", "backup"]),
            patch.object(routing.provider_manager, "lease", _lease_factory(providers, used)),
        ):
            async with routing.fallback_provider("extract", "tenant") as pool:
                data = await pool.chat_json([{"role": "user", "content": "hi"}])
        self.assertEqual(data, {"provider": "backup"})
        self.assertEqual(pool.name, "backup")


class RoutedTaskTests(unittest.TestCase):
    """新增任务键只在显式配置时生效，否则沿用旧任务。"""

    def test_configured_task_wins(self) -> None:
        chat = {"model_routing": {"learner": " fast ", "extract": "deep"}}
        with patch.object(routing, "get_config_manager", return_value=_FakeConfigManager(chat)):
            self.assertEqual(routing.routed_task("learner", "extract"), "learner")

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


class FallbackConfigTests(unittest.TestCase):
    """fallback 配置的默认值与校验。"""

    def test_default_is_empty_list(self) -> None:
        self.assertEqual(SYSTEM_DEFAULTS["chat"]["provider_fallback"], [])

    def test_accepts_string_list(self) -> None:
        chat = copy.deepcopy(SYSTEM_DEFAULTS["chat"])
        chat["providers"] = {"backup": {"provider_type": "chat"}, "third": {"provider_type": "chat"}}
        chat["provider_fallback"] = ["backup", "third"]
        validate_system_config({"chat": chat})

    def test_rejects_non_string_list(self) -> None:
        for fallback in ("backup", [1], {"name": "backup"}):
            chat = copy.deepcopy(SYSTEM_DEFAULTS["chat"])
            chat["provider_fallback"] = fallback
            with self.subTest(fallback=fallback), self.assertRaises(ValueError):
                validate_system_config({"chat": chat})
