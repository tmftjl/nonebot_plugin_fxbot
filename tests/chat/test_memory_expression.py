"""P4 表达学习：解析、合并、复审、选择、注入与控制台隔离。"""

from __future__ import annotations

import unittest
from uuid import uuid4
from typing import Any
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state, config as config_module, migrate
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.inject import memory_context
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatMessage, utc_now_naive
from src.plugins.nonebot_plugin_fxbot.chat.memory.expression import (
    ExpressionItem,
    text_hash,
    parse_review,
    merge_candidates,
    learn_expressions,
    parse_expressions,
    build_learn_prompt,
    select_expressions,
    expression_job_tick,
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


def _learn_payload(*items: dict[str, Any]) -> dict[str, Any]:
    return {"expressions": list(items)}


def _approve_all(count: int) -> dict[str, Any]:
    return {"results": [{"index": index, "approved": True} for index in range(1, count + 1)]}


class _FakeConfig:
    """只提供表达学习与注入需要的配置。"""

    def __init__(
        self,
        *,
        expression_learn: bool = True,
        enabled: bool = True,
        learn: bool = True,
        llm_select: bool = False,
        sample_size: int = 50,
        inject_top_k: int = 5,
        memory: dict[str, Any] | None = None,
    ) -> None:
        self.expression_learn = expression_learn
        self.enabled = enabled
        self.learn = learn
        self.llm_select = llm_select
        self.sample_size = sample_size
        self.inject_top_k = inject_top_k
        self.memory: dict[str, Any] = {
            "learn_enabled": True,
            "max_input_chars_per_batch": 6000,
            "inject_profile_chars": 300,
            "inject_facts_chars": 400,
            "inject_episodes_chars": 300,
            "inject_style_chars": 150,
            "inject_jargon_chars": 100,
            "inject_expressions_chars": 200,
            "inject_total_chars": 1500,
        }
        self.memory.update(memory or {})

    def get_system(self) -> dict[str, Any]:
        return {
            "chat": {
                "provider": "",
                "privacy": {"expression_learn": self.expression_learn, "style_learn": False},
                "expression": {
                    "enabled": self.enabled,
                    "learn": self.learn,
                    "llm_select": self.llm_select,
                    "sample_size": self.sample_size,
                    "inject_top_k": self.inject_top_k,
                },
                "jargon": {"enabled": False, "inject_max": 5},
                "memory": self.memory,
                "model_routing": {"extract": ""},
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


class ParseExpressionTests(unittest.TestCase):
    """表达解析只保留有情境、有说法且不含敏感信息的条目。"""

    def setUp(self) -> None:
        self.messages = [
            _message(21, "好家伙 这也太离谱了"),
            _message(22, "确实，笑死", sender=U2, name="小红"),
        ]

    def test_valid_payload_is_normalized(self) -> None:
        parsed = parse_expressions(
            {
                "expressions": [
                    {
                        "situation": "别人在炫耀时",
                        "style": "笑死，又来",
                        "type": "tone",
                        "source_seq": 21,
                    },
                    {
                        "situation": "别人在炫耀时",
                        "style": "笑死，又来",
                        "type": "phrase",
                        "source_seq": 21,
                    },
                    {"situation": "密码 abc123", "style": "笑死", "type": "phrase"},
                    {"situation": "无关情境", "style": "无关说法", "type": "unknown", "source_seq": 999},
                ]
            },
            self.messages,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(
            [(item.situation, item.style, item.type, item.source_seq) for item in parsed],
            [("别人在炫耀时", "笑死，又来", "tone", 21), ("无关情境", "无关说法", "phrase", 0)],
        )

    def test_invalid_payload_returns_none_and_empty_is_valid(self) -> None:
        self.assertIsNone(parse_expressions("not json", self.messages))
        self.assertIsNone(parse_expressions({}, self.messages))
        self.assertEqual(parse_expressions({"expressions": []}, self.messages), [])

    def test_prompt_carries_seq_and_rules(self) -> None:
        prompt = build_learn_prompt(self.messages)

        self.assertIn("[21]", prompt[1]["content"])
        self.assertIn("情境", prompt[0]["content"])


class MergeExpressionTests(unittest.TestCase):
    """相似情境与说法合并，计数按 0.006 递增并有上限。"""

    def test_same_expression_in_one_batch_merges(self) -> None:
        items = [
            ExpressionItem("别人在群里炫耀成绩时", "笑死，又来"),
            ExpressionItem("别人在群里炫耀成绩时", "笑死，又来"),
        ]

        new_items, updates = merge_candidates(items, [])

        self.assertEqual(len(new_items), 1)
        self.assertAlmostEqual(new_items[0]["count"], 0.012)
        self.assertEqual(new_items[0]["learn"], 2)
        self.assertEqual(updates, {})

    def test_fuzzy_similar_expression_merges(self) -> None:
        class _Row:
            id = 9
            situation = "别人在群里炫耀成绩时"
            style = "笑死，又来"
            count = 0.006

        new_items, updates = merge_candidates(
            [ExpressionItem("别人在群里炫耀成绩的时候", "笑死，又来了")],
            [_Row()],
        )

        self.assertEqual(new_items, [])
        self.assertAlmostEqual(updates[9][0], 0.012)
        self.assertEqual(updates[9][1], 1)

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

    def test_existing_count_updates_and_is_capped(self) -> None:
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
        self.assertAlmostEqual(updates[7][0], 5.0)
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

    async def test_first_batch_needs_40_messages(self) -> None:
        await self._seed_many(39)
        provider = _QueueProvider(_learn_payload({"situation": "炫耀时", "style": "笑死"}), _approve_all(1))

        with _config():
            self.assertFalse(await learn_expressions(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 0)
        await self._seed("第40楼 这也太离谱了")
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

    async def test_privacy_off_skips_learning(self) -> None:
        await self._seed_many(40)
        provider = _QueueProvider(_learn_payload({"situation": "炫耀时", "style": "笑死"}))

        with _config(expression_learn=False):
            self.assertFalse(await learn_expressions(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 0)
        self.assertIsNone(await chat_repository.get_cursor(self.tenant, "expression"))

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

        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertAlmostEqual(row.count, 0.012)
        self.assertEqual(row.learn_count, 2)

    async def test_expression_job_tick_processes_due_tenant_once(self) -> None:
        await self._seed_many(40)
        provider = _QueueProvider(
            _learn_payload({"situation": "炫耀时", "style": "笑死"}),
            _approve_all(1),
        )
        snapshot = [row for row in await chat_repository.scheduling_snapshot() if row.tenant == self.tenant]

        with (
            _config(),
            patch.object(chat_repository, "scheduling_snapshot", new=AsyncMock(return_value=snapshot)),
        ):
            self.assertEqual(await expression_job_tick(provider=provider), 1)
            self.assertEqual(await expression_job_tick(provider=provider), 0)

        self.assertEqual(provider.calls, 2)

    async def test_expression_job_tick_respects_privacy(self) -> None:
        await self._seed_many(40)
        provider = _QueueProvider(_learn_payload({"situation": "炫耀时", "style": "笑死"}))
        snapshot = [row for row in await chat_repository.scheduling_snapshot() if row.tenant == self.tenant]

        with (
            _config(expression_learn=False),
            patch.object(chat_repository, "scheduling_snapshot", new=AsyncMock(return_value=snapshot)),
        ):
            self.assertEqual(await expression_job_tick(provider=provider), 0)

        self.assertEqual(provider.calls, 0)


class ExpressionInjectionTests(_ExpressionTestCase):
    """只注入已复审表达；阈值、权重、预算、使用计数与租户隔离。"""

    async def test_checked_expression_is_injected(self) -> None:
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来", "count": 1.0}])

        with _config():
            block = await memory_context(self.tenant, U1, query="别人在群里炫耀成绩，你怎么看")

        self.assertIn("【表达参考】", block)
        self.assertIn("笑死，又来", block)
        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertEqual(row.use_count, 1)

    async def test_unchecked_and_rejected_are_not_injected(self) -> None:
        await self._save_expressions(
            [
                {"situation": "别人在群里炫耀成绩时", "style": "笑死，又来", "checked": False},
                {"situation": "别人在群里炫耀成绩时", "style": "就这", "rejected": True},
            ]
        )

        with _config():
            block = await memory_context(self.tenant, U1, query="别人在群里炫耀成绩，你怎么看")

        self.assertNotIn("【表达参考】", block)

    async def test_low_similarity_is_not_injected(self) -> None:
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"}])

        with _config():
            block = await memory_context(self.tenant, U1, query="今天晚饭吃什么")

        self.assertNotIn("【表达参考】", block)

    async def test_top_k_limits_injection(self) -> None:
        await self._save_expressions(
            [
                {"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"},
                {"situation": "别人在群里炫耀成绩的时候", "style": "就这"},
            ]
        )

        with _config(inject_top_k=1):
            block = await memory_context(self.tenant, U1, query="别人在群里炫耀成绩，你怎么看")

        self.assertEqual(block.count("当「"), 1)

    async def test_expression_budget_truncates_and_usage_requires_fit(self) -> None:
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"}])

        with _config(memory={"inject_expressions_chars": 10}):
            block = await memory_context(self.tenant, U1, query="别人在群里炫耀成绩，你怎么看")
        self.assertNotIn("【表达参考】可以参考这些说法", block)

        with _config(memory={"inject_total_chars": 10}):
            block = await memory_context(self.tenant, U1, query="别人在群里炫耀成绩，你怎么看")
        self.assertNotIn("【表达参考】", block)
        [row] = await chat_repository.list_expressions(self.tenant)
        self.assertEqual(row.use_count, 1)

    async def test_other_tenant_sees_nothing(self) -> None:
        await self._save_expressions([{"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"}])
        other = f"test:{uuid4().hex}"
        await chat_repository.set_learn_enabled(other, True)

        with _config():
            self.assertEqual(await memory_context(other, U1, query="别人在群里炫耀成绩，你怎么看"), "")

    async def test_llm_selector_and_fuzzy_fallback(self) -> None:
        await self._save_expressions(
            [
                {"situation": "别人在群里炫耀成绩时", "style": "笑死，又来"},
                {"situation": "别人在群里炫耀成绩的时候", "style": "就这"},
            ]
        )
        rows = await chat_repository.list_expressions(self.tenant, status="active")
        by_style = {row.style: row for row in rows}

        provider = _QueueProvider({"selected_ids": [int(by_style["就这"].id or 0)]})
        selected = await select_expressions(
            self.tenant,
            "今天晚饭吃什么",
            rows,
            {"llm_select": True, "sample_size": 50, "inject_top_k": 5, "enabled": True},
            provider=provider,
        )
        self.assertEqual([row.style for row in selected], ["就这"])

        failing = _QueueProvider(ValueError("down"), ValueError("down"))
        selected = await select_expressions(
            self.tenant,
            "别人在群里炫耀成绩，你怎么看",
            rows,
            {"llm_select": True, "sample_size": 50, "inject_top_k": 5, "enabled": True},
            provider=failing,
        )
        self.assertEqual(len(selected), 2)


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

        self.assertEqual(result["memories"], 0)
        self.assertEqual(await chat_repository.list_expressions(self.tenant), [])
        self.assertEqual(len(await chat_repository.list_expressions(other)), 1)


class ReviewParseTests(unittest.TestCase):
    """复审结果按候选下标对齐，缺项视为不通过。"""

    def test_parse_review_defaults_missing_to_rejected(self) -> None:
        self.assertEqual(parse_review({"results": [{"index": 1, "approved": True}]}, 2), [True, False])
        self.assertIsNone(parse_review({}, 2))
