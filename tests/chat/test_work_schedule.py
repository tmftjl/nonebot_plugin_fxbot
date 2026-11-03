"""工作时间、法定调休、离线缓存及关闭边界的回归。"""

from __future__ import annotations

import json
import asyncio
import unittest
from types import SimpleNamespace
from pathlib import Path
from datetime import datetime
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import work_schedule as schedule
from src.plugins.nonebot_plugin_fxbot.utils.tz import SHANGHAI_TZ
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.utils.cache import cache
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior import turn_scheduler
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import conversation
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import validate_system_config
from src.plugins.nonebot_plugin_fxbot.chat.focus.group_runtime import FocusEntry, FocusGroupRuntime


def config(**values) -> dict:
    """构造只含两个新增选项的时段配置。"""
    return {"enabled": True, "rest_time_no_reply": True, "work_time": "08:30-17:30", **values}


def at(month: int, day: int, hour: int = 10, minute: int = 0) -> datetime:
    """构造北京时间日期，避免测试受运行机器时区影响。"""
    return datetime(2026, month, day, hour, minute, tzinfo=SHANGHAI_TZ)


class WorkTimeTests(unittest.TestCase):
    """不联网验证工作日覆盖规则和精确时间边界。"""

    def setUp(self) -> None:
        cache.clear_prefix((schedule._DOMAIN,))
        self.addCleanup(cache.clear_prefix, (schedule._DOMAIN,))
        for year in (2025, 2027):
            cache.set((schedule._DOMAIN, year), {})
        cache.set((schedule._DOMAIN, 2026), {"2026-01-04": False, "2026-10-01": True})

    def test_time_boundaries(self) -> None:
        for hour, minute, expected in [(8, 29, False), (8, 30, True), (17, 29, True), (17, 30, False)]:
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(schedule.schedule_status(config(), at=at(11, 3, hour, minute))["allowed"], expected)

    def test_public_holiday_weekend_and_makeup(self) -> None:
        for month, day, expected in [(10, 1, False), (11, 7, False), (1, 4, True), (11, 3, True)]:
            with self.subTest(month=month, day=day):
                self.assertEqual(schedule.schedule_status(config(), at=at(month, day))["allowed"], expected)

    def test_next_year_notice_can_override_december(self) -> None:
        cache.set((schedule._DOMAIN, 2027), {"2026-12-31": True})
        self.assertFalse(schedule.schedule_status(config(), at=at(12, 31))["allowed"])

    def test_disabled_switch_does_not_need_calendar(self) -> None:
        with patch.object(schedule, "calendar_days") as load:
            self.assertTrue(schedule.schedule_status(config(rest_time_no_reply=False), at=at(10, 1))["allowed"])
        load.assert_not_called()

    def test_manual_off_is_never_reopened(self) -> None:
        with patch.object(schedule, "now", return_value=at(11, 3)):
            self.assertFalse(schedule.can_reply(config(enabled=False)))

    def test_missing_calendar_fails_closed(self) -> None:
        with patch.object(schedule, "calendar_days", return_value=None):
            status = schedule.schedule_status(config(), at=at(11, 3))
        self.assertFalse(status["allowed"])
        self.assertEqual(status["reason"], "calendar_unavailable")

    def test_configuration_validation(self) -> None:
        validate_system_config({"chat": config()})
        for value in ["8.30-5.30", "25:00-17:30", "17:30-08:30", "08:30-08:30", None, True]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_system_config({"chat": config(work_time=value)})
        with self.assertRaises(ValueError):
            validate_system_config({"chat": config(rest_time_no_reply="true")})

    def test_settings_are_read_again_after_hot_reload(self) -> None:
        chat = config(work_time="09:00-09:30")
        manager = SimpleNamespace(get_system=lambda: {"chat": chat})
        with (
            patch.object(schedule, "get_config_manager", return_value=manager),
            patch.object(schedule, "now", return_value=at(11, 3)),
        ):
            self.assertFalse(schedule.can_reply())
            chat["work_time"] = "08:30-17:30"
            self.assertTrue(schedule.can_reply())
            chat["rest_time_no_reply"] = False
            chat["enabled"] = False
            self.assertFalse(schedule.can_reply())

    def test_console_exposes_exact_requested_fields(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.console.routes.config import _chat_config_tab

        card = next(card for card in _chat_config_tab()["cards"] if card["key"] == "chat")
        fields = {entry["field"]: entry for entry in card["schemas"]}
        self.assertEqual(fields["work_time"]["default"], "08:30-17:30")
        self.assertEqual(fields["rest_time_no_reply"]["component"], "Switch")


class WorkLifePromptTests(unittest.TestCase):
    """日常作息设定只在已有开关开启时注入各模型入口。"""

    def test_disabled_switch_adds_no_persona_setting(self) -> None:
        self.assertEqual(schedule.work_life_prompt(config(rest_time_no_reply=False)), "")
        self.assertEqual(schedule.work_life_prompt({}), "")

    def test_setting_uses_configured_hours_without_inventing_specific_events(self) -> None:
        prompt = schedule.work_life_prompt(config(work_time="09:15-18:45"))
        for text in ("09:15-18:45", "调休补班", "打游戏", "看电视", "出去玩", "不反复强调或编造具体经历"):
            self.assertIn(text, prompt)
        self.assertNotIn("08:30-17:30", prompt)
        self.assertLess(len(prompt), 120)

    def test_normal_planner_and_replyer_follow_switch_and_hot_reload(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.replyer.prompt import build_persona_text

        for group_id in ("group", None):
            for metadata in ({"planner_loop": True}, {"planner_loop": True, "planner_reply": True}):
                with self.subTest(group_id=group_id, metadata=metadata):
                    request = ChatRequest(
                        "work-life-test",
                        "user",
                        group_id,
                        "昨天下班干嘛去了",
                        "turn",
                        "msg",
                        "mention",
                        metadata=metadata,
                    )
                    chat = {**config(), "persona": "小鱼"}
                    _, prompt = build_persona_text(request, chat, {})
                    self.assertEqual(prompt.count("[日常作息]"), 1)
                    self.assertIn("08:30-17:30", prompt)
                    chat["work_time"] = "09:00-18:00"
                    _, updated = build_persona_text(request, chat, {})
                    self.assertIn("09:00-18:00", updated)
                    chat["rest_time_no_reply"] = False
                    _, disabled = build_persona_text(request, chat, {})
                    self.assertNotIn("[日常作息]", disabled)

    def test_focus_planner_follows_same_switch(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.focus import group_runtime

        chat = {**config(), "persona": "小鱼"}
        manager = SimpleNamespace(get_system=lambda: {"chat": chat})
        group = FocusGroupRuntime("work-life-test")
        with patch.object(group_runtime, "get_config_manager", return_value=manager):
            self.assertEqual(group._system_prompt().count("[日常作息]"), 1)
            self.assertIn("打游戏", group._system_prompt())
            chat["rest_time_no_reply"] = False
            self.assertNotIn("[日常作息]", group._system_prompt())


class CalendarRefreshTests(unittest.IsolatedAsyncioTestCase):
    """校验下载成功持久化、断网续用及坏数据不覆盖。"""

    def setUp(self) -> None:
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.enterContext(patch.object(schedule, "data_dir", return_value=self.root))
        self.enterContext(patch.object(schedule, "now", return_value=at(11, 3)))
        self.enterContext(
            patch.object(
                schedule, "get_config_manager", return_value=SimpleNamespace(get_system=lambda: {"chat": config()})
            )
        )
        cache.clear_prefix((schedule._DOMAIN,))
        self.addCleanup(cache.clear_prefix, (schedule._DOMAIN,))

    def payload(self, year: int = 2026) -> dict:
        """构造和上游同构的年度日历。"""
        return {
            "year": year,
            "papers": ["https://www.gov.cn/example"],
            "days": [{"date": f"{year}-01-01", "isOffDay": True}],
        }

    def client(self, payload: dict) -> SimpleNamespace:
        """模拟当前年成功、相邻年尚未公布。"""

        async def get(url, **kwargs):
            if "2026.json" in url:
                return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: payload)
            return SimpleNamespace(status_code=404)

        return SimpleNamespace(get=AsyncMock(side_effect=get))

    async def test_success_survives_memory_cache_clear_and_no_request_per_message(self) -> None:
        client = self.client(self.payload())
        with patch.object(schedule, "get_shared_async_client", AsyncMock(return_value=client)):
            await schedule.refresh_calendar()
            await schedule.refresh_calendar()
        path = self.root / "work_calendar" / "2026.json"
        self.assertTrue(path.is_file())
        cache.clear_prefix((schedule._DOMAIN,))
        self.assertEqual(schedule.calendar_days(2026), {"2026-01-01": True})
        for _ in range(10):
            schedule.schedule_status(config(), at=at(11, 3))
        self.assertEqual(client.get.await_count, 3)

    async def test_failure_and_invalid_payload_keep_last_valid_copy(self) -> None:
        with patch.object(schedule, "get_shared_async_client", AsyncMock(return_value=self.client(self.payload()))):
            await schedule.refresh_calendar()
        path = self.root / "work_calendar" / "2026.json"
        before = path.read_text(encoding="utf-8")
        for bad in [{}, {**self.payload(), "days": [{"date": "2026-01-01", "isOffDay": "false"}]}]:
            cache.pop((schedule._DOMAIN, "refresh", 2026))
            with patch.object(schedule, "get_shared_async_client", AsyncMock(return_value=self.client(bad))):
                await schedule.refresh_calendar()
            self.assertEqual(path.read_text(encoding="utf-8"), before)
        offline = SimpleNamespace(get=AsyncMock(side_effect=OSError("offline")))
        cache.pop((schedule._DOMAIN, "refresh", 2026))
        with patch.object(schedule, "get_shared_async_client", AsyncMock(return_value=offline)):
            await schedule.refresh_calendar()
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), self.payload())


