"""OneBot V11 消息适配。"""

from __future__ import annotations

from pathlib import Path
import time
from typing import Any

from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot

from .events import event_is_group, event_is_private
from .bot import PlatformAdapter
from .message_utils import _image_bytes


class OneBotV11MessageAdapter(PlatformAdapter):
    """OneBot V11 消息适配器。"""

    def extract_group_member_add(self, event: Any) -> dict[str, str] | None:
        """识别 OneBot V11 新成员入群通知。"""
        if str(getattr(event, "notice_type", "") or "") != "group_increase":
            return None
        group_id = str(getattr(event, "group_id", "") or "").strip()
        user_id = str(getattr(event, "user_id", "") or "").strip()
        if not group_id or not user_id:
            return None
        return {"group_id": group_id, "user_id": user_id}

    def match(self, bot: Bot) -> bool:
        return isinstance(bot, OneBotV11Bot)

    def is_mention_segment(self, segment):
        return getattr(segment, "type", "") == "at"

    def mention_target(self, segment):
        return (
            str((getattr(segment, "data", {}) or {}).get("qq") or "") or None
            if self.is_mention_segment(segment)
            else None
        )

    def user_avatar(self, bot, user_id):
        return f"http://q1.qlogo.cn/g?b=qq&nk={user_id}&s=640"

    def group_avatar(self, bot, group_id):
        return f"https://p.qlogo.cn/gh/{group_id}/{group_id}/"

    async def get_user(self, bot, user_id):
        return await self._api(bot, "get_stranger_info", user_id=int(user_id))

    @staticmethod
    def message_segment_class():
        """获取 OneBot V11 MessageSegment 类。"""
        from nonebot.adapters.onebot.v11 import MessageSegment

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
            return await self._api(
                bot, "send_group_msg", group_id=int(target["group_id"]), message=message
            )
        if target.get("user_id") is not None:
            return await self._api(
                bot, "send_private_msg", user_id=int(target["user_id"]), message=message
            )
        raise RuntimeError("无法识别消息目标")

    async def _api(self, bot: Bot, name: str, **kwargs: Any) -> Any:
        method = getattr(bot, name, None)
        if callable(method):
            return await method(**kwargs)
        return await bot.call_api(name, **kwargs)

    async def delete_message(self, bot, message_id):
        return await self._api(bot, "delete_msg", message_id=message_id)

    async def get_message(self, bot, message_id):
        return await self._api(bot, "get_msg", message_id=message_id)

    async def get_group_info(self, bot, group_id):
        return await self._api(bot, "get_group_info", group_id=int(group_id))

    async def get_group_member(self, bot, group_id, user_id):
        return await self._api(
            bot, "get_group_member_info", group_id=int(group_id), user_id=int(user_id)
        )

    async def get_group_member_name(self, bot, group_id, user_id, event=None):
        member = await self.get_group_member(bot, group_id, user_id)
        name = str(member.get("nickname") or "").strip()
        if not name:
            raise RuntimeError("OneBot get_group_member_info 未返回 nickname")
        return name

    async def get_group_member_role(self, bot, group_id, user_id):
        member = await self.get_group_member(bot, group_id, user_id)
        return str(member.get("role") or "member")

    async def get_group_members(self, bot, group_id):
        return await self._api(bot, "get_group_member_list", group_id=int(group_id))

    async def get_muted_members(self, bot, group_id):
        now = int(time.time())
        members = await self.get_group_members(bot, group_id)
        return [
            {
                "user_id": str(member.get("user_id")),
                "nickname": member.get("card") or member.get("nickname") or member.get("user_id"),
                "role": member.get("role", "member"),
                "remaining": until - now,
                "mute_until": until,
            }
            for member in members or []
            if (until := int(member.get("shut_up_timestamp", 0) or 0)) > now
        ]

    async def get_group_list(self, bot):
        return await self._api(bot, "get_group_list")

    async def ban(self, bot, group_id, user_id, duration):
        return await self._api(
            bot,
            "set_group_ban",
            group_id=int(group_id),
            user_id=int(user_id),
            duration=int(duration),
        )

    async def kick(self, bot, group_id, user_id, reject_add_request=False):
        return await self._api(
            bot,
            "set_group_kick",
            group_id=int(group_id),
            user_id=int(user_id),
            reject_add_request=reject_add_request,
        )

    async def whole_ban(self, bot, group_id, enable):
        return await self._api(bot, "set_group_whole_ban", group_id=int(group_id), enable=enable)

    async def set_admin(self, bot, group_id, user_id, enable):
        return await self._api(
            bot,
            "set_group_admin",
            group_id=int(group_id),
            user_id=int(user_id),
            enable=enable,
        )

    async def leave_group(self, bot, group_id):
        return await self._api(bot, "set_group_leave", group_id=int(group_id))

    async def set_special_title(self, bot, group_id, user_id, title):
        return await self._api(
            bot,
            "set_group_special_title",
            group_id=int(group_id),
            user_id=int(user_id),
            special_title=title,
        )

    async def set_essence(self, bot, message_id, enable):
        return await self._api(
            bot,
            "set_essence_msg" if enable else "delete_essence_msg",
            message_id=message_id,
        )

    async def like(self, bot, user_id, times=1):
        return await self._api(bot, "send_like", user_id=int(user_id), times=int(times))

    async def upload_file(self, bot, **kwargs):
        return await self._api(bot, "upload_file", **kwargs)

    async def send_forward_messages(
        self, bot: Bot, event: Any, messages: list[Any], *, nickname: str = "FxBot"
    ) -> bool:
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

        group_id = getattr(event, "group_id", None)
        if event_is_group(event) and group_id is not None:
            await self._api(bot, "send_group_forward_msg", group_id=int(group_id), messages=nodes)
            return True

        user_id_value = getattr(event, "user_id", None)
        if event_is_private(event) and user_id_value is not None:
            await self._api(
                bot, "send_private_forward_msg", user_id=int(user_id_value), messages=nodes
            )
            return True
        return False
