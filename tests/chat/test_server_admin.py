"""服务器模式身份边界、持久化、确认和有界子进程回归。"""

from __future__ import annotations

import sys
import asyncio
import tempfile
import unittest
import importlib
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import Mock, AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import hooks, session_hooks
from src.plugins.nonebot_plugin_fxbot.plugins import server_admin as plugin
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolError, ToolContext, ToolRuntime
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.permission import checker as permission_checker
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.chat.bindings import PERSONA, get_binding, set_binding
from src.plugins.nonebot_plugin_fxbot.config.manager import ConfigManager
from src.plugins.nonebot_plugin_fxbot.plugins.server_admin import store, access, config as plugin_config

execution = importlib.import_module("src.plugins.nonebot_plugin_fxbot.plugins.server_admin.executor")

BASE = "onebot11:bot:private:admin"
SERVER = "onebot11:bot#server:private:admin"


def bot():
    """无需平台连接的 Bot 身份。"""
    return SimpleNamespace(self_id="bot")


def event(text="", user="admin", group=None):
    """通过统一适配器读取的真实场景字段。"""
    return SimpleNamespace(
        user_id=user, group_id=group, message_type="group" if group else "private", get_plaintext=lambda: text
    )


def permissions():
    """显式启用的插件权限节点，不依赖空配置放行。"""
    return {
        "top": {"enabled": True},
        "sub_plugins": {
            "server_admin": {
                "top": {"enabled": True},
                "commands": {"mode": {"enabled": True}, "group_mode": {"enabled": True}},
            }
        },
    }


class AccessTests(unittest.TestCase):
    """实际事件、会话标记、管理员及插件开关必须同时有效。"""

    def setUp(self) -> None:
        self.enterContext(patch.object(access, "platform_for_bot", return_value="onebot11"))
        self.enterContext(patch.object(access, "is_superuser", side_effect=lambda user: user == "admin"))
        self.storage = SimpleNamespace(load=Mock(return_value=permissions()))
        self.enterContext(patch.object(access, "get_storage", return_value=self.storage))
        self.enterContext(patch.object(access, "enabled", return_value=True))

    def test_administrator_private_server_session_is_allowed(self) -> None:
        self.assertTrue(access.execution_allowed(SERVER, bot(), event(), "admin"))

    def test_group_ordinary_session_and_forged_user_are_denied(self) -> None:
        for sid, actor, user, group in (
            (BASE, event(), "admin", None),
            (SERVER, event(group="group"), "admin", None),
            (SERVER, event(user="other"), "admin", None),
            (SERVER, event(), "other", None),
            (SERVER, event(), "admin", "group"),
            (SERVER.replace("bot#", "other#"), event(), "admin", None),
        ):
            self.assertFalse(access.execution_allowed(sid, bot(), actor, user, group))

    def test_absent_malformed_and_disabled_permission_nodes_deny(self) -> None:
        for config in ({}, {"top": {}}, permissions()):
            if "sub_plugins" in config:
                config["sub_plugins"]["server_admin"]["top"]["enabled"] = False
            self.storage.load.return_value = config
            self.assertFalse(access.execution_allowed(SERVER, bot(), event(), "admin"))
        self.storage.load.side_effect = OSError("unavailable")
        self.assertFalse(access.execution_allowed(SERVER, bot(), event(), "admin"))

    def test_closed_mode_and_missing_event_deny(self) -> None:
        with patch.object(access, "enabled", return_value=False):
            self.assertFalse(access.execution_allowed(SERVER, bot(), event(), "admin"))
        self.assertFalse(access.execution_allowed(SERVER, bot(), None, "admin"))

    def test_marked_group_event_reuses_the_same_private_server_session(self) -> None:
        marked = event("#服务器模式# ls", group="group")
        access.mark_group_event(marked)
        self.assertTrue(access.execution_allowed(SERVER, bot(), marked, "admin"))
        self.assertFalse(access.execution_allowed(SERVER, bot(), event(group="group"), "admin"))
        self.assertFalse(access.execution_allowed(SERVER, bot(), marked, "other"))
        self.assertFalse(access.execution_allowed(BASE, bot(), marked, "admin"))
        with patch.object(access, "enabled", return_value=False):
            self.assertFalse(access.execution_allowed(SERVER, bot(), marked, "admin"))
        with patch.object(access, "is_superuser", return_value=False):
            self.assertFalse(access.execution_allowed(SERVER, bot(), marked, "admin"))

    def test_robot_admin_is_not_superuser_even_with_permission_whitelists(self) -> None:
        with (
            patch.object(permission_checker, "_is_superuser", return_value=False),
            patch.object(permission_checker, "_bot_admins", return_value={"admin"}),
            patch.object(permission_checker, "get_storage", return_value=SimpleNamespace(load=lambda: {})),
        ):
            self.assertFalse(permission_checker.is_superuser("admin"))
            self.assertFalse(permission_checker.is_superuser("other"))

    def test_only_configured_superuser_is_allowed(self) -> None:
        driver = SimpleNamespace(config=SimpleNamespace(superusers={"owner"}))
        with patch.object(permission_checker, "get_driver", return_value=driver):
            self.assertTrue(permission_checker.is_superuser("owner"))
            self.assertFalse(permission_checker.is_superuser("admin"))

    def test_tool_rejects_bot_admin_even_when_mode_and_switch_are_enabled(self) -> None:
        with patch.object(access, "is_superuser", return_value=False):
            self.assertFalse(access.execution_allowed(SERVER, bot(), event(), "admin"))
            self.assertFalse(access.owner_allowed(bot(), event()))


