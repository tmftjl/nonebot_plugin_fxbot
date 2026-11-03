"""P4 表达学习：解析、合并、复审、选择、注入与控制台隔离。"""

from __future__ import annotations

import re
import json
import unittest
from uuid import uuid4
from typing import Any
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import update
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import (
    state as memory_state,
    config as config_module,
    migrate,
)
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.inject import learning_context
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage, utc_now_naive
from src.plugins.nonebot_plugin_fxbot.chat.memory.expression import (
    CHUNK_LENGTH,
    ExpressionItem,
    text_hash,
    merge_candidates,
    learn_expressions,
    parse_expressions,
    build_learn_prompt,
    split_learning_text,
    build_learning_sources,
    parse_text_expressions,
    extract_text_expressions,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository

U1 = "50001"
U2 = "50002"


def _message(seq: int, content: str, sender: str = U1, name: str = "小明") -> ChatMessage:
    """构造一条带主键、尚未落库的消息。"""
    message = ChatMessage(
        tenant="test",
        sender_id=sender,
        sender_name=name,
        content=content,
        dedupe_key=f"msg:{seq}",
        occurred_at=utc_now_naive(),
    )
    message.id = seq
    return message


def _learn_payload(*items: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**item, "id": item.get("source_seq", "")} for item in items]


def _approve_all(count: int) -> dict[str, Any]:
    return {"results": [{"index": index, "approved": True} for index in range(1, count + 1)]}


class _FakeConfig:
    """只提供表达学习与注入需要的配置。"""

    def __init__(
        self,
        *,
        checked_only: bool = False,
        self_reflect: bool = True,
        learning_list: list[dict[str, Any]] | None = None,
        groups: list[dict[str, Any]] | None = None,
    ) -> None:
        self.checked_only = checked_only
        self.self_reflect = self_reflect
        self.learning_list = learning_list or []
        self.groups = groups or []

    def get_system(self) -> dict[str, Any]:
        return {
            "chat": {
                "provider": "",
                "expression": {
                    "checked_only": self.checked_only,
                    "self_reflect": self.self_reflect,
                    "learning_list": self.learning_list,
                    "groups": self.groups,
                },
                "jargon": {"learning_list": [], "groups": []},
                "model_routing": {"learner": ""},
            }
        }


def _config(**kwargs: Any) -> Any:
    return patch.object(config_module, "get_config_manager", return_value=_FakeConfig(**kwargs))


class _QueueProvider:
    """按顺序返回预设结果；异常项直接抛出。"""

    def __init__(self, *payloads: Any) -> None:
        self.payloads = list(payloads)
        self.calls = 0
        self.prompts: list[Any] = []

    async def chat_json(self, prompt: Any, schema: Any, *, model: str | None = None) -> Any:
        self.calls += 1
        self.prompts.append(prompt)
        if not self.payloads:
            raise RuntimeError("没有更多预设结果")
        payload = self.payloads.pop(0)
        if isinstance(payload, BaseException):
            raise payload
        return payload

    async def chat(self, messages, **kwargs):
        payload = await self.chat_json(messages, None)
        if isinstance(payload, list):
            match = re.search(r"\[id:(\d+)\]", messages[0]["content"])
            payload = [{**item, "id": item.get("id") or (match.group(1) if match else "")} for item in payload]
        if isinstance(payload, dict) and "results" in payload:
            results = payload["results"]
            if len(results) > 1:
                self.payloads[:0] = [{"suitable": item["approved"]} for item in results[1:]]
            payload = {"suitable": bool(results[0]["approved"])} if results else {}
        return LLMResponse(content=json.dumps(payload, ensure_ascii=False))


