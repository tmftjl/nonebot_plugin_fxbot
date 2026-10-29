"""消息接收过滤与单条识图上限。"""

from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import router, service as service_module, message_adapter as adapter_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.behavior import debounce
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import SYSTEM_DEFAULTS, validate_system_config
from src.plugins.nonebot_plugin_fxbot.adapter.platforms.onebot12 import OneBotV12MessageAdapter

from .support import RouterUnitTests


def _event(text: str) -> SimpleNamespace:
    event = SimpleNamespace(text=text, user_id="u1", group_id="g1", message=[])
    event.get_plaintext = lambda: event.text
    return event


def _cfg(**values: object) -> dict[str, object]:
    settings: dict[str, object] = {"image_parse_threshold": 5, "ban_words": [], "ban_msgs_regex": []}
    settings.update(values)
    return {"message_receive": settings}


class MessageFilterTests(unittest.TestCase):
    """屏蔽词与正则。"""

    def test_ban_word(self) -> None:
        self.assertTrue(router._message_filtered(_cfg(ban_words=["广告"]), _event("这是广告")))
        self.assertFalse(router._message_filtered(_cfg(ban_words=["广告"]), _event("你好")))

    def test_ban_regex(self) -> None:
        self.assertTrue(router._message_filtered(_cfg(ban_msgs_regex=[r"\d{6}"]), _event("编号 123456")))
        self.assertFalse(router._message_filtered(_cfg(ban_msgs_regex=[r"\d{6}"]), _event("编号 12345")))

    def test_bad_regex_is_ignored(self) -> None:
        self.assertFalse(router._message_filtered(_cfg(ban_msgs_regex=["("]), _event("你好")))

    def test_empty_text_is_not_filtered(self) -> None:
        self.assertFalse(router._message_filtered(_cfg(ban_words=["广告"]), _event("")))


class _Config:
    def __init__(self, threshold: int) -> None:
        self.threshold = threshold

    def get_system(self) -> dict[str, object]:
        return {"chat": {"message_receive": {"image_parse_threshold": self.threshold}}}


def _voice_event() -> SimpleNamespace:
    segments = [SimpleNamespace(type="record", data={"url": "https://example.com/a.amr"})]
    event = SimpleNamespace(user_id="u1", group_id="g1", message=segments, message_id="m1")
    event.get_message = lambda: segments
    event.get_plaintext = lambda: ""
    return event