class ModeTests(unittest.IsolatedAsyncioTestCase):
    """按需人格播种和会话 ID 派生不污染普通聊天。"""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        manager = ConfigManager()
        self.enterContext(patch.object(store, "get_manager", return_value=manager))
        self.enterContext(
            patch(
                "src.plugins.nonebot_plugin_fxbot.config.storage.config_dir", side_effect=lambda name: self.folder(name)
            )
        )
        self.enterContext(
            patch(
                "src.plugins.nonebot_plugin_fxbot.chat.bindings.config_dir", side_effect=lambda name: self.folder(name)
            )
        )
        self.enterContext(
            patch(
                "src.plugins.nonebot_plugin_fxbot.chat.personas.get_personas_dir",
                side_effect=lambda: self.folder("personas"),
            )
        )
        self.enterContext(patch.object(store, "persona_text", return_value=""))
        cache.clear()
        self.addCleanup(cache.clear)

    def folder(self, name):
        path = self.root / name
        path.mkdir(exist_ok=True)
        return path

    def test_derivation_preserves_real_user_and_is_idempotent(self) -> None:
        self.assertEqual(store.server_session(BASE), SERVER)
        self.assertEqual(store.server_session(SERVER), SERVER)
        self.assertEqual(store.base_session(SERVER), BASE)
        self.assertFalse(store.is_server_session(BASE))
        self.assertFalse(store.is_server_session("onebot11:bot:private:admin#server"))

    def test_first_binding_only_and_mode_survives_cache_clear(self) -> None:
        self.assertFalse(store.enabled(BASE))
        store.set_mode(BASE, True)
        self.assertTrue(store.enabled(BASE))
        self.assertEqual(get_binding(PERSONA, SERVER), "运维")
        self.assertEqual(get_binding(PERSONA, BASE), "")
        set_binding(PERSONA, SERVER, "用户选择")
        store.set_mode(BASE, False)
        store.set_mode(BASE, True)
        cache.clear()
        self.assertTrue(store.enabled(BASE))
        self.assertEqual(get_binding(PERSONA, SERVER), "用户选择")

    def test_persona_is_resynced_to_bundled_seed_on_activation(self) -> None:
        store.set_mode(BASE, True)
        path = self.folder("personas") / "运维.md"
        path.write_text("我改过的人格", encoding="utf-8")
        store.set_mode(BASE, False)
        store.set_mode(BASE, True)
        seed = Path(store.__file__).with_name("persona.md").read_text(encoding="utf-8").strip()
        self.assertEqual(path.read_text(encoding="utf-8"), seed)

    def test_configured_persona_overrides_bundled_seed(self) -> None:
        with patch.object(store, "persona_text", return_value="控制台人格"):
            store.set_mode(BASE, True)
        path = self.folder("personas") / "运维.md"
        self.assertEqual(path.read_text(encoding="utf-8"), "控制台人格")

    def test_invalid_state_file_does_not_fall_back_to_enabled(self) -> None:
        store.set_mode(BASE, True)
        store._proxy().path.write_text("not json", encoding="utf-8")
        cache.clear()
        with self.assertRaises(RuntimeError):
            store.enabled(BASE)

    async def test_commands_enter_leave_and_cancel_without_clear_history(self) -> None:
        with (
            patch.object(plugin, "owner_allowed", return_value=True),
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin, "audit"),
            patch.object(plugin.executor, "cancel", return_value=True) as cancel,
        ):
            self.assertIn("已开启", await plugin.handle_mode(bot(), event("#服务器模式")))
            self.assertTrue(store.enabled(BASE))
            self.assertIn("已退出", await plugin.handle_mode(bot(), event("#聊天模式")))
            self.assertFalse(store.enabled(BASE))
            cancel.assert_called_once_with(SERVER)

    def test_only_mode_commands_remain(self) -> None:
        for text in ("#开启服务器模式", "#退出服务器模式", "#确认", "#取消", "执行吧"):
            self.assertIsNone(plugin._COMMAND.fullmatch(text))
        for text in ("#服务器模式", "#聊天模式"):
            self.assertIsNotNone(plugin._COMMAND.fullmatch(text))

    async def test_mode_rule_only_checks_owner(self) -> None:
        with patch.object(plugin, "owner_allowed", return_value=False):
            self.assertFalse(await plugin._rule(bot(), event("#服务器模式")))
        with patch.object(plugin, "owner_allowed", return_value=True):
            self.assertTrue(await plugin._rule(bot(), event("#服务器模式")))

    async def test_group_prefix_requires_enabled_server_session(self) -> None:
        with (
            patch.object(plugin, "owner_allowed_group", return_value=True),
            patch.object(plugin, "group_actor_base", return_value=BASE),
            patch.object(plugin.store, "enabled", return_value=False),
        ):
            self.assertIn(
                "未开启",
                await plugin.handle_group_turn(bot(), event("#服务器模式# ls", group="group"), SimpleNamespace()),
            )
        with patch.object(plugin, "owner_allowed_group", return_value=True):
            self.assertIn(
                "输入内容",
                await plugin.handle_group_turn(bot(), event("#服务器模式#  ", group="group"), SimpleNamespace()),
            )

    async def test_group_prefix_routes_into_private_server_session(self) -> None:
        store_events = SimpleNamespace(
            mark_seen=Mock(return_value=True),
            next_seq=Mock(return_value=1),
            epoch=Mock(return_value=0),
            activity=Mock(return_value=1),
        )
        service = SimpleNamespace(
            session_store=store_events,
            ensure_hydrated=AsyncMock(),
            process_and_send=AsyncMock(),
        )
        request = ChatRequest("onebot11:bot:group:group", "admin", "group", "#服务器模式# ls", "t", "k", "private")
        with (
            patch.object(plugin, "owner_allowed_group", return_value=True),
            patch.object(plugin, "group_actor_base", return_value=BASE),
            patch.object(plugin.store, "enabled", return_value=True),
            patch.object(plugin, "chat_service", service),
            patch.object(plugin, "event_identity", return_value=SimpleNamespace(dedupe_key="msg:1")),
            patch.object(plugin, "adapt_message_event", AsyncMock(return_value=request)),
            patch.object(plugin.default_runtime_factory, "create", return_value="runtime"),
            patch.object(plugin, "mark_group_event") as mark,
        ):
            self.assertEqual(
                await plugin.handle_group_turn(bot(), event("#服务器模式# ls", group="group"), "matcher"), ""
            )
        self.assertEqual(request.session_id, SERVER)
        self.assertIsNone(request.group_id)
        self.assertEqual(request.text, "ls")
        self.assertEqual(request.trigger_kind, "private")
        mark.assert_called_once()
        service.process_and_send.assert_awaited_once_with(request, "runtime")

    def test_guard_accepts_private_and_group_entry_sessions(self) -> None:
        store.set_mode(BASE, True)
        private = hooks.ChatHookContext(SERVER, user_id="admin")
        group = hooks.ChatHookContext(SERVER, user_id="admin", group_id="group")
        with (
            patch.object(plugin, "plugin_enabled", return_value=True),
            patch.object(plugin, "is_superuser", return_value=True),
        ):
            self.assertTrue(plugin._guard(private, {}))
            self.assertTrue(plugin._guard(group, {}))
        with patch.object(plugin, "is_superuser", return_value=False):
            self.assertFalse(plugin._guard(group, {}))

    def test_hooks_route_and_protect_disabled_server_sessions(self) -> None:
        store.set_mode(BASE, True)
        self.assertEqual(plugin._resolve(bot(), event(), BASE), SERVER)
        flags = {
            "allow_sharing": True,
            "allow_debounce": False,
            "allow_typing": False,
            "allow_split": False,
        }
        with patch.object(plugin, "policy_switches", return_value=flags):
            self.assertEqual(
                plugin._policy(SERVER),
                session_hooks.SessionPolicy(False, True, False, False, False, False, allow_rest_time=True),
            )
        request = ChatRequest(SERVER, "admin", None, "hi", "turn", "msg", "private")
        context = hooks.hook_context(request)
        with patch.object(plugin, "plugin_enabled", return_value=False):
            self.assertFalse(plugin._guard(context, {}))
        self.assertFalse(plugin._tools(SimpleNamespace(session_id=BASE), {"tool_name": "server_exec"}))
        self.assertIsNone(plugin._tools(SimpleNamespace(session_id=BASE), {"tool_name": "send_file"}))
        with patch.object(plugin, "_guard", return_value=True):
            with patch.object(plugin.executor, "pending", return_value=None):
                self.assertFalse(plugin._tools(SimpleNamespace(session_id=SERVER), {"tool_name": "server_confirm"}))
            with patch.object(plugin.executor, "pending", return_value={"command": "x"}):
                self.assertTrue(plugin._tools(SimpleNamespace(session_id=SERVER), {"tool_name": "server_confirm"}))
            self.assertTrue(plugin._tools(SimpleNamespace(session_id=SERVER), {"tool_name": "send_file"}))


