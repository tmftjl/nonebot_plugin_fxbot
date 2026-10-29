"""表情注册、选择与可见历史的 MaiBot 行为回归。"""

from __future__ import annotations

import base64
import asyncio
import hashlib
import tempfile
import unittest
from io import BytesIO
from uuid import uuid4
from types import SimpleNamespace
from pathlib import Path
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from PIL import Image
from src.plugins.nonebot_plugin_fxbot.db import base_models
from src.plugins.nonebot_plugin_fxbot.chat import emoji, vision
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest, ChatResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory import state, migrate
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import emoji_tools
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatTurn, ChatTurnItem
from src.plugins.nonebot_plugin_fxbot.chat.message_receive import sender
from src.plugins.nonebot_plugin_fxbot.chat.memory.repository import chat_repository


def request(tenant: str) -> ChatRequest:
    """构造单一会话的正常聊天请求。"""
    return ChatRequest(
        session_id=tenant,
        user_id="user",
        group_id=None,
        text="开心",
        turn_id=uuid4().hex,
        dedupe_key=uuid4().hex,
        trigger_kind="private",
        message_id="m1",
        accepted_epoch=0,
    )


class EmojiSelectionTests(unittest.IsolatedAsyncioTestCase):
    """选择模型使用真实近期上下文、上游提示词与编号规则。"""

    def setUp(self) -> None:
        self.request = request("test:emoji-selector")
        self.store = ChatSessionStore()
        self.service = SimpleNamespace(
            session_store=self.store, is_current=lambda *args: True, _persist_turn_start=AsyncMock(return_value=None)
        )
        self.context = SimpleNamespace(
            request=self.request, runtime=SimpleNamespace(bot=object(), matcher=object()), epoch=0
        )
        self.rows = [SimpleNamespace(hash="a", description="开心"), SimpleNamespace(hash="b", description="难过")]
        self.provider = SimpleNamespace(
            chat=AsyncMock(return_value=LLMResponse(content='{"emoji_index":2,"reason":"符合语气"}'))
        )

        @asynccontextmanager
        async def lease(*args):
            yield self.provider

        self.enterContext(patch.object(emoji_tools, "fallback_provider", lease))
        self.enterContext(patch.object(emoji_tools, "selector_task", return_value="emoji"))
        self.enterContext(patch.object(emoji_tools, "emoji_catalog", AsyncMock(return_value=self.rows)))
        self.enterContext(patch.object(emoji_tools, "emoji_collage", return_value="data:image/png;base64,test"))
        self.lookup = self.enterContext(
            patch.object(
                emoji_tools,
                "emoji_by_hash",
                AsyncMock(side_effect=lambda tenant, digest: next(row for row in self.rows if row.hash == digest)),
            )
        )
        self.deliver = self.enterContext(patch.object(emoji_tools, "deliver_emoji", AsyncMock(return_value=True)))

    async def test_selection_uses_recent_twelve_messages_and_upstream_prompt(self) -> None:
        for index in range(15):
            self.store.append(self.request.session_id, {"role": "user", "content": f"消息{index}"})
        result = await emoji_tools.send_selected(self.service, self.context, "不应拿这段指导替代上下文")
        self.assertTrue(result["ok"])
        prompt = self.provider.chat.await_args.args[0]
        self.assertIn("根据上下文和当前语气", prompt[0]["content"])
        self.assertEqual([message["content"] for message in prompt[1:13]], [f"消息{index}" for index in range(3, 15)])
        self.assertNotIn("不应拿这段指导替代上下文", str(prompt))
        self.assertEqual(self.provider.chat.await_args.kwargs, {"temperature": 0.7, "max_tokens": 8192})
        self.assertEqual(self.deliver.await_args.args[5].hash, "b")

    async def test_bad_or_out_of_range_output_selects_first_candidate(self) -> None:
        for output in ("not json", '{"emoji_index":99}', '{"emoji_index":0}'):
            with self.subTest(output=output):
                self.provider.chat.return_value = LLMResponse(content=output)
                result = await emoji_tools.send_selected(self.service, self.context, "")
                self.assertTrue(result["ok"])
                self.assertEqual(self.deliver.await_args.args[5].hash, "a")

    async def test_cleared_session_during_selection_does_not_send(self) -> None:
        async def model(*args, **kwargs):
            self.service.is_current = lambda *args: False
            return LLMResponse(content='{"emoji_index":1}')

        self.provider.chat.side_effect = model
        self.assertFalse((await emoji_tools.send_selected(self.service, self.context, ""))["ok"])
        self.deliver.assert_not_awaited()
        self.service._persist_turn_start.assert_not_awaited()

    async def test_disabled_candidate_cannot_be_sent_after_selection(self) -> None:
        self.lookup.side_effect = None
        self.lookup.return_value = None
        self.assertFalse((await emoji_tools.send_selected(self.service, self.context, ""))["ok"])
        self.deliver.assert_not_awaited()


