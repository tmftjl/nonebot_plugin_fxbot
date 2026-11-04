"""P3 群风格与黑话：解析校验、学习周期、注入排序与删除隔离。"""

from __future__ import annotations

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
from src.plugins.nonebot_plugin_fxbot.console.routes import chat as chat_routes
from src.plugins.nonebot_plugin_fxbot.chat.memory.style import (
    StyleJargon,
    StyleResult,
    StyleExample,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.inject import _STYLE_CHARS, learning_context
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import (
    ChatStyle,
    ChatJargon,
    ChatMessage,
    utc_now_naive,
)
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository

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
            }
        }


def _config(**kwargs: Any) -> Any:
    return patch.object(config_module, "get_config_manager", return_value=_FakeConfig(**kwargs))


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


class StyleInjectionTests(_StyleTestCase):
    """风格只供回复器、黑话只供规划器，完整注入并保持租户隔离。"""

    async def _seed_style(self, *, summary: str = "群里说话短、爱接梗") -> None:
        await self._save()
        async with base_models.async_maker() as session:
            await session.execute(
                update(ChatStyle).where(ChatStyle.tenant == self.tenant).values(manual_override=summary)
            )
            await session.commit()

    async def planner(self, text="yyds", **kwargs):
        return await learning_context(
            self.tenant,
            phase="planner",
            history=[{"role": "user", "_sender_id": U1, "_source_ids": ["m1"], "content": text}],
            **kwargs,
        )

    async def test_stages_are_separate(self) -> None:
        await self._seed_style()
        with _config():
            planner = await self.planner()
            reply = await learning_context(self.tenant)
        self.assertIn("yyds：永远的神", planner)
        self.assertNotIn("【表达风格】", planner)
        self.assertIn("【表达风格】群里说话短、爱接梗", reply)
        self.assertNotIn("yyds", reply)

    async def test_old_automatic_style_is_not_injected(self) -> None:
        await self._seed_style(summary="")
        with _config():
            self.assertEqual(await learning_context(self.tenant), "")

    async def test_jargon_requires_context_hit(self) -> None:
        await self._seed_style()
        with _config():
            self.assertEqual(await self.planner("今天吃什么"), "")

    async def test_complete_jargon_is_not_truncated(self) -> None:
        await self._seed_style(summary="风格" * 200)
        async with base_models.async_maker() as session:
            await session.execute(update(ChatJargon).where(ChatJargon.tenant == self.tenant).values(meaning="永" * 300))
            await session.commit()
        with _config():
            planner = await self.planner()
            reply = await learning_context(self.tenant)
        self.assertIn("yyds：" + "永" * 300, planner)
        self.assertEqual(len(reply), _STYLE_CHARS)

    async def test_rebuild_deduplicates_usage(self) -> None:
        await self._seed_style()
        references = {}
        with _config():
            first = await self.planner(references=references)
            second = await self.planner(references=references)
        self.assertEqual(first, second)
        self.assertEqual(first.count("yyds："), 1)
        [row] = await chat_repository.list_jargon(self.tenant)
        self.assertEqual(row.use_count, 1)

    async def test_other_tenant_sees_nothing(self) -> None:
        await self._seed_style()
        with _config():
            self.assertEqual(await learning_context("test:other"), "")

    async def test_self_and_internal_messages_are_not_matched(self) -> None:
        await self._seed_style()
        history = [
            {"role": "user", "_sender_id": U1, "_self_message": True, "content": "yyds"},
            {"role": "user", "_sender_id": U1, "_planner_internal": True, "content": "yyds"},
            {"role": "user", "content": "yyds"},
        ]
        with _config():
            self.assertEqual(await learning_context(self.tenant, phase="planner", history=history), "")

    async def test_match_scans_before_last_two_messages(self) -> None:
        await self._seed_style()
        history = [
            {"role": "user", "_sender_id": U1, "_source_ids": [str(i)], "content": text}
            for i, text in enumerate(["yyds", *["今天吃饭"] * 20])
        ]
        with _config():
            block = await learning_context(self.tenant, phase="planner", history=history)
        self.assertIn("yyds：永远的神", block)

    async def test_use_switch_still_blocks_jargon(self) -> None:
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        await self._seed_style()
        item = {"platform": "onebot11", "item_id": self.tenant.rsplit(":", 1)[-1], "type": "group", "use": False}
        with _config(learning_list=[item]):
            self.assertEqual(await self.planner(), "")

    async def test_shared_jargon_scope_and_usage_are_preserved(self) -> None:
        sibling = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        self.tenant = sibling
        await self._seed("yyds 好家伙")
        await self._save()
        self.tenant = f"onebot11:{uuid4().hex}:group:{uuid4().hex}"
        groups = [
            {
                "targets": [
                    {"platform": "onebot11", "item_id": tenant.rsplit(":", 1)[-1], "type": "group"}
                    for tenant in (self.tenant, sibling)
                ]
            }
        ]
        with _config(groups=groups):
            self.assertIn("yyds：永远的神", await self.planner())
        [row] = await chat_repository.list_jargon(sibling)
        self.assertEqual(row.use_count, 1)

    async def test_rejected_jargon_is_not_injected(self) -> None:
        await self._seed_style()
        async with base_models.async_maker() as session:
            await session.execute(update(ChatJargon).where(ChatJargon.tenant == self.tenant).values(status="rejected"))
            await session.commit()
        with _config():
            self.assertEqual(await self.planner(), "")


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