class BlocklistTests(unittest.TestCase):
    """默认黑名单只拦不可恢复操作，正常清理和只读查询不受影响。"""

    def setUp(self) -> None:
        rules = plugin_config.DEFAULTS["blocked_commands"]
        self.enterContext(
            patch.object(
                plugin_config,
                "REG",
                SimpleNamespace(load=lambda: {"blocked_commands": rules}, _last_error=None),
            )
        )

    def test_defaults_block_irreversible_commands(self) -> None:
        for command in (
            "rm -rf /",
            "sudo rm -fr /*",
            "rm --recursive --force /",
            "rm -rf --no-preserve-root /",
            "mkfs.ext4 /dev/sda",
            "wipefs -a /dev/sda",
            "fdisk /dev/sda",
            "dd if=/dev/zero of=/dev/sda",
            "echo x > /dev/sda",
            ":(){ :|:& };:",
        ):
            self.assertTrue(plugin_config.blocked_reason(command), command)

    def test_cleanup_and_read_only_forms_are_allowed(self) -> None:
        for command in (
            "rm -rf /tmp/build",
            "rm -rf ~/build",
            "fdisk -l /dev/sda",
            "dd if=/dev/zero of=/tmp/x bs=1M count=1",
            "echo hi",
            "docker ps",
        ):
            self.assertEqual(plugin_config.blocked_reason(command), "", command)

    def test_unreadable_config_blocks_everything(self) -> None:
        with patch.object(plugin_config, "REG", SimpleNamespace(load=lambda: {}, _last_error="corrupt")):
            self.assertTrue(plugin_config.blocked_reason("echo hi"))