class SchedulePipelineTests(unittest.IsolatedAsyncioTestCase):
    """关闭时不规划、不发送，且 Focus 不因待处理消息而空转。"""

    async def test_scheduler_drops_closed_window_requests(self) -> None:
        scheduler = turn_scheduler.NormalChatScheduler()
        service = SimpleNamespace(queue_message=AsyncMock())
        request = ChatRequest("tenant", "user", "group", "你好", "turn", "msg", "mention")
        with patch.object(turn_scheduler, "can_reply", return_value=False):
            await scheduler.submit(service, request, None)
        self.assertEqual(scheduler.chats, {})
        service.queue_message.assert_not_awaited()

    async def test_service_skips_model_during_rest(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat import service as service_module

        service = ChatService(ChatSessionStore())
        request = ChatRequest("tenant", "user", None, "你好", "turn", "msg", "private")
        with (
            patch.object(service_module, "can_reply", return_value=False),
            patch.object(service, "_process", AsyncMock()) as process,
        ):
            response = await service._process_locked(request, None)
        self.assertFalse(response.text)
        process.assert_not_awaited()

    async def test_planning_is_cancelled_when_work_time_ends(self) -> None:
        opened = True
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def model(*args, **kwargs):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        store = ChatSessionStore()
        service = SimpleNamespace(is_current=lambda *args: True, session_store=store)
        request = ChatRequest("tenant", "user", "group", "你好", "turn", "msg", "mention")
        context = SimpleNamespace(request=request, epoch=0)
        with (
            patch.object(conversation, "can_reply", side_effect=lambda: opened),
            patch.object(conversation, "request_step", model),
        ):
            task = asyncio.create_task(conversation.interruptible_step(service, context, object(), [], []))
            await started.wait()
            opened = False
            self.assertIsNone(await asyncio.wait_for(task, 2))
        self.assertTrue(cancelled.is_set())

    async def test_send_boundary_aborts_before_first_part(self) -> None:
        request = ChatRequest("tenant", "user", "group", "你好", "turn", "msg", "mention")
        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            is_current=lambda *args: True,
            is_latest=lambda *args: True,
            mark_send_aborted=AsyncMock(),
        )
        matcher = SimpleNamespace(send=AsyncMock())
        response = ChatResponse(text="已经生成的回答")
        with (
            patch.object(sender, "can_reply", return_value=False),
            patch.object(sender, "record_send_trace", AsyncMock()),
        ):
            result = await sender.send_parts(service, request, response, SimpleNamespace(), matcher)
        self.assertEqual(result, "")
        matcher.send.assert_not_awaited()

    async def test_focus_closed_window_consumes_pending_and_waits(self) -> None:
        from src.plugins.nonebot_plugin_fxbot.chat.focus import group_runtime

        group = FocusGroupRuntime("work-schedule")
        group.members["tenant"] = object()
        group._ready.add("tenant")
        group._pending["tenant"] = [FocusEntry([{"role": "user", "content": "休息时段"}], {"tenant"}, 1)]
        with patch.object(group_runtime, "can_reply", return_value=False):
            task = asyncio.create_task(group._idle_or_turn())
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            self.assertFalse(group._ready)
            group._event.set()
            await asyncio.wait_for(task, 2)
