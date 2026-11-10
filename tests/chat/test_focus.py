"""Focus 共享组：配置归属、检查点、入站排队、控制工具与会话内回复。"""

from __future__ import annotations

import json
import base64
import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks, service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.focus import (
    tools as tools_module,
    context as context_module,
    manager as manager_module,
    group_runtime as group_runtime_module,
)
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolSpec, executor as executor_module, default_registry
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.focus.tools import FocusToolRouter
from src.plugins.nonebot_plugin_fxbot.chat.focus.context import FocusEntry, FocusCheckpointStore
from src.plugins.nonebot_plugin_fxbot.chat.focus.manager import FocusModeManager, _scope_key
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config
from src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime import FocusMember, FocusMatcher, FocusGroupRuntime

SESSION = "test:bot:group:g1"
OTHER = "test:bot:group:g2"


def _request(**values: object) -> ChatRequest:
    fields: dict[str, object] = {
        "session_id": SESSION,
        "user_id": "u1",
        "group_id": "g1",
        "text": "你好",
        "turn_id": "turn-1",
        "dedupe_key": "msg:1",
        "trigger_kind": "mention",
        "message_id": "1",
        "user_name": "Alice",
        "platform": "test",
    }
    fields.update(values)
    return ChatRequest(**fields)  # type: ignore[arg-type]


def _member(request: ChatRequest) -> FocusMember:
    return FocusMember(
        session_id=request.session_id,
        bot=SimpleNamespace(self_id="bot"),
        event=SimpleNamespace(),
        target={"user_id": request.user_id},
        request=request,
        is_group=request.is_group,
    )


def _focus_config(**values: object) -> dict[str, object]:
    config: dict[str, object] = {
        "enabled": True,
        "on_private": False,
        "chat_whitelist": [],
        "groups": [],
    }
    config.update(values)
    return config


class FocusTraceContextTests(unittest.IsolatedAsyncioTestCase):
    """共享规划绑定组身份和参与成员，成功、失败与取消后都恢复外层上下文。"""

    async def test_group_context_is_bound_and_restored(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
        from src.plugins.nonebot_plugin_fxbot.chat.trace_context import bind_turn, unbind_turn, current_turn

        for failure in (None, RuntimeError("failed"), asyncio.CancelledError()):
            with self.subTest(failure=failure):
                seen = []

                async def chat(**kwargs):
                    seen.append(current_turn())
                    if failure is not None:
                        raise failure
                    return LLMResponse(content="ok")

                provider = SimpleNamespace(provider_id="p", get_model=lambda: "m", chat=chat)
                lease = MagicMock()
                lease.__aenter__.return_value = provider
                lease.__aexit__.return_value = False
                group = FocusGroupRuntime("group:shared-trace")
                group.members = {SESSION: _member(_request()), OTHER: _member(_request(session_id=OTHER))}
                token = bind_turn("outer", "outer-turn")
                try:
                    with (
                        patch.object(group_runtime_module, "fallback_provider", return_value=lease),
                        patch.object(group_runtime_module, "guard", AsyncMock(return_value=True)),
                        patch.object(group_runtime_module, "planner_snapshot", return_value={}),
                    ):
                        if failure is None:
                            await group._chat([], [])
                        else:
                            with self.assertRaises(type(failure)):
                                await group._chat([], [])
                    self.assertEqual(current_turn().session_id, "outer")
                finally:
                    unbind_turn(token)
                self.assertEqual(len(seen), 1)
                self.assertEqual(seen[0].session_id, group.scope_id)
                self.assertEqual(seen[0].related_sessions, (SESSION, OTHER))
                self.assertTrue(seen[0].turn_id)


class FocusSystemPromptTests(unittest.IsolatedAsyncioTestCase):
    """共享规划只贡献一次系统段，参考资料不混入系统指令。"""

    async def test_shared_request_has_one_group_scoped_section(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import prompts

        seen = []

        def contribute(context):
            seen.append(context)
            return hooks.SystemPromptSection("独立系统段", 100)

        chat = {"persona": "小鱼", "behavior": {}}
        manager = SimpleNamespace(get_system=lambda: {"chat": chat})
        group = FocusGroupRuntime("shared-test")
        group.members = {SESSION: _member(_request()), OTHER: _member(_request(session_id=OTHER))}
        with (
            patch.object(hooks, "_hooks", {}),
            patch.object(group_runtime_module, "get_config_manager", return_value=manager),
            patch.object(prompts, "get_persona_text", return_value="# 人格特征\n友好"),
            patch.object(group_runtime_module, "get_bot_group_role", AsyncMock(return_value="member")),
            patch.object(group_runtime_module, "_memory_block", AsyncMock(return_value="")),
            patch.object(group_runtime_module, "_learning_reference", AsyncMock(return_value="")),
            patch.object(group, "_has_unconsidered", return_value=True),
            patch.object(group_runtime_module.chat_service.session_store, "get", return_value=[]),
        ):
            hooks.register_chat_hook("system_prompt", "test", contribute)
            references = await group._build_references()
            self.assertEqual(len(references), 2)
            self.assertEqual(seen, [])
            messages = group._build_messages(references)
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0], hooks.ChatHookContext("shared-test", phase="focus_planner"))
        self.assertEqual(messages[0]["role"], "system")
        self.assertEqual(messages[0]["content"].count("独立系统段"), 1)
        self.assertNotIn("独立系统段", str(messages[1:]))
        self.assertIn("每次工具调用必须填写非空 session_id", messages[0]["content"])