class SkipConfirmationTests(unittest.TestCase):
    """免确认开关默认关闭，配置损坏时也保持关闭。"""

    def test_switch_reads_config(self) -> None:
        with patch.object(
            plugin_config, "REG", SimpleNamespace(load=lambda: {"skip_confirmation": True}, _last_error=None)
        ):
            self.assertTrue(plugin_config.skip_confirmation())

    def test_unreadable_config_keeps_confirmation(self) -> None:
        with patch.object(
            plugin_config, "REG", SimpleNamespace(load=lambda: {"skip_confirmation": True}, _last_error="corrupt")
        ):
            self.assertFalse(plugin_config.skip_confirmation())


class PersonaConfigTests(unittest.TestCase):
    """控制台人格优先，留空或配置损坏时回退内置人格。"""

    def test_persona_reads_config(self) -> None:
        with patch.object(
            plugin_config, "REG", SimpleNamespace(load=lambda: {"persona": " 自定义 "}, _last_error=None)
        ):
            self.assertEqual(plugin_config.persona_text(), "自定义")

    def test_unreadable_config_falls_back(self) -> None:
        with patch.object(
            plugin_config, "REG", SimpleNamespace(load=lambda: {"persona": "自定义"}, _last_error="corrupt")
        ):
            self.assertEqual(plugin_config.persona_text(), "")


