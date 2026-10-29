"""P3 群风格与黑话：解析校验、学习周期、注入排序与删除隔离。"""

from __future__ import annotations

import json
import asyncio
import unittest
from uuid import uuid4
from typing import Any
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import update
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat.memory import (
    state as memory_state,
    style as style_module,
    config as config_module,
    migrate,
)
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.style import (
    StyleJargon,
    StyleResult,
    StyleExample,
    learn_style,
    parse_style,
    build_prompt,
    style_job_tick,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.inject import _STYLE_CHARS, _JARGON_CHARS, learning_context
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import (
    ChatStyle,
    ChatJargon,
    ChatMessage,
    utc_now_naive,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import TenantJobState, chat_repository

U1 = "40001"
U2 = "40002"


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


def _result(
    *,
    rules: tuple[str, ...] = ("多用短句",),
    examples: tuple[StyleExample, ...] = (),
    summary: str = "群里说话短、爱接梗",
    jargon: tuple[StyleJargon, ...] = (),
) -> StyleResult:
    return StyleResult(rules=rules, examples=examples, summary=summary, jargon=jargon)


def _active(term: str, meaning: str = "永远的神") -> StyleJargon:
    return StyleJargon(term=term, normalized=term.casefold(), meaning=meaning, status="active")


class _FakeConfig:
    """只提供风格学习与注入需要的配置。"""

    def __init__(
        self,
        *,
        learning_list: list[dict[str, Any]] | None = None,
        expression_learning_list: list[dict[str, Any]] | None = None,
        groups: list[dict[str, Any]] | None = None,
    ) -> None:
        self.learning_list = learning_list or []
        self.expression_learning_list = expression_learning_list or []
        self.groups = groups or []

    def get_system(self) -> dict[str, Any]:
        return {
            "chat": {
                "provider": "",
                "expression": {"learning_list": self.expression_learning_list},
                "jargon": {
                    "learning_list": self.learning_list,
                    "groups": self.groups,
                },
                "model_routing": {"style": ""},
            }
        }


def _config(**kwargs: Any) -> Any:
    return patch.object(config_module, "get_config_manager", return_value=_FakeConfig(**kwargs))


class _StyleProvider:
    """按调用次数返回固定结果；异常项直接抛出。"""

    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.calls = 0
        self.prompts: list[Any] = []

    async def chat_json(self, prompt: Any, schema: Any, *, model: str | None = None) -> Any:
        self.calls += 1
        self.prompts.append(prompt)
        if isinstance(self.payload, BaseException):
            raise self.payload
        return self.payload


class ParseStyleTests(unittest.TestCase):
    """示例必须逐字引用原文，黑话词条必须出现在批次里。"""

    def setUp(self) -> None:
        self.messages = [
            _message(11, "好家伙 yyds 这也太顶了", sender=U1),
            _message(12, "确实，笑死", sender=U2, name="小红"),
        ]

    def test_valid_payload_keeps_rules_examples_and_jargon(self) -> None:
        parsed = parse_style(
            {
                "rules": ["多用短句", "  ", "多用短句"],
                "examples": [
                    {"text": "好家伙 yyds 这也太顶了", "seq": 11},
                    {"text": "不存在的原话", "seq": 11},
                    {"text": "确实，笑死", "seq": 999},
                ],
                "summary": "短句、接梗、爱用 yyds",
                "jargon": [
                    {"term": "yyds", "meaning": "永远的神", "confidence": 0.9},
                    {"term": "笑死", "meaning": "很好笑", "confidence": 0.5},
                    {"term": "不存在", "meaning": "编的", "confidence": 0.9},
                ],
            },
            self.messages,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.rules, ("多用短句",))
        self.assertEqual([(item.text, item.seq) for item in parsed.examples], [("好家伙 yyds 这也太顶了", 11)])
        self.assertEqual(parsed.summary, "短句、接梗、爱用 yyds")
        self.assertEqual(
            [(item.term, item.status) for item in parsed.jargon],
            [("yyds", "active"), ("笑死", "rejected")],
        )

    def test_known_terms_are_not_relearned(self) -> None:
        parsed = parse_style(
            {"jargon": [{"term": "yyds", "meaning": "永远的神", "confidence": 0.9}]},
            self.messages,
            known_terms=["YYDS"],
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.jargon, ())

    def test_sensitive_meaning_and_summary_are_dropped(self) -> None:
        parsed = parse_style(
            {
                "summary": "密码 abc123",
                "rules": ["密码是 123456"],
                "jargon": [{"term": "yyds", "meaning": "密码 abc123", "confidence": 0.9}],
            },
            self.messages,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.summary, "")
        self.assertEqual(parsed.rules, ())
        self.assertEqual(parsed.jargon, ())

    def test_non_dict_payload_is_rejected(self) -> None:
        self.assertIsNone(parse_style("not json", self.messages))

    def test_prompt_carries_message_seq_and_known_terms(self) -> None:
        prompt = build_prompt(self.messages, ["yyds"])

        self.assertIn("[11]", prompt[1]["content"])
        self.assertIn("已知黑话", prompt[0]["content"])
        self.assertIn("yyds", prompt[0]["content"])


class _StyleTestCase(unittest.IsolatedAsyncioTestCase):
    """共享临时租户与消息落库辅助。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        memory_state.mark_ready()
        self.tenant = f"test:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)

    async def _seed(self, content: str, *, sender: str = U1, name: str = "小明") -> ChatMessage:
        message = ChatMessage(
            tenant=self.tenant,
            sender_id=sender,
            sender_name=name,
            content=content,
            dedupe_key=f"msg:{uuid4().hex}",
            occurred_at=utc_now_naive(),
        )
        message.id = await chat_repository.append_message(message)
        return message

    async def _seed_many(self, count: int, prefix: str = "第") -> list[ChatMessage]:
        return [await self._seed(f"{prefix}{index}楼 yyds 好家伙") for index in range(count)]

    def _payload(self, message: ChatMessage, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "rules": ["多用短句", "爱接梗"],
            "examples": [{"text": message.content, "seq": int(message.id or 0)}],
            "summary": "群里说话短、爱接梗",
            "jargon": [{"term": "yyds", "meaning": "永远的神", "confidence": 0.9}],
        }
        payload.update(overrides)
        return payload

    async def _save(self, seq_start: int = 1, seq_end: int = 10, *, source_batch_id: str = "style:1:10") -> None:
        await chat_repository.save_style(
            self.tenant,
            _result(examples=(StyleExample("原话", seq_start),), jargon=(_active("yyds"),)),
            seq_start=seq_start,
            seq_end=seq_end,
            evidence_count=2,
            source_batch_id=source_batch_id,
        )


class StyleLearningTests(_StyleTestCase):
    """首次 200 条、每周复查、隐私开关与失败不回退。"""

    async def test_first_style_needs_200_clean_messages(self) -> None:
        messages = await self._seed_many(199)
        provider = _StyleProvider(self._payload(messages[0]))

        with _config():
            self.assertFalse(await learn_style(self.tenant, provider=provider))
        self.assertEqual(provider.calls, 0)
        self.assertIsNone(await chat_repository.get_style(self.tenant))

        last = await self._seed("第199楼 yyds 好家伙")
        with _config():
            self.assertTrue(await learn_style(self.tenant, provider=provider))
        self.assertEqual(provider.calls, 1)

        style = await chat_repository.get_style(self.tenant)
        self.assertIsNotNone(style)
        self.assertEqual((style.learn_count, style.version, style.use_count), (1, 1, 0))
        self.assertEqual(style.evidence_count, 200)
        self.assertEqual(style.evidence_seq_end, int(last.id or 0))
        self.assertIn("多用短句", json.loads(style.rules))
        jargon = await chat_repository.list_jargon(self.tenant)
        self.assertEqual([(row.term, row.status, row.count) for row in jargon], [("yyds", "active", 1)])

    async def test_weekly_review_allows_small_new_batch(self) -> None:
        messages = await self._seed_many(200)
        first = _StyleProvider(self._payload(messages[-1]))
        with _config():
            self.assertTrue(await learn_style(self.tenant, provider=first))

        await self._seed("一周后的新消息 yyds")
        soon = _StyleProvider(self._payload(messages[-1]))
        with _config():
            self.assertFalse(await learn_style(self.tenant, provider=soon, now=utc_now_naive() + timedelta(days=1)))
        self.assertEqual(soon.calls, 0)

        later = _StyleProvider(self._payload(messages[-1]))
        with _config():
            self.assertTrue(await learn_style(self.tenant, provider=later, now=utc_now_naive() + timedelta(days=8)))
        self.assertEqual(later.calls, 1)
        style = await chat_repository.get_style(self.tenant)
        self.assertEqual((style.learn_count, style.version), (2, 2))
        self.assertEqual((await chat_repository.list_jargon(self.tenant))[0].count, 1)

    async def test_jargon_learning_off_strips_jargon(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        messages = await self._seed_many(200)
        provider = _StyleProvider(self._payload(messages[0]))
        item = {
            "platform": "onebot11",
            "item_id": self.tenant.rsplit(":", 1)[-1],
            "type": "group",
            "learn": False,
        }

        with _config(learning_list=[item]):
            self.assertTrue(await learn_style(self.tenant, provider=provider))

        self.assertEqual(await chat_repository.list_jargon(self.tenant), [])
        style = await chat_repository.get_style(self.tenant)
        self.assertIn("多用短句", json.loads(style.rules))

    async def test_tenant_learn_off_skips_learning(self) -> None:
        messages = await self._seed_many(200)
        await chat_repository.set_learn_enabled(self.tenant, False)
        provider = _StyleProvider(self._payload(messages[0]))

        with _config():
            self.assertFalse(await learn_style(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 0)

    async def test_expression_learn_off_skips_style_learning(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        messages = await self._seed_many(200)
        provider = _StyleProvider(self._payload(messages[0]))
        item = {
            "platform": "onebot11",
            "item_id": self.tenant.rsplit(":", 1)[-1],
            "type": "group",
            "learn": False,
        }

        with _config(expression_learning_list=[item]):
            self.assertFalse(await learn_style(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 0)
        self.assertIsNone(await chat_repository.get_style(self.tenant))
        self.assertIsNone(await chat_repository.get_cursor(self.tenant, "style"))

    async def test_provider_failure_then_recovery(self) -> None:
        messages = await self._seed_many(200)
        with _config():
            failing = _StyleProvider(ValueError("bad json"))
            self.assertFalse(await learn_style(self.tenant, provider=failing))
        self.assertEqual(failing.calls, 2)
        self.assertIsNone(await chat_repository.get_style(self.tenant))

        await self._seed("恢复后的消息 yyds")
        recovery = _StyleProvider(self._payload(messages[0]))
        with _config():
            self.assertTrue(await learn_style(self.tenant, provider=recovery, now=utc_now_naive() + timedelta(days=8)))
        self.assertEqual(recovery.calls, 1)
        self.assertIsNotNone(await chat_repository.get_style(self.tenant))

    async def test_invalid_prefix_does_not_stall_learning(self) -> None:
        """前 400 条全是命令时，扫描进度继续推进，后续有效消息仍能被学到。"""
        for index in range(400):
            await self._seed(f"/签到 第{index}楼")
        messages = await self._seed_many(200)
        provider = _StyleProvider(self._payload(messages[-1]))

        with _config():
            self.assertFalse(await learn_style(self.tenant, provider=provider))
            self.assertTrue(await learn_style(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 1)
        self.assertIsNotNone(await chat_repository.get_style(self.tenant))

    async def test_empty_result_advances_scan_without_repeating_batch(self) -> None:
        """模型合法返回“没有可学习内容”时推进扫描，不反复处理同一批。"""
        messages = await self._seed_many(200)
        provider = _StyleProvider({"rules": [], "examples": [], "summary": "", "jargon": []})

        with _config():
            self.assertFalse(await learn_style(self.tenant, provider=provider))
            self.assertFalse(await learn_style(self.tenant, provider=provider))

        self.assertEqual(provider.calls, 1)
        cursor = await chat_repository.get_cursor(self.tenant, "style")
        self.assertEqual(cursor.cursor_seq, int(messages[-1].id or 0))
        self.assertIsNone(await chat_repository.get_style(self.tenant))

    async def test_style_job_tick_processes_due_tenant_once(self) -> None:
        messages = await self._seed_many(200)
        provider = _StyleProvider(self._payload(messages[-1]))
        snapshot = [row for row in await chat_repository.scheduling_snapshot() if row.tenant == self.tenant]

        with (
            _config(),
            patch.object(chat_repository, "scheduling_snapshot", new=AsyncMock(return_value=snapshot)),
        ):
            self.assertEqual(await style_job_tick(provider=provider), 1)
            self.assertEqual(await style_job_tick(provider=provider), 0)

        self.assertEqual(provider.calls, 1)

    async def test_style_job_tick_limits_concurrency_not_tenants(self) -> None:
        """并发上限只限制同时运行的任务；所有到期租户都会被尝试。"""
        states = [
            TenantJobState(tenant="test:new", last_message_id=30, message_count=300, learn_enabled=True),
            TenantJobState(tenant="test:mid", last_message_id=20, message_count=300, learn_enabled=True),
            TenantJobState(tenant="test:old", last_message_id=10, message_count=300, learn_enabled=None),
            TenantJobState(tenant="test:off", last_message_id=40, message_count=300, learn_enabled=False),
        ]
        seen: list[str] = []
        running = 0
        peak = 0

        async def fake_learn(tenant: str, **_: Any) -> bool:
            nonlocal running, peak
            seen.append(tenant)
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1
            return True

        with (
            patch.object(style_module, "expression_config", return_value={"max_expression_learner": 1}),
            patch.object(chat_repository, "scheduling_snapshot", new=AsyncMock(return_value=states)),
            patch.object(style_module, "learn_style", new=fake_learn),
        ):
            self.assertEqual(await style_job_tick(), 3)

        self.assertEqual(seen, ["test:new", "test:mid", "test:old"])
        self.assertEqual(peak, 1)

    async def test_style_result_is_dropped_when_evidence_retracted_during_call(self) -> None:
        """模型调用期间证据被撤回时，结果不落库。"""
        messages = await self._seed_many(200)
        target = int(messages[-1].id or 0)
        payload = self._payload(messages[-1])

        class _Provider:
            async def chat_json(self, *_args: Any, **_kwargs: Any) -> Any:
                async with base_models.async_maker() as session:  # type: ignore[misc]
                    await session.execute(update(ChatMessage).where(ChatMessage.id == target).values(deleted=True))
                    await session.commit()
                return payload

        with _config():
            self.assertFalse(await learn_style(self.tenant, provider=_Provider()))

        self.assertIsNone(await chat_repository.get_style(self.tenant))

    async def test_style_deleted_during_call_is_not_recreated(self) -> None:
        """模型调用期间风格被删除时，旧结果不能把它写回来。"""
        messages = await self._seed_many(200)
        await self._save()
        payload = self._payload(messages[-1])
        tenant = self.tenant

        class _Provider:
            async def chat_json(self, *_args: Any, **_kwargs: Any) -> Any:
                await chat_repository.delete_style(tenant)
                return payload

        with _config():
            self.assertFalse(await learn_style(self.tenant, provider=_Provider()))

        self.assertIsNone(await chat_repository.get_style(self.tenant))


class StyleCommitTests(_StyleTestCase):
    """版本推进、人工覆盖保护、黑话状态与删除隔离。"""

    async def test_upsert_advances_version_and_jargon_counts(self) -> None:
        await self._save(seq_start=10, seq_end=20)

        await chat_repository.save_style(
            self.tenant,
            _result(
                summary="新的摘要",
                examples=(),
                jargon=(
                    StyleJargon(term="yyds", normalized="yyds", meaning="永远滴神", status="active"),
                    StyleJargon(term="笑死", normalized="笑死", meaning="很好笑", status="rejected"),
                ),
            ),
            seq_start=21,
            seq_end=30,
            evidence_count=10,
            source_batch_id="style:21:30",
        )

        style = await chat_repository.get_style(self.tenant)
        self.assertEqual((style.version, style.learn_count), (2, 2))
        self.assertEqual(style.summary, "新的摘要")
        self.assertEqual((style.evidence_seq_start, style.evidence_seq_end), (21, 30))
        jargon = {row.term: row for row in await chat_repository.list_jargon(self.tenant)}
        self.assertEqual((jargon["yyds"].count, jargon["yyds"].learn_count, jargon["yyds"].meaning), (2, 2, "永远滴神"))
        self.assertEqual(jargon["笑死"].status, "rejected")

    async def test_rejected_meaning_does_not_override_active(self) -> None:
        await self._save()

        await chat_repository.save_style(
            self.tenant,
            _result(jargon=(StyleJargon(term="yyds", normalized="yyds", meaning="垃圾释义", status="rejected"),)),
            seq_start=11,
            seq_end=20,
            evidence_count=2,
            source_batch_id="style:11:20",
        )

        [row] = await chat_repository.list_jargon(self.tenant)
        self.assertEqual((row.status, row.meaning, row.count), ("active", "永远的神", 2))

    async def test_manual_version_conflict_abandons_result(self) -> None:
        await self._save()
        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(
                update(ChatStyle)
                .where(ChatStyle.tenant == self.tenant)
                .values(manual_override="人工摘要", manual_version=1)
            )
            await session.commit()

        saved = await chat_repository.save_style(
            self.tenant,
            _result(summary="自动摘要覆盖"),
            seq_start=30,
            seq_end=40,
            evidence_count=2,
            source_batch_id="style:30:40",
            expected_manual_version=0,
        )

        self.assertFalse(saved)
        style = await chat_repository.get_style(self.tenant)
        self.assertEqual(style.summary, "群里说话短、爱接梗")
        self.assertEqual(style.manual_override, "人工摘要")

    async def test_usage_counters_are_separate(self) -> None:
        await self._save()

        await chat_repository.mark_style_used(self.tenant)
        await chat_repository.mark_jargon_used(self.tenant, ["yyds"])

        style = await chat_repository.get_style(self.tenant)
        [row] = await chat_repository.list_jargon(self.tenant)
        self.assertEqual((style.learn_count, style.use_count), (1, 1))
        self.assertIsNotNone(style.last_used_at)
        self.assertEqual((row.learn_count, row.use_count), (1, 1))
        self.assertIsNotNone(row.last_used_at)

    async def test_delete_is_tenant_scoped(self) -> None:
        await self._save()
        [row] = await chat_repository.list_jargon(self.tenant)
        other = f"test:{uuid4().hex}"

        self.assertFalse(await chat_repository.delete_jargon(other, int(row.id)))
        self.assertFalse(await chat_repository.delete_style(other))
        self.assertEqual(len(await chat_repository.list_jargon(self.tenant)), 1)
        self.assertIsNotNone(await chat_repository.get_style(self.tenant))

        self.assertTrue(await chat_repository.delete_jargon(self.tenant, int(row.id)))
        self.assertTrue(await chat_repository.delete_style(self.tenant))
        self.assertEqual(await chat_repository.list_jargon(self.tenant), [])
        self.assertIsNone(await chat_repository.get_style(self.tenant))

    async def test_recall_drops_quoted_example_and_batch_jargon(self) -> None:
        message = await self._seed("好家伙 yyds 这也太顶了")
        message.platform_message_id = "pm-style-1"
        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(
                update(ChatMessage)
                .where(ChatMessage.tenant == self.tenant, ChatMessage.id == message.id)
                .values(platform_message_id="pm-style-1")
            )
            await session.commit()
        seq = int(message.id or 0)
        await chat_repository.save_style(
            self.tenant,
            _result(examples=(StyleExample(message.content, seq),), jargon=(_active("yyds"),)),
            seq_start=seq,
            seq_end=seq,
            evidence_count=1,
            source_batch_id=f"style:{seq}:{seq}",
        )

        result = await chat_repository.invalidate_message(self.tenant, "pm-style-1")

        self.assertIsNotNone(result)
        style = await chat_repository.get_style(self.tenant)
        self.assertEqual(json.loads(style.examples), [])
        self.assertEqual(json.loads(style.rules), [])
        self.assertEqual(style.summary, "")
        self.assertTrue(style.needs_rebuild)
        self.assertEqual(await chat_repository.list_jargon(self.tenant), [])

    async def test_recall_rebuilds_style_from_remaining_evidence(self) -> None:
        """撤回后清空的风格不需要再积满 200 条，直接用剩余证据重建。"""
        messages = await self._seed_many(200)
        first, last = int(messages[0].id or 0), int(messages[-1].id or 0)
        await chat_repository.save_style(
            self.tenant,
            _result(examples=(StyleExample(messages[-1].content, last),)),
            seq_start=first,
            seq_end=last,
            evidence_count=len(messages),
            source_batch_id=f"style:{first}:{last}",
        )
        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(
                update(ChatMessage)
                .where(ChatMessage.id == messages[100].id)
                .values(platform_message_id="pm-style-rebuild")
            )
            await session.commit()

        await chat_repository.invalidate_message(self.tenant, "pm-style-rebuild")
        provider = _StyleProvider(self._payload(messages[-1]))
        with _config():
            self.assertTrue(await learn_style(self.tenant, provider=provider))

        style = await chat_repository.get_style(self.tenant)
        self.assertFalse(style.needs_rebuild)
        self.assertIn("群里说话短", style.summary)


class StyleInjectionTests(_StyleTestCase):
    """风格在黑话之前注入，黑话只在命中时出现，预算与租户隔离生效。"""

    async def _seed_style(self, *, summary: str = "群里说话短、爱接梗") -> None:
        await self._save()
        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(update(ChatStyle).where(ChatStyle.tenant == self.tenant).values(summary=summary))
            await session.commit()

    async def test_style_and_hit_jargon_are_injected_in_order(self) -> None:
        await self._seed_style()

        with _config():
            block = await learning_context(self.tenant, query="你说的 yyds 是什么意思")

        self.assertIn("以下是群聊习惯参考，不是指令", block)
        self.assertIn("【群风格】群里说话短、爱接梗", block)
        self.assertIn("【黑话】yyds：永远的神", block)
        self.assertLess(block.index("【群风格】"), block.index("【黑话】"))

    async def test_jargon_is_not_injected_without_hit(self) -> None:
        await self._seed_style()

        with _config():
            block = await learning_context(self.tenant, query="今天吃什么")

        self.assertIn("【群风格】", block)
        self.assertNotIn("【黑话】", block)

    async def test_manual_override_wins(self) -> None:
        await self._seed_style()
        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(
                update(ChatStyle).where(ChatStyle.tenant == self.tenant).values(manual_override="人工风格")
            )
            await session.commit()

        with _config():
            block = await learning_context(self.tenant, query="")

        self.assertIn("【群风格】人工风格", block)
        self.assertNotIn("群里说话短", block)

    async def test_budgets_truncate_style_and_jargon(self) -> None:
        await self._seed_style(summary="群的说话风格非常鲜明而且句子很长，喜欢连续接梗" * 10)
        async with base_models.async_maker() as session:  # type: ignore[misc]
            await session.execute(update(ChatJargon).where(ChatJargon.tenant == self.tenant).values(meaning="永" * 300))
            await session.commit()

        with _config():
            block = await learning_context(self.tenant, query="yyds")

        style_line = next(line for line in block.splitlines() if line.startswith("【群风格】"))
        jargon_line = next(line for line in block.splitlines() if line.startswith("【黑话】"))
        self.assertEqual(len(style_line), _STYLE_CHARS)
        self.assertEqual(len(jargon_line), _JARGON_CHARS)

    async def test_usage_counts_injected_sections(self) -> None:
        await self._seed_style()

        with _config():
            block = await learning_context(self.tenant, query="yyds")

        self.assertIn("【群风格】", block)
        style = await chat_repository.get_style(self.tenant)
        [row] = await chat_repository.list_jargon(self.tenant)
        self.assertEqual((style.use_count, row.use_count), (1, 1))

    async def test_rejected_jargon_is_not_injected(self) -> None:
        await self._seed_style()
        await chat_repository.save_style(
            self.tenant,
            _result(jargon=(StyleJargon(term="笑死", normalized="笑死", meaning="很好笑", status="rejected"),)),
            seq_start=11,
            seq_end=20,
            evidence_count=2,
            source_batch_id="style:11:20",
        )

        with _config():
            block = await learning_context(self.tenant, query="笑死")

        self.assertNotIn("【黑话】", block)

    async def test_jargon_use_off_skips_injection(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        await self._seed_style()
        item = {
            "platform": "onebot11",
            "item_id": self.tenant.rsplit(":", 1)[-1],
            "type": "group",
            "use": False,
        }

        with _config(learning_list=[item]):
            block = await learning_context(self.tenant, query="yyds")

        self.assertIn("【群风格】", block)
        self.assertNotIn("【黑话】", block)

    async def test_expression_use_off_skips_style_but_keeps_jargon(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        await self._seed_style()
        item = {
            "platform": "onebot11",
            "item_id": self.tenant.rsplit(":", 1)[-1],
            "type": "group",
            "use": False,
        }
        trace: dict[str, Any] = {}

        with _config(expression_learning_list=[item]):
            block = await learning_context(self.tenant, query="yyds", trace=trace)

        self.assertNotIn("【群风格】", block)
        self.assertIn("【黑话】yyds：永远的神", block)
        self.assertEqual(trace["injected"]["style"], 0)
        self.assertEqual(trace["injected"]["chars"]["style"], 0)
        self.assertEqual((await chat_repository.get_style(self.tenant)).use_count, 0)

    async def test_expression_learn_off_keeps_style_injection(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(self.tenant, True)
        await self._seed_style()
        item = {
            "platform": "onebot11",
            "item_id": self.tenant.rsplit(":", 1)[-1],
            "type": "group",
            "learn": False,
        }

        with _config(expression_learning_list=[item]):
            block = await learning_context(self.tenant, query="yyds")

        self.assertIn("【群风格】", block)

    async def test_shared_jargon_group_merges_tenants(self) -> None:
        sibling = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await chat_repository.set_learn_enabled(sibling, True)
        self.tenant = sibling
        await self._seed("yyds 好家伙")
        await self._save()

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
            block = await learning_context(self.tenant, query="yyds")

        self.assertIn("【黑话】yyds：永远的神", block)

    async def test_other_tenant_sees_nothing(self) -> None:
        await self._seed_style()
        other = f"test:{uuid4().hex}"
        await chat_repository.set_learn_enabled(other, True)

        with _config():
            self.assertEqual(await learning_context(other, query="yyds"), "")


class ConsoleRouteTests(_StyleTestCase):
    """控制台按租户读取与删除；不存在时明确报错。"""

    async def test_reads_and_deletes_by_tenant(self) -> None:
        await self._save()
        [row] = await chat_repository.list_jargon(self.tenant)

        payload = await chat_routes.get_style(self.tenant)
        self.assertEqual(payload["style"]["summary"], "群里说话短、爱接梗")
        self.assertTrue(payload["style"]["updated_at"].endswith("+08:00"))
        listed = await chat_routes.list_jargon(self.tenant, "active")
        self.assertEqual([item["term"] for item in listed["items"]], ["yyds"])

        tenants = await chat_routes.list_tenants()
        self.assertIn(self.tenant, [item["tenant"] for item in tenants["tenants"]])

        self.assertEqual(await chat_routes.delete_jargon(int(row.id), self.tenant), {"deleted": True})
        with self.assertRaises(HTTPException) as missing:
            await chat_routes.delete_jargon(int(row.id), self.tenant)
        self.assertEqual(missing.exception.status_code, 404)

        self.assertEqual(await chat_routes.delete_style(self.tenant), {"deleted": True})
        with self.assertRaises(HTTPException) as no_style:
            await chat_routes.delete_style(self.tenant)
        self.assertEqual(no_style.exception.status_code, 404)

    async def test_empty_tenant_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as error:
            await chat_routes.get_style("   ")

        self.assertEqual(error.exception.status_code, 400)
