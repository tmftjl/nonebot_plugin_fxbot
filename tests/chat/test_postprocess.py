"""回复后处理与时间感知测试。"""

from __future__ import annotations

import unittest
from datetime import timezone, timedelta
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat import service as service_module
from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import state as memory_state
from src.plugins.nonebot_plugin_fxbot.chat.service import ChatService
from src.plugins.nonebot_plugin_fxbot.chat.session import ChatSessionStore
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse, provider_manager
from src.plugins.nonebot_plugin_fxbot.chat.behavior.postprocess import postprocess_reply

from .support import ReplyPipelineTests

_CST = timezone(timedelta(hours=8))


def _request() -> ChatRequest:
    """构造最小私聊请求。"""
    return ChatRequest(
        session_id="tenant",
        user_id="user",
        group_id=None,
        text="你好",
        turn_id="turn-1",
        dedupe_key="msg:1",
        trigger_kind="private",
    )


class PostprocessReplyTests(unittest.TestCase):
    """只改发送文本，不碰正常正文。"""

    def test_strips_speaker_and_answer_prefix(self) -> None:
        self.assertEqual(postprocess_reply("友利奈绪(3226795632): 答：你好"), "你好")
        self.assertEqual(postprocess_reply("用户3226795632：你好"), "你好")

    def test_keeps_plain_colon_text(self) -> None:
        self.assertEqual(postprocess_reply("注意: 明天见"), "注意: 明天见")

    def test_removes_code_fences_and_extra_blank_lines(self) -> None:
        text = "看这个：\n```python\nprint(1)\n```\n\n\n结束（以上）"
        self.assertEqual(postprocess_reply(text), "看这个：\nprint(1)\n\n结束")

    def test_empty_text_stays_empty(self) -> None:
        self.assertEqual(postprocess_reply("  （以上）  "), "")


class _Config:
    """按测试需要返回行为开关。"""

    def __init__(
        self,
        behavior: dict[str, object] | None = None,
        post_process: dict[str, object] | None = None,
    ) -> None:
        self.behavior = behavior or {}
        self.post_process = post_process or {}

    def get_system(self) -> dict[str, object]:
        return {
            "chat": {
                "max_context_size": 10,
                "persona": "",
                "provider": "",
                "behavior": self.behavior,
                "response_post_process": self.post_process,
                "response_splitter": {"enable": False},
            }
        }


class _Provider:
    """记录提示词并返回带噪声的回复。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def chat(self, messages: list[dict[str, object]], tools: object = None, **kwargs) -> LLMResponse:
        self.messages = messages
        return LLMResponse(content="友利奈绪(3226795632): 答：你好（以上）")


class PostprocessWiringTests(ReplyPipelineTests):
    """清理文本进入历史，generated_text 保留模型原文。"""

    async def _process(
        self,
        behavior: dict[str, object] | None = None,
        post_process: dict[str, object] | None = None,
    ) -> tuple[object, _Provider, ChatSessionStore]:
        provider = _Provider()

        @asynccontextmanager
        async def lease(_name: str):
            yield provider

        store = ChatSessionStore()
        with (
            patch.object(service_module, "get_config_manager", return_value=_Config(behavior, post_process)),
            patch.object(provider_manager, "lease", lease),
            patch.object(memory_state, "is_ready", return_value=False),
            patch.object(service_module, "memory_block", new=AsyncMock(return_value="")),
        ):
            response = await ChatService(store).process(_request())
        return response, provider, store

    async def test_clean_text_reaches_history(self) -> None:
        response, _provider, store = await self._process({})

        self.assertEqual(response.text, "你好")
        self.assertEqual(response.metadata["reply_parts"], ["你好"])
        with patch.object(memory_state, "is_ready", return_value=False):
            await ChatService(store).mark_sent(_request(), response)
        self.assertEqual(store.get("tenant")[-1], {"role": "assistant", "content": "你好"})

    async def test_replyer_uses_source_reference_instruction(self) -> None:
        _response, provider, _store = await self._process({})
        self.assertIn("你可以参考【回复信息参考】中的信息", str(provider.messages[0]["content"]))

    async def test_post_process_can_be_disabled(self) -> None:
        response, _provider, _store = await self._process(post_process={"enable_response_post_process": False})

        self.assertEqual(response.text, "友利奈绪(3226795632): 答：你好（以上）")

    async def test_generated_text_keeps_raw_model_output(self) -> None:
        with patch.object(service_module.ChatService, "_persist_turn_end", new=AsyncMock()) as persist:
            await self._process({})

        self.assertEqual(persist.await_args.args[-1], "友利奈绪(3226795632): 答：你好（以上）")


if __name__ == "__main__":
    unittest.main()