class PolicySwitchTests(unittest.TestCase):
    """会话能力默认保持保守，学习共享默认开启，配置损坏时回退默认值。"""

    def _flags(self, data: dict, error: str | None = None) -> dict[str, bool]:
        with patch.object(plugin_config, "REG", SimpleNamespace(load=lambda: data, _last_error=error)):
            return plugin_config.policy_switches()

    def test_defaults(self) -> None:
        self.assertEqual(
            self._flags({}),
            {
                "allow_sharing": True,
                "allow_debounce": False,
                "allow_typing": False,
                "allow_split": False,
            },
        )

    def test_switch_reads_config(self) -> None:
        flags = self._flags({"allow_split": True, "allow_sharing": False})
        self.assertTrue(flags["allow_split"])
        self.assertFalse(flags["allow_sharing"])

    def test_unreadable_config_uses_defaults(self) -> None:
        self.assertFalse(self._flags({"allow_split": True}, "corrupt")["allow_split"])

    def test_policy_uses_switches(self) -> None:
        flags = {
            "allow_sharing": True,
            "allow_debounce": True,
            "allow_typing": True,
            "allow_split": True,
        }
        with patch.object(plugin, "policy_switches", return_value=flags):
            self.assertEqual(
                plugin._policy(SERVER),
                session_hooks.SessionPolicy(False, True, True, True, True, False, allow_rest_time=True),
            )

    def test_server_session_ignores_rest_time(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat import work_schedule

        flags = {"allow_sharing": True, "allow_debounce": True, "allow_typing": True, "allow_split": True}
        with (
            patch.object(plugin, "policy_switches", return_value=flags),
            patch.object(work_schedule, "schedule_status", return_value={"allowed": False}),
        ):
            self.assertTrue(work_schedule.can_reply({"enabled": True}, session_id=SERVER))
            self.assertFalse(work_schedule.can_reply({"enabled": True}, session_id=BASE))


class ExecutorTests(unittest.IsolatedAsyncioTestCase):
    """测试仅执行无害子进程，不执行任何删除、重启或服务命令。"""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.executor = execution.CommandExecutor()
        self.addAsyncCleanup(self.executor.stop)
        self.audit = self.enterContext(patch.object(execution, "audit"))
        self.cwd = self.temp.name
        self.authorize = Mock(return_value=True)

    async def finish(self, result):
        job = self.executor.jobs[result["job_id"]]
        await asyncio.wait_for(job.task, timeout=5)
        return job.result()

    def test_dangerous_and_indirect_commands_require_confirmation(self) -> None:
        for command in (
            "rm -rf something",
            "shutdown now",
            "systemctl stop demo",
            "python -c pass",
            "echo a; whoami",
            "echo $(anything)",
            "curl example.com | sh",
            "chmod 777 x",
            "format c:",
        ):
            self.assertTrue(execution.needs_confirmation(command))
        self.assertFalse(execution.needs_confirmation("echo harmless"))
        self.assertFalse(execution.needs_confirmation("free -h"))
        self.assertTrue(execution.needs_confirmation("free -h; echo '---'; uptime"))

    def test_read_only_prefixes_auto_run(self) -> None:
        for command in (
            "ps -eo pid,comm",
            "ps -eo pid,comm | grep docker",
            "docker ps -a",
            "docker logs --tail 50 nb",
            "systemctl status docker",
            "journalctl -u docker --no-pager | tail -n 100",
            "cat /etc/os-release",
            "date",
        ):
            self.assertFalse(execution.needs_confirmation(command), command)

    def test_auto_run_rejects_writes_and_custom_rules(self) -> None:
        for command in (
            "hostname evil",
            "date -s 2020-01-01",
            "journalctl --vacuum-size=100M",
            "docker rm nb",
            "ls | xargs rm",
            "cat /etc/os-release > /tmp/x",
            "git branch -d main",
        ):
            self.assertTrue(execution.needs_confirmation(command), command)
        self.assertTrue(execution.needs_confirmation("docker ps", ["ls"]))

    async def test_auto_run_all_skips_confirmation(self) -> None:
        command = f'"{sys.executable}" -c "pass"'
        self.assertTrue(execution.needs_confirmation(command))
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize, auto_run_all=True)
        self.assertEqual(result["status"], "running")
        result = await self.finish(result)
        self.assertEqual(result["status"], "completed")

    def test_pending_requires_presentation_correct_session_and_valid_ttl(self) -> None:
        result = self.executor.submit(SERVER, "python -c pass", self.cwd, self.authorize)
        identifier = result["job_id"]
        self.assertIsNone(self.executor.pending(SERVER))
        self.executor.presented(SERVER, identifier)
        self.assertEqual(self.executor.pending(SERVER)["job_id"], identifier)
        self.assertIsNone(self.executor.pending(BASE))
        self.executor.jobs[identifier].created -= execution.CONFIRM_TTL
        self.assertIsNone(self.executor.pending(SERVER))

    async def test_safe_command_finishes_and_output_is_bounded(self) -> None:
        result = self.executor.submit(SERVER, "echo harmless", self.cwd, self.authorize)
        result = await self.finish(result)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["returncode"], 0)
        self.assertIn("harmless", result["output"])
        self.assertLessEqual(len(result["output"]), 4000)

    def test_audit_failure_prevents_submission(self) -> None:
        self.audit.side_effect = OSError("disk full")
        with self.assertRaises(OSError):
            self.executor.submit(SERVER, "echo harmless", self.cwd, self.authorize)
        self.assertEqual(self.executor.jobs, {})

    def test_same_session_cannot_queue_second_command(self) -> None:
        self.executor.submit(SERVER, "not-run", self.cwd, self.authorize)
        with self.assertRaises(ValueError):
            self.executor.submit(SERVER, "another-not-run", self.cwd, self.authorize)

    async def test_revocation_terminates_running_command(self) -> None:
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"], "m1")
        self.executor.confirm(SERVER, "m2")
        await asyncio.sleep(0.05)
        self.authorize.return_value = False
        finished = await self.finish(result)
        self.assertEqual(finished["status"], "cancelled")

    async def test_cancel_and_shutdown_terminate_running_command(self) -> None:
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"], "m1")
        self.executor.confirm(SERVER, "m2")
        await asyncio.sleep(0.05)
        await asyncio.wait_for(self.executor.stop(), timeout=5)
        self.assertEqual(self.executor.jobs[result["job_id"]].status, "cancelled")

    async def test_child_process_is_killed_at_timeout(self) -> None:
        """通过无害的子进程与 PID 文件验证 Windows Job 或 POSIX 进程组清理。"""
        import psutil

        script = Path(self.cwd) / "spawn.py"
        pid_file = Path(self.cwd) / "child.pid"
        script.write_text(
            "import subprocess,sys,time\nfrom pathlib import Path\n"
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])\n"
            "Path('child.pid').write_text(str(p.pid),encoding='utf-8')\ntime.sleep(30)\n",
            encoding="utf-8",
        )
        result = self.executor.submit(SERVER, f'"{sys.executable}" "{script}"', self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"], "m1")
        with patch.object(execution, "COMMAND_TIMEOUT", 0.8):
            self.executor.confirm(SERVER, "m2")
            finished = await self.finish(result)
        self.assertEqual(finished["status"], "timeout")
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text(encoding="utf-8"))
        self.assertFalse(psutil.pid_exists(pid))

    async def test_confirmation_requires_presentation_and_a_later_event(self) -> None:
        command = f'"{sys.executable}" -c "print(123)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        identifier = result["job_id"]
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(self.executor.jobs[identifier].task)
        with self.assertRaises(ValueError):
            self.executor.confirm(SERVER, "m2")
        self.executor.presented(SERVER, identifier, "m1")
        for source in ("", "m1"):
            with self.assertRaises(ValueError):
                self.executor.confirm(SERVER, source)
        self.assertIsNone(self.executor.jobs[identifier].task)
        self.executor.confirm(SERVER, "m2")
        with self.assertRaises(ValueError):
            self.executor.confirm(SERVER, "m3")
        self.assertIn("123", (await self.finish(result))["output"])

    def test_expired_or_revoked_confirmation_does_not_start(self) -> None:
        result = self.executor.submit(SERVER, "not-run", self.cwd, self.authorize)
        job = self.executor.jobs[result["job_id"]]
        self.executor.presented(SERVER, job.id, "m1")
        job.created -= 121
        with self.assertRaises(ValueError):
            self.executor.confirm(SERVER, "m2")
        result = self.executor.submit(SERVER, "not-run", self.cwd, self.authorize)
        job = self.executor.jobs[result["job_id"]]
        self.executor.presented(SERVER, job.id, "m1")
        self.authorize.return_value = False
        with self.assertRaises(PermissionError):
            self.executor.confirm(SERVER, "m2")
        self.assertIsNone(job.task)

    async def test_timeout_stops_long_running_process(self) -> None:
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"], "m1")
        with patch.object(execution, "COMMAND_TIMEOUT", 0.15):
            self.executor.confirm(SERVER, "m2")
            finished = await self.finish(result)
        self.assertEqual(finished["status"], "timeout")

    async def test_continuous_output_is_drained_and_truncated(self) -> None:
        command = f'"{sys.executable}" -c "print(\'a\'*100000)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"], "m1")
        self.executor.confirm(SERVER, "m2")
        finished = await self.finish(result)
        self.assertEqual(finished["status"], "completed")
        self.assertEqual(len(finished["output"]), 4000)
        self.assertTrue(finished["truncated"])

    def test_cancelled_confirmation_cannot_execute_and_query_is_scoped(self) -> None:
        result = self.executor.submit(SERVER, "not-run", self.cwd, self.authorize)
        with self.assertRaises(ValueError):
            self.executor.query(BASE, result["job_id"])
        self.assertTrue(self.executor.cancel(SERVER))
        with self.assertRaises(ValueError):
            self.executor.confirm(SERVER, "m2")