class IgnoredCommandTests(RouterUnitTests):
    """命令前缀消息既不触发回复，也不通过群历史入口保存。"""

    async def test_prefix_message_with_image_never_reaches_chat_or_history(self) -> None:
        config = {**SYSTEM_DEFAULTS["chat"], "command_prefixes": ["#", "gs", ""]}
        for group_id in (None, "group"):
            for text in ("#测试", "  #测试", "gs查询"):
                with self.subTest(group_id=group_id, text=text):
                    event = _event(text)
                    event.group_id = group_id
                    with (
                        patch.object(router, "should_process_fxbot_message", return_value=True),
                        patch.object(router, "_chat_cfg", return_value=config),
                        patch.object(router, "_match_context", return_value=True),
                        patch.object(router, "_should_record_context", return_value=True),
                        patch.object(router, "_has_image", return_value=True),
                        patch.object(adapter_module, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
                    ):
                        self.assertFalse(await router._ai_fallback_rule(object(), event))
                        self.assertFalse(await router._context_rule(object(), event))

    async def test_direct_history_entry_rejects_prefix_without_touching_storage(self) -> None:
        service = service_module.ChatService(ChatSessionStore())
        config = SimpleNamespace(get_system=lambda: {"chat": {**SYSTEM_DEFAULTS["chat"], "command_prefixes": ["#"]}})
        with (
            patch.object(service_module, "get_config_manager", return_value=config),
            patch.object(service, "_hydrate", new=AsyncMock()) as hydrate,
            patch.object(service_module.history_module, "record_group_message", new=AsyncMock()) as record,
        ):
            await service.record_group_message(
                "own", "user", "测试", "  #查询", is_group=True, images=["image"], has_image=True
            )
        hydrate.assert_not_awaited()
        record.assert_not_awaited()
        self.assertEqual(service.session_store.get("own"), [])


class VoiceRuleTests(RouterUnitTests):
    """纯语音没有文字，但要在适配阶段放行并转写。"""

    async def test_voice_only_message_reaches_adaptation(self) -> None:
        with (
            patch.object(router, "should_process_fxbot_message", return_value=True),
            patch.object(router, "_chat_cfg", return_value={"enabled": True, "command_prefixes": ["/"]}),
            patch.object(router, "_match_context", return_value=True),
        ):
            self.assertTrue(await router._ai_fallback_rule(object(), _voice_event()))

    async def test_empty_message_is_still_rejected(self) -> None:
        event = _voice_event()
        event.message = []
        event.get_message = lambda: []
        with (
            patch.object(router, "should_process_fxbot_message", return_value=True),
            patch.object(router, "_chat_cfg", return_value={"enabled": True, "command_prefixes": ["/"]}),
            patch.object(router, "_match_context", return_value=True),
        ):
            self.assertFalse(await router._ai_fallback_rule(object(), event))

    async def test_context_records_voice_transcription(self) -> None:
        recorded: dict[str, object] = {}

        async def record(_session_id: str, _user_id: str, _user_name: str, text: str, **_kwargs: object) -> None:
            recorded["text"] = text

        service = SimpleNamespace(
            session_store=ChatSessionStore(),
            record_group_message=AsyncMock(side_effect=record),
        )
        with (
            patch.object(router, "chat_service", service),
            patch.object(adapter_module, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
            patch.object(router, "transcribe_event", new=AsyncMock(return_value="[语音: 你好]")),
            patch.object(router, "collect_mentions", new=AsyncMock(return_value=[])),
            patch.object(router, "default_debounce_registry", debounce.DebounceRegistry()),
            patch.object(router.focus_mode_manager, "enabled", return_value=False),
            patch.object(router.focus_mode_manager, "attached", return_value=False),
        ):
            await router._handle_context(object(), _voice_event())
        self.assertEqual(recorded["text"], "[语音: 你好]")


def _request(count: int) -> ChatRequest:
    return ChatRequest(
        session_id="tenant",
        user_id="u1",
        group_id="g1",
        text="看图",
        turn_id="turn",
        dedupe_key="msg:1",
        trigger_kind="mention",
        images=[f"img-{index}" for index in range(count)],
    )


class ContextHistoryLimitTests(RouterUnitTests):
    """无需回复的群消息按群聊上限记录上下文。"""

    async def test_context_uses_session_history_limit(self) -> None:
        for is_group, private_limit, group_limit in ((False, 2, 0), (True, 0, 2), (False, 0, 2), (True, 2, 0)):
            with self.subTest(is_group=is_group, private_limit=private_limit, group_limit=group_limit):
                service = service_module.ChatService(ChatSessionStore())
                cfg = {
                    **SYSTEM_DEFAULTS["chat"],
                    "enabled": True,
                    "private_enabled": True,
                    "max_private_context_size": private_limit,
                    "max_context_size": group_limit,
                }
                limit = group_limit if is_group else private_limit
                with (
                    patch.object(router, "chat_service", service),
                    patch.object(router, "_chat_cfg", return_value=cfg),
                    patch.object(router, "should_process_fxbot_message", return_value=True),
                    patch.object(router, "_persona_trigger", return_value=""),
                    patch.object(router, "_proactive_gate", new=AsyncMock(return_value=False)),
                    patch.object(router, "collect_mentions", new=AsyncMock(return_value=[])),
                    patch.object(router, "default_debounce_registry", debounce.DebounceRegistry()),
                    patch.object(router.focus_mode_manager, "enabled", return_value=False),
                    patch.object(router.focus_mode_manager, "attached", return_value=False),
                    patch.object(adapter_module, "get_platform_adapter", return_value=OneBotV12MessageAdapter()),
                    patch.object(
                        service_module,
                        "get_config_manager",
                        return_value=SimpleNamespace(get_system=lambda: {"chat": cfg}),
                    ),
                    patch.object(service_module.memory_state, "is_ready", return_value=False),
                    patch.object(service, "_hydrate", new=AsyncMock()),
                ):
                    for message_id in ("1", "2", "3"):
                        event = _event(f"消息{message_id}")
                        event.group_id = "g1" if is_group else None
                        event.message_id = message_id
                        self.assertEqual(await router._context_rule(object(), event), is_group and limit > 0)
                        if is_group and limit > 0:
                            await router._handle_context(object(), event)
                    history = service.session_store.get(router.event_identity(object(), event).session_id)
                self.assertEqual(len(history), 3 if is_group and limit > 0 else 0)
                if is_group and limit > 0:
                    self.assertTrue(history[0]["content"].endswith("消息1"))
                    self.assertTrue(history[-1]["content"].endswith("消息3"))


class ImageThresholdTests(unittest.IsolatedAsyncioTestCase):
    """超过上限时不调用视觉 Provider。"""

    async def test_skips_when_over_threshold(self) -> None:
        with (
            patch.object(service_module, "get_config_manager", return_value=_Config(5)),
            patch.object(service_module, "fallback_provider", side_effect=AssertionError("不应调用")),
        ):
            self.assertEqual(await service_module._describe_images(_request(6)), [])

    async def test_describes_within_threshold(self) -> None:
        class _Provider:
            async def __aenter__(self) -> object:
                return object()

            async def __aexit__(self, *args: object) -> None:
                return None

        with (
            patch.object(service_module, "get_config_manager", return_value=_Config(5)),
            patch.object(service_module, "fallback_provider", return_value=_Provider()),
            patch.object(service_module, "describe_images", return_value=["一只猫"]) as describe,
        ):
            self.assertEqual(await service_module._describe_images(_request(5)), ["一只猫"])
        describe.assert_awaited_once()


class _VisualConfig:
    def __init__(self, visual: dict[str, object]) -> None:
        self.visual = visual

    def get_system(self) -> dict[str, object]:
        return {"chat": {"visual": self.visual}}


async def _later(value: list[str], delay: float = 0.0) -> list[str]:
    if delay:
        await asyncio.sleep(delay)
    return value


class ImageDescriptionPipelineTests(unittest.IsolatedAsyncioTestCase):
    """图片入历史后，后续生成请求不再重复调用视觉模型。"""

    async def test_recorded_picture_is_recognized_once_across_generation_requests(self) -> None:
        config = deepcopy(SYSTEM_DEFAULTS["chat"])
        config["model_routing"]["vision"] = "test-vision"
        config["response_splitter"]["enable"] = False
        request = _request(1)
        request.message_id = "picture"
        request.metadata["history_recorded"] = True
        service = service_module.ChatService(ChatSessionStore())
        provider = SimpleNamespace(chat=AsyncMock(return_value=LLMResponse(content="看到一只猫")), provider_id="test")

        @asynccontextmanager
        async def lease(*args):
            yield provider

        with (
            patch.object(
                service_module, "get_config_manager", return_value=SimpleNamespace(get_system=lambda: {"chat": config})
            ),
            patch.object(service_module.memory_state, "is_ready", return_value=False),
            patch.object(service, "_hydrate", AsyncMock()),
            patch.object(service_module.history_module, "fallback_provider", lease),
            patch.object(
                service_module.history_module, "describe_sources", AsyncMock(return_value=["一只猫"])
            ) as describe,
            patch.object(
                service_module, "_describe_images", AsyncMock(side_effect=AssertionError("重复识图"))
            ) as repeated,
            patch.object(service_module, "_memory_block", AsyncMock(return_value="")),
            patch.object(service_module.generator, "allowed_tools", AsyncMock(return_value=(set(), []))),
            patch.object(service, "_persist_turn_start", AsyncMock(return_value=None)),
            patch.object(service, "_persist_turn_end", AsyncMock(return_value=None)),
        ):
            await service.record_group_message(
                request.session_id,
                request.user_id,
                request.user_name,
                request.text,
                is_group=request.is_group,
                images=request.images,
                message_id=request.message_id,
            )
            for _ in range(2):
                await service._process_with_provider(request, None, provider, "test", focus_instruction="描述图片")
            self.assertEqual(provider.chat.await_count, 2)
            self.assertIn("一只猫", str(provider.chat.await_args.kwargs["messages"]))
            describe.assert_awaited_once()
            repeated.assert_not_awaited()


class ImageDescriptionReuseTests(unittest.TestCase):
    """已识别图片在同一消息的规划和回复阶段复用，不混用其他图片或消息。"""

    def test_current_history_description_is_reused(self) -> None:
        request = _request(1)
        request.message_id = "picture"
        history = [{"role": "user", "_source_ids": ["picture"], "_image_descriptions": ["一只猫"]}]
        self.assertEqual(service_module._cached_image_descriptions(request, history), ["一只猫"])
        request.message_id = "different-picture"
        self.assertIsNone(service_module._cached_image_descriptions(request, history))

    def test_replyer_can_reuse_current_turn_descriptions(self) -> None:
        request = _request(1)
        request.metadata["recognized_images"] = {"sources": list(request.images), "descriptions": ["一只猫"]}
        self.assertEqual(service_module._cached_image_descriptions(request, []), ["一只猫"])
        request.images = ["different-image"]
        self.assertIsNone(service_module._cached_image_descriptions(request, []))

    def test_failed_or_ambiguous_history_description_is_not_reused(self) -> None:
        request = _request(1)
        request.message_id = "picture"
        history = [{"role": "user", "_source_ids": ["picture"], "_image_descriptions": [""]}]
        self.assertIsNone(service_module._cached_image_descriptions(request, history))
        history[0]["_image_descriptions"] = ["一只猫"]
        request.batched = True
        self.assertIsNone(service_module._cached_image_descriptions(request, history))


class WaitDescriptionsTests(unittest.IsolatedAsyncioTestCase):
    """生成前取识图结果；超时、失败或不等待时保留原图。"""

    async def test_returns_descriptions(self) -> None:
        task = asyncio.create_task(_later(["一只猫"]))
        with patch.object(service_module, "_image_wait_seconds", return_value=1.0):
            self.assertEqual(await service_module._wait_image_descriptions(task), ["一只猫"])

    async def test_no_wait_keeps_images(self) -> None:
        self.assertEqual(await service_module._wait_image_descriptions(None), [])
        task = asyncio.create_task(_later(["一只猫"]))
        with patch.object(service_module, "_image_wait_seconds", return_value=0.0):
            self.assertEqual(await service_module._wait_image_descriptions(task), [])
        task.cancel()

    async def test_timeout_keeps_images(self) -> None:
        task = asyncio.create_task(_later(["一只猫"], delay=0.5))
        with patch.object(service_module, "_image_wait_seconds", return_value=0.01):
            self.assertEqual(await service_module._wait_image_descriptions(task), [])
        task.cancel()

    async def test_failure_keeps_images(self) -> None:
        async def boom() -> list[str]:
            raise RuntimeError("vision down")

        task = asyncio.create_task(boom())
        with patch.object(service_module, "_image_wait_seconds", return_value=1.0):
            self.assertEqual(await service_module._wait_image_descriptions(task), [])


class VisualConfigTests(unittest.TestCase):
    """图片数量、大小与画质。"""

    def _helpers(self, visual: dict[str, object]):
        with patch.object(adapter_module, "get_config_manager", return_value=_VisualConfig(visual)):
            return (
                adapter_module._max_image_count(),
                adapter_module._max_image_bytes(),
                adapter_module._max_image_side(),
            )

    def test_defaults(self) -> None:
        self.assertEqual(self._helpers({}), (64, 16 * 1024 * 1024, 512))

    def test_configured_values(self) -> None:
        self.assertEqual(
            self._helpers({"max_image_num": 2, "max_image_size_mb": 1.5, "image_quality": "normal"}),
            (2, int(1.5 * 1024 * 1024), 512),
        )

    def test_zero_means_unlimited(self) -> None:
        self.assertEqual(self._helpers({"max_image_num": 0, "max_image_size_mb": 0})[0:2], (0, None))

    def test_invalid_values_fall_back(self) -> None:
        self.assertEqual(
            self._helpers({"max_image_num": -1, "max_image_size_mb": "big", "image_quality": "nope"}),
            (64, 16 * 1024 * 1024, 512),
        )

    def test_read_failure_falls_back(self) -> None:
        with patch.object(adapter_module, "get_config_manager", side_effect=RuntimeError("bad")):
            self.assertEqual(adapter_module._max_image_count(), 64)

    def test_validation(self) -> None:
        validate_system_config(
            {"chat": {"visual": {"image_quality": "normal", "max_image_num": 0, "max_image_size_mb": 0}}}
        )
        for visual in (
            {"image_quality": "ultra"},
            {"max_image_num": -1},
            {"max_image_num": 1.5},
            {"max_image_size_mb": "big"},
            {"wait_image_recognize_max_time": -1},
            {"handle_oversized_images": "yes"},
            {"oversized_image_handle_method": "keep"},
        ):
            with self.subTest(visual=visual), self.assertRaises(ValueError):
                validate_system_config({"chat": {"visual": visual}})


class OversizedImageTests(unittest.IsolatedAsyncioTestCase):
    """过大图片按配置压缩或丢弃。"""

    async def test_model_data_url_can_be_loaded_again_for_history_description(self) -> None:
        data = self._noisy_png(24)
        with patch.object(adapter_module, "_fetch_remote_image", new=AsyncMock()) as fetch:
            first = await adapter_module.load_images([data])
            self.assertEqual(len(first), 1)
            self.assertEqual(await adapter_module.load_image_bytes(first[0]), data)
            self.assertEqual(await adapter_module.load_images(first), first)
        fetch.assert_not_awaited()

    async def test_inline_images_enforce_type_encoding_and_size(self) -> None:
        import base64

        for source in ("data:text/plain;base64,SGVsbG8=", "data:image/png,raw", "data:image/png;base64,???"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                await adapter_module.load_image_bytes(source)
        source = "data:image/png;base64," + base64.b64encode(self._noisy_png(24)).decode("ascii")
        with self.assertRaises(adapter_module.ResponseTooLargeError):
            await adapter_module.load_image_bytes(source, limit=100)

    @staticmethod
    def _noisy_png(side: int = 600) -> bytes:
        from io import BytesIO

        from PIL import Image

        buffer = BytesIO()
        Image.effect_noise((side, side), 120).convert("RGB").save(buffer, format="PNG")
        return buffer.getvalue()

    def _config(self, **visual: object) -> _VisualConfig:
        return _VisualConfig({"image_quality": "high", **visual})

    async def test_compress_oversized_to_limit(self) -> None:
        data = self._noisy_png()
        limit = 20000
        with patch.object(
            adapter_module,
            "get_config_manager",
            return_value=self._config(
                max_image_size_mb=limit / 1024 / 1024,
                handle_oversized_images=True,
                oversized_image_handle_method="compress",
            ),
        ):
            self.assertEqual(adapter_module._download_limit(limit, compress_oversized=True), 32 * 1024 * 1024)
            loaded = await adapter_module._load_image(data)
        self.assertLessEqual(len(loaded), limit)
        self.assertTrue(loaded.startswith(b"\xff\xd8\xff"))

    async def test_remote_oversized_is_compressed(self) -> None:
        data = self._noisy_png()
        limit = 20000
        with (
            patch.object(
                adapter_module,
                "get_config_manager",
                return_value=self._config(
                    max_image_size_mb=limit / 1024 / 1024,
                    handle_oversized_images=True,
                    oversized_image_handle_method="compress",
                ),
            ),
            patch.object(adapter_module, "_fetch_remote_image", new=AsyncMock(return_value=data)),
        ):
            loaded = await adapter_module._load_image("https://example.com/a.png")
        self.assertLessEqual(len(loaded), limit)
        self.assertTrue(loaded.startswith(b"\xff\xd8\xff"))

    async def test_discard_oversized(self) -> None:
        data = self._noisy_png(64)
        limit = 100
        with (
            patch.object(
                adapter_module,
                "get_config_manager",
                return_value=self._config(
                    max_image_size_mb=limit / 1024 / 1024,
                    handle_oversized_images=True,
                    oversized_image_handle_method="discard",
                ),
            ),
            self.assertRaises(adapter_module.ResponseTooLargeError),
        ):
            await adapter_module._load_image(data)

    async def test_handle_disabled_skips_oversized(self) -> None:
        data = self._noisy_png(64)
        limit = 100
        with (
            patch.object(
                adapter_module,
                "get_config_manager",
                return_value=self._config(
                    max_image_size_mb=limit / 1024 / 1024,
                    handle_oversized_images=False,
                ),
            ),
            self.assertRaises(adapter_module.ResponseTooLargeError),
        ):
            await adapter_module._load_image(data)

    async def test_emoji_collection_never_compresses(self) -> None:
        data = self._noisy_png(64)
        with (
            patch.object(
                adapter_module,
                "get_config_manager",
                return_value=self._config(max_image_size_mb=100 / 1024 / 1024),
            ),
            self.assertRaises(adapter_module.ResponseTooLargeError),
        ):
            await adapter_module.load_image_bytes(data, limit=100)

    def test_image_wait_seconds(self) -> None:
        with patch.object(service_module, "get_config_manager", return_value=_VisualConfig({})):
            self.assertEqual(service_module._image_wait_seconds(), 32.0)
        with patch.object(
            service_module,
            "get_config_manager",
            return_value=_VisualConfig({"wait_image_recognize_max_time": 0}),
        ):
            self.assertEqual(service_module._image_wait_seconds(), 0.0)


class MessageReceiveConfigTests(unittest.TestCase):
    """默认值与校验。"""

    def test_defaults(self) -> None:
        settings = SYSTEM_DEFAULTS["chat"]["message_receive"]
        self.assertEqual(settings["image_parse_threshold"], 5)
        self.assertEqual(settings["ban_words"], [])

    def test_validation(self) -> None:
        validate_system_config({"chat": {"message_receive": {"ban_msgs_regex": [r"\d+"]}}})
        for settings in (
            {"image_parse_threshold": -1},
            {"ban_words": "广告"},
            {"ban_msgs_regex": ["("]},
        ):
            with self.subTest(settings=settings), self.assertRaises(ValueError):
                validate_system_config({"chat": {"message_receive": settings}})
