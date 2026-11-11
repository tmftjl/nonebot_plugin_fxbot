"""群聊日报的历史回溯、统计、模型输出清洗与 HTML 渲染回归。"""

from __future__ import annotations

import os
import tempfile
import unittest

os.environ.setdefault("LOG_LEVEL", "INFO")

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    _test_dir = tempfile.mkdtemp(prefix="fxbot-group-analysis-tests-")
    nonebot.init(fxbot_data_dir=_test_dir, fxbot_config_dir=_test_dir)

nonebot.load_plugin("src.plugins.nonebot_plugin_fxbot.plugins.group_analysis")

from src.plugins.nonebot_plugin_fxbot.config.proxy import ConfigProxy  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot11 import (  # noqa: E402
    OneBotV11MessageAdapter,
    _cq_text,
    _history_message,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.config import (  # noqa: E402
    DEFAULTS,
    bounded_days,
    validate_config,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.history import (  # noqa: E402
    HistoryMessage,
    build_stats,
    build_transcript,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.analyzer import _clean  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.renderer import _build_html  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.ui_schema import get_ui_schema  # noqa: E402


class FakeBot:
    def __init__(self, app_name: str, pages: list[list[dict]], self_id: str = "10000") -> None:
        self.self_id = self_id
        self.app_name = app_name
        self.pages = pages
        self.calls: list[tuple[str, dict]] = []

    def _page(self, params: dict) -> list[dict]:
        # 锚点页包含锚点本身并继续向更早回溯，与 NapCat/sl 的实际语义一致。
        anchor = params.get("message_id") if "message_id" in params else params.get("message_seq")
        flat = [message for page in self.pages for message in page]
        if anchor is None:
            return flat[:2]
        index = next(
            (position for position, message in enumerate(flat) if str(message["message_id"]) == str(anchor)),
            None,
        )
        return flat[index:] if index is not None else []

    async def call_api(self, api: str, **kwargs):
        self.calls.append((api, kwargs))
        if api == "get_version_info":
            return {"app_name": self.app_name}
        if api == "get_group_msg_history":
            return {"messages": self._page(kwargs)}
        raise AssertionError(f"unexpected api: {api}")


def _raw(message_id: int, seq: int, timestamp: int, user_id: int, text: str) -> dict:
    content = [{"type": "image", "data": {}}] if text == "[image]" else [{"type": "text", "data": {"text": text}}]
    return {
        "message_id": message_id,
        "message_seq": seq,
        "time": timestamp,
        "user_id": user_id,
        "sender": {"card": f"card-{user_id}", "nickname": f"nick-{user_id}"},
        "message": content,
    }


def _pages() -> list[list[dict]]:
    # 每页按接口习惯逆序返回；第二页与第一页有一条重叠消息。
    return [
        [_raw(4, 4, 4000, 30003, "第四条"), _raw(3, 3, 3000, 30001, "第三条")],
        [_raw(3, 3, 3000, 30001, "第三条"), _raw(2, 2, 2000, 30002, "[image]"), _raw(1, 1, 1000, 30001, "第一条")],
    ]


class AdapterHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_standard_dialect_pages_by_seq_and_dedupes_cutoff(self) -> None:
        bot = FakeBot("NapCat", _pages())
        messages = await OneBotV11MessageAdapter().fetch_group_history(bot, "555", start_time=1500, limit=10)
        self.assertEqual([item.message_id for item in messages], ["2", "3", "4"])
        self.assertEqual(messages[0].text, "[图片]")
        self.assertEqual(messages[0].images, 1)
        history_calls = [kwargs for api, kwargs in bot.calls if api == "get_group_msg_history"]
        self.assertTrue(history_calls[0]["reverseOrder"])
        self.assertEqual(history_calls[1]["message_seq"], 3)

    async def test_sl_dialect_uses_message_id_without_reverse_order(self) -> None:
        bot = FakeBot("sl", _pages(), self_id="10001")
        messages = await OneBotV11MessageAdapter().fetch_group_history(bot, "555", start_time=0, limit=10)
        self.assertEqual([item.message_id for item in messages], ["1", "2", "3", "4"])
        history_calls = [kwargs for api, kwargs in bot.calls if api == "get_group_msg_history"]
        self.assertNotIn("reverseOrder", history_calls[0])
        self.assertIn("message_id", history_calls[1])

    def test_raw_cq_fallback_and_empty_messages(self) -> None:
        assert _history_message({"time": 0}) is None
        assert _history_message({"time": 1000, "message": []}) is None
        parsed = _history_message(
            {"time": 1000, "user_id": 1, "raw_message": "看图[CQ:image,file=a.jpg]和[CQ:face,id=1]"}
        )
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.text, parsed.images), ("看图[图片]和[表情]", 1))
        self.assertEqual(_cq_text("[CQ:at,qq=1] 你好"), "@ 你好")


class ReportDataTests(unittest.TestCase):
    def test_stats_and_transcript(self) -> None:
        messages = [
            HistoryMessage("1", 1000, "a", "小明", "你好", 0),
            HistoryMessage("2", 2000, "a", "小明", "再见", 0),
            HistoryMessage("3", 3000, "c", "阿巴", "[图片]", 1),
        ]
        stats = build_stats(messages)
        self.assertEqual((stats["total"], stats["participants"], stats["images"]), (3, 2, 1))
        self.assertEqual(stats["top_speakers"][0], {"name": "小明", "count": 2})
        transcript = build_transcript(messages)
        self.assertIn("小明: 你好", transcript)
        self.assertIn("阿巴: [图片]", transcript)

    def test_model_output_is_clamped_and_filtered(self) -> None:
        cleaned = _clean(
            {
                "topics": [
                    {"title": "话题", "summary": "摘要", "keywords": ["a", 3], "participants": None},
                    {"summary": "缺标题"},
                ],
                "titles": [{"name": "小明", "title": "称号", "reason": "理由"}, "bad"],
                "quotes": [{"content": "原话", "sender": "小明", "reason": "理由"}],
                "quality": {"score": 150, "comment": "不错", "highlights": ["亮点"], "suggestions": None},
            }
        )
        self.assertEqual(len(cleaned["topics"]), 1)
        self.assertEqual(cleaned["topics"][0]["participants"], [])
        self.assertEqual(len(cleaned["titles"]), 1)
        self.assertEqual(cleaned["quality"]["score"], 100)
        self.assertEqual(cleaned["quality"]["suggestions"], [])
        self.assertEqual(_clean(None)["topics"], [])

    def test_days_are_bounded_by_config(self) -> None:
        self.assertEqual(bounded_days(None), 1)
        self.assertEqual(bounded_days(0), 1)
        self.assertEqual(bounded_days(999), 7)

    def test_html_escapes_untrusted_group_and_model_text(self) -> None:
        html = _build_html(
            {
                "group_id": "1",
                "group_name": "<script>alert(1)</script>",
                "label": "<b>日期</b>",
                "stats": {"total": 1, "participants": 1, "images": 0, "hours": [], "top_speakers": []},
                "analysis": {
                    "topics": [{"title": "<img src=x>", "summary": "摘要", "keywords": [], "participants": []}],
                    "titles": [],
                    "quotes": [],
                    "quality": {"score": 10, "comment": "", "highlights": [], "suggestions": []},
                },
            }
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x>", html)
        self.assertIn("&lt;script&gt;", html)


class ConfigShapeTests(unittest.TestCase):
    def test_saved_groups_survive_reload(self) -> None:
        proxy = ConfigProxy("group_analysis_test", DEFAULTS, validator=validate_config)
        proxy.save({"basic": {"days": 3, "max_days": 5}, "auto": {"auto_hour": 9}})
        loaded = proxy.load()
        self.assertEqual((loaded["basic"]["days"], loaded["basic"]["max_days"]), (3, 5))
        self.assertEqual(loaded["auto"]["auto_hour"], 9)
        self.assertEqual(loaded["basic"]["max_messages"], 1000)

    def test_ui_schema_fields_match_defaults(self) -> None:
        for card in get_ui_schema()["cards"]:
            group = DEFAULTS
            for part in card["key"].split(".")[1:]:
                group = group[part]
            for item in card["schemas"]:
                self.assertIn(item["field"], group)


if __name__ == "__main__":
    unittest.main()