class ToolTests(unittest.IsolatedAsyncioTestCase):
    """独立工具入口不相信 allowed_names、调用者声明或同一轮的自我确认。"""

    async def test_simple_query_returns_ready_output_without_extra_poll(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = execution.CommandExecutor()
            self.addAsyncCleanup(runner.stop)
            with (
                patch.object(plugin, "execution_allowed", return_value=True),
                patch.object(plugin, "executor", runner),
                patch.object(execution, "audit"),
            ):
                result = await plugin.server_exec(
                    ToolContext(user_id="admin", session_id=SERVER),
                    ToolRuntime(),
                    command="echo harmless",
                    cwd=directory,
                )
            self.assertEqual(result["status"], "completed")
            self.assertIn("harmless", result["output"])

    def test_prompt_explains_natural_confirmation(self) -> None:
        with (
            patch.object(plugin, "skip_confirmation", return_value=False),
            patch.object(plugin, "startup_marker_path", return_value=Path("/tmp/startup.json")),
        ):
            prompt = plugin._prompt(hooks.ChatHookContext(SERVER))
            self.assertIn("server_confirm", prompt)
            self.assertIn("明确同意", prompt)
            self.assertIn("别重复提交", prompt)
            self.assertIsNone(plugin._prompt(hooks.ChatHookContext(BASE)))

    def test_prompt_switches_when_confirmation_is_skipped(self) -> None:
        with (
            patch.object(plugin, "skip_confirmation", return_value=True),
            patch.object(plugin, "startup_marker_path", return_value=Path("/tmp/startup.json")),
        ):
            prompt = plugin._prompt(hooks.ChatHookContext(SERVER))
        self.assertIn("免确认", prompt)
        self.assertIn("直接执行", prompt)

    def test_prompt_carries_restart_facts(self) -> None:
        with (
            patch.object(plugin, "skip_confirmation", return_value=False),
            patch.object(plugin, "startup_marker_path", return_value=Path("/tmp/startup.json")),
        ):
            prompt = plugin._prompt(hooks.ChatHookContext(SERVER))
        self.assertIn("execv", prompt)
        self.assertIn("启动记录不存在", prompt)
        self.assertIn("数据目录", prompt)

    def test_prompt_reports_startup_marker(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            marker = Path(folder) / "startup.json"
            marker.write_text(
                '{"pid": 42, "started_at": "2026-11-06 15:30:02", "started_epoch": 1793940602}',
                encoding="utf-8",
            )
            with (
                patch.object(plugin, "skip_confirmation", return_value=False),
                patch.object(plugin, "startup_marker_path", return_value=marker),
            ):
                prompt = plugin._prompt(hooks.ChatHookContext(SERVER))
        self.assertIn("pid=42", prompt)
        self.assertIn("1793940602", prompt)

    def test_prompt_lists_empty_service_file_path(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            with (
                patch.object(plugin, "skip_confirmation", return_value=False),
                patch.object(plugin, "startup_marker_path", return_value=Path(folder) / "startup.json"),
                patch.object(plugin, "data_dir", side_effect=lambda name=None: Path(folder)),
            ):
                prompt = plugin._prompt(hooks.ChatHookContext(SERVER))
        self.assertIn("服务清单", prompt)
        self.assertIn("services.md", prompt)

    def test_prompt_injects_filled_service_list(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "services.md").write_text("- nb: 监听 8080", encoding="utf-8")
            with (
                patch.object(plugin, "skip_confirmation", return_value=False),
                patch.object(plugin, "startup_marker_path", return_value=Path(folder) / "startup.json"),
                patch.object(plugin, "data_dir", side_effect=lambda name=None: Path(folder)),
            ):
                prompt = plugin._prompt(hooks.ChatHookContext(SERVER))
        self.assertIn("监听 8080", prompt)

    async def test_forged_tool_call_fails_before_submit(self) -> None:
        with (
            patch.object(plugin, "execution_allowed", return_value=False),
            patch.object(plugin.executor, "submit") as submit,
        ):
            with self.assertRaises(ToolError):
                await plugin.server_exec(
                    ToolContext(user_id="admin", session_id=SERVER), ToolRuntime(), command="echo x"
                )
        submit.assert_not_called()

    async def test_blocked_command_is_rejected_before_submit(self) -> None:
        with (
            patch.object(plugin, "execution_allowed", return_value=True),
            patch.object(plugin, "skip_confirmation", return_value=True),
            patch.object(plugin, "blocked_reason", return_value="rm -rf"),
            patch.object(plugin, "audit"),
            patch.object(plugin.executor, "submit") as submit,
        ):
            with self.assertRaises(ToolError):
                await plugin.server_exec(
                    ToolContext(user_id="admin", session_id=SERVER), ToolRuntime(), command="rm -rf /"
                )
        submit.assert_not_called()

    async def test_pending_command_is_shown_before_confirm_enabled(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock())
        result = {"job_id": "123", "status": "pending", "command": "not-run", "cwd": "/tmp"}
        with (
            patch.object(plugin, "execution_allowed", return_value=True),
            patch.object(plugin.executor, "submit", return_value=result),
            patch.object(plugin.executor, "presented") as shown,
        ):
            await plugin.server_exec(
                ToolContext(user_id="admin", session_id=SERVER), ToolRuntime(matcher=matcher), command="not-run"
            )
        matcher.send.assert_awaited_once()
        shown.assert_called_once_with(SERVER, "123", "")
        self.assertIn("not-run", matcher.send.await_args.args[0])

    async def test_failed_confirmation_delivery_revokes_pending_job(self) -> None:
        matcher = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("offline")))
        result = {"job_id": "123", "status": "pending", "command": "not-run", "cwd": "/tmp"}
        with (
            patch.object(plugin, "execution_allowed", return_value=True),
            patch.object(plugin.executor, "submit", return_value=result),
            patch.object(plugin.executor, "presented") as shown,
            patch.object(plugin.executor, "cancel") as cancel,
        ):
            with self.assertRaises(RuntimeError):
                await plugin.server_exec(
                    ToolContext(user_id="admin", session_id=SERVER), ToolRuntime(matcher=matcher), command="not-run"
                )
        shown.assert_not_called()
        cancel.assert_called_once_with(SERVER)

    async def test_server_confirm_runs_presented_command_after_user_reply(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = execution.CommandExecutor()
            self.addAsyncCleanup(runner.stop)
            with (
                patch.object(plugin, "execution_allowed", return_value=True),
                patch.object(plugin, "executor", runner),
                patch.object(plugin, "_message_key", return_value="m2"),
                patch.object(execution, "audit"),
            ):
                pending = runner.submit(
                    SERVER, f'"{sys.executable}" -c "print(123)"', directory, Mock(return_value=True)
                )
                runner.presented(SERVER, pending["job_id"], "m1")
                result = await plugin.server_confirm(
                    ToolContext(user_id="admin", session_id=SERVER),
                    ToolRuntime(bot=bot(), event=event("行")),
                )
            self.assertEqual(result["status"], "completed")
            self.assertIn("123", result["output"])

    async def test_server_confirm_rejects_same_message_as_submission(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = execution.CommandExecutor()
            self.addAsyncCleanup(runner.stop)
            with (
                patch.object(plugin, "execution_allowed", return_value=True),
                patch.object(plugin, "executor", runner),
                patch.object(plugin, "_message_key", return_value="m1"),
                patch.object(execution, "audit"),
            ):
                pending = runner.submit(SERVER, "not-run", directory, Mock(return_value=True))
                runner.presented(SERVER, pending["job_id"], "m1")
                with self.assertRaises(ToolError):
                    await plugin.server_confirm(
                        ToolContext(user_id="admin", session_id=SERVER),
                        ToolRuntime(bot=bot(), event=event("行")),
                    )
                job = runner.jobs[pending["job_id"]]
                self.assertEqual(job.status, "pending")
                self.assertIsNone(job.task)

    async def test_clear_notification_cancels_only_server_session(self) -> None:
        with patch.object(plugin.executor, "cancel") as cancel:
            await plugin._observe("session_clear_start", hooks.ChatHookContext(SERVER), {})
            await plugin._observe("session_clear_start", hooks.ChatHookContext(BASE), {})
        cancel.assert_called_once_with(SERVER)
