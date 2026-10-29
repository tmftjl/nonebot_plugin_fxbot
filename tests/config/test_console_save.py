"""控制台保存入口的字段、引用校验与命名空间隔离测试。"""

from __future__ import annotations

import unittest
from copy import deepcopy
from hashlib import sha1
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.config.manager import ConfigManager
from src.plugins.nonebot_plugin_fxbot.console.routes import config as config_routes, ai_providers
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS


class ConsoleConfigSaveTests(unittest.IsolatedAsyncioTestCase):
    """所有提交先校验，拒绝时主文件、历史和内存保持不变。"""

    def setUp(self) -> None:
        directory = TemporaryDirectory(prefix="fxbot-console-save-")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.manager = ConfigManager()
        self.system = self.manager.register("system")
        self.plugin = self.manager.register("example", {"enabled": True})
        self.other = self.manager.register("example", {"value": 1}, filename="other.json")
        self.proxies = (self.system, self.plugin, self.other)
        for index, proxy in enumerate(self.proxies):
            proxy.storage._path = self.directory / f"{index}.json"
            cache.pop(proxy._cache_key)
            self.addCleanup(cache.pop, proxy._cache_key)
        self.valid = deepcopy(SYSTEM_DEFAULTS)
        self.valid["chat"].update(
            provider="main",
            providers={
                "main": {"provider_type": "chat"},
                "backup": {"provider_type": "chat"},
                "stt": {"provider_type": "stt"},
                "tts": {"provider_type": "tts"},
                "embed": {"provider_type": "embedding"},
            },
        )
        self.valid["chat"]["model_routing"].update(
            reply="main", voice="stt", tts="tts", embedding="embed", image_embedding="embed"
        )
        self.system.save(self.valid)
        self.plugin.save({"enabled": True})
        self.other.save({"value": 1})

    def _snapshot(self) -> dict[str, bytes]:
        """记录主配置和有效历史的原始字节。"""
        return {path.name: path.read_bytes() for path in self.directory.iterdir()}

    def test_invalid_fields_and_provider_references_do_not_write(self) -> None:
        cases = (
            ({"max_context_size": -1}, "chat.max_context_size"),
            ({"expression": {"checked_only": "yes"}}, "chat.expression.checked_only"),
            ({"provider": "missing"}, "chat.provider"),
            ({"provider": 1}, "chat.provider"),
            ({"provider": "stt"}, "chat.provider"),
            ({"providers": []}, "chat.providers"),
            ({"model_routing": {"reply": "missing"}}, "chat.model_routing.reply"),
            ({"model_routing": {"reply": "embed"}}, "chat.model_routing.reply"),
            ({"model_routing": {"voice": "main"}}, "chat.model_routing.voice"),
            ({"model_routing": {"tts": "main"}}, "chat.model_routing.tts"),
            ({"model_routing": {"embedding": "main"}}, "chat.model_routing.embedding"),
            ({"model_routing": {"image_embedding": "missing"}}, "chat.model_routing.image_embedding"),
        )
        before = self._snapshot()
        for changes, field in cases:
            invalid = deepcopy(self.valid)
            invalid["chat"].update(changes)
            with (
                self.subTest(changes=changes),
                patch("src.plugins.nonebot_plugin_fxbot.config.storage.ConfigStorage.write") as write,
                self.assertRaisesRegex(ValueError, field),
            ):
                self.manager.save_console_configs({"system": invalid, "example": {"enabled": False}})
            write.assert_not_called()
            self.assertEqual(self._snapshot(), before)
            self.assertEqual(self.manager.get_system(), self.valid)
            self.assertEqual(self.plugin.load(), {"enabled": True})

    def test_direct_system_save_cannot_skip_validator(self) -> None:
        before = self._snapshot()
        for invalid in ({"chat": {"max_context_size": -1}}, {"chat": {"provider": "missing"}}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                self.manager.register("system").save(invalid)
        self.assertEqual(self._snapshot(), before)

    def test_invalid_later_namespace_rejects_entire_submission(self) -> None:
        def validate_plugin(data: dict) -> None:
            """模拟子插件注册的字段校验。"""
            if not isinstance(data.get("enabled"), bool):
                raise ValueError("example.enabled 必须是布尔值")

        self.plugin.validator = validate_plugin
        updated = deepcopy(self.valid)
        updated["membership"]["enabled"] = True
        before = self._snapshot()
        for value in ({"enabled": "yes"}, None, []):
            with (
                self.subTest(value=value),
                patch("src.plugins.nonebot_plugin_fxbot.config.storage.ConfigStorage.write") as write,
                self.assertRaises(ValueError),
            ):
                self.manager.save_console_configs({"system": updated, "example": value})
            write.assert_not_called()
            self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.manager.get_system(), self.valid)

    def test_namespace_save_preserves_unsubmitted_and_non_config_files(self) -> None:
        system_bytes = self.system.path.read_bytes()
        history_bytes = self.system.history_storage.path.read_bytes()
        other_bytes = self.other.path.read_bytes()
        self.manager.save_console_configs({"example": {"enabled": False}, "unknown": {"value": 2}})
        self.assertEqual(self.plugin.load(), {"enabled": False})
        self.assertEqual(self.system.path.read_bytes(), system_bytes)
        self.assertEqual(self.system.history_storage.path.read_bytes(), history_bytes)
        self.assertEqual(self.other.path.read_bytes(), other_bytes)
        self.assertEqual(len(self.manager._proxies), 3)

    def test_later_write_failure_rolls_back_files_and_memory(self) -> None:
        updated = deepcopy(self.valid)
        updated["chat"]["max_context_size"] += 1
        before = self._snapshot()
        cached = cache.get(self.system._cache_key)
        error = OSError("disk error")
        with (
            patch.object(self.plugin.storage, "write", side_effect=error),
            self.assertRaises(OSError) as raised,
        ):
            self.manager.save_console_configs({"system": updated, "example": {"enabled": False}})
        self.assertIs(raised.exception, error)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.system._last_valid, self.valid)
        self.assertEqual(cache.get(self.system._cache_key), cached)
        self.assertEqual(self.system.load(), self.valid)
        self.assertEqual(self.plugin.load(), {"enabled": True})

    def test_later_history_failure_rolls_back_partially_saved_proxy(self) -> None:
        self.plugin.validator = lambda data: None
        self.plugin.save({"enabled": True})
        updated = deepcopy(self.valid)
        updated["chat"]["max_context_size"] += 1
        before = self._snapshot()
        error = OSError("history error")
        write = type(self.plugin.storage).write

        def fail_history(storage, data):
            if storage.path == self.plugin.history_storage.path:
                raise error
            return write(storage, data)

        with (
            patch("src.plugins.nonebot_plugin_fxbot.config.storage.ConfigStorage.write", new=fail_history),
            self.assertRaises(OSError) as raised,
        ):
            self.manager.save_console_configs({"system": updated, "example": {"enabled": False}})
        self.assertIs(raised.exception, error)
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(self.system._last_valid, self.valid)
        self.assertEqual(self.plugin._last_valid, {"enabled": True})
        self.assertEqual(self.system.load(), self.valid)
        self.assertEqual(self.plugin.load(), {"enabled": True})

    def test_snapshot_read_failure_does_not_write(self) -> None:
        before = self._snapshot()
        read_bytes = Path.read_bytes

        def fail_read(path):
            if path == self.plugin.path:
                raise OSError("snapshot error")
            return read_bytes(path)

        with (
            patch.object(Path, "read_bytes", new=fail_read),
            patch("src.plugins.nonebot_plugin_fxbot.config.storage.ConfigStorage.write") as write,
            self.assertRaisesRegex(OSError, "snapshot error"),
        ):
            self.manager.save_console_configs({"system": self.valid, "example": {"enabled": False}})
        write.assert_not_called()
        self.assertEqual(self._snapshot(), before)

    def test_rollback_removes_previously_missing_files_and_cache(self) -> None:
        self.system.path.unlink()
        self.system.history_storage.path.unlink()
        self.system._last_valid = None
        cache.pop(self.system._cache_key)
        before = self._snapshot()
        with (
            patch.object(self.plugin, "save", side_effect=OSError("disk error")),
            self.assertRaisesRegex(OSError, "disk error"),
        ):
            self.manager.save_console_configs({"system": self.valid, "example": {"enabled": False}})
        self.assertEqual(self._snapshot(), before)
        self.assertIsNone(self.system._last_valid)
        self.assertIsNone(cache.get(self.system._cache_key))

    def test_successful_multi_namespace_save_updates_files_and_memory(self) -> None:
        updated = deepcopy(self.valid)
        updated["chat"]["max_context_size"] += 1
        with (
            patch("src.plugins.nonebot_plugin_fxbot.chat.memory.tasks.setup_memory_tasks"),
            patch("src.plugins.nonebot_plugin_fxbot.membership.tasks.setup_membership_tasks"),
        ):
            self.manager.save_console_configs({"system": updated, "example": {"enabled": False}})
        for proxy, expected in ((self.system, updated), (self.plugin, {"enabled": False})):
            with self.subTest(namespace=proxy.namespace):
                self.assertEqual(proxy.storage.read(strict=True), expected)
                self.assertEqual(proxy._last_valid, expected)
                self.assertEqual(cache.get(proxy._cache_key)[1:], (expected, expected))
                self.assertEqual(proxy.load(), expected)
        self.assertEqual(self.system.history_storage.read(strict=True), updated)

    def test_empty_references_and_web_service_routing_remain_valid(self) -> None:
        updated = deepcopy(self.valid)
        updated["chat"].update(provider="", providers={})
        updated["chat"]["model_routing"] = {"reply": "", "web_search": "search"}
        updated["chat"]["web"]["services"] = [{"name": "search", "adapter": "deepseek"}]
        self.manager.save_console_configs({"system": updated})
        self.assertEqual(self.manager.get_system()["chat"]["model_routing"]["web_search"], "search")

    async def test_config_route_returns_clear_validation_errors_without_writing(self) -> None:
        before = self._snapshot()
        for chat in ({"max_context_size": -1}, {"provider": "missing"}):
            with (
                self.subTest(chat=chat),
                patch.object(config_routes, "get_manager", return_value=self.manager),
                self.assertRaises(HTTPException) as raised,
            ):
                await config_routes.update_config({"system": {"chat": chat}})
            self.assertEqual(raised.exception.status_code, 400)
            self.assertIn("chat.", raised.exception.detail)
            self.assertEqual(self._snapshot(), before)

    async def test_config_revisions_include_only_writable_namespaces(self) -> None:
        with patch.object(config_routes, "get_manager", return_value=self.manager):
            configs = await config_routes.get_config()
        self.assertEqual(
            configs["revisions"],
            {
                "system": sha1(self.system.path.read_bytes()).hexdigest(),
                "example": sha1(self.plugin.path.read_bytes()).hexdigest(),
            },
        )
        self.plugin.path.unlink()
        self.assertEqual(self.manager.revisions(["example", "unknown"]), {"example": ""})

    async def test_stale_revisions_return_conflict_without_writing(self) -> None:
        updated_system = deepcopy(self.valid)
        updated_system["chat"]["max_context_size"] += 1
        for namespace, proxy, updated in (
            ("system", self.system, updated_system),
            ("example", self.plugin, {"enabled": False}),
        ):
            with self.subTest(namespace=namespace):
                payload = self.manager.get_console_configs()
                payload["base_revisions"] = payload.pop("revisions")
                proxy.save(updated)
                self.assertNotEqual(
                    self.manager.revisions([namespace])[namespace], payload["base_revisions"][namespace]
                )
                before = self._snapshot()
                with (
                    patch.object(config_routes, "get_manager", return_value=self.manager),
                    patch("src.plugins.nonebot_plugin_fxbot.config.storage.ConfigStorage.write") as write,
                    self.assertRaises(HTTPException) as raised,
                ):
                    await config_routes.update_config(payload)
                self.assertEqual(raised.exception.status_code, 409)
                self.assertIn(namespace, raised.exception.detail)
                write.assert_not_called()
                self.assertEqual(self._snapshot(), before)

    async def test_matching_revisions_check_only_submitted_namespaces(self) -> None:
        revisions = self.manager.get_console_configs()["revisions"]
        updated_system = deepcopy(self.valid)
        updated_system["chat"]["max_context_size"] += 1
        self.system.save(updated_system)
        self.other.save({"value": 2})
        with patch.object(config_routes, "get_manager", return_value=self.manager):
            result = await config_routes.update_config(
                {"example": {"enabled": False}, "base_revisions": revisions, "unknown": {}}
            )
        self.assertEqual(result, {"success": True})
        self.assertEqual(self.plugin.load(), {"enabled": False})

    async def test_save_without_base_revisions_preserves_legacy_behavior(self) -> None:
        payload = self.manager.get_console_configs()
        payload.pop("revisions")
        self.plugin.save({"enabled": False})
        with patch.object(config_routes, "get_manager", return_value=self.manager):
            result = await config_routes.update_config(payload)
        self.assertEqual(result, {"success": True})
        self.assertEqual(self.plugin.load(), {"enabled": True})

    async def test_provider_delete_rejects_dangling_task_reference(self) -> None:
        before = self._snapshot()
        with patch.object(ai_providers, "get_manager", return_value=self.manager):
            result = await ai_providers.delete_ai_provider("main")
        self.assertFalse(result["success"])
        self.assertIn("chat.model_routing.reply", result["message"])
        self.assertEqual(self._snapshot(), before)

    async def test_runtime_save_error_does_not_expose_internal_details(self) -> None:
        with (
            patch.object(config_routes, "get_manager", return_value=self.manager),
            patch.object(self.manager, "save_console_configs", side_effect=OSError("private/path")),
            self.assertRaises(HTTPException) as raised,
        ):
            await config_routes.update_config({"system": self.valid})
        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "保存失败，请稍后再试")
