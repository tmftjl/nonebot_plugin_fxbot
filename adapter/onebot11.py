"""OneBot V11 消息适配。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from nonebot.adapters import Bot

from .message import MessageAdapter, _image_bytes, register_message_adapter
from .support import event_is_group, event_is_private, has_onebot_v11, is_onebot_v11


@register_message_adapter
class OneBotV11MessageAdapter(MessageAdapter):
    """OneBot V11 消息适配器。"""

    def match(self, bot: Bot) -> bool:
        return is_onebot_v11(bot)

    @staticmethod
    def message_segment_class():
        """获取 OneBot V11 MessageSegment 类。"""
        if not has_onebot_v11():
            return None
        try:
            from nonebot.adapters.onebot.v11 import MessageSegment
        except Exception:
            return None
        return MessageSegment

    def build_segment(self, bot: Bot, seg_type: str, data: Any = None) -> Any:
        from nonebot.adapters.onebot.v11 import MessageSegment

        if seg_type == "text":
            return MessageSegment.text(str(data))
        if seg_type == "at":
            return MessageSegment.at(str(data))
        if seg_type == "image":
            if isinstance(data, str) and data.startswith(("http://", "https://", "base64://")):
                return MessageSegment.image(data)
            return MessageSegment.image(_image_bytes(data))
        if seg_type == "record":
            return MessageSegment.record(_image_bytes(data) if isinstance(data, Path) else data)
        if seg_type == "video":
            if isinstance(data, Path):
                return MessageSegment.video(data.resolve().as_uri())
            return MessageSegment.video(data)
        raise ValueError(f"不支持的消息段类型: {seg_type}")

    def build_message(self, bot: Bot, segments: list[Any]) -> Any:
        from nonebot.adapters.onebot.v11 import Message

        return Message(segments)

    async def send_message_to_target(self, bot: Bot, target: dict[str, Any], message: Any) -> Any:
        if target.get("group_id") is not None:
            group_id = int(target["group_id"])
            if hasattr(bot, "send_group_msg"):
                return await bot.send_group_msg(group_id=group_id, message=message)
            return await bot.call_api("send_group_msg", group_id=group_id, message=message)
        if target.get("user_id") is not None:
            user_id = int(target["user_id"])
            if hasattr(bot, "send_private_msg"):
                return await bot.send_private_msg(user_id=user_id, message=message)
            return await bot.call_api("send_private_msg", user_id=user_id, message=message)
        raise RuntimeError("无法识别消息目标")

    async def send_forward_messages(self, bot: Bot, event: Any, messages: list[Any], *, nickname: str = "FxBot") -> bool:
        """发送 OneBot V11 合并转发消息。"""
        from nonebot.adapters.onebot.v11 import MessageSegment

        if not hasattr(MessageSegment, "node_custom"):
            return False

        user_id_raw = str(getattr(bot, "self_id", "0") or "0")
        user_id: int | str = int(user_id_raw) if user_id_raw.isdigit() else user_id_raw
        nodes = [
            MessageSegment.node_custom(user_id=user_id, nickname=nickname, content=message)
            for message in messages
        ]

        try:
            group_id = getattr(event, "group_id", None)
            if event_is_group(event) and group_id is not None:
                if hasattr(bot, "send_group_forward_msg"):
                    await bot.send_group_forward_msg(group_id=int(group_id), messages=nodes)
                else:
                    await bot.call_api("send_group_forward_msg", group_id=int(group_id), messages=nodes)
                return True

            user_id_value = getattr(event, "user_id", None)
            if event_is_private(event) and user_id_value is not None:
                if hasattr(bot, "send_private_forward_msg"):
                    await bot.send_private_forward_msg(user_id=int(user_id_value), messages=nodes)
                else:
                    await bot.call_api("send_private_forward_msg", user_id=int(user_id_value), messages=nodes)
                return True
        except Exception:
            return False
        return False
