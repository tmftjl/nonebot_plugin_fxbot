"""派发快照复用、独立对象隔离和后台审计生命周期回归。"""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from weakref import WeakSet
from contextvars import ContextVar
from unittest.mock import Mock, patch

from src.plugins.nonebot_plugin_fxbot.chat import audit, hooks, extensions
from src.plugins.nonebot_plugin_fxbot.chat.hook_registry import CallbackRegistry, ordered_callbacks, register_callback


class RegistryTests(unittest.TestCase):
    """读取快照不排序，所有注册变化即时反映且不改旧快照。"""

    def test_ordered_snapshot_is_reused_until_registration_changes(self) -> None:
        registry = CallbackRegistry()
        remove = register_callback(registry, "last", lambda: None)
        first = ordered_callbacks(registry)
        self.assertIs(first, ordered_callbacks(registry))
        register_callback(registry, "first", lambda: None, priority=0)
        second = ordered_callbacks(registry)
        self.assertIsNot(first, second)
        self.assertEqual([name for name, _ in second], ["first", "last"])
        remove()
        self.assertEqual([name for name, _ in ordered_callbacks(registry)], ["first"])
        self.assertEqual([name for name, _ in first], ["last"])

    def test_replacement_preserves_position_and_old_remover_identity(self) -> None:
        registry = CallbackRegistry()
        callback = Mock()
        old = register_callback(registry, "first", callback)
        register_callback(registry, "second", callback)
        current = register_callback(registry, "first", callback)
        old()
        self.assertEqual(list(registry), ["first", "second"])
        current()
        self.assertEqual(list(registry), ["second"])


