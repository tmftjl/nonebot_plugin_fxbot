"""能力开关：工具结果整理与子代理派发。"""

from __future__ import annotations

import json
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module, capabilities as capabilities_module
from src.plugins.nonebot_plugin_fxbot.chat.focus import focus_mode_manager
from src.plugins.nonebot_plugin_fxbot.chat.tools import result as result_module, subagent
from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.tools.store import ToolResultStore
from src.plugins.nonebot_plugin_fxbot.chat.tools.types import ToolError, ToolContext
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)
from src.plugins.nonebot_plugin_fxbot.chat.providers.entities import LLMResponse


def _caps(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "tool_result_condense": False,
        "tool_result_condense_threshold": 3000,
        "planner_subagent": False,
        "advanced_sub_agent": False,
    }
    values.update(overrides)
    return values


class CapabilitiesConfigTests(unittest.TestCase):
    """默认值、校验与容错。"""

    def test_defaults(self) -> None:
        self.assertEqual(
            SYSTEM_DEFAULTS["chat"]["capabilities"],
            {
                "tool_result_condense": False,
                "tool_result_condense_threshold": 3000,
                "planner_subagent": False,
                "advanced_sub_agent": False,
            },
        )

    def test_validation(self) -> None:
        validate_system_config({"chat": {"capabilities": _caps(tool_result_condense=True)}})
        with self.assertRaisesRegex(ValueError, "tool_result_condense 必须是布尔值"):
            validate_system_config({"chat": {"capabilities": _caps(tool_result_condense="yes")}})
        with self.assertRaisesRegex(ValueError, "threshold"):
            validate_system_config({"chat": {"capabilities": _caps(tool_result_condense_threshold=499)}})
        with self.assertRaisesRegex(ValueError, "chat.capabilities 必须是对象"):
            validate_system_config({"chat": {"capabilities": []}})

    def test_config_merges_defaults_and_tolerates_errors(self) -> None:
        class _Manager:
            def __init__(self, chat: dict) -> None:
                self.chat = chat

            def get_system(self) -> dict:
                return {"chat": self.chat}

        chat = {"capabilities": {"planner_subagent": True}}
        with patch.object(capabilities_module, "get_config_manager", return_value=_Manager(chat)):
            values = capabilities_module.capabilities_config()
        self.assertTrue(values["planner_subagent"])
        self.assertEqual(values["tool_result_condense_threshold"], 3000)
        with patch.object(capabilities_module, "get_config_manager", side_effect=RuntimeError):
            self.assertFalse(capabilities_module.capabilities_config()["tool_result_condense"])

    def test_threshold_is_clamped(self) -> None:
        for raw, expected in ((100, 500), (999999, 30000), ("bad", 3000)):
            with self.subTest(raw=raw):
                with patch.object(
                    capabilities_module, "capabilities_config", return_value=_caps(tool_result_condense_threshold=raw)
                ):
                    self.assertEqual(capabilities_module.condense_threshold(), expected)


