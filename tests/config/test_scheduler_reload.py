"""控制台保存后的调度热更新回归测试。"""

from __future__ import annotations

import sys
import unittest
from copy import deepcopy
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from apscheduler.schedulers.background import BackgroundScheduler
from src.plugins.nonebot_plugin_fxbot.chat import emoji
from src.plugins.nonebot_plugin_fxbot.membership import tasks as membership_tasks
from src.plugins.nonebot_plugin_fxbot.chat.memory import tasks as memory_tasks
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.config.manager import ConfigManager
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS


class SchedulerReloadTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        directory = TemporaryDirectory(prefix="fxbot-scheduler-reload-")
        self.addCleanup(directory.cleanup)
        self.manager = ConfigManager()
        self.proxy = self.manager.register("system")
        self.proxy.storage._path = Path(directory.name) / "config.json"
        cache.pop(self.proxy._cache_key)
        self.addCleanup(cache.pop, self.proxy._cache_key)
        self.config = deepcopy(SYSTEM_DEFAULTS)
        self.proxy.save(self.config)
        self.scheduler = BackgroundScheduler()
        self.scheduler.start(paused=True)
        self.addCleanup(self.scheduler.shutdown)
        for patcher in (
            patch.dict(sys.modules, {"nonebot_plugin_apscheduler": SimpleNamespace(scheduler=self.scheduler)}),
            patch.object(emoji, "get_config_manager", return_value=self.manager),
            patch.object(membership_tasks, "get_config_manager", return_value=self.manager),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_save_updates_emoji_intervals_without_duplicate_jobs(self) -> None:
        memory_tasks.setup_memory_tasks()
        self.config["chat"]["emoji"]["check_interval"] = 17
        self.config["chat"]["emoji"]["cache_cleanup"]["check_interval_hours"] = 2.5
        self.manager.save_console_configs({"system": self.config})
        self.manager.save_console_configs({"system": self.config})
        for job_id, seconds in (("fxbot_chat_emoji_reconcile", 1020), ("fxbot_chat_emoji_cleanup", 9000)):
            with self.subTest(job_id=job_id):
                self.assertEqual(self.scheduler.get_job(job_id).trigger.interval.total_seconds(), seconds)
                self.assertEqual(sum(job.id == job_id for job in self.scheduler.get_jobs()), 1)

    async def test_disable_removes_job_and_blocks_queued_execution(self) -> None:
        self.config["membership"]["enable_scheduler"] = True
        self.config["membership"]["auto_leave_expired_groups"] = True
        self.manager.save_console_configs({"system": self.config})
        queued_job = self.scheduler.get_job("fxbot_membership_check").func
        self.config["membership"]["enable_scheduler"] = False
        self.manager.save_console_configs({"system": self.config})
        self.assertIsNone(self.scheduler.get_job("fxbot_membership_check"))
        self.manager.save_console_configs({"system": self.config})
        with (
            patch.object(membership_tasks, "check_and_process_memberships", new_callable=AsyncMock) as process,
            patch.object(membership_tasks, "_leave_group", new_callable=AsyncMock) as leave,
        ):
            self.assertEqual(await queued_job(), membership_tasks.MembershipTaskResult())
        process.assert_not_awaited()
        leave.assert_not_awaited()

    def test_enable_and_reschedule_use_new_time(self) -> None:
        self.config["membership"]["enable_scheduler"] = False
        self.manager.save_console_configs({"system": self.config})
        self.assertIsNone(self.scheduler.get_job("fxbot_membership_check"))
        self.config["membership"]["enable_scheduler"] = True
        for schedule_time in ("07:23", "19:41"):
            with self.subTest(schedule_time=schedule_time):
                self.config["membership"]["schedule_time"] = schedule_time
                self.manager.save_console_configs({"system": self.config})
                fields = {
                    field.name: str(field) for field in self.scheduler.get_job("fxbot_membership_check").trigger.fields
                }
                self.assertEqual(f"{int(fields['hour']):02d}:{int(fields['minute']):02d}", schedule_time)
                self.assertEqual(fields["second"], "0")
                self.assertEqual(sum(job.id == "fxbot_membership_check" for job in self.scheduler.get_jobs()), 1)

    async def test_enabled_entry_runs_membership_processing(self) -> None:
        self.config["membership"]["enable_scheduler"] = True
        self.proxy.save(self.config)
        result = membership_tasks.MembershipTaskResult(left=1, expired=2)
        with patch.object(membership_tasks, "check_and_process_memberships", return_value=result) as process:
            self.assertIs(await membership_tasks.membership_task_job(), result)
        process.assert_awaited_once_with()

    def test_reconfiguration_failure_keeps_saved_config_and_replays_other_setup(self) -> None:
        self.config["membership"]["schedule_time"] = "09:14"
        with (
            patch.object(memory_tasks, "setup_memory_tasks", side_effect=RuntimeError("scheduler unavailable")),
            patch("src.plugins.nonebot_plugin_fxbot.config.manager.logger.warning") as warning,
        ):
            self.manager.save_console_configs({"system": self.config})
        warning.assert_called_once()
        self.assertEqual(self.manager.get_system()["membership"]["schedule_time"], "09:14")
        self.assertIsNotNone(self.scheduler.get_job("fxbot_membership_check"))

    def test_failed_save_does_not_reconfigure_jobs(self) -> None:
        for error in (ValueError("invalid config"), OSError("write failed")):
            with (
                self.subTest(error=error),
                patch.object(self.proxy, "save", side_effect=error),
                patch.object(memory_tasks, "setup_memory_tasks") as memory_setup,
                patch.object(membership_tasks, "setup_membership_tasks") as membership_setup,
                self.assertRaises(type(error)),
            ):
                self.manager.save_console_configs({"system": self.config})
            memory_setup.assert_not_called()
            membership_setup.assert_not_called()