class ParseExpressionTests(unittest.TestCase):
    """表达解析只保留有情境、有说法且不含敏感信息的条目。"""

    def setUp(self) -> None:
        self.messages = [
            _message(21, "好家伙 这也太离谱了"),
            _message(22, "确实，笑死", sender=U2, name="小红"),
        ]

    def test_valid_payload_is_normalized(self) -> None:
        parsed = parse_expressions(
            [
                {
                    "situation": "别人在炫耀时",
                    "style": "笑死，又来",
                    "type": "tone",
                    "id": 21,
                },
                {
                    "situation": "别人在炫耀时",
                    "style": "笑死，又来",
                    "type": "phrase",
                    "id": 21,
                },
                {"situation": "密码 abc123", "style": "笑死", "type": "phrase"},
                {"situation": "无关情境", "style": "无关说法", "type": "unknown", "id": 999},
            ],
            self.messages,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(
            [(item.situation, item.style, item.type, item.source_seq) for item in parsed],
            [("别人在炫耀时", "笑死，又来", "tone", 21)],
        )

    def test_invalid_payload_returns_none_and_empty_is_valid(self) -> None:
        self.assertIsNone(parse_expressions("not json", self.messages))
        self.assertIsNone(parse_expressions({}, self.messages))
        self.assertIsNone(parse_expressions({"expressions": []}, self.messages))
        self.assertEqual(parse_expressions([], self.messages), [])

    def test_prompt_carries_seq_and_rules(self) -> None:
        prompt = build_learn_prompt(self.messages)

        self.assertIn("[id:21]", prompt[0]["content"])
        self.assertIn("场景", prompt[0]["content"])


class TextExpressionLearningTests(unittest.IsolatedAsyncioTestCase):
    """粘贴文本按段提取候选，依据直接取原文。"""

    def test_split_prefers_paragraph_boundaries(self) -> None:
        text = "第一段。" * 1200
        chunks = split_learning_text(text)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(chunk) <= CHUNK_LENGTH for chunk in chunks))
        self.assertEqual("".join(chunks), text)

    def test_parse_keeps_evidence_and_deduplicates(self) -> None:
        sources = build_learning_sources("甲：这也太离谱了。\n乙：确实，笑死。")
        parsed = parse_text_expressions(
            json.dumps(
                {
                    "candidates": [
                        {"situation": "别人炫耀时", "style": "使用 笑死", "source_id": 1},
                        {"situation": "别人炫耀时", "style": "笑死", "source_id": 99},
                        {"situation": "", "style": "空情境"},
                    ]
                },
                ensure_ascii=False,
            ),
            sources,
        )

        self.assertEqual(
            [(item.situation, item.style, item.evidence) for item in parsed],
            [("别人炫耀时", "笑死", "甲：这也太离谱了。")],
        )

    async def test_extract_uses_learner_provider(self) -> None:
        provider = _QueueProvider({"candidates": [{"situation": "被夸时", "style": "低调点", "source_id": 1}]})

        items = await extract_text_expressions("甲：你太强了。\n乙：低调点。", "tenant", provider=provider)

        self.assertEqual([(item.situation, item.style) for item in items], [("被夸时", "低调点")])
        self.assertEqual(provider.calls, 1)

    def test_invalid_payload_raises(self) -> None:
        with self.assertRaises(ValueError):
            parse_text_expressions("", {})
        with self.assertRaises(ValueError):
            parse_text_expressions('{"candidates": "x"}', {})
        with self.assertRaises(ValueError):
            parse_text_expressions(json.dumps({"candidates": [{}] * 31}), {})