class FocusConfigTests(unittest.TestCase):
    """白名单、私聊开关与共享组身份。"""

    def test_defaults_are_disabled(self) -> None:
        self.assertEqual(
            SYSTEM_DEFAULTS["chat"]["focus"],
            {"enabled": False, "on_private": False, "chat_whitelist": [], "groups": []},
        )

    def test_validation(self) -> None:
        validate_system_config({"chat": {"focus": _focus_config()}})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"focus": {"enabled": "yes"}}})
        with self.assertRaises(ValueError):
            validate_system_config(
                {"chat": {"focus": {"chat_whitelist": [{"platform": "test", "type": "channel", "item_id": "1"}]}}}
            )
        with self.assertRaises(ValueError):
            validate_system_config({"chat": {"focus": {"groups": [{"targets": [{}]}]}}})

    def test_disabled_and_private(self) -> None:
        manager = FocusModeManager()
        with patch.object(manager_module, "focus_config", return_value=_focus_config(enabled=False)):
            self.assertFalse(manager.is_enabled_for_request(_request()))
        with patch.object(manager_module, "focus_config", return_value=_focus_config(on_private=False)):
            self.assertTrue(manager.is_enabled_for_request(_request()))
            self.assertFalse(manager.is_enabled_for_request(_request(group_id=None, session_id="test:bot:private:u1")))

    def test_whitelist_limits_chats(self) -> None:
        whitelist = [{"platform": "test", "type": "group", "item_id": "g1"}]
        manager = FocusModeManager()
        with patch.object(manager_module, "focus_config", return_value=_focus_config(chat_whitelist=whitelist)):
            self.assertTrue(manager.is_enabled_for_request(_request()))
            self.assertFalse(manager.is_enabled_for_request(_request(session_id=OTHER, group_id="g2")))

    def test_scope_key(self) -> None:
        with patch.object(manager_module, "focus_config", return_value=_focus_config()):
            self.assertEqual(_scope_key(_request()), "session:" + SESSION)
            self.assertNotEqual(_scope_key(_request()), _scope_key(_request(session_id=OTHER)))
        groups = [
            {
                "targets": [
                    {"platform": "test", "type": "group", "item_id": "g1"},
                    {"platform": "test", "type": "group", "item_id": "g2"},
                ]
            }
        ]
        reversed_targets = [{"targets": list(reversed(groups[0]["targets"]))}]
        with patch.object(manager_module, "focus_config", return_value=_focus_config(groups=groups)):
            key = _scope_key(_request())
            self.assertTrue(key.startswith("group:"))
            self.assertEqual(key, _scope_key(_request(session_id=OTHER, group_id="g2")))
            self.assertEqual(
                _scope_key(_request(session_id="test:bot:group:g9", group_id="g9")), "session:test:bot:group:g9"
            )
        with patch.object(manager_module, "focus_config", return_value=_focus_config(groups=reversed_targets)):
            self.assertEqual(_scope_key(_request()), key)


