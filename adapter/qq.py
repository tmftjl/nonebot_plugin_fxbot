"""QQ 官方适配器消息适配。"""

from __future__ import annotations

import base64
from pathlib import Path
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
        if seg_type == "video":
            if isinstance(data, str) and data.startswith(("http://", "https://")):
                return MessageSegment.video(data)
            if not hasattr(MessageSegment, "file_video"):
                raise ValueError("当前 QQ 官方适配器版本不支持本地视频发送，请升级 nonebot-adapter-qq")
            if isinstance(data, str) and data.startswith("base64://"):
                return MessageSegment.file_video(base64.b64decode(data[9:]))
            if isinstance(data, Path):
                return MessageSegment.file_video(data)
            if isinstance(data, bytes):
                return MessageSegment.file_video(data)
            if isinstance(data, str):
                path = Path(data)
                if path.exists():
                    return MessageSegment.file_video(path)
            raise ValueError("QQ 官方适配器视频发送仅支持 URL、base64、本地路径或字节数据")
        raise ValueError(f"不支持的消息段类型: {seg_type}")

    def build_message(self, bot: Bot, segments: list[Any]) -> Any:
        from nonebot.adapters.qq import Message

        return Message(segments)

    async def send_message_to_target(self, bot: Bot, target: dict[str, Any], message: Any) -> Any:
        if target.get("group_openid") is not None:
            return await bot.send_to_group(group_openid=str(target["group_openid"]), message=message)
        if target.get("user_openid") is not None:
            return await bot.send_to_c2c(openid=str(target["user_openid"]), message=message)
        raise RuntimeError("无法识别消息目标")

    async def send_forward_messages(self, bot: Bot, event: Any, messages: list[Any], *, nickname: str = "FxBot") -> bool:
        """QQ 官方适配器按顺序发送转发消息。"""
        if not messages:
            return False
        for message in messages:
            await bot.send(event, message)
        return True
