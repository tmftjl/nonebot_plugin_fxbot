"""在导入插件前初始化独立的 NoneBot 测试环境。"""

from __future__ import annotations

import os
import tempfile

os.environ.setdefault("LOG_LEVEL", "INFO")

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    _test_dir = tempfile.mkdtemp(prefix="fxbot-chat-tests-")
    nonebot.init(fxbot_data_dir=_test_dir, fxbot_config_dir=_test_dir)
