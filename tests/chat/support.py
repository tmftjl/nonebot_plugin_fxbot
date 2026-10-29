"""在导入插件前初始化独立的 NoneBot 测试环境。"""

from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("LOG_LEVEL", "INFO")

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    _test_dir = tempfile.mkdtemp(prefix="fxbot-chat-tests-")
    nonebot.init(fxbot_data_dir=_test_dir, fxbot_config_dir=_test_dir)


class ReplyPipelineTests(unittest.IsolatedAsyncioTestCase):
    """隔离回复生成和发送链；Planner 调度由独立会话测试覆盖。"""

    def run(self, result=None):
        from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService

        original = ChatService._process_with_provider

        async def generate(service, request, runtime, *args, focus_instruction=None, **kwargs):
            return await original(
                service, request, runtime, *args, focus_instruction=focus_instruction or request.text, **kwargs
            )

        with (
            patch.object(ChatService, "_process_with_provider", generate),
            patch.object(ChatService, "queue_message", AsyncMock(return_value=False)),
        ):
            return super().run(result)


class RouterUnitTests(unittest.IsolatedAsyncioTestCase):
    """路由测试隔离后台调度；真实调度顺序由 test_turn_scheduler 覆盖。"""

    def run(self, result=None):
        from types import SimpleNamespace

        from src.plugins.nonebot_plugin_fxbot.chat.router import normal_scheduler

        async def submit(service, request, runtime):
            if isinstance(service, SimpleNamespace):
                try:
                    await service.process_and_send(request, runtime)
                except Exception:
                    return False

        with patch.object(normal_scheduler, "submit", AsyncMock(side_effect=submit)):
            return super().run(result)