class DispatchTests(unittest.IsolatedAsyncioTestCase):
    """普通通知仍按原时序执行，后台审计为显式可选项。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(hooks, "_hooks", {}))
        self.enterContext(patch.object(hooks, "logger", Mock()))
        self.enterContext(patch.object(audit, "_observers", WeakSet()))
        self.enterContext(patch.object(audit, "logger", Mock()))
        self.addAsyncCleanup(audit.stop_audit_observers)
        self.context = hooks.ChatHookContext("test:bot:private:user")

    def extension(self):
        extension = extensions.ChatExtension("test")
        self.addCleanup(extension.close)
        return extension

    async def test_immutable_payload_is_shared_and_cannot_be_mutated(self) -> None:
        snapshots = []
        extension = self.extension()
        extension.on("observer", lambda event, context, details: snapshots.append(details), key="one")
        extension.on("observer", lambda event, context, details: snapshots.append(details), key="two")
        await hooks.notify("event", self.context, values=[{"text": "original"}])
        self.assertIs(snapshots[0], snapshots[1])
        with self.assertRaises(TypeError):
            snapshots[0]["values"][0]["text"] = "changed"

    async def test_custom_mutable_objects_are_copied_for_each_subscriber(self) -> None:
        payload = SimpleNamespace(values=["original"])
        seen = []
        extension = self.extension()

        def mutate(event, context, details):
            details["payload"].values.append("changed")

        extension.on("observer", mutate)
        extension.on("observer", lambda event, context, details: seen.extend(details["payload"].values), key="later")
        await hooks.notify("event", self.context, payload=payload)
        self.assertEqual(payload.values, ["original"])
        self.assertEqual(seen, ["original"])

    async def test_unmatched_event_does_not_copy_payload(self) -> None:
        callback = Mock()
        self.extension().on("observer", callback, events={"selected"})
        with patch.object(hooks, "_freeze", side_effect=AssertionError("must not copy")):
            await hooks.notify("other", self.context, items=["large"])
        callback.assert_not_called()
        await hooks.notify("selected", self.context)
        callback.assert_called_once()

    async def test_unregistration_during_dispatch_affects_next_dispatch_only(self) -> None:
        calls = []
        extension = self.extension()
        extension.on("observer", lambda *args: remove())
        remove = extension.on("observer", lambda *args: calls.append("later"), key="later")
        await hooks.notify("event", self.context)
        await hooks.notify("event", self.context)
        self.assertEqual(calls, ["later"])

    async def test_background_notify_returns_without_waiting_for_callback(self) -> None:
        entered, release, completed = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def callback(*args):
            entered.set()
            await release.wait()
            completed.set()

        self.extension().on("observer", callback, background=True)
        await hooks.notify("event", self.context)
        await asyncio.wait_for(entered.wait(), 1)
        self.assertFalse(completed.is_set())
        release.set()
        await asyncio.wait_for(completed.wait(), 1)

    async def test_background_queue_is_bounded_fifo_and_drops_newest(self) -> None:
        entered, release = asyncio.Event(), asyncio.Event()
        seen = []

        async def callback(event, *args):
            seen.append(event)
            entered.set()
            await release.wait()

        observer = audit.AuditObserver("test", callback, queue_size=1)
        observer("first", self.context, {})
        await asyncio.wait_for(entered.wait(), 1)
        observer("second", self.context, {})
        observer("dropped", self.context, {})
        self.assertEqual(observer.dropped, 1)
        release.set()
        await asyncio.wait_for(observer.queue.join(), 1)
        self.assertEqual(seen, ["first", "second"])
        await observer.stop()

    async def test_background_callbacks_do_not_inherit_ingress_context(self) -> None:
        identity = ContextVar("test-chat-identity", default="no-user")
        seen = []
        finished = asyncio.Event()

        def callback(*args):
            seen.append(identity.get())
            finished.set()

        self.extension().on("observer", callback, background=True)
        token = identity.set("privileged-user")
        try:
            await hooks.notify("event", self.context)
        finally:
            identity.reset(token)
        await asyncio.wait_for(finished.wait(), 1)
        self.assertEqual(seen, ["no-user"])

    async def test_audit_failure_does_not_skip_next_event(self) -> None:
        seen = []

        def callback(event, *args):
            seen.append(event)
            if event == "broken":
                raise RuntimeError("test failure")

        observer = audit.AuditObserver("test", callback)
        observer("broken", self.context, {})
        observer("next", self.context, {})
        await asyncio.wait_for(observer.queue.join(), 1)
        self.assertEqual(seen, ["broken", "next"])
        await observer.stop()

    async def test_timed_out_audit_does_not_skip_next_event(self) -> None:
        seen = []

        async def callback(event, *args):
            seen.append(event)
            if event == "timeout":
                await asyncio.Event().wait()

        observer = audit.AuditObserver("test", callback)
        with patch.object(hooks, "HOOK_TIMEOUT_SECONDS", 0.01):
            observer("timeout", self.context, {})
            observer("next", self.context, {})
            await asyncio.wait_for(observer.queue.join(), 1)
        self.assertEqual(seen, ["timeout", "next"])
        await observer.stop()

    async def test_idle_worker_restarts_for_later_notifications(self) -> None:
        seen = []
        observer = audit.AuditObserver("test", lambda event, *args: seen.append(event))
        observer("first", self.context, {})
        await asyncio.wait_for(observer.queue.join(), 1)
        self.assertIsNone(observer.worker)
        observer("second", self.context, {})
        await asyncio.wait_for(observer.queue.join(), 1)
        self.assertEqual(seen, ["first", "second"])
        await observer.stop()

    async def test_replacing_same_subscription_discards_old_queue_and_remover(self) -> None:
        extension = self.extension()
        old_callback, current_callback = Mock(), Mock()
        old = extension.on("observer", old_callback, background=True)
        await hooks.notify("old-queued", self.context)
        extension.on("observer", current_callback, background=True)
        old()
        self.assertEqual(len(extension._removers), 1)
        await hooks.notify("new", self.context)
        await asyncio.sleep(0)
        old_callback.assert_not_called()
        current_callback.assert_called_once()

    async def test_close_and_shutdown_cancel_running_and_queued_audit(self) -> None:
        entered, exited = asyncio.Event(), asyncio.Event()

        async def callback(*args):
            try:
                entered.set()
                await asyncio.Event().wait()
            finally:
                exited.set()

        extension = self.extension()
        extension.on("observer", callback, background=True)
        await hooks.notify("running", self.context)
        await asyncio.wait_for(entered.wait(), 1)
        await hooks.notify("queued", self.context)
        extension.close()
        await audit.stop_audit_observers()
        self.assertTrue(exited.is_set())
        self.assertEqual(len(audit._observers), 0)

    async def test_registration_rejects_background_guards_and_invalid_options(self) -> None:
        extension = self.extension()
        with self.assertRaises(ValueError):
            extension.on("request_guard", Mock(), background=True)
        with self.assertRaises(ValueError):
            extension.on("observer", Mock(), background=True, queue_size=0)
        with self.assertRaises(ValueError):
            extension.on("observer", Mock(), events="event")
        with self.assertRaises(ValueError):
            extension.on("reply_text", Mock(), events={"event"})
