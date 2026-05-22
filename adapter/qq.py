"""QQ 官方适配器消息适配。"""

from __future__ import annotations

import base64
from typing import Any

from nonebot.adapters import Bot

from .message import MessageAdapter, _image_bytes, register_message_adapter
from .support import is_qq_official


@register_message_adapter
class QQOfficialMessageAdapter(MessageAdapter):
    """QQ 官方消息适配器。"""

    def match(self, bot: Bot) -> bool:
        return is_qq_official(bot)

    def build_segment(self, bot: Bot, seg_type: str, data: Any = None) -> Any:
        from nonebot.adapters.qq import MessageSegment

        if seg_type == "text":
            return MessageSegment.text(str(data))
        if seg_type == "at":
            return MessageSegment.mention_user(str(data))
        if seg_type == "image":
            if isinstance(data, str) and data.startswith(("http://", "https://")):
                return MessageSegment.image(data)
            if isinstance(data, str) and data.startswith("base64://"):
                return MessageSegment.file_image(base64.b64decode(data[9:]))
            return MessageSegment.file_image(_image_bytes(data))
        if seg_type == "record":
            if isinstance(data, str) and data.startswith(("http://", "https://")):
                return MessageSegment.audio(data)
            if isinstance(data, str) and data.startswith("base64://"):
                return MessageSegment.file_audio(base64.b64decode(data[9:]))
            return MessageSegment.file_audio(_image_bytes(data))
        raise ValueError(f"不支持的消息段类型: {seg_type}")

    def build_message(self, bot: Bot, segments: list[Any]) -> Any:
        from nonebot.adapters.qq import Message

        return Message(segments)

    async def send_text_to_target(self, bot: Bot, target: dict[str, Any], text: str) -> Any:
        if target.get("group_openid") is not None:
            message = self.build_message(bot, [self.build_segment(bot, "text", text)])
            return await bot.send_to_group(group_openid=str(target["group_openid"]), message=message)
        raise RuntimeError("无法识别消息目标")