class CondenseTests(unittest.IsolatedAsyncioTestCase):
    """超长成功结果才整理，失败或没有缩短时保留原文。"""

    def test_gating(self) -> None:
        with patch.object(result_module, "capabilities_config", return_value=_caps(tool_result_condense=True)):
            self.assertTrue(result_module.should_condense_tool_result("web_search", "字" * 3000, success=True))
            self.assertFalse(result_module.should_condense_tool_result("web_search", "字" * 2999, success=True))
            self.assertFalse(result_module.should_condense_tool_result("web_search", "字" * 3000, success=False))
            self.assertFalse(result_module.should_condense_tool_result("read_tool_result", "字" * 3000, success=True))
        with patch.object(result_module, "capabilities_config", return_value=_caps()):
            self.assertFalse(result_module.should_condense_tool_result("web_search", "字" * 3000, success=True))

    def test_task_text_is_self_contained(self) -> None:
        text = result_module.build_tool_result_condense_task("web_search", {"query": "天气"}, "字" * 25000)
        self.assertIn("web_search", text)
        self.assertIn('"query": "天气"', text)
        self.assertIn("只提供了前 20000 字", text)
        self.assertIn("字" * 20000, text)
        self.assertNotIn("字" * 20001, text)

    async def test_condense_saves_original_and_returns_pointer(self) -> None:
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="整理后的结论")))
        store = ToolResultStore()
        with (
            patch.object(result_module, "tool_result_store", store),
            patch.object(result_module, "fallback_provider", lambda *a, **k: _Lease(provider)),
        ):
            text = await result_module.condense_tool_result(
                "web_search", "{}", "字" * 5000, session_id="s-1", timeout=5
            )
        self.assertIn("r1", text or "")
        self.assertTrue((text or "").endswith("整理后的结论"))
        self.assertEqual(len(store.get("s-1", "r1").content), 5000)  # type: ignore[union-attr]

    async def test_condense_keeps_original_when_not_shorter_or_failed(self) -> None:
        long_provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="字" * 6000)))
        with (
            patch.object(result_module, "tool_result_store", ToolResultStore()),
            patch.object(result_module, "fallback_provider", lambda *a, **k: _Lease(long_provider)),
        ):
            self.assertIsNone(
                await result_module.condense_tool_result("web_search", "{}", "字" * 5000, session_id="s", timeout=5)
            )
        broken = SimpleNamespace(chat=AsyncMock(side_effect=RuntimeError("boom")))
        with (
            patch.object(result_module, "fallback_provider", lambda *a, **k: _Lease(broken)),
            patch.object(result_module, "logger"),
        ):
            self.assertIsNone(
                await result_module.condense_tool_result("web_search", "{}", "字" * 5000, session_id="s", timeout=5)
            )

    async def test_prepare_condenses_success_only(self) -> None:
        with (
            patch.object(result_module, "should_condense_tool_result", return_value=True),
            patch.object(result_module, "condense_tool_result", AsyncMock(return_value="结论")),
        ):
            content = await result_module.prepare_tool_result_content(
                {"ok": True, "result": "字" * 5000}, session_id="s"
            )
            self.assertEqual(json.loads(content), {"ok": True, "result": "结论"})
            failed = await result_module.prepare_tool_result_content(
                {"ok": False, "error": "错" * 5000}, session_id="s"
            )
        self.assertFalse(json.loads(failed)["ok"])

    async def test_prepare_falls_back_when_condense_returns_none(self) -> None:
        with (
            patch.object(result_module, "should_condense_tool_result", return_value=True),
            patch.object(result_module, "condense_tool_result", AsyncMock(return_value=None)),
        ):
            content = await result_module.prepare_tool_result_content(
                {"ok": True, "result": "字" * 5000}, session_id="s"
            )
        self.assertEqual(json.loads(content)["ok"], True)


class _Lease:
    def __init__(self, provider: object) -> None:
        self.provider = provider

    async def __aenter__(self) -> object:
        return self.provider

    async def __aexit__(self, *exc: object) -> None:
        return None


class ClearToolResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_clear_session_discards_tool_results_under_session_lock(self) -> None:
        service = service_module.ChatService(ChatSessionStore())
        store = ToolResultStore()
        store.save("s-1", "web_search", "old")
        clear = store.clear

        def clear_locked(session_id: str) -> None:
            self.assertTrue(service.session_store.lock(session_id).locked())
            clear(session_id)

        with (
            patch.object(service_module, "tool_result_store", store),
            patch.object(store, "clear", side_effect=clear_locked) as clear_mock,
            patch.object(service_module.memory_state, "is_ready", return_value=False),
            patch.object(focus_mode_manager, "clear_context", new_callable=AsyncMock),
        ):
            await service.clear_session("s-1")

        clear_mock.assert_called_once_with("s-1")
        self.assertIsNone(store.get("s-1", "r1"))
        self.assertEqual(store.save("s-1", "web_search", "new"), "r2")
        self.assertIsNone(store.get("s-1", "r1"))


