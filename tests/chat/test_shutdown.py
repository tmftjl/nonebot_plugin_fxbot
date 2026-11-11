"""关闭顺序、Focus 检查点与后台子代理的退出保障。"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from contextlib import ExitStack
from unittest.mock import Mock, AsyncMock, patch

from src.plugins import nonebot_plugin_fxbot as plugin
from src.plugins.nonebot_plugin_fxbot.chat.focus import manager as focus_module
from src.plugins.nonebot_plugin_fxbot.chat.tools import subagent
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.tools.types import ToolError, ToolContext
from src.plugins.nonebot_plugin_fxbot.chat.focus.context import FocusEntry, FocusCheckpointStore
from src.plugins.nonebot_plugin_fxbot.chat.focus.manager import FocusModeManager
from src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime import FocusMember, FocusGroupRuntime


class ShutdownTests(unittest.IsolatedAsyncioTestCase):
    """入口清理先于其他关闭钩子，且单步异常不会截断收尾。"""

    async def test_shutdown_registered_last_once_even_on_startup_failure(self) -> None:
        for failed in (False, True):
            with self.subTest(failed=failed):
                early, stats = AsyncMock(), AsyncMock()
                hooks = [early]

                async def initialize() -> None:
                    hooks.append(stats)
                    if failed:
                        raise RuntimeError("startup failed")

                driver = SimpleNamespace(on_shutdown=hooks.append)
                with (
                    patch.object(plugin, "_shutdown_registered", False),
                    patch.object(plugin, "get_driver", return_value=driver),
                    patch.object(plugin.bootstrap, "init", side_effect=initialize) as initialize_mock,
                ):
                    if failed:
                        with self.assertRaisesRegex(RuntimeError, "startup failed"):
                            await plugin._fxbot_startup()
                    else:
                        await plugin._fxbot_startup()
                    initialize_mock.side_effect = None
                    await plugin._fxbot_startup()
                self.assertEqual(list(reversed(hooks)), [plugin._fxbot_shutdown, stats, early])

    async def test_cleanup_order_and_each_failure_is_logged(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat import audit
        from src.plugins.nonebot_plugin_fxbot.utils import http
        from src.plugins.nonebot_plugin_fxbot.chat.focus import focus_mode_manager
        from src.plugins.nonebot_plugin_fxbot.chat.providers import provider_manager
        from src.plugins.nonebot_plugin_fxbot.chat.continuation import reply_scheduler
        from src.plugins.nonebot_plugin_fxbot.console.chat_channel import console_chat
        from src.plugins.nonebot_plugin_fxbot.chat.amemorix.runtime import amemorix_runtime
        from src.plugins.nonebot_plugin_fxbot.chat.amemorix.writeback import chat_writeback

        steps = [
            (console_chat, "stop"),
            (reply_scheduler, "stop"),
            (focus_mode_manager, "stop_all"),
            (subagent.subagent_tasks, "cancel_all"),
            (chat_writeback, "stop"),
            (audit, "stop_audit_observers"),
            (amemorix_runtime, "stop"),
            (provider_manager, "reset"),
            (http, "close_shared_async_client"),
        ]
        for failed in (None, *range(len(steps))):
            with self.subTest(failed=failed), ExitStack() as stack:
                calls = []

                def cleanup(index: int) -> AsyncMock:
                    async def run() -> None:
                        calls.append(index)
                        if failed == index:
                            raise RuntimeError("cleanup failed")

                    return AsyncMock(side_effect=run)

                for index, (target, method) in enumerate(steps):
                    stack.enter_context(patch.object(target, method, cleanup(index)))
                logger = stack.enter_context(patch.object(plugin, "logger"))
                await plugin._fxbot_shutdown()
                self.assertEqual(calls, list(range(len(steps))))
                self.assertEqual(logger.opt.return_value.warning.call_count, int(failed is not None))


class FocusShutdownTests(unittest.IsolatedAsyncioTestCase):
    """关闭全部共享组时取消任务、落盘并拒绝重新登记。"""

    async def test_stop_waits_for_inflight_registration(self) -> None:
        manager = FocusModeManager()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        async def register(*args: object, **kwargs: object) -> None:
            entered.set()
            await release.wait()
            calls.append("register")

        async def stop() -> None:
            calls.append("stop")

        group = SimpleNamespace(add_member=Mock(), register=register, stop=stop)
        manager.groups["global"] = group
        request = ChatRequest("session", "user", None, "消息", "turn", "message", "private")
        member = FocusMember("session", None, None, {}, request, False)
        with patch.object(focus_module, "_scope_key", return_value="global"):
            registration = asyncio.create_task(manager.register(member, request, is_at=True))
            await entered.wait()
            shutdown = asyncio.create_task(manager.stop_all())
            await asyncio.sleep(0)
            self.assertFalse(shutdown.done())
            release.set()
            await asyncio.gather(registration, shutdown)
        self.assertEqual(calls, ["register", "stop"])

    async def test_stop_all_checkpoints_and_is_idempotent_despite_group_failure(self) -> None:
        for failed in (False, True):
            with self.subTest(failed=failed), tempfile.TemporaryDirectory() as root, ExitStack() as stack:
                manager = FocusModeManager()
                handles = []
                groups = []
                for scope in ("first", "second"):
                    store = FocusCheckpointStore(scope, Path(root))
                    group = FocusGroupRuntime(scope, store=store)
                    group._pending[scope] = [FocusEntry([{"role": "user", "content": "待保存"}], {scope})]
                    group._worker = asyncio.create_task(asyncio.Event().wait())
                    group._debounce_tasks[scope] = asyncio.create_task(asyncio.Event().wait())
                    handles.extend([group._worker, group._debounce_tasks[scope]])
                    groups.append(group)
                    manager.groups[scope] = group
                    manager._scope_by_session[scope] = scope
                    manager._requests[scope] = SimpleNamespace()
                saves = [
                    stack.enter_context(patch.object(group._store, "save", wraps=group._store.save)) for group in groups
                ]
                if failed:
                    saves[0].side_effect = OSError("checkpoint failed")
                logger = stack.enter_context(patch.object(focus_module, "logger"))
                await asyncio.sleep(0)
                await manager.stop_all()
                await manager.stop_all()
                self.assertTrue(all(handle.done() for handle in handles))
                self.assertTrue(all(group._closed for group in groups))
                self.assertEqual(manager.groups, {})
                self.assertEqual(manager._requests, {})
                self.assertEqual(manager._scope_by_session, {})
                for save in saves:
                    save.assert_called_once()
                self.assertEqual(logger.opt.return_value.warning.call_count, int(failed))
                restored = FocusGroupRuntime("second", store=groups[1]._store)
                await restored.restore()
                self.assertEqual(restored.entries[0].messages, [{"role": "user", "content": "待保存"}])
                request = ChatRequest("new", "user", None, "新消息", "turn", "message", "private")
                member = FocusMember("new", None, None, {}, request, False)
                with patch.object(focus_module, "FocusGroupRuntime") as constructor:
                    await manager.register(member, request, is_at=True)
                constructor.assert_not_called()
                self.assertEqual(manager._requests, {})


class SubagentShutdownTests(unittest.IsolatedAsyncioTestCase):
    """全局取消跨会话生效，等待任务退出且屏蔽迟到结论。"""

    async def test_cancel_all_is_idempotent_and_discards_late_results(self) -> None:
        for cancellation in ("propagate", "return", "raise"):
            with self.subTest(cancellation=cancellation):
                manager = subagent._TaskManager()
                exited = []

                async def run(context: ToolContext, *args: object) -> str:
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        if cancellation == "propagate":
                            raise
                        if cancellation == "raise":
                            raise RuntimeError("late failure") from None
                        return "迟到结论"
                    finally:
                        exited.append(context.session_id)

                with (
                    patch.object(subagent, "_run_sub_agent", side_effect=run),
                    patch.object(subagent, "_deliver", Mock()) as deliver,
                ):
                    tasks = [
                        manager.start(
                            ToolContext(session_id=session_id),
                            SimpleNamespace(),
                            description="后台任务",
                            granted_tools=[],
                            max_rounds=1,
                        )
                        for session_id in ("first", "second")
                    ]
                    await asyncio.sleep(0)
                    await manager.cancel_all()
                    await manager.cancel_all()
                    self.assertCountEqual(exited, ["first", "second"])
                    self.assertTrue(all(task.handle.done() for task in tasks))
                    self.assertTrue(all(task.status == "stopped" and task.discard_result for task in tasks))
                    deliver.assert_not_called()
                    with self.assertRaises(ToolError):
                        manager.start(
                            ToolContext(session_id="new"),
                            SimpleNamespace(),
                            description="新任务",
                            granted_tools=[],
                            max_rounds=1,
                        )