class MergeExpressionTests(unittest.TestCase):
    """相似情境与说法合并，计数按 0.006 递增并有上限。"""

    def test_same_expression_in_one_batch_merges(self) -> None:
        items = [
            ExpressionItem("别人在群里炫耀成绩时", "笑死，又来"),
            ExpressionItem("别人在群里炫耀成绩时", "笑死，又来"),
        ]

        new_items, updates = merge_candidates(items, [])

        self.assertEqual(len(new_items), 1)
        self.assertAlmostEqual(new_items[0]["count"], 2)
        self.assertEqual(new_items[0]["learn"], 2)
        self.assertEqual(updates, {})

    def test_similar_expression_is_stored_separately(self) -> None:
        class _Row:
            id = 9
            situation = "别人在群里炫耀成绩时"
            style = "笑死，又来"
            count = 0.006

        new_items, updates = merge_candidates(
            [ExpressionItem("别人在群里炫耀成绩的时候", "笑死，又来了")],
            [_Row()],
        )

        self.assertEqual(len(new_items), 1)
        self.assertAlmostEqual(new_items[0]["count"], 1)
        self.assertEqual(updates, {})

    def test_different_expression_is_new(self) -> None:
        class _Row:
            id = 11
            situation = "别人在炫耀时"
            style = "笑死，又来"
            count = 1.0

        new_items, updates = merge_candidates(
            [ExpressionItem("今天晚饭吃什么", "随便")],
            [_Row()],
        )

        self.assertEqual(len(new_items), 1)
        self.assertEqual(new_items[0]["situation"], "今天晚饭吃什么")
        self.assertEqual(updates, {})

    def test_existing_count_increases_without_cap(self) -> None:
        class _Row:
            id = 7
            situation = "别人在群里炫耀成绩时"
            style = "笑死，又来"
            count = 4.998

        new_items, updates = merge_candidates(
            [ExpressionItem("别人在群里炫耀成绩时", "笑死，又来")],
            [_Row()],
        )

        self.assertEqual(new_items, [])
        self.assertAlmostEqual(updates[7][0], 5.998)
        self.assertEqual(updates[7][1], 1)