class EmojiRoutingAndVisionTests(unittest.IsolatedAsyncioTestCase):
    """路由与图片提示词采用当前配置和上游格式。"""

    def test_selector_route_priority(self) -> None:
        for routing, expected in (
            ({"emoji": "dedicated", "vision": "v"}, "emoji"),
            ({"vision": "planner-model"}, "planner"),
            ({"vision": "v"}, "vision"),
        ):
            config = SimpleNamespace(get_system=lambda: {"chat": {"model_routing": routing}})
            with (
                patch.object(emoji_tools, "get_config_manager", return_value=config),
                patch.object(emoji_tools, "candidate_providers", return_value=["planner-model"]),
            ):
                self.assertEqual(emoji_tools.selector_task("tenant"), expected)
        with patch.object(
            emoji_tools, "get_config_manager", return_value=SimpleNamespace(get_system=lambda: {"chat": {}})
        ):
            with self.assertRaises(ValueError):
                emoji_tools.selector_task("tenant")

    async def test_description_uses_plain_tags_and_gif_contact_sheet(self) -> None:
        output = BytesIO()
        first = Image.new("RGB", (20, 10), "red")
        first.save(
            output, format="GIF", save_all=True, append_images=[Image.new("RGB", (20, 10), "blue")], duration=100
        )
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="开心， 开心；Happy,happy")))
        description, tags = await vision.describe_emoji(
            provider, "data:image/gif;base64," + base64.b64encode(output.getvalue()).decode("ascii")
        )
        self.assertEqual((description, tags), ("开心,Happy", ["开心", "Happy"]))
        options = provider.chat.await_args.kwargs
        content = options["messages"][0]["content"]
        self.assertIn("输入类型：GIF", content[0]["text"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(options["max_tokens"], 8192)

    async def test_review_uses_upstream_screenshot_and_text_rules(self) -> None:
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="否")))
        self.assertFalse(await vision.review_emoji(provider, "data:image/png;base64,test"))
        prompt = provider.chat.await_args.kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("不能是任何形式的截图", prompt)
        self.assertIn("不要出现5个以上文字", prompt)


