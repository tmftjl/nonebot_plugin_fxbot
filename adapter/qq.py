"""QQ 官方适配器消息适配。"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
from pathlib import Path
import time
from typing import Any

from nonebot.adapters import Bot
from nonebot.adapters.qq import Bot as QQBot
from nonebot.adapters.qq.event import GroupMemberAddEvent
from nonebot.adapters.qq.models import SetMemberMuteState

from .bot import PlatformAdapter, UnsupportedCapability
from .message_utils import _image_bytes


class QQOfficialMessageAdapter(PlatformAdapter):
    """QQ 官方消息适配器。"""

    def extract_group_member_add(self, event: Any) -> dict[str, str] | None:
        """识别 QQ 官方新成员入群通知。"""
        if not isinstance(event, GroupMemberAddEvent):
            return None
        return {
            "group_id": str(event.group_openid),
            "user_id": str(event.member_openid),
        }

    def match(self, bot: Bot) -> bool:
        return isinstance(bot, QQBot)

    def is_mention_segment(self, segment):
        return getattr(segment, "type", "") in {"mention", "mention_user"}

    def mention_target(self, segment):
        if not self.is_mention_segment(segment):
            return None
        data = getattr(segment, "data", {}) or {}
        return str(data.get("user_id") or data.get("id") or data.get("target") or "") or None

    def user_avatar(self, bot, user_id):
        return f"https://q.qlogo.cn/qqapp/{getattr(bot, 'self_id', '')}/{user_id}/100"

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
                raise ValueError(
                    "当前 QQ 官方适配器版本不支持本地视频发送，请升级 nonebot-adapter-qq"
                )
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
            return await bot.send_to_group(
                group_openid=str(target["group_openid"]), message=message
            )
        if target.get("user_openid") is not None:
            return await bot.send_to_c2c(openid=str(target["user_openid"]), message=message)
        raise RuntimeError("无法识别消息目标")

    async def send_group_message(self, bot, group_id, message):
        return await bot.send_to_group(group_openid=str(group_id), message=message)

    async def send_private_message(self, bot, user_id, message):
        return await bot.send_to_c2c(openid=str(user_id), message=message)

    async def get_group_info(self, bot, group_id):
        return await bot.get_group_info(group_id=str(group_id))

    async def get_group_member(self, bot, group_id, user_id):
        state = await bot.get_group_bot_state(group_id=str(group_id))
        bot_openid = getattr(getattr(bot, "self_info", None), "id", None)
        if str(user_id) in {str(bot.self_id), str(bot_openid)}:
            return {"user_id": str(user_id), "role": state.member_role}
        raise UnsupportedCapability("获取群成员信息")

    async def get_group_member_name(self, bot, group_id, user_id, event=None):
        for segment in getattr(event, "get_message", lambda: ())():
            data = getattr(segment, "data", {}) or {}
            target_id = str(data.get("user_id") or data.get("id") or data.get("target") or "")
            if target_id != str(user_id):
                continue
            name = str(data.get("username") or data.get("nickname") or "").strip()
            if name:
                return name
        raise UnsupportedCapability("QQ 消息段未提供目标昵称")

    async def get_group_member_role(self, bot, group_id, user_id):
        state = await bot.get_group_bot_state(group_id=str(group_id))
        bot_openid = getattr(getattr(bot, "self_info", None), "id", None)
        if str(user_id) in {str(bot.self_id), str(bot_openid)}:
            return str(state.member_role)
        raise UnsupportedCapability("获取群成员身份")

    async def get_group_mute_setting(self, bot, group_id):
        return await bot.get_group_mute_setting(group_id=str(group_id))

    async def get_muted_members(self, bot, group_id):
        setting = await self.get_group_mute_setting(bot, group_id)
        return [
            {
                "user_id": member.member_openid,
                "nickname": member.username,
                "role": "member",
                "remaining": max(0, int(member.mute_expire_at.timestamp() - time.time())),
                "mute_until": int(member.mute_expire_at.timestamp()),
            }
            for member in setting.members
        ]

    async def set_group_members_mute(self, bot, group_id, members):
        states = [
            member if isinstance(member, SetMemberMuteState) else SetMemberMuteState(**member)
            for member in members
        ]
        return await bot.set_group_members_mute(group_id=str(group_id), members=states)

    async def ban(self, bot, group_id, user_id, duration):
        operation = "del" if int(duration) <= 0 else "add"
        expire_at = None
        if operation != "del":
            expire_at = datetime.now(timezone.utc) + timedelta(seconds=int(duration))
        return await self.set_group_members_mute(
            bot,
            group_id,
            [{"op": operation, "member_openid": str(user_id), "mute_expire_at": expire_at}],
        )

    async def send_forward_messages(
        self, bot: Bot, event: Any, messages: list[Any], *, nickname: str = "FxBot"
    ) -> bool:
        """QQ 官方适配器按顺序发送转发消息。"""
        if not messages:
            return False
        for message in messages:
            await self.send_event(bot, event, message)
        return True