class _ExpressionTestCase(unittest.IsolatedAsyncioTestCase):
    """共享临时租户与消息落库辅助。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.tenant = f"test:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)

    async def _seed(self, content: str, *, platform_message_id: str | None = None) -> ChatMessage:
        message = ChatMessage(
            tenant=self.tenant,
            sender_id=U1,
            sender_name="小明",
            platform_message_id=platform_message_id,
            content=content,
            dedupe_key=f"msg:{uuid4().hex}",
            occurred_at=utc_now_naive(),
        )
        message.id = await chat_repository.append_message(message)
        return message

    async def _seed_many(self, count: int = 40, prefix: str = "第") -> list[ChatMessage]:
        return [await self._seed(f"{prefix}{index}楼 这也太离谱了") for index in range(count)]

    async def _save_expressions(self, specs: list[dict[str, Any]], *, batch_id: str = "expression:1:100") -> None:
        cursor = await chat_repository.get_cursor(self.tenant, "expression")
        expected = int(cursor.cursor_seq) if cursor else 0
        items = []
        for spec in specs:
            situation = str(spec["situation"])
            style = str(spec["style"])
            items.append(
                {
                    "situation": situation,
                    "situation_hash": text_hash(situation),
                    "style": style,
                    "style_hash": text_hash(style),
                    "type": "phrase",
                    "count": float(spec.get("count", 1.0)),
                    "checked": bool(spec.get("checked", True)),
                    "rejected": bool(spec.get("rejected", False)),
                    "source_message_seq": int(spec.get("source_message_seq", 0)),
                    "learn_count": 1,
                }
            )
        saved = await chat_repository.save_expressions(
            self.tenant,
            items,
            {},
            cursor_seq=max(expected, 1),
            expected_cursor=expected,
            source_batch_id=batch_id,
        )
        self.assertTrue(saved)


class ExpressionLearningTests(_ExpressionTestCase):
    """40 条触发、隐私开关、失败不推进游标、复审与跨批次合并。"""

    async def test_first_batch_needs_10_messages(self) -> None:
        await self._seed_many(9)
        provider = _QueueProvider(_learn_payload({"situation": "炫耀时", "style": "笑死"}), _approve_all(1))

        with _config():
            self.assertFalse(await learn_expressions(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 0)
        await self._seed("第10楼 这也太离谱了")
        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=provider))
        self.assertEqual(provider.calls, 2)

    async def test_review_result_is_stored(self) -> None:
        messages = await self._seed_many(40)
        provider = _QueueProvider(
            _learn_payload(
                {"situation": "炫耀成绩时", "style": "笑死，又来", "source_seq": int(messages[0].id or 0)},
                {"situation": "有人求助时", "style": "让我看看", "source_seq": int(messages[1].id or 0)},
            ),
            {"results": [{"index": 1, "approved": True}, {"index": 2, "approved": False}]},
        )

        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=provider))

        rows = {row.situation: row for row in await chat_repository.list_expressions(self.tenant)}
        self.assertTrue(rows["炫耀成绩时"].checked)
        self.assertFalse(rows["炫耀成绩时"].rejected)
        self.assertTrue(rows["有人求助时"].checked)
        self.assertTrue(rows["有人求助时"].rejected)
        cursor = await chat_repository.get_cursor(self.tenant, "expression")
        self.assertEqual(cursor.cursor_seq, int(messages[-1].id or 0))

    async def test_tenant_switch_off_skips_learning(self) -> None:
        await self._seed_many(40)
        await chat_repository.set_learn_enabled(self.tenant, False)
        provider = _QueueProvider(_learn_payload({"situation": "炫耀时", "style": "笑死"}))

        with _config():
            self.assertFalse(await learn_expressions(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 0)

    async def test_model_failure_does_not_advance_cursor(self) -> None:
        messages = await self._seed_many(40)
        provider = _QueueProvider(ValueError("bad json"), ValueError("bad json"))

        with _config():
            self.assertFalse(await learn_expressions(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 2)
        self.assertIsNone(await chat_repository.get_cursor(self.tenant, "expression"))

        recovery = _QueueProvider(
            _learn_payload({"situation": "炫耀时", "style": "笑死"}),
            _approve_all(1),
        )
        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=recovery))
        self.assertEqual(recovery.calls, 2)
        cursor = await chat_repository.get_cursor(self.tenant, "expression")
        self.assertEqual(cursor.cursor_seq, int(messages[-1].id or 0))

    async def test_review_failure_keeps_unchecked(self) -> None:
        await self._seed_many(40)
        provider = _QueueProvider(
            _learn_payload({"situation": "炫耀时", "style": "笑死"}),
            ValueError("review down"),
            ValueError("review down"),
        )

        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=provider))

        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertFalse(row.checked)
        self.assertFalse(row.rejected)

    async def test_self_reflect_off_skips_review(self) -> None:
        await self._seed_many(40)
        provider = _QueueProvider(_learn_payload({"situation": "炫耀时", "style": "笑死"}))

        with _config(self_reflect=False):
            self.assertTrue(await learn_expressions(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 1)
        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertTrue(row.checked)
        self.assertFalse(row.rejected)

    async def test_learning_list_learn_off_skips_tenant(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        await self._seed_many(40)
        item = {
            "platform": "onebot11",
            "item_id": self.tenant.rsplit(":", 1)[-1],
            "type": "group",
            "learn": False,
        }
        provider = _QueueProvider(_learn_payload({"situation": "炫耀时", "style": "笑死"}))

        with _config(learning_list=[item]):
            self.assertFalse(await learn_expressions(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 0)

    async def test_empty_result_still_advances_cursor(self) -> None:
        await self._seed_many(40)
        provider = _QueueProvider(_learn_payload())

        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=provider))
        self.assertEqual(provider.calls, 1)

        with _config():
            self.assertFalse(await learn_expressions(self.tenant, provider=provider))
        self.assertEqual(provider.calls, 1)

    async def test_second_batch_merges_and_counts(self) -> None:
        await self._seed_many(40)
        first = _QueueProvider(
            _learn_payload({"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"}),
            _approve_all(1),
        )
        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=first))

        await self._seed_many(40, prefix="第二批")
        second = _QueueProvider(
            _learn_payload({"situation": "别人在群里炫耀成绩的时候", "style": "笑死，又来了"}),
            _approve_all(1),
        )
        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=second))

        rows = await chat_repository.list_expressions(self.tenant)
        self.assertEqual(len(rows), 2)
        self.assertEqual([row.count for row in rows], [1, 1])

    async def test_expression_result_is_dropped_when_evidence_retracted_during_call(self) -> None:
        """模型调用期间证据被撤回时，结果与游标都不落库。"""
        messages = await self._seed_many(40)
        target = int(messages[0].id or 0)
        payloads = [
            _learn_payload({"situation": "炫耀时", "style": "笑死"}),
            _approve_all(1),
        ]

        class _Provider:
            async def chat_json(self, *_args: Any, **_kwargs: Any) -> Any:
                async with base_models.async_maker() as session:  # type: ignore[misc]
                    await session.execute(update(ChatMessage).where(ChatMessage.id == target).values(deleted=True))
                    await session.commit()
                return payloads.pop(0)

        with _config():
            self.assertFalse(await learn_expressions(self.tenant, provider=_Provider()))

        self.assertEqual(await chat_repository.list_expressions(self.tenant), [])
        cursor = await chat_repository.get_cursor(self.tenant, "expression")
        self.assertEqual(cursor.cursor_seq if cursor else 0, 0)


class ExpressionInjectionTests(_ExpressionTestCase):
    """只注入已复审表达；阈值、权重、预算、使用计数与租户隔离。"""

    async def test_checked_expression_is_injected(self) -> None:
        await self._save_expressions(
            [{"situation": "别人在群里炫耀成绩时", "style": f"笑死，又来{index}", "count": 1.0} for index in range(10)]
        )

        with _config():
            block = await learning_context(self.tenant, query="别人在群里炫耀成绩，你怎么看")

        self.assertIn("【表达参考】", block)
        self.assertIn("笑死，又来", block)
        rows = await chat_repository.list_expressions(self.tenant)
        self.assertEqual(sum(row.use_count for row in rows), 5)

    async def test_unchecked_and_rejected_are_not_injected(self) -> None:
        await self._save_expressions(
            [
                {"situation": "别人在群里炫耀成绩时", "style": "笑死，又来", "checked": False},
                {"situation": "别人在群里炫耀成绩时", "style": "就这", "rejected": True},
            ]
        )

        with _config():
            block = await learning_context(self.tenant, query="别人在群里炫耀成绩，你怎么看")

        self.assertNotIn("【表达参考】", block)

    async def test_low_similarity_is_not_injected(self) -> None:
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"}])

        with _config():
            block = await learning_context(self.tenant, query="今天晚饭吃什么")

        self.assertNotIn("【表达参考】", block)

    async def test_injection_cap_is_five(self) -> None:
        await self._save_expressions(
            [{"situation": "别人在群里炫耀成绩时", "style": f"笑死，又来{index}"} for index in range(10)]
        )

        with _config():
            block = await learning_context(self.tenant, query="别人在群里炫耀成绩，你怎么看")

        self.assertEqual(block.count("当「"), 5)

    async def test_other_tenant_sees_nothing(self) -> None:
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"}])
        other = f"test:{uuid4().hex}"
        await chat_repository.set_learn_enabled(other, True)

        with _config():
            self.assertEqual(await learning_context(other, query="别人在群里炫耀成绩，你怎么看"), "")

    async def test_learning_list_use_off_skips_injection(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来", "count": 1.0}])
        item = {
            "platform": "onebot11",
            "item_id": self.tenant.rsplit(":", 1)[-1],
            "type": "group",
            "use": False,
        }

        with _config(learning_list=[item]):
            block = await learning_context(self.tenant, query="别人在群里炫耀成绩，你怎么看")

        self.assertEqual(block, "")

    async def test_checked_only_filters_before_minimum_pool_size(self) -> None:
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来", "count": 1.0}])
        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertTrue(await chat_repository.review_expression(self.tenant, int(row.id or 0), False))
        await self._save_expressions(
            [{"situation": "别人在群里炫耀成绩的时候", "style": "就这", "count": 1.0}],
            batch_id="expression:101:200",
        )

        with _config(checked_only=True):
            block = await learning_context(self.tenant, query="别人在群里炫耀成绩，你怎么看")
        self.assertNotIn("【表达参考】", block)
        self.assertNotIn("就这", block)

        with _config():
            block = await learning_context(self.tenant, query="别人在群里炫耀成绩，你怎么看")
        self.assertNotIn("【表达参考】", block)
        manual = await chat_repository.list_expressions(self.tenant, manual_only=True)
        self.assertEqual([item.style for item in manual], ["笑死，又来"])

    async def test_shared_expression_group_merges_tenants(self) -> None:
        sibling = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(sibling, True)
        self.tenant = sibling
        await self._seed("群里有人说 yyds")
        await chat_repository.save_expressions(
            sibling,
            [
                {
                    "situation": "别人在群里炫耀成绩时",
                    "situation_hash": text_hash("别人在群里炫耀成绩时"),
                    "style": f"笑死，又来{index}",
                    "style_hash": text_hash(f"笑死，又来{index}"),
                    "type": "phrase",
                    "count": 1.0,
                    "checked": True,
                    "rejected": False,
                }
                for index in range(10)
            ],
            {},
            cursor_seq=1,
            expected_cursor=0,
            source_batch_id="expression:1:1",
        )
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        groups = [
            {
                "targets": [
                    {"platform": "onebot11", "item_id": self.tenant.rsplit(":", 1)[-1], "type": "group"},
                    {"platform": "onebot11", "item_id": sibling.rsplit(":", 1)[-1], "type": "group"},
                ]
            }
        ]

        with _config(groups=groups):
            block = await learning_context(self.tenant, query="别人在群里炫耀成绩，你怎么看")

        self.assertIn("笑死，又来", block)


class ConsoleExpressionTests(_ExpressionTestCase):
    """控制台读取、改判与删除都按租户隔离。"""

    async def test_reads_reviews_and_deletes_by_tenant(self) -> None:
        await self._save_expressions([{"situation": "炫耀成绩时", "style": "笑死，又来", "checked": False}])
        [row] = await chat_repository.list_expressions(self.tenant)
        expression_id = int(row.id or 0)

        listed = await chat_routes.list_expressions(self.tenant, "unchecked")
        self.assertEqual([item["style"] for item in listed["items"]], ["笑死，又来"])
        self.assertFalse(listed["items"][0]["checked"])

        reviewed = await chat_routes.review_expression(expression_id, self.tenant, False)
        self.assertEqual(reviewed, {"rejected": False})
        active = await chat_routes.list_expressions(self.tenant, "active")
        self.assertEqual([item["id"] for item in active["items"]], [expression_id])
        self.assertTrue(active["items"][0]["checked"])

        tenants = await chat_routes.list_tenants()
        self.assertIn(self.tenant, [item["tenant"] for item in tenants["tenants"]])

        self.assertEqual(
            await chat_routes.delete_expression(expression_id, self.tenant),
            {"deleted": True},
        )
        with self.assertRaises(HTTPException) as missing:
            await chat_routes.delete_expression(expression_id, self.tenant)
        self.assertEqual(missing.exception.status_code, 404)

    async def test_review_filters_and_validation(self) -> None:
        await self._save_expressions([{"situation": "炫耀时", "style": "笑死"}])
        [row] = await chat_repository.list_expressions(self.tenant)

        await chat_routes.review_expression(int(row.id or 0), self.tenant, True)
        rejected = await chat_routes.list_expressions(self.tenant, "rejected")
        self.assertEqual(len(rejected["items"]), 1)

        with self.assertRaises(HTTPException) as bad_status:
            await chat_routes.list_expressions(self.tenant, "unknown")
        self.assertEqual(bad_status.exception.status_code, 400)

        with self.assertRaises(HTTPException) as empty:
            await chat_routes.list_expressions("   ", "all")
        self.assertEqual(empty.exception.status_code, 400)

    async def test_manual_create_and_import_mark_active(self) -> None:
        created = await chat_routes.create_expression(
            {"tenant": self.tenant, "situation": " 别人炫耀时 ", "style": " 使用 笑死 "}
        )
        self.assertEqual((created["item"]["situation"], created["item"]["style"]), ("别人炫耀时", "笑死"))
        self.assertTrue(created["item"]["checked"])
        self.assertTrue(created["item"]["manual_checked"])
        self.assertEqual(created["item"]["count"], 1.0)

        again = await chat_routes.create_expression({"tenant": self.tenant, "situation": "别人炫耀时", "style": "笑死"})
        self.assertEqual(again["item"]["id"], created["item"]["id"])

        imported = await chat_routes.import_expressions(
            {
                "tenant": self.tenant,
                "items": [
                    {"situation": "被夸时", "style": "低调点"},
                    {"situation": "被夸时", "style": "低调点"},
                ],
            }
        )
        self.assertEqual(imported["imported"], 1)
        active = await chat_routes.list_expressions(self.tenant, "active")
        self.assertEqual({item["style"] for item in active["items"]}, {"笑死", "低调点"})

        with self.assertRaises(HTTPException) as invalid:
            await chat_routes.import_expressions({"tenant": self.tenant, "items": [{"situation": "", "style": ""}]})
        self.assertEqual(invalid.exception.status_code, 400)

    async def test_prepare_text_reports_chunks(self) -> None:
        prepared = await chat_routes.prepare_expression_learning({"text": "这是一段要学习的文本。"})
        self.assertEqual(prepared["chunks"], ["这是一段要学习的文本。"])

        with self.assertRaises(HTTPException) as empty:
            await chat_routes.prepare_expression_learning({"text": "   "})
        self.assertEqual(empty.exception.status_code, 400)

    async def test_cross_tenant_delete_is_rejected(self) -> None:
        await self._save_expressions([{"situation": "炫耀时", "style": "笑死"}])
        [row] = await chat_repository.list_expressions(self.tenant)
        other = f"test:{uuid4().hex}"

        with self.assertRaises(HTTPException) as missing:
            await chat_routes.delete_expression(int(row.id or 0), other)
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(len(await chat_repository.list_expressions(self.tenant)), 1)


class ExpressionInvalidationTests(_ExpressionTestCase):
    """撤回批次内消息时删除对应表达，其他租户不受影响。"""

    async def test_recalled_message_removes_batch_expressions(self) -> None:
        message = await self._seed("这也能炫耀", platform_message_id="platform-1")
        seq = int(message.id or 0)
        await self._save_expressions(
            [{"situation": "炫耀时", "style": "笑死", "source_message_seq": seq}],
            batch_id=f"expression:{seq}:{seq}",
        )
        other = f"test:{uuid4().hex}"
        await chat_repository.set_learn_enabled(other, True)
        await chat_repository.save_expressions(
            other,
            [
                {
                    "situation": "炫耀时",
                    "situation_hash": text_hash("炫耀时"),
                    "style": "笑死",
                    "style_hash": text_hash("笑死"),
                    "type": "phrase",
                    "count": 1.0,
                    "checked": True,
                    "rejected": False,
                    "source_message_seq": seq,
                    "learn_count": 1,
                }
            ],
            {},
            cursor_seq=1,
            expected_cursor=0,
            source_batch_id=f"expression:{seq}:{seq}",
        )

        result = await chat_repository.invalidate_message(self.tenant, "platform-1")

        self.assertEqual(result["jargon_batches"], 0)
        self.assertEqual(result["expressions"], 1)
        self.assertEqual(await chat_repository.list_expressions(self.tenant), [])
        self.assertEqual(len(await chat_repository.list_expressions(other)), 1)

    async def test_recalled_batch_rolls_back_merged_contribution(self) -> None:
        """撤回批次消息时，合并到既有表达上的计数按贡献回滚。"""
        messages = await self._seed_many(40)
        await chat_repository.save_expressions(
            self.tenant,
            [
                {
                    "situation": "别人在群里炫耀成绩时",
                    "situation_hash": text_hash("别人在群里炫耀成绩时"),
                    "style": "笑死，又来",
                    "style_hash": text_hash("笑死，又来"),
                    "type": "phrase",
                    "count": 1.0,
                    "checked": True,
                    "rejected": False,
                    "source_message_seq": 0,
                    "learn_count": 1,
                }
            ],
            {},
            cursor_seq=0,
            expected_cursor=0,
            source_batch_id="expression:0:0",
        )
        provider = _QueueProvider(_learn_payload({"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"}))
        with _config():
            self.assertTrue(await learn_expressions(self.tenant, provider=provider))

        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertAlmostEqual(row.count, 2)
        self.assertEqual(row.learn_count, 2)

        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(
                update(ChatMessage).where(ChatMessage.id == messages[5].id).values(platform_message_id="pm-expr")
            )
            await session.commit()
        result = await chat_repository.invalidate_message(self.tenant, "pm-expr")

        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertEqual(result["rolled_back"], 1)
        self.assertAlmostEqual(row.count, 1.0)
        self.assertEqual(row.learn_count, 1)
        self.assertEqual(json.loads(row.contributions), [])