class CheckpointTests(unittest.IsolatedAsyncioTestCase):
    """时间线与等待状态的落盘与恢复。"""

    async def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = FocusCheckpointStore("global", Path(root))
            entry = FocusEntry([{"role": "user", "content": "[a]\n你好"}], {"a"}, 3, {"a": [[3, "msg:1"]]})
            store.save([entry], {"sequence": 3, "considered": {"a": 1}, "seen": ["a\0msg:1"]})
            payload = store.load()
            self.assertEqual(payload["sequence"], 3)
            restored = FocusGroupRuntime("global", store=store)
            await restored.restore()
            self.assertEqual(restored.entries[0].messages, entry.messages)
            self.assertEqual(restored.entries[0].inputs, {"a": [[3, "msg:1"]]})
            self.assertEqual(restored._considered, {"a": 1})

    def test_corrupted_file_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = FocusCheckpointStore("global", Path(root))
            store.path.write_text("{not json", encoding="utf-8")
            self.assertEqual(store.load(), {})

    def test_purge_replace_failure_preserves_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            store = FocusCheckpointStore("global", Path(root))
            store.save([FocusEntry([{"role": "user", "content": "原始内容"}], {SESSION})], {})
            original = store.path.read_bytes()
            with patch.object(context_module.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    FocusCheckpointStore.purge_unloaded_session(SESSION, set(), Path(root))
            self.assertEqual(store.path.read_bytes(), original)
            self.assertEqual(list(Path(root).glob("*.tmp")), [])


class GroupRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """入站排队、去重、等待状态与控制工具。"""

    def setUp(self) -> None:
        self._root = tempfile.TemporaryDirectory()
        self.addCleanup(self._root.cleanup)
        self.store = FocusCheckpointStore("global", Path(self._root.name))
        self.group = FocusGroupRuntime("global", store=self.store)
        patcher = patch.object(FocusGroupRuntime, "_ensure_worker", lambda self: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_planner_samples(self) -> None:
        self.group.add_member(_member(_request()))
        self.group.add_member(_member(_request(session_id=OTHER, group_id="g2")))
        self.group.entries = [FocusEntry([{"role": "user", "content": "input"}], {SESSION, OTHER}, 1)]
        provider = SimpleNamespace(provider_id="fallback", get_model=lambda: "actual-model", chat=AsyncMock())
        lease = MagicMock()
        lease.__aenter__ = AsyncMock(return_value=provider)
        lease.__aexit__ = AsyncMock(return_value=False)
        with (
            patch.object(group_runtime_module, "fallback_provider", return_value=lease) as routing,
            patch.object(FocusToolRouter, "refresh", AsyncMock(return_value=[])),
            patch.object(FocusGroupRuntime, "_build_references", AsyncMock(return_value=[])),
            patch.object(group_runtime_module.chat_repository, "save_planner_sample", AsyncMock()) as save,
        ):
            for names in ([], ["wait"], ["stop"], ["reply", "stop"]):
                with self.subTest(names=names):
                    self.group._wait_until = None
                    provider.chat.return_value = SimpleNamespace(
                        content="",
                        tool_calls=[{"function": {"name": name}} for name in names],
                        to_message_dict=lambda: {"role": "assistant", "content": ""},
                    )

                    async def execute(batch, response, source_session=""):
                        if "wait" in names:
                            self.group._wait_until = 123.0
                        return True

                    with patch.object(FocusGroupRuntime, "_execute_tools", side_effect=execute):
                        await self.group._run_turn()
                    sample = save.await_args.args[0]
                    self.assertEqual(sample.tenant, SESSION)
                    self.assertTrue(sample.turn_id.startswith("focus-"))
                    self.assertEqual(sample.action, "tool:" + ",".join(names) if names else "no_reply")
                    self.assertEqual(sample.provider, "fallback")
                    self.assertEqual(sample.model, "actual-model")
                    features = json.loads(sample.features)
                    self.assertEqual(features["session_count"], 2)
                    self.assertGreaterEqual(features["input_count"], 1)
                    self.assertEqual(features["tool_count"], len(names))
                    self.assertEqual(features["waiting"], "wait" in names)
            self.assertEqual(len({call.args[0].turn_id for call in save.await_args_list}), 4)
            routing.assert_called_with("planner", SESSION)
            save.side_effect = RuntimeError("storage unavailable")
            await self.group._run_turn()

    async def test_health_reports_focus_groups_and_worker_failure(self) -> None:
        manager = FocusModeManager()
        manager.groups["global"] = self.group
        self.group.add_member(_member(_request()))
        self.group._worker = asyncio.create_task(asyncio.Event().wait())
        self.group._wait_until = 123.0
        with (
            patch.object(chat_routes, "focus_mode_manager", manager),
            patch.object(chat_routes, "get_bots", return_value={}),
            patch.object(chat_routes, "is_ready", return_value=True),
            patch.object(chat_routes, "degraded_reason", return_value=""),
            patch.object(chat_routes, "_scheduler_jobs", return_value=[{"id": "fxbot_chat_test"}]),
        ):
            try:
                health = await chat_routes.chat_health()
                self.assertTrue(health["focus_ok"])
                self.assertTrue(health["storage_ready"])
                self.assertTrue(health["scheduler_ready"])
                self.assertEqual(health["online_bots"], 0)
                self.assertIn("enabled", health)
                self.assertEqual(health["degraded_reason"], "")
                self.assertEqual(
                    health["focus_groups"],
                    [
                        {
                            "scope": "global",
                            "sessions": [SESSION],
                            "worker_alive": True,
                            "consecutive_failures": 0,
                            "waiting": True,
                            "checkpoint_path": str(self.store.path),
                        }
                    ],
                )
                health["focus_groups"][0]["sessions"].clear()
                self.assertIn(SESSION, self.group.members)
                self.group._failures = manager_module.FAILURE_THRESHOLD
                self.assertTrue((await chat_routes.chat_health())["focus_ok"])
                self.group._failures += 1
                self.assertFalse((await chat_routes.chat_health())["focus_ok"])
            finally:
                self.group._worker.cancel()
                await asyncio.gather(self.group._worker, return_exceptions=True)

            async def fail():
                raise RuntimeError("worker failed")

            self.group._failures = 0
            self.group._worker = asyncio.create_task(fail())
            await asyncio.gather(self.group._worker, return_exceptions=True)
            health = await chat_routes.chat_health()
            self.assertFalse(health["focus_ok"])
            self.assertFalse(health["focus_groups"][0]["worker_alive"])
            with patch.object(FocusModeManager, "status_snapshot", side_effect=RuntimeError("unavailable")):
                health = await chat_routes.chat_health()
                self.assertEqual(health["focus_groups"], [])
                self.assertTrue(health["focus_ok"])
                self.assertTrue(health["storage_ready"])
            with patch.object(chat_routes, "focus_mode_manager", FocusModeManager()):
                health = await chat_routes.chat_health()
                self.assertEqual(health["focus_groups"], [])
                self.assertTrue(health["focus_ok"])

    async def test_register_dedupes_and_queues(self) -> None:
        request = _request()
        self.group.add_member(_member(request))
        await self.group.register(_member(request), request, is_at=True)
        await self.group.register(_member(request), request, is_at=True)
        self.assertEqual(len(self.group._pending[SESSION]), 1)
        self.assertIn(SESSION, self.group._ready)
        entry = self.group._pending[SESSION][0]
        self.assertTrue(entry.messages[0]["content"].startswith(f"[{SESSION}]"))
        self.assertIn(f'msg_id="{request.message_id or request.dedupe_key}"', entry.messages[0]["content"])
        self.assertIn("Alice", entry.messages[0]["content"])
        self.group._take_ready_inputs()
        self.assertEqual(len(self.group.entries), 1)
        self.assertFalse(self.group._ready)

    async def test_non_at_waits_for_quiet_window(self) -> None:
        request = _request(message_id="2", dedupe_key="msg:2")
        self.group.add_member(_member(request))
        with patch("src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime.quiet_wait", lambda same: 0.0):
            await self.group.register(_member(request), request, is_at=False)
            await asyncio.sleep(0.01)
        self.assertIn(SESSION, self.group._ready)

    async def test_wait_validates_and_pauses(self) -> None:
        request = _request()
        self.group.add_member(_member(request))
        content, paused = self.group.control_wait({"seconds": -1}, SESSION)
        self.assertFalse(paused)
        self.assertFalse(json.loads(content)["ok"])
        content, paused = self.group.control_wait({"seconds": 30}, SESSION)
        self.assertTrue(paused)
        self.assertTrue(json.loads(content)["ok"])
        self.assertIsNotNone(self.group._wait_until)
        self.assertEqual(self.group._wait_session_id, SESSION)
        self.assertEqual(self.group._consecutive_waits, 1)

    async def test_pending_at_wakes_registered_wait(self) -> None:
        request = _request()
        self.group.add_member(_member(request))
        await self.group.register(_member(request), request, is_at=True)
        content, paused = self.group.control_wait({"seconds": 30}, SESSION)
        self.assertTrue(paused)
        self.assertIsNone(self.group._wait_until)
        self.assertIn(SESSION, self.group._ready)
        self.assertIn("@", json.loads(content)["result"])

    async def test_finish_wait_queues_reference(self) -> None:
        request = _request()
        self.group.add_member(_member(request))
        self.group.control_wait({"seconds": 30}, SESSION)
        self.group._ready.clear()
        self.group._finish_wait("等待到期")
        entries = self.group._pending[SESSION]
        self.assertEqual(len(entries), 1)
        self.assertIn("等待结束", entries[0].messages[0]["content"])
        self.assertIn(SESSION, self.group._ready)

    async def test_stop_resets_wait_counter(self) -> None:
        self.group._consecutive_waits = 4
        content, paused = self.group.control_stop()
        self.assertTrue(paused)
        self.assertTrue(json.loads(content)["ok"])
        self.assertEqual(self.group._consecutive_waits, 0)

    async def test_messages_include_references_and_reminder(self) -> None:
        self.group.entries = [FocusEntry([{"role": "user", "content": "你好"}], {SESSION}, 1)]
        messages = self.group._build_messages(["<focus_reference>记忆</focus_reference>"])
        self.assertIn("<focus_reference>记忆</focus_reference>", messages[-1]["content"])
        self.assertIn("思考是否进行工具调用", messages[-1]["content"])
        self.assertTrue(messages[1]["content"].startswith("时间："))
        self.group._consecutive_waits = 3
        self.assertIn("连续等待", self.group._build_messages([])[-1]["content"])

    async def test_purge_session_drops_entries(self) -> None:
        self.group.entries = [
            FocusEntry([{"role": "user", "content": "a"}], {SESSION}, 1),
            FocusEntry([{"role": "user", "content": "b"}], {OTHER}, 2),
        ]
        await self.group.purge_session(SESSION)
        self.assertEqual([entry.messages[0]["content"] for entry in self.group.entries], ["b"])

    async def test_router_controls(self) -> None:
        self.group.add_member(_member(_request()))
        router = FocusToolRouter(self.group)
        with patch.object(self.group, "is_member_valid", return_value=True):
            content, paused = await router.invoke("stop", {"session_id": SESSION})
        self.assertTrue(paused)
        self.assertTrue(json.loads(content)["ok"])
        content, paused = await router.invoke("reply", {"session_id": "missing", "reply_reference": "接一句"})
        self.assertFalse(paused)
        self.assertFalse(json.loads(content)["ok"])


class WorkerRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """共享规划器单次失败只退避重试，不会永久停机。"""

    async def test_run_retries_after_failure(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            group = FocusGroupRuntime("global", store=FocusCheckpointStore("global", Path(root)))
            group.add_member(_member(_request()))
            group._ready.add(SESSION)
            turns: list[int] = []

            async def fake_turn(_self: object) -> None:
                turns.append(len(turns) + 1)
                if len(turns) == 1:
                    raise RuntimeError("模型超时")
                group.members.clear()

            with (
                patch.object(FocusGroupRuntime, "_run_turn", fake_turn),
                patch.object(group_runtime_module, "MAX_FAILURE_BACKOFF", 0),
            ):
                await group.run()

        self.assertEqual(turns, [1, 2])
        self.assertEqual(group._failures, 0)


class ManagerTests(unittest.IsolatedAsyncioTestCase):
    """归属登记与退出。"""

    async def test_clear_unloaded_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            focus_root = Path(root) / "focus"
            manager = FocusModeManager()
            stores = [FocusCheckpointStore(scope, focus_root) for scope in ("global", "old-group")]
            other = FocusEntry(
                [{"role": "user", "content": "保留其他聊天"}],
                {OTHER},
                5,
                {OTHER: [[5, "other-message"]]},
                sources={OTHER: ["other-message"]},
            )
            for store in stores:
                group = FocusGroupRuntime(store.scope_id, store=store)
                group.entries = [
                    FocusEntry(
                        [{"role": "user", "content": "清空会话内容"}],
                        {SESSION, OTHER},
                        3,
                        {SESSION: [[3, "message"]]},
                        sources={SESSION: ["message"]},
                    ),
                    FocusEntry([], set(), inputs={SESSION: [[4, "input-only"]]}),
                    FocusEntry([], set(), sources={SESSION: ["source-only"]}),
                    other,
                ]
                group._sequence = 5
                group._considered = {SESSION: 3, OTHER: 2}
                group._seen.update(dict.fromkeys([SESSION + "\0message", OTHER + "\0other-message"]))
                group._wait_session_id = SESSION
                group._wait_until = 123.0
                await group.stop()
            unrelated = FocusCheckpointStore("unrelated", focus_root)
            unrelated.save([other], {"wait_session_id": OTHER, "wait_until": 456.0})
            original = unrelated.path.read_bytes()
            with (
                patch.object(context_module, "data_dir", return_value=Path(root)),
                patch.object(manager_module, "focus_config", side_effect=AssertionError("不得读取配置分组")),
            ):
                await manager.clear_context(SESSION)
                await manager.clear_context(SESSION)
            self.assertEqual(manager.groups, {})
            self.assertEqual(unrelated.path.read_bytes(), original)
            for store in stores:
                restored = FocusGroupRuntime(store.scope_id, store=store)
                await restored.restore()
                self.assertEqual(restored.entries, [other])
                self.assertEqual(restored._sequence, 5)
                self.assertEqual(restored._considered, {OTHER: 2})
                self.assertEqual(list(restored._seen), [OTHER + "\0other-message"])
                self.assertIsNone(restored._wait_session_id)
                self.assertIsNone(restored._wait_until)
            self.assertEqual(list(focus_root.glob("*.tmp")), [])

    async def test_clear_skips_invalid_and_loaded_checkpoints(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            focus_root = Path(root) / "focus"
            manager = FocusModeManager()
            entry = FocusEntry([{"role": "user", "content": "待清理"}], {SESSION})
            loaded = FocusCheckpointStore("loaded", focus_root)
            loaded.save([entry], {})
            group = FocusGroupRuntime("loaded", store=loaded)
            group.entries = [entry]
            manager.groups["loaded"] = group
            invalid_files = []
            for scope, payload in (
                ("future", {"version": 999, "scope_id": "future", "entries": []}),
                ("mismatch", {"version": 1, "scope_id": "different", "entries": []}),
            ):
                store = FocusCheckpointStore(scope, focus_root)
                payload["entries"] = [{"messages": [], "session_ids": [SESSION]}]
                store.path.write_text(json.dumps(payload), encoding="utf-8")
                invalid_files.append(store.path)
            broken = focus_root / "broken.json"
            broken.write_text("{not json", encoding="utf-8")
            invalid_files.append(broken)
            originals = {path: path.read_bytes() for path in invalid_files}
            with (
                patch.object(context_module, "data_dir", return_value=Path(root)),
                patch.object(context_module.os, "replace", wraps=context_module.os.replace) as replace,
            ):
                await manager.clear_context(SESSION)
            self.assertEqual(group.entries, [])
            self.assertEqual(loaded.load()["entries"], [])
            self.assertEqual(replace.call_count, 1)
            for path, original in originals.items():
                self.assertEqual(path.read_bytes(), original)

    async def test_clear_without_checkpoint_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with patch.object(context_module, "data_dir", return_value=Path(root)):
                await FocusModeManager().clear_context(SESSION)
            self.assertFalse((Path(root) / "focus").exists())

    async def test_register_and_detach(self) -> None:
        manager = FocusModeManager()
        request = _request()
        with (
            patch.object(manager_module, "focus_config", return_value=_focus_config()),
            patch.object(FocusGroupRuntime, "_ensure_worker", lambda self: None),
        ):
            await manager.register(_member(request), request, is_at=True)
            self.assertTrue(manager.attached(SESSION))
            self.assertTrue(manager.is_enabled_for_session(SESSION))
            self.assertEqual(manager.scope_key(SESSION), "session:" + SESSION)
            self.assertEqual(len(manager.groups), 1)
            await manager.detach(SESSION)
        self.assertFalse(manager.attached(SESSION))
        self.assertFalse(manager.is_enabled_for_session(SESSION))
        self.assertEqual(manager.groups, {})


class FocusReplyTests(unittest.IsolatedAsyncioTestCase):
    """Focus 指定方向后由该聊天自己的回复模型发送。"""

    async def test_focus_reply_sends_and_returns_text(self) -> None:
        service = ChatService(ChatSessionStore())
        captured: list[str | None] = []

        async def fake_process(self: ChatService, request: ChatRequest, runtime: object, *, focus_instruction=None):
            captured.append(focus_instruction)
            return ChatResponse(text="你好呀", epoch=0)

        runtime = SimpleNamespace(matcher=FocusMatcher(SimpleNamespace(), {"user_id": "u1"}))
        with (
            patch.object(ChatService, "_hydrate", AsyncMock()),
            patch.object(ChatService, "_process", fake_process),
            patch.object(service_module.sender_module, "send_parts", AsyncMock(return_value="你好呀")) as send_parts,
        ):
            text = await service.focus_reply(_request(), runtime, "接一句", is_valid=lambda: True)
        self.assertEqual(text, "你好呀")
        self.assertEqual(captured, ["接一句"])
        self.assertTrue(send_parts.await_count == 1)

    async def test_focus_reply_returns_only_sent_part_after_second_part_fails(self) -> None:
        service = ChatService(ChatSessionStore())
        matcher = SimpleNamespace(send=AsyncMock(side_effect=[None, RuntimeError("send failed")]))
        response = ChatResponse(text="first second", epoch=0, metadata={"reply_parts": ["first", "second"]})
        with (
            patch.object(service, "_process_locked", AsyncMock(return_value=response)),
            patch.object(service, "mark_sending", AsyncMock()),
            patch.object(service, "mark_sent", AsyncMock()),
            patch.object(service, "mark_send_failed", AsyncMock()),
            patch.object(service_module.sender_module, "typing_time", return_value=0.0),
            patch.object(service_module.sender_module, "send_voice", AsyncMock()),
            patch.object(service_module.sender_module, "send_emoji", AsyncMock()),
            patch.object(service_module.sender_module.chat_writeback, "on_reply"),
        ):
            text = await service.focus_reply(
                _request(message_id=None), SimpleNamespace(matcher=matcher), "respond", is_valid=lambda: True
            )
        self.assertEqual(text, "first")
        self.assertEqual(matcher.send.await_count, 2)

    async def test_focus_reply_without_text_skips_send(self) -> None:
        service = ChatService(ChatSessionStore())

        async def fake_process(self: ChatService, request: ChatRequest, runtime: object, *, focus_instruction=None):
            return ChatResponse(text="", epoch=0)

        runtime = SimpleNamespace(matcher=SimpleNamespace(send=AsyncMock()))
        with (
            patch.object(ChatService, "_hydrate", AsyncMock()),
            patch.object(ChatService, "_process", fake_process),
            patch.object(service_module.sender_module, "send_parts", AsyncMock()) as send_parts,
        ):
            self.assertEqual(await service.focus_reply(_request(), runtime, "接一句", is_valid=lambda: True), "")
        self.assertEqual(send_parts.await_count, 0)


class FocusReplyValidityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.group = FocusGroupRuntime(
            "session:" + SESSION, store=FocusCheckpointStore("session:" + SESSION, Path(root.name))
        )
        self.member = _member(_request())
        self.group.add_member(self.member)
        self.manager = FocusModeManager()
        self.manager.groups["session:" + SESSION] = self.group
        self.manager._requests[SESSION] = self.member.request
        self.manager._scope_by_session[SESSION] = "session:" + SESSION
        self.service = ChatService(ChatSessionStore())
        self.config = _focus_config()
        self.process = AsyncMock(return_value=ChatResponse(text="reply", epoch=0))
        self.send = AsyncMock(return_value="reply")
        for patcher in (
            patch.object(manager_module, "focus_mode_manager", self.manager),
            patch.object(manager_module, "focus_config", return_value=self.config),
            patch.object(group_runtime_module, "chat_service", self.service),
            patch.object(self.service, "_process_locked", self.process),
            patch.object(self.service, "_send_parts", self.send),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def assert_reply_rejected(self) -> None:
        content, paused = await self.group.reply(self.member, "respond")
        self.assertFalse(json.loads(content)["ok"])
        self.assertFalse(paused)
        self.send.assert_not_awaited()
        self.assertEqual(self.group._sent, [])

    async def test_removed_member_cannot_reply(self) -> None:
        await self.group.remove_member(SESSION, clear_context=True)
        await self.assert_reply_rejected()
        self.process.assert_not_awaited()

    async def test_router_rejects_removed_member(self) -> None:
        await self.group.remove_member(SESSION, clear_context=True)
        content, paused = await FocusToolRouter(self.group).invoke(
            "reply", {"session_id": SESSION, "reply_reference": "respond"}
        )
        self.assertFalse(json.loads(content)["ok"])
        self.assertFalse(paused)
        self.process.assert_not_awaited()
        self.send.assert_not_awaited()

    async def test_detached_member_cannot_reply(self) -> None:
        await self.manager.detach(SESSION)
        await self.assert_reply_rejected()
        self.process.assert_not_awaited()

    async def test_member_removed_while_waiting_for_turn(self) -> None:
        async with self.service.session_store.turn_lock(SESSION):
            task = asyncio.create_task(self.group.reply(self.member, "respond"))
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            await self.group.remove_member(SESSION, clear_context=True)
        content, paused = await task
        self.assertFalse(json.loads(content)["ok"])
        self.assertFalse(paused)
        self.process.assert_not_awaited()
        self.send.assert_not_awaited()
        self.assertEqual(self.group._sent, [])

    async def test_member_invalidated_during_generation(self) -> None:
        for invalidation in ("remove", "detach", "disable", "scope", "replace"):
            with self.subTest(invalidation=invalidation):
                self.group._closed = False
                self.group.add_member(self.member)
                self.manager.groups["session:" + SESSION] = self.group
                self.manager._requests[SESSION] = self.member.request
                self.manager._scope_by_session[SESSION] = "session:" + SESSION
                self.config.update(_focus_config())

                async def generate(*args, **kwargs):
                    if invalidation == "remove":
                        await self.group.remove_member(SESSION, clear_context=True)
                    elif invalidation == "detach":
                        await self.manager.detach(SESSION)
                    elif invalidation == "disable":
                        self.config["enabled"] = False
                    elif invalidation == "scope":
                        self.config["groups"] = [{"targets": [{"platform": "test", "type": "group", "item_id": "g1"}]}]
                    else:
                        self.group.add_member(_member(_request()))
                    return ChatResponse(text="reply", epoch=0)

                self.process.side_effect = generate
                await self.assert_reply_rejected()

    async def test_matcher_rechecks_member_after_send_is_queued(self) -> None:
        from src.plugins.nonebot_plugin_fxbot import adapter

        async def send_parts(request, response, runtime, matcher):
            await self.group.remove_member(SESSION, clear_context=True)
            await matcher.send(SimpleNamespace())
            return "reply"

        self.send.side_effect = send_parts
        with patch.object(adapter, "send_message_to_target", AsyncMock()) as send_message:
            content, paused = await self.group.reply(self.member, "respond")
        self.assertFalse(json.loads(content)["ok"])
        self.assertFalse(paused)
        self.send.assert_awaited_once()
        send_message.assert_not_awaited()
        self.assertEqual(self.group._sent, [])

    async def test_member_removed_during_send_does_not_restore_shared_context(self) -> None:
        async def send_parts(*args, **kwargs):
            await self.group.remove_member(SESSION, clear_context=True)
            return "reply"

        self.send.side_effect = send_parts
        content, paused = await self.group.reply(self.member, "respond")
        self.assertFalse(json.loads(content)["ok"])
        self.assertFalse(paused)
        self.assertEqual(self.group._sent, [])

    async def test_valid_member_can_reply(self) -> None:
        content, paused = await self.group.reply(self.member, "respond")
        self.assertTrue(json.loads(content)["ok"])
        self.assertFalse(paused)
        self.process.assert_awaited_once()
        self.send.assert_awaited_once()
        self.assertEqual(len(self.group._sent), 1)

    async def test_focus_reply_options_reach_target_sender(self) -> None:
        target = self.member.request.message_id
        self.member.request.images = ["base64://" + base64.b64encode(b"synthetic-picture").decode("ascii")]
        self.member.request.metadata["planner_emoji_candidates"] = {"700": "own-emoji-hash"}
        row = SimpleNamespace(sender_id="42")
        with (
            patch.object(group_runtime_module, "reply_at_enabled", return_value=True),
            patch.object(group_runtime_module.chat_repository, "message_by_identity", AsyncMock(return_value=row)),
        ):
            content, paused = await FocusToolRouter(self.group).invoke(
                "reply",
                {
                    "session_id": SESSION,
                    "msg_id": target,
                    "reply_reference": "接一句",
                    "reply_style": "简短表达",
                    "attach_pic": [{"msg_id": target}],
                    "attach_at": [target, target],
                    "attach_emoji": 700,
                },
            )
        self.assertTrue(json.loads(content)["ok"])
        self.assertFalse(paused)
        request = self.process.await_args.args[0]
        self.assertEqual(request.metadata["reply_target_id"], target)
        self.assertEqual(request.metadata["reply_at_users"], ["42"])
        response = self.send.await_args.args[1]
        self.assertEqual(response.metadata["attach_pictures"], [b"synthetic-picture"])
        self.assertEqual(response.metadata["attach_emoji"], "own-emoji-hash")
        self.assertEqual(self.process.await_args.kwargs["focus_instruction"], "接一句")
        self.assertEqual(self.process.await_args.args[0].metadata["requested_reply_style"], "简短表达")
        self.assertNotIn("reply_pictures", self.member.request.metadata)

    async def test_focus_rejects_another_sessions_reply_target(self) -> None:
        with patch.object(group_runtime_module.chat_repository, "message_by_identity", AsyncMock(return_value=None)):
            content, _ = await FocusToolRouter(self.group).invoke(
                "reply",
                {
                    "session_id": SESSION,
                    "msg_id": "foreign-message",
                    "reply_reference": "回复别的会话",
                },
            )
        self.assertFalse(json.loads(content)["ok"])
        self.process.assert_not_awaited()

    async def test_focus_exposes_same_attachment_schema_as_normal_planner(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import conversation
        from src.plugins.nonebot_plugin_fxbot.chat.focus.tools import _control_definitions

        with patch.object(conversation, "emoji_config", return_value={"use_new_send_logic": True}):
            definitions = _control_definitions([SESSION])
        reply = next(item["function"] for item in definitions if item["function"]["name"] == "reply")
        self.assertTrue({"msg_id", "reply_reference", "session_id"} <= set(reply["parameters"]["required"]))
        self.assertTrue({"attach_pic", "attach_emoji", "reply_style"} <= set(reply["parameters"]["properties"]))
        self.assertTrue({"send_emoji", "show_emoji_list"} <= {item["function"]["name"] for item in definitions})

    async def test_focus_cannot_use_another_members_tool_picture(self) -> None:
        self.group.tools.pending_media = [
            FocusEntry(
                [
                    {"role": "user", "_tool_media": {"tool_result:foreign:1": "base64://aW1hZ2U="}},
                ],
                {OTHER},
            )
        ]
        with patch.object(group_runtime_module.chat_repository, "message_by_identity", AsyncMock(return_value=None)):
            content, _ = await FocusToolRouter(self.group).invoke(
                "reply",
                {
                    "session_id": SESSION,
                    "msg_id": self.member.request.message_id,
                    "reply_reference": "附图",
                    "attach_pic": [{"media_index": "tool_result:foreign:1"}],
                },
            )
        self.assertFalse(json.loads(content)["ok"])
        self.process.assert_not_awaited()


class FocusPrivilegedToolTests(unittest.IsolatedAsyncioTestCase):
    """共享组特权工具只认目标聊天本轮的输入来源。"""

    TOOL = "focus_canary_admin"
    PLAIN = "focus_canary_plain"

    def setUp(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        self.group = FocusGroupRuntime("global", store=FocusCheckpointStore("global", Path(root.name)))
        self.member = _member(_request())
        self.other = _member(_request(session_id=OTHER, group_id="g2", message_id="9", dedupe_key="msg:9"))
        self.member.allowed_names = {self.TOOL, self.PLAIN}
        self.other.allowed_names = {self.TOOL, self.PLAIN}
        self.group.add_member(self.member)
        self.group.add_member(self.other)
        self.manager = FocusModeManager()
        self.manager.groups["global"] = self.group
        for member in (self.member, self.other):
            self.manager._requests[member.session_id] = member.request
            self.manager._scope_by_session[member.session_id] = "global"
        self.calls: list[str] = []

        def handler(context, runtime, **kwargs):
            self.calls.append(context.session_id)
            return "done"

        default_registry.register(
            ToolSpec(
                name=self.TOOL,
                description="测试特权工具",
                parameters={"type": "object", "properties": {}},
                handler=handler,
                permission="fxbot:test_admin",
            )
        )
        default_registry.register(
            ToolSpec(
                name=self.PLAIN,
                description="测试普通工具",
                parameters={"type": "object", "properties": {}},
                handler=handler,
            )
        )
        self.addCleanup(default_registry.unregister, self.TOOL)
        self.addCleanup(default_registry.unregister, self.PLAIN)
        for patcher in (
            patch.object(manager_module, "focus_mode_manager", self.manager),
            patch.object(manager_module, "focus_config", return_value=_focus_config()),
            patch.object(self.manager, "scope_key", return_value="global"),
            patch.object(executor_module, "check_permission", AsyncMock(return_value=True)),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.router = FocusToolRouter(self.group)

    async def test_privileged_tool_rejected_from_other_session(self) -> None:
        content, paused = await self.router.invoke(self.TOOL, {"session_id": SESSION}, OTHER)

        payload = json.loads(content)
        self.assertFalse(paused)
        self.assertFalse(payload["ok"])
        self.assertIn("特权操作", payload["error"])
        self.assertEqual(self.calls, [])

    async def test_privileged_tool_allowed_from_same_session(self) -> None:
        content, paused = await self.router.invoke(self.TOOL, {"session_id": SESSION}, SESSION)

        self.assertFalse(paused)
        self.assertTrue(json.loads(content)["ok"])
        self.assertEqual(self.calls, [SESSION])

    async def test_plain_tool_crosses_sessions(self) -> None:
        content, paused = await self.router.invoke(self.PLAIN, {"session_id": SESSION}, OTHER)

        self.assertFalse(paused)
        self.assertTrue(json.loads(content)["ok"])
        self.assertEqual(self.calls, [SESSION])

    async def test_business_session_argument_is_preserved(self) -> None:
        captured = []

        def handler(context, runtime, session_id):
            captured.append((context.session_id, session_id))
            return "done"

        spec = default_registry.get(self.PLAIN)
        default_registry.register(
            replace(
                spec,
                handler=handler,
                parameters={
                    "type": "object",
                    "properties": {"session_id": {"type": "string"}},
                    "required": ["session_id"],
                },
            )
        )
        with patch.object(tools_module, "allowed_tools", AsyncMock(return_value=({self.PLAIN}, []))):
            definitions = await self.router.refresh()
        schema = next(item["function"]["parameters"] for item in definitions if item["function"]["name"] == self.PLAIN)
        self.assertIn("tool_arguments", schema["required"])
        content, paused = await self.router.invoke(
            self.PLAIN, {"session_id": SESSION, "tool_arguments": {"session_id": "business-session"}}, SESSION
        )
        self.assertFalse(paused)
        self.assertTrue(json.loads(content)["ok"])
        self.assertEqual(captured, [(SESSION, "business-session")])
