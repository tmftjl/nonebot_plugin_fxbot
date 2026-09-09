"""OneBot V12 message compatibility for FxBot."""

from __future__ import annotations

import base64
from typing import Any

from nonebot.adapters import Bot
from nonebot.adapters.onebot.v12 import Bot as OneBotV12Bot

from ..core.bot import PlatformAdapter, _image_bytes


class OneBotV12MessageAdapter(PlatformAdapter):
    @staticmethod
    def _file_id(data: Any) -> str:
        if isinstance(data, str) and data.startswith(("http://", "https://", "base64://")):
            return data
        encoded = base64.b64encode(_image_bytes(data)).decode("ascii")
        return f"base64://{encoded}"

    @staticmethod
    async def _api(bot: Bot, action: str, **params: Any) -> Any:
        return await bot.call_api(action, **params)

    def match(self, bot: Bot) -> bool:
        return isinstance(bot, OneBotV12Bot)

    def is_mention_segment(self, segment: Any) -> bool:
        return getattr(segment, "type", "") == "mention"

    def mention_target(self, segment: Any) -> str | None:
        if not self.is_mention_segment(segment):
            return None
        return str((getattr(segment, "data", {}) or {}).get("user_id") or "") or None

    @staticmethod
    def message_segment_class() -> type:
        from nonebot.adapters.onebot.v12 import MessageSegment

        return MessageSegment

    def build_segment(self, bot: Bot, seg_type: str, data: Any = None) -> Any:
        from nonebot.adapters.onebot.v12 import MessageSegment

        if seg_type == "text":
            return MessageSegment.text(str(data))
        if seg_type == "at":
            return MessageSegment.mention(str(data))
        if seg_type == "image":
            return MessageSegment.image(self._file_id(data))
        if seg_type == "record":
            return MessageSegment.voice(self._file_id(data))
        if seg_type == "video":
            return MessageSegment.video(self._file_id(data))
        if seg_type == "file":
            return MessageSegment.file(self._file_id(data))
        raise ValueError(f"unsupported OneBot V12 segment type: {seg_type}")

    def build_message(self, bot: Bot, segments: list[Any]) -> Any:
        from nonebot.adapters.onebot.v12 import Message

        return Message(segments)

    async def send_message_to_target(self, bot: Bot, target: dict[str, Any], message: Any) -> Any:
        if target.get("group_id") is not None:
            return await self._api(
                bot, "send_message", detail_type="group", group_id=str(target["group_id"]), message=message
            )
        if target.get("user_id") is not None:
            return await self._api(
                bot, "send_message", detail_type="private", user_id=str(target["user_id"]), message=message
            )
        raise RuntimeError("cannot identify OneBot V12 message target")

    async def get_user(self, bot: Bot, user_id: str) -> Any:
        return await self._api(bot, "get_user_info", user_id=str(user_id))

    async def get_group_info(self, bot: Bot, group_id: str) -> Any:
        return await self._api(bot, "get_group_info", group_id=str(group_id))

    async def get_group_member(self, bot: Bot, group_id: str, user_id: str) -> Any:
        return await self._api(bot, "get_group_member_info", group_id=str(group_id), user_id=str(user_id))

    async def get_group_member_name(self, bot: Bot, group_id: str, user_id: str, event: Any = None) -> str:
        member = await self.get_group_member(bot, group_id, user_id)
        name = str(member.get("member_name") or member.get("user_name") or "").strip()
        if not name:
            raise RuntimeError("OneBot V12 member info has no name")
        return name

    async def get_group_members(self, bot: Bot, group_id: str) -> Any:
        return await self._api(bot, "get_group_member_list", group_id=str(group_id))

    async def get_group_list(self, bot: Bot) -> Any:
        return await self._api(bot, "get_group_list")

    async def delete_message(self, bot: Bot, message_id: int | str, *, group_id=None, user_id=None) -> Any:
        return await self._api(bot, "delete_message", message_id=str(message_id))