class EmojiRegistrationAndDeliveryTests(unittest.IsolatedAsyncioTestCase):
    """使用真实数据库验证名额互斥、维护周期与成功交付历史。"""

    async def asyncSetUp(self) -> None:
        await base_models.init_database()
        await migrate.ensure_schema()
        state.mark_ready()
        self.tenant = "test:emoji-parity:" + uuid4().hex
        self.addAsyncCleanup(chat_repository.clear_tenant, self.tenant)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.config = {"max_reg_num": 1, "do_replace": False, "steal_emoji": True}
        config = SimpleNamespace(
            get_system=lambda: {"chat": {"emoji": self.config, "model_routing": {"vision": "test"}}}
        )
        self.enterContext(patch.object(emoji, "get_config_manager", return_value=config))
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="取消注册编号2")))

        @asynccontextmanager
        async def lease(*args):
            yield provider

        self.provider = provider
        self.enterContext(patch.object(emoji, "fallback_provider", lease))
        self.describe = self.enterContext(
            patch.object(emoji, "describe_emoji", AsyncMock(return_value=("开心", ["开心"])))
        )

    async def pending(self, index: int):
        """保存不同内容的待识别图片。"""
        output = BytesIO()
        Image.new("RGB", (8, 8), (index, 0, 0)).save(output, format="PNG")
        data = output.getvalue()
        digest = hashlib.sha256(data).hexdigest()
        path = self.folder / f"{digest}.png"
        path.write_bytes(data)
        return await chat_repository.save_collected_emoji(self.tenant, digest, str(path))

    async def test_concurrent_analysis_cannot_overbook_registration_slot(self) -> None:
        rows = [await self.pending(index) for index in (1, 2)]
        results = await asyncio.gather(*(emoji._analyze(self.tenant, row.hash, Path(row.path)) for row in rows))
        self.assertEqual(sum(results), 1)
        self.assertEqual((await chat_repository.count_emoji(self.tenant))["registered"], 1)
        self.assertEqual(self.describe.await_count, 1)

    async def test_periodic_maintenance_registers_one_per_session(self) -> None:
        self.config["max_reg_num"] = 3
        for index in (1, 2, 3):
            await self.pending(index)
        await emoji.reconcile_emoji_cache()
        self.assertEqual((await chat_repository.count_emoji(self.tenant))["registered"], 1)
        await emoji.reconcile_emoji_cache()
        self.assertEqual((await chat_repository.count_emoji(self.tenant))["registered"], 2)

    async def test_failed_description_is_not_repeated_every_maintenance(self) -> None:
        row = await self.pending(1)
        self.describe.return_value = ("", [])
        self.assertFalse(await emoji._analyze(self.tenant, row.hash, Path(row.path)))
        self.assertFalse(await emoji._analyze(self.tenant, row.hash, Path(row.path)))
        self.assertEqual(self.describe.await_count, 1)
        stored = await chat_repository.get_emoji(self.tenant, row.hash)
        self.assertTrue(stored.vlm_processed)
        self.assertFalse(stored.registered)

    async def test_replacement_keeps_duplicate_sample_indexes(self) -> None:
        self.config.update(max_reg_num=2, do_replace=True)
        old, other, new = [await self.pending(index) for index in (1, 2, 3)]
        await chat_repository.set_emoji_registered(self.tenant, old.hash, True)
        await chat_repository.set_emoji_registered(self.tenant, other.hash, True)
        with patch.object(emoji.random, "choices", return_value=[old, old]):
            self.assertTrue(await emoji._analyze(self.tenant, new.hash, Path(new.path)))
        self.assertFalse((await chat_repository.get_emoji(self.tenant, old.hash)).registered)
        self.assertTrue((await chat_repository.get_emoji(self.tenant, other.hash)).registered)
        self.assertTrue((await chat_repository.get_emoji(self.tenant, new.hash)).registered)
        options = self.provider.chat.await_args.kwargs
        self.assertEqual((options["temperature"], options["max_tokens"]), (0.7, 16384))
        self.assertIn("编号: 2", self.provider.chat.await_args.args[0][0]["content"])
        self.assertIn("添加时间:", self.provider.chat.await_args.args[0][0]["content"])

    async def test_successful_delivery_persists_receipt_and_visible_emoji(self) -> None:
        row = await self.pending(1)
        await chat_repository.set_emoji_analysis(self.tenant, row.hash, description="开心", tags=["开心"], passed=True)
        row = await chat_repository.get_emoji(self.tenant, row.hash)
        sending = request(self.tenant)
        turn_id = await chat_repository.upsert_turn(ChatTurn(tenant=self.tenant, turn_key=sending.turn_id))
        response = ChatResponse(text="[表情包]", metadata={"turn_db_id": turn_id})
        store = ChatSessionStore()
        store.append(self.tenant, {"role": "user", "content": "开心"})
        service = ChatService(store)
        matcher = SimpleNamespace(send=AsyncMock(return_value={"message_id": "sent-emoji"}))
        runtime = SimpleNamespace(bot=object())
        with (
            patch.object(sender, "build_message_segment", return_value="image"),
            patch.object(sender, "build_message", return_value="payload"),
        ):
            self.assertTrue(await sender.deliver_emoji(service, sending, response, runtime, matcher, row, 1))
            self.assertFalse(await sender.deliver_emoji(service, sending, response, runtime, matcher, row, 1))
        [stored] = await ChatTurnItem.select_rows(tenant=self.tenant)
        self.assertEqual((stored.visible, stored.platform_message_id), (True, "sent-emoji"))
        self.assertEqual(store.get(self.tenant)[-1]["content"], "[表情包: 开心]")
        self.assertEqual((await chat_repository.get_emoji(self.tenant, row.hash)).count, 1)
        matcher.send.assert_awaited_once()