class SubagentToolTests(unittest.IsolatedAsyncioTestCase):
    """授权、同步多轮与后台任务。"""

    def _context(self, session_id: str = "s-1") -> ToolContext:
        return ToolContext(session_id=session_id, metadata={"allowed_tools": ["query_memory"]})

    def test_grants_only_caller_tools(self) -> None:
        context = self._context()
        self.assertEqual(subagent._granted_tools(context, ["query_memory", "web_search"]), ["query_memory"])
        self.assertEqual(subagent._granted_tools(context, ["query_memory", "subagent"]), ["query_memory"])
        self.assertEqual(subagent._granted_tools(context, "query_memory"), [])

    async def test_rejects_empty_requests(self) -> None:
        with self.assertRaisesRegex(ToolError, "task"):
            await subagent.subagent_tool(self._context(), SimpleNamespace(), task="", tools=["query_memory"])
        with self.assertRaisesRegex(ToolError, "没有可以授权"):
            await subagent.subagent_tool(self._context(), SimpleNamespace(), task="查", tools=["web_search"])

    async def test_sync_run_executes_granted_tool(self) -> None:
        provider = SimpleNamespace(
            chat=AsyncMock(
                side_effect=[
                    LLMResponse(
                        content="",
                        tool_calls=[
                            {"id": "c1", "type": "function", "function": {"name": "query_memory", "arguments": "{}"}}
                        ],
                    ),
                    LLMResponse(content="结论"),
                ]
            )
        )
        execute = AsyncMock(return_value={"ok": True, "result": "资料"})
        with (
            patch.object(subagent, "fallback_provider", lambda *a, **k: _Lease(provider)),
            patch.object(subagent, "execute_tool", execute),
            patch.object(subagent, "capabilities_config", return_value=_caps()),
        ):
            result = await subagent.subagent_tool(self._context(), SimpleNamespace(), task="查", tools=["query_memory"])
        self.assertEqual(result, "结论")
        execute.assert_awaited_once()
        self.assertEqual(provider.chat.await_count, 2)

    async def test_background_task_delivers_conclusion(self) -> None:
        store = ChatSessionStore()
        manager = subagent._TaskManager()
        with (
            patch.object(subagent, "subagent_tasks", manager),
            patch.object(subagent, "default_session_store", store),
            patch.object(subagent, "_run_sub_agent", AsyncMock(return_value="后台结论")),
            patch.object(subagent, "capabilities_config", return_value=_caps(advanced_sub_agent=True)),
        ):
            data = await subagent.subagent_tool(self._context(), SimpleNamespace(), task="查", tools=["query_memory"])
            task = manager.get(str(data["task_id"]))
            self.assertIsNotNone(task)
            await task.handle  # type: ignore[union-attr]
        self.assertEqual(task.status, "completed")  # type: ignore[union-attr]
        history = store.get("s-1")
        self.assertIn("subagent_result", history[0]["content"])
        self.assertIn("后台结论", history[0]["content"])

    async def test_clear_session_discards_background_results(self) -> None:
        for cancellation in ("propagate", "return", "raise"):
            with self.subTest(cancellation=cancellation):
                manager = subagent._TaskManager()
                service = service_module.ChatService()
                store = subagent.default_session_store
                self.assertIs(service.session_store, store)
                session_id = f"subagent-clear-{cancellation}"
                self.addCleanup(store.clear, session_id)
                started = asyncio.Event()
                release = asyncio.Event()

                async def run(*args: object) -> str:
                    started.set()
                    try:
                        await release.wait()
                    except asyncio.CancelledError:
                        if cancellation == "propagate":
                            raise
                        await release.wait()
                        if cancellation == "raise":
                            raise RuntimeError("late failure") from None
                    return "stale conclusion"

                with (
                    patch.object(subagent, "subagent_tasks", manager),
                    patch.object(subagent, "_run_sub_agent", side_effect=run),
                    patch.object(service_module.memory_state, "is_ready", return_value=False),
                    patch.object(focus_mode_manager, "clear_context", new_callable=AsyncMock),
                ):
                    task = manager.start(
                        self._context(session_id), SimpleNamespace(), description="old", granted_tools=[], max_rounds=1
                    )
                    try:
                        await asyncio.wait_for(started.wait(), timeout=5)
                        await service.clear_session(session_id)
                        await asyncio.sleep(0)
                    finally:
                        release.set()
                        await asyncio.gather(task.handle, return_exceptions=True)
                self.assertEqual(store.get(session_id), [])
                self.assertEqual(task.status, "stopped")

    async def test_cancel_session_is_scoped_and_allows_new_tasks(self) -> None:
        manager = subagent._TaskManager()
        store = ChatSessionStore()
        with (
            patch.object(subagent, "default_session_store", store),
            patch.object(subagent, "_run_sub_agent", AsyncMock(return_value="new conclusion")),
        ):
            pending = manager.start(
                self._context(), SimpleNamespace(), description="old", granted_tools=[], max_rounds=1
            )
            other = manager.start(
                self._context("s-2"), SimpleNamespace(), description="other", granted_tools=[], max_rounds=1
            )
            manager.cancel_session("s-1")
            manager.cancel_session("s-1")
            current = manager.start(
                self._context(), SimpleNamespace(), description="new", granted_tools=[], max_rounds=1
            )
            await asyncio.gather(pending.handle, other.handle, current.handle, return_exceptions=True)
        self.assertTrue(pending.handle.cancelled())
        self.assertEqual(pending.status, "stopped")
        self.assertEqual(other.status, "completed")
        self.assertEqual(current.status, "completed")
        self.assertEqual(len(store.get("s-1")), 1)
        self.assertEqual(len(store.get("s-2")), 1)

    async def test_cancel_session_tolerates_finished_tasks(self) -> None:
        manager = subagent._TaskManager()
        store = ChatSessionStore()
        with (
            patch.object(subagent, "default_session_store", store),
            patch.object(subagent, "_run_sub_agent", AsyncMock(return_value="old conclusion")),
        ):
            task = manager.start(self._context(), SimpleNamespace(), description="old", granted_tools=[], max_rounds=1)
            await task.handle
            manager.cancel_session("s-1")
            manager.cancel_session("missing")
            store.clear("s-1")
            subagent._deliver(task)
        self.assertEqual(task.status, "completed")
        self.assertFalse(task.handle.cancelled())
        self.assertEqual(store.get("s-1"), [])

    async def test_background_refuses_when_full(self) -> None:
        manager = subagent._TaskManager()
        for index in range(3):
            manager._tasks[f"subagent-{index}"] = subagent.SubagentTask(
                task_id=f"subagent-{index}", session_id="s-1", description="旧", granted_tools=[]
            )
        with (
            patch.object(subagent, "subagent_tasks", manager),
            patch.object(subagent, "capabilities_config", return_value=_caps(advanced_sub_agent=True)),
        ):
            with self.assertRaisesRegex(ToolError, "后台运行"):
                await subagent.subagent_tool(self._context(), SimpleNamespace(), task="查", tools=["query_memory"])

    async def test_status_and_stop_are_session_scoped(self) -> None:
        manager = subagent._TaskManager()
        handle = SimpleNamespace(cancel=lambda: None)
        running = subagent.SubagentTask(
            task_id="subagent-1", session_id="s-1", description="查", granted_tools=["query_memory"], handle=handle
        )
        other = subagent.SubagentTask(task_id="subagent-2", session_id="s-2", description="别的会话", granted_tools=[])
        manager._tasks = {"subagent-1": running, "subagent-2": other}
        with patch.object(subagent, "subagent_tasks", manager):
            detail = await subagent.subagent_status_tool(self._context(), SimpleNamespace(), task_id="subagent-1")
            listing = await subagent.subagent_status_tool(self._context(), SimpleNamespace())
            with self.assertRaisesRegex(ToolError, "不存在"):
                await subagent.subagent_status_tool(self._context(), SimpleNamespace(), task_id="subagent-2")
            stopped = await subagent.subagent_stop_tool(self._context(), SimpleNamespace(), task_id="subagent-1")
            with self.assertRaisesRegex(ToolError, "不存在"):
                await subagent.subagent_stop_tool(self._context(), SimpleNamespace(), task_id="subagent-2")
        self.assertEqual(detail["status"], "running")
        self.assertEqual([item["task_id"] for item in listing["tasks"]], ["subagent-1"])
        self.assertEqual(stopped["status"], "stopped")

    def test_allowed_tools_follow_capabilities(self) -> None:
        specs = [
            SimpleNamespace(name=name)
            for name in ("subagent", "subagent_status", "subagent_stop", "web_search", "like")
        ]
        tools = [{"function": {"name": spec.name}} for spec in specs]

        async def _names(caps: dict) -> set[str]:
            with (
                patch.object(generator.default_registry, "list", return_value=specs),
                patch.object(generator.default_registry, "to_openai_tools", return_value=tools),
                patch.object(generator, "tool_allowed", new=AsyncMock(return_value=True)),
                patch.object(generator, "web_runtime", return_value=SimpleNamespace(enabled=True)),
                patch.object(generator, "capabilities_config", return_value=caps),
            ):
                names, _ = await generator.allowed_tools(SimpleNamespace())
            return names

        self.assertEqual(asyncio.run(_names(_caps())), {"web_search", "like"})
        self.assertEqual(asyncio.run(_names(_caps(planner_subagent=True))), {"web_search", "like", "subagent"})
        self.assertEqual(
            asyncio.run(_names(_caps(planner_subagent=True, advanced_sub_agent=True))),
            {"web_search", "like", "subagent", "subagent_status", "subagent_stop"},
        )


if __name__ == "__main__":
    unittest.main()
