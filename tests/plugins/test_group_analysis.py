"""群聊日报的历史回溯、统计、分析器与双主题渲染回归。"""

from __future__ import annotations

import os
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("LOG_LEVEL", "INFO")

import nonebot

try:
    nonebot.get_driver()
except ValueError:
    _test_dir = tempfile.mkdtemp(prefix="fxbot-group-analysis-tests-")
    nonebot.init(fxbot_data_dir=_test_dir, fxbot_config_dir=_test_dir)

nonebot.load_plugin("src.plugins.nonebot_plugin_fxbot.plugins.group_analysis")

from src.plugins.nonebot_plugin_fxbot.config.proxy import ConfigProxy  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis import analyzers  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.chat.providers.entities import (  # noqa: E402
    TokenUsage,
    LLMResponse,
    JSONResponse,
    JSONResponseError,
    token_usage_of,
)
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot11 import (  # noqa: E402
    OneBotV11MessageAdapter,
    _cq_text,
    _history_message,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.atri import build_atri_html  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.config import (  # noqa: E402
    DEFAULTS,
    bounded_days,
    validate_config,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.history import (  # noqa: E402
    HistoryMessage,
    build_stats,
    build_user_stats,
    build_analysis_messages,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.renderer import _build_html  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.analyzers import runner  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.ui_schema import get_ui_schema  # noqa: E402
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.analyzers.quote import (  # noqa: E402
    GoldenQuoteAnalyzer,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.analyzers.title import (  # noqa: E402
    UserTitleAnalyzer,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.analyzers.topic import (  # noqa: E402
    TopicAnalyzer,
)
from src.plugins.nonebot_plugin_fxbot.plugins.group_analysis.analyzers.quality import (  # noqa: E402
    ChatQualityAnalyzer,
)


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

    def test_raw_cq_fallback_and_reply_id(self) -> None:
        assert _history_message({"time": 0}) is None
        assert _history_message({"time": 1000, "message": []}) is None
        parsed = _history_message(
            {"time": 1000, "user_id": 1, "raw_message": "看图[CQ:image,file=a.jpg]和[CQ:face,id=1]"}
        )
        self.assertIsNotNone(parsed)
        self.assertEqual((parsed.text, parsed.images), ("看图[图片]和[表情]", 1))
        self.assertEqual(_cq_text("[CQ:at,qq=1] 你好"), "@ 你好")
        segments = [{"type": "reply", "data": {"id": 42}}, {"type": "text", "data": {"text": "在"}}]
        replied = _history_message({"time": 1000, "user_id": 1, "message": segments})
        self.assertIsNotNone(replied)
        self.assertEqual(replied.reply_to_id, "42")


class ReportDataTests(unittest.TestCase):
    def test_stats_and_analysis_input(self) -> None:
        messages = [
            HistoryMessage("1", 1000, "a", "小明", "你好", 0),
            HistoryMessage("2", 2000, "a", "小明", "再见", 0, "1"),
            HistoryMessage("3", 3000, "c", "阿巴", "[图片]", 1),
            HistoryMessage("4", 4000, "c", "阿巴", "/群聊分析", 0),
            HistoryMessage("5", 5000, "bot", "机器人", "自动回复", 0),
        ]
        stats = build_stats(messages)
        self.assertEqual((stats["total"], stats["participants"], stats["images"]), (5, 3, 1))
        self.assertEqual(stats["top_speakers"][0], {"name": "小明", "count": 2})
        rows = build_analysis_messages(messages, {"bot"})
        self.assertEqual([row["content"] for row in rows], ["[消息:1] 你好", "[回复:1] 再见", "[图片]"])
        self.assertEqual(rows[0]["time"], "08:16")
        self.assertEqual(rows[0]["user_id"], "a")

    def test_user_stats_match_title_prompt_fields(self) -> None:
        messages = [
            HistoryMessage("1", 1000, "a", "小明", "你好", 0),
            HistoryMessage("2", 2000, "a", "小明", "哈哈[表情]", 0, "1"),
            HistoryMessage("3", 3000, "c", "阿巴", "[图片]", 1),
        ]
        users = build_user_stats(messages)
        self.assertEqual(users[0]["name"], "小明")
        self.assertEqual(users[0]["message_count"], 2)
        self.assertEqual(users[0]["emoji_ratio"], 0.5)
        self.assertEqual(users[0]["reply_ratio"], 0.5)
        self.assertEqual(users[0]["avg_chars"], 2.0)

    def test_days_are_bounded_by_config(self) -> None:
        self.assertEqual(bounded_days(None), 1)
        self.assertEqual(bounded_days(0), 1)
        self.assertEqual(bounded_days(999), 7)


class _Model:
    def __init__(self, structured: object = None, plain: list[object] | None = None) -> None:
        self.usage = TokenUsage()
        self.chat_json = AsyncMock(side_effect=self._structured)
        self.chat = AsyncMock(side_effect=self._chat)
        self._structured_result = structured if structured is not None else {}
        self._plain = list(plain or [])

    async def _structured(self, **kwargs: object) -> JSONResponse:
        if isinstance(self._structured_result, Exception):
            raise self._structured_result
        return JSONResponse.from_response(LLMResponse(content=json.dumps(self._structured_result, ensure_ascii=False)))

    async def _chat(self, *args: object, **kwargs: object) -> SimpleNamespace:
        item = self._plain.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(content=item)

    async def __aenter__(self) -> "_Model":
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


_MESSAGES = [{"time": "09:05", "sender": "小明", "content": "你好", "user_id": "123"}]
_USERS = [
    {
        "name": "小明",
        "user_id": "123",
        "message_count": 2,
        "avg_chars": 1.5,
        "emoji_ratio": 0.5,
        "night_ratio": 0.0,
        "reply_ratio": 0.5,
    }
]


class AnalyzerTests(unittest.IsolatedAsyncioTestCase):
    def test_prompts_use_reference_formats(self) -> None:
        topic_prompt = TopicAnalyzer(_Model(), 5).build_prompt(_MESSAGES)
        self.assertIn("[09:05] [123]: 你好", topic_prompt)
        title_prompt = UserTitleAnalyzer(_Model(), 8).build_prompt(_USERS)
        self.assertIn("- 小明 (ID:123): 发言2条, 平均1.5字, 表情比例0.5, 夜间发言比例0.0, 回复比例0.5", title_prompt)

    async def test_topic_contributors_are_mapped_back_to_nicknames(self) -> None:
        model = _Model(structured={"topics": [{"topic": "闲聊", "contributors": ["[123]"], "detail": "细节"}]})
        result = await TopicAnalyzer(model, 5).analyze(_MESSAGES)
        self.assertEqual(result[0]["contributors"], ["小明"])
        self.assertEqual(result[0]["contributor_ids"], ["123"])

    async def test_quote_sender_id_becomes_nickname(self) -> None:
        model = _Model(structured={"quotes": [{"content": "原话", "sender": "[123]", "reason": "原因"}]})
        result = await GoldenQuoteAnalyzer(model, 5).analyze(_MESSAGES)
        self.assertEqual((result[0]["sender"], result[0]["user_id"]), ("小明", "123"))

    async def test_quality_dimensions_are_normalized(self) -> None:
        model = _Model(
            structured={
                "quality": {
                    "title": "锐评",
                    "subtitle": "副标题",
                    "dimensions": [
                        {"name": "日常水群", "percentage": 80, "comment": "点评"},
                        {"name": "技术讨论", "percentage": 60, "comment": "点评"},
                    ],
                    "summary": "总结",
                }
            }
        )
        result = await ChatQualityAnalyzer(model, 8).analyze(_MESSAGES)
        assert result is not None
        self.assertEqual([item["percentage"] for item in result["dimensions"]], [57.1, 42.9])

    async def test_degrades_to_plain_json_when_structured_output_fails(self) -> None:
        model = _Model(
            structured=RuntimeError("response_format not supported"),
            plain=['```json\n{"topics": [{"topic": "降级话题", "contributors": [], "detail": "细节"}]}\n```'],
        )
        result = await TopicAnalyzer(model, 5).analyze(_MESSAGES)
        self.assertEqual(result[0]["topic"], "降级话题")
        model.chat.assert_awaited_once()

    async def test_repairs_when_plain_output_is_not_json(self) -> None:
        model = _Model(
            structured=RuntimeError("response_format not supported"),
            plain=["不是 JSON", '{"topics": [{"topic": "修复后", "contributors": [], "detail": "细节"}]}'],
        )
        result = await TopicAnalyzer(model, 5).analyze(_MESSAGES)
        self.assertEqual(result[0]["topic"], "修复后")
        self.assertEqual(model.chat.await_count, 2)

    async def test_parse_failure_reuses_original_output_without_another_request(self) -> None:
        response = LLMResponse(content='[{"topic": "复用", "contributors": [], "detail": "细节"}]')
        model = _Model(structured=JSONResponseError(response))
        result = await TopicAnalyzer(model, 5).analyze(_MESSAGES)
        self.assertEqual(result[0]["topic"], "复用")
        model.chat.assert_not_awaited()

    async def test_quality_parse_failure_reuses_original_output(self) -> None:
        response = LLMResponse(
            content='说明 {"title":"锐评","dimensions":'
            '[{"name":"闲聊","percentage":100,"comment":"点评"}],"summary":"总结"}'
        )
        model = _Model(structured=JSONResponseError(response))
        result = await ChatQualityAnalyzer(model, 8).analyze(_MESSAGES)
        self.assertEqual(result["title"], "锐评")
        model.chat.assert_not_awaited()

    async def test_all_features_disabled_skips_the_model(self) -> None:
        features = dict.fromkeys(
            (
                "topic_analysis_enabled",
                "user_title_analysis_enabled",
                "golden_quote_analysis_enabled",
                "chat_quality_analysis_enabled",
            ),
            False,
        )
        with patch.object(runner, "fallback_provider", side_effect=AssertionError("model must not be called")):
            result = await analyzers.analyze("1", _MESSAGES, users=_USERS, features=features)
        self.assertEqual(result, analyzers.empty_analysis())

    async def test_analysis_reports_accumulated_token_usage(self) -> None:
        model = _Model(structured={"topics": [{"topic": "闲聊", "contributors": [], "detail": "细节"}]})
        model.usage = TokenUsage(120, 30, 150)
        with patch.object(runner, "fallback_provider", return_value=model):
            result = await analyzers.analyze("1", _MESSAGES, users=_USERS)
        self.assertEqual(result["usage"], {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150})


class TokenUsageTests(unittest.TestCase):
    def test_usage_adapts_to_each_protocol(self) -> None:
        openai_like = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        self.assertEqual(
            token_usage_of(openai_like).to_dict(), {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        gemini_like = SimpleNamespace(usage_metadata=SimpleNamespace(prompt_token_count=4, candidates_token_count=6))
        self.assertEqual(
            token_usage_of(gemini_like).to_dict(), {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10}
        )
        vertex_like = {"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 2, "totalTokenCount": 5}}
        self.assertEqual(
            token_usage_of(vertex_like).to_dict(), {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}
        )
        self.assertEqual(token_usage_of(None).to_dict(), {})

    def test_response_preserves_usage_and_merge(self) -> None:
        raw = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15))
        response = LLMResponse(content="{}", raw_response=raw)
        result = JSONResponse.from_response(response)
        self.assertIs(result.response, response)
        self.assertIs(result.response.raw_response, raw)
        usage = TokenUsage()
        usage.merge(result.response.usage)
        usage.merge(result.response.usage)
        self.assertEqual(usage.to_dict(), {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30})


def _report(analysis: dict) -> dict:
    return {
        "group_id": "1",
        "group_name": "测试群",
        "label": "最近 24 小时",
        "stats": {"total": 1, "participants": 1, "images": 0, "hours": [], "top_speakers": []},
        "analysis": analysis,
    }


_EMPTY = {
    "topics": [],
    "titles": [],
    "quotes": [],
    "quality": {"title": "", "subtitle": "", "dimensions": [], "summary": ""},
}


class RendererTests(unittest.TestCase):
    def test_miku_escapes_untrusted_group_and_model_text(self) -> None:
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
                    "quality": {"title": "", "subtitle": "", "dimensions": [], "summary": ""},
                },
            }
        )
        self.assertNotIn("<script>", html)
        self.assertNotIn("<img src=x>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_atri_escapes_and_drops_empty_sections(self) -> None:
        html = build_atri_html(
            {
                **_report(_EMPTY),
                "analysis": {
                    "topics": [{"title": "<img src=x>", "summary": "摘要", "participants": ["<b>"]}],
                    "titles": [],
                    "quotes": [],
                    "quality": {"title": "", "subtitle": "", "dimensions": [], "summary": ""},
                },
            }
        )
        self.assertIn("亚托莉的群聊观测日志", html)
        self.assertIn("高亮记忆碎片", html)
        self.assertNotIn("神人名片颁发", html)
        self.assertNotIn("<img src=x>", html)
        self.assertIn("&lt;img src=x&gt;", html)

    def test_atri_renders_token_usage(self) -> None:
        analysis = {
            **_EMPTY,
            "usage": {"prompt_tokens": 12000, "completion_tokens": 345, "total_tokens": 12345},
        }
        html = build_atri_html(_report(analysis))
        self.assertIn("能量解析雷达", html)
        self.assertIn("12,345", html)
        self.assertIn("12,000", html)
        self.assertIn("345", html)

    def test_atri_renders_all_sections(self) -> None:
        analysis = {
            "topics": [{"title": "话题", "summary": "摘要", "participants": ["小明"], "contributor_ids": ["1"]}],
            "titles": [{"name": "小明", "user_id": "1", "title": "龙王", "mbti": "INTJ", "reason": "理由"}],
            "quotes": [{"content": "原话", "sender": "小明", "reason": "原因", "user_id": "1"}],
            "quality": {
                "title": "锐评",
                "subtitle": "副标题",
                "dimensions": [{"name": "水群", "percentage": 100, "comment": "很水"}],
                "summary": "总结",
            },
        }
        html = build_atri_html(_report(analysis))
        for title in ("高亮记忆碎片", "神人名片颁发", "亚托莉的宝藏瓶", "亚托莉观测报告"):
            self.assertIn(title, html)


class ConfigShapeTests(unittest.TestCase):
    def test_saved_groups_survive_reload(self) -> None:
        proxy = ConfigProxy("group_analysis_test", DEFAULTS, validator=validate_config)
        proxy.save({"basic": {"days": 3, "max_days": 5}, "auto": {"auto_hour": 9}})
        loaded = proxy.load()
        self.assertEqual((loaded["basic"]["days"], loaded["basic"]["max_days"]), (3, 5))
        self.assertEqual(loaded["auto"]["auto_hour"], 9)
        self.assertEqual(loaded["basic"]["max_messages"], 1000)
        self.assertEqual(loaded["basic"]["theme"], "miku")

    def test_invalid_theme_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_config({"basic": {"theme": "unknown"}})

    def test_ui_schema_fields_match_defaults(self) -> None:
        for card in get_ui_schema()["cards"]:
            group = DEFAULTS
            for part in card["key"].split(".")[1:]:
                group = group[part]
            for item in card["schemas"]:
                self.assertIn(item["field"], group)


if __name__ == "__main__":
    unittest.main()
