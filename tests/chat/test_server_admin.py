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
from src.plugins.nonebot_plugin_fxbot.plugins.server_admin import store, access

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
                "commands": {"mode": {"enabled": True}},
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
        cache.clear()
        self.addCleanup(cache.clear)
        self.enterContext(patch.object(plugin.chat_service, "ensure_hydrated", AsyncMock()))

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

    def test_pristine_seed_is_upgraded_on_next_activation(self) -> None:
        store.set_mode(BASE, True)
        path = self.folder("personas") / "运维.md"
        path.write_text("旧版人格", encoding="utf-8")
        with patch.object(store, "_LEGACY_SEEDS", frozenset({store._digest("旧版人格")})):
            store.set_mode(BASE, False)
            store.set_mode(BASE, True)
        seed = Path(store.__file__).with_name("persona.md").read_text(encoding="utf-8").strip()
        self.assertEqual(path.read_text(encoding="utf-8"), seed)

    def test_user_edited_persona_is_not_overwritten(self) -> None:
        store.set_mode(BASE, True)
        path = self.folder("personas") / "运维.md"
        path.write_text("我改过的人格", encoding="utf-8")
        store.set_mode(BASE, False)
        store.set_mode(BASE, True)
        self.assertEqual(path.read_text(encoding="utf-8"), "我改过的人格")

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

    def test_removed_commands_no_longer_match(self) -> None:
        for text in ("#开启服务器模式", "#退出服务器模式"):
            self.assertIsNone(plugin._COMMAND.fullmatch(text))
        for text in ("#服务器模式", "#聊天模式", "#确认", "#取消"):
            self.assertIsNotNone(plugin._COMMAND.fullmatch(text))

    async def test_confirmation_automatically_summarizes_completed_output(self) -> None:
        job = SimpleNamespace(id="job", status="running", output="", returncode=None, truncated=False)

        async def finish():
            job.status, job.output, job.returncode = "completed", "Memory: 3.6G total", 0

        job.task = asyncio.create_task(finish())
        fake = SimpleNamespace(
            jobs={"job": job},
            confirm=Mock(return_value={"job_id": "job"}),
            pending_id=Mock(return_value="123456abcdef"),
        )
        with (
            patch.object(plugin, "owner_allowed", return_value=True),
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin, "execution_allowed", return_value=True),
            patch.object(plugin, "executor", fake),
            patch.object(plugin, "_report_result", AsyncMock()) as report,
        ):
            actor, source, matcher = bot(), event("执行吧"), object()
            result = await plugin.handle_mode(actor, source, matcher)
        self.assertEqual(result, "")
        report.assert_awaited_once_with(
            actor, source, matcher, SERVER, job, plugin.chat_service.session_store.epoch(SERVER)
        )
        self.assertEqual(job.output, "Memory: 3.6G total")
        fake.confirm.assert_called_once_with(SERVER, "123456abcdef")

    def test_natural_confirmation_only_accepts_whole_unambiguous_phrases(self) -> None:
        identifier = "123456abcdef"
        with (
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin.executor, "pending_id", return_value=identifier),
        ):
            for phrase in ("确认执行", "执行吧", "可以执行！", f"确认执行 {identifier}"):
                self.assertEqual(plugin._action(bot(), event(phrase)), ("确认", identifier))
            for phrase in ("取消执行", "不要执行", "别执行了"):
                self.assertEqual(plugin._action(bot(), event(phrase)), ("取消", identifier))
            for phrase in ("好", "嗯", "确认执行吗", "不要确认执行", "他说：执行吧", "确认执行 000000000000"):
                self.assertIsNone(plugin._action(bot(), event(phrase)))

    def test_natural_confirmation_requires_pending_job_and_binds_event(self) -> None:
        with (
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin.executor, "pending_id", side_effect=["", "123456abcdef", "000000000000"]),
        ):
            self.assertIsNone(plugin._action(bot(), event("执行吧")))
            source = event("执行吧")
            self.assertEqual(plugin._action(bot(), source), ("确认", "123456abcdef"))
            self.assertEqual(plugin._action(bot(), source), ("确认", "123456abcdef"))

    async def test_natural_confirmation_requires_owner_and_active_mode(self) -> None:
        with (
            patch.object(plugin, "owner_allowed", return_value=False),
            patch.object(plugin.executor, "pending_id") as pending,
        ):
            self.assertFalse(await plugin._rule(bot(), event("执行吧")))
            pending.assert_not_called()
        with (
            patch.object(plugin, "owner_allowed", return_value=True),
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin.executor, "pending_id", return_value="123456abcdef"),
        ):
            self.assertFalse(await plugin._rule(bot(), event("执行吧")))
            store.set_mode(BASE, True)
            self.assertTrue(await plugin._rule(bot(), event("执行吧")))

    async def test_stale_natural_cancellation_cannot_cancel_replacement_job(self) -> None:
        with (
            patch.object(plugin, "owner_allowed", return_value=True),
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin, "execution_allowed", return_value=True),
            patch.object(plugin.executor, "pending_id", side_effect=["123456abcdef", "000000000000"]),
            patch.object(plugin.executor, "cancel") as cancel,
        ):
            self.assertIn("没有匹配", await plugin.handle_mode(bot(), event("取消执行")))
            cancel.assert_not_called()

    async def test_result_summary_uses_real_output_and_rechecks_permission_and_epoch(self) -> None:
        result = {"status": "completed", "returncode": 1, "output": "diagnostic output", "truncated": True}
        job = SimpleNamespace(status="completed", result=lambda: result)
        identity = SimpleNamespace(dedupe_key="message", message_id="source", platform="onebot11")
        with (
            patch.object(plugin, "event_identity", return_value=identity),
            patch.object(plugin, "execution_allowed", return_value=True) as allowed,
            patch.object(plugin.chat_service, "is_current", return_value=True) as current,
            patch.object(plugin.chat_service, "focus_reply", AsyncMock(return_value="summary")) as reply,
        ):
            self.assertEqual(await plugin._report_result(bot(), event(), object(), SERVER, job, 42), "summary")
            request, runtime, instruction = reply.call_args.args
            self.assertEqual((request.session_id, request.user_id, request.accepted_epoch), (SERVER, "admin", 42))
            self.assertIn("diagnostic output", instruction)
            self.assertIn('"returncode": 1', instruction)
            valid = reply.call_args.kwargs["is_valid"]
            self.assertTrue(valid())
            allowed.return_value = False
            self.assertFalse(valid())
            allowed.return_value, current.return_value = True, False
            self.assertFalse(valid())
            current.return_value, job.status = True, "cancelled"
            self.assertFalse(valid())

    async def test_confirmation_does_not_deliver_after_permission_revoked(self) -> None:
        job = SimpleNamespace(id="job", status="completed", output="private output", returncode=0, truncated=False)
        job.task = asyncio.create_task(asyncio.sleep(0))
        fake = SimpleNamespace(jobs={"job": job}, confirm=Mock(return_value={"job_id": "job"}))
        with (
            patch.object(plugin, "owner_allowed", return_value=True),
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin, "execution_allowed", side_effect=[True, False]),
            patch.object(plugin, "executor", fake),
        ):
            self.assertEqual(await plugin.handle_mode(bot(), event("#确认")), "")

    async def test_cancelled_confirmation_does_not_deliver_stale_output(self) -> None:
        job = SimpleNamespace(id="job", status="cancelled", output="stale", returncode=None, truncated=False)
        job.task = asyncio.create_task(asyncio.sleep(30))
        job.task.cancel()
        fake = SimpleNamespace(jobs={"job": job}, confirm=Mock(return_value={"job_id": "job"}))
        with (
            patch.object(plugin, "owner_allowed", return_value=True),
            patch.object(plugin, "actor_base", return_value=BASE),
            patch.object(plugin, "execution_allowed", return_value=True),
            patch.object(plugin, "executor", fake),
        ):
            self.assertEqual(await plugin.handle_mode(bot(), event("#确认")), "")

    def test_hooks_route_and_protect_disabled_server_sessions(self) -> None:
        store.set_mode(BASE, True)
        self.assertEqual(plugin._resolve(bot(), event(), BASE), SERVER)
        self.assertEqual(plugin._policy(SERVER), session_hooks.SessionPolicy(False, False, False, False, False))
        request = ChatRequest(SERVER, "admin", None, "hi", "turn", "msg", "private")
        context = hooks.hook_context(request)
        with patch.object(plugin, "plugin_enabled", return_value=False):
            self.assertFalse(plugin._guard(context, {}))
        self.assertFalse(plugin._tools(SimpleNamespace(session_id=BASE), {"tool_name": "server_exec"}))


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

    def test_pending_id_requires_presentation_correct_session_and_valid_ttl(self) -> None:
        result = self.executor.submit(SERVER, "python -c pass", self.cwd, self.authorize)
        identifier = result["job_id"]
        self.assertEqual(self.executor.pending_id(SERVER), "")
        self.executor.presented(SERVER, identifier)
        self.assertEqual(self.executor.pending_id(SERVER), identifier)
        self.assertEqual(self.executor.pending_id(BASE), "")
        self.executor.jobs[identifier].created -= execution.CONFIRM_TTL
        self.assertEqual(self.executor.pending_id(SERVER), "")

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
        self.executor.presented(SERVER, result["job_id"])
        self.executor.confirm(SERVER)
        await asyncio.sleep(0.05)
        self.authorize.return_value = False
        finished = await self.finish(result)
        self.assertEqual(finished["status"], "cancelled")

    async def test_cancel_and_shutdown_terminate_running_command(self) -> None:
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"])
        self.executor.confirm(SERVER)
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
        self.executor.presented(SERVER, result["job_id"])
        with patch.object(execution, "COMMAND_TIMEOUT", 0.8):
            self.executor.confirm(SERVER)
            finished = await self.finish(result)
        self.assertEqual(finished["status"], "timeout")
        self.assertTrue(pid_file.exists())
        pid = int(pid_file.read_text(encoding="utf-8"))
        self.assertFalse(psutil.pid_exists(pid))

    async def test_confirmation_requires_presentation_owner_and_single_use(self) -> None:
        command = f'"{sys.executable}" -c "print(123)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.assertEqual(result["status"], "pending")
        self.assertIsNone(self.executor.jobs[result["job_id"]].task)
        with self.assertRaises(ValueError):
            self.executor.confirm(SERVER)
        self.executor.presented(SERVER, result["job_id"])
        with self.assertRaises(ValueError):
            self.executor.confirm(BASE, result["job_id"])
        self.executor.confirm(SERVER, result["job_id"])
        with self.assertRaises(ValueError):
            self.executor.confirm(SERVER, result["job_id"])
        self.assertIn("123", (await self.finish(result))["output"])

    def test_expired_or_revoked_confirmation_does_not_start(self) -> None:
        result = self.executor.submit(SERVER, "not-run", self.cwd, self.authorize)
        job = self.executor.jobs[result["job_id"]]
        job.shown = True
        job.created -= 121
        with self.assertRaises(ValueError):
            self.executor.confirm(SERVER)
        result = self.executor.submit(SERVER, "not-run", self.cwd, self.authorize)
        job = self.executor.jobs[result["job_id"]]
        job.shown = True
        self.authorize.return_value = False
        with self.assertRaises(PermissionError):
            self.executor.confirm(SERVER)
        self.assertIsNone(job.task)

    async def test_timeout_stops_long_running_process(self) -> None:
        command = f'"{sys.executable}" -c "import time; time.sleep(30)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"])
        with patch.object(execution, "COMMAND_TIMEOUT", 0.15):
            self.executor.confirm(SERVER)
            finished = await self.finish(result)
        self.assertEqual(finished["status"], "timeout")

    async def test_continuous_output_is_drained_and_truncated(self) -> None:
        command = f'"{sys.executable}" -c "print(\'a\'*100000)"'
        result = self.executor.submit(SERVER, command, self.cwd, self.authorize)
        self.executor.presented(SERVER, result["job_id"])
        self.executor.confirm(SERVER)
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
            self.executor.confirm(SERVER)


class ToolTests(unittest.IsolatedAsyncioTestCase):
    """独立工具入口不相信 allowed_names、调用者声明或模型代确认。"""

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

    def test_prompt_keeps_protocol_without_internal_ids(self) -> None:
        prompt = plugin._prompt(hooks.ChatHookContext(SERVER))
        self.assertIn("确认执行", prompt)
        self.assertIn("不重复提交", prompt)
        self.assertNotIn("job_id", prompt)
        self.assertIsNone(plugin._prompt(hooks.ChatHookContext(BASE)))

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
        shown.assert_called_once_with(SERVER, "123")
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

    async def test_clear_notification_cancels_only_server_session(self) -> None:
        with patch.object(plugin.executor, "cancel") as cancel:
            await plugin._observe("session_clear_start", hooks.ChatHookContext(SERVER), {})
            await plugin._observe("session_clear_start", hooks.ChatHookContext(BASE), {})
        cancel.assert_called_once_with(SERVER)
