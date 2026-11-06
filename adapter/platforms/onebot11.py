"""OneBot V11 消息适配。"""

from __future__ import annotations

import time
import asyncio
from typing import Any
from pathlib import Path

from nonebot import logger
from nonebot.adapters import Bot
from nonebot.adapters.onebot.v11 import Bot as OneBotV11Bot

from ..core.bot import ReplyInfo, RecallInfo, PlatformAdapter, UnsupportedCapability, _image_bytes


def _member_role(member: Any, user_id: str) -> str | None:
    """规范化接口字典或事件发送者对象，缺失身份时不推测。"""
    value = member.get if isinstance(member, dict) else lambda key: getattr(member, key, None)
    member_id = value("user_id")
    if member_id is not None and str(member_id) != str(user_id):
        return None
    role = str(value("role") or "")
    return role if role in {"owner", "admin", "member"} else None


class OneBotV11MessageAdapter(PlatformAdapter):
    """OneBot V11 消息适配器。"""

    def event_id(self, event: Any) -> str | None:
        """OneBot V11 不提供事件 ID。"""
        return None

    def event_sender_role(self, event: Any) -> str | None:
        """直接读取群消息发送者身份，不调用任何成员查询接口。"""
        if not self.event_group_id(event):
            return None
        return _member_role(getattr(event, "sender", None), self.event_user_id(event))

    def event_message_id(self, event: Any) -> str | None:
        """读取 OneBot V11 消息 ID。"""
        value = getattr(event, "message_id", None)
        return str(value) if value is not None else None

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

    def supports_reply(self) -> bool:
        return True

    def build_reply_segment(self, bot: Bot, message_id: int | str) -> Any:
        from nonebot.adapters.onebot.v11 import MessageSegment

        return MessageSegment.reply(str(message_id))

    def build_message(self, bot: Bot, segments: list[Any]) -> Any:
        from nonebot.adapters.onebot.v11 import Message

        return Message(segments)

    async def send_message_to_target(self, bot: Bot, target: dict[str, Any], message: Any) -> Any:
        if target.get("group_id") is not None:
            return await self._api(bot, "send_group_msg", group_id=int(target["group_id"]), message=message)
        if target.get("user_id") is not None:
            return await self._api(bot, "send_private_msg", user_id=int(target["user_id"]), message=message)
        raise RuntimeError("无法识别消息目标")

    async def send_file_to_target(self, bot: Bot, target: dict[str, Any], path: Path, name: str) -> Any:
        """OneBot V11 用 upload_*_file 发文件，要求 OneBot 实现能读到这个路径。"""
        if target.get("group_id") is not None:
            return await self._api(
                bot, "upload_group_file", group_id=int(target["group_id"]), file=str(path), name=name
            )
        if target.get("user_id") is not None:
            return await self._api(
                bot, "upload_private_file", user_id=int(target["user_id"]), file=str(path), name=name
            )
        raise RuntimeError("无法识别消息目标")

    async def _api(self, bot: Bot, name: str, **kwargs: Any) -> Any:
        method = getattr(bot, name, None)
        if callable(method):
            return await method(**kwargs)
        return await bot.call_api(name, **kwargs)

    async def delete_message(self, bot, message_id, *, group_id=None, user_id=None):
        return await self._api(bot, "delete_msg", message_id=message_id)

    async def get_message(self, bot, message_id):
        return await self._api(bot, "get_msg", message_id=message_id)

    async def get_forward_messages(self, bot, forward_id):
        """OneBot 实现里转发 ID 有的叫 id，有的叫 message_id。"""
        try:
            payload = await self._api(bot, "get_forward_msg", id=str(forward_id))
        except Exception:
            payload = await self._api(bot, "get_forward_msg", message_id=str(forward_id))
        return _parse_forward_nodes(payload)

    def extract_recall(self, event: Any) -> RecallInfo | None:
        """识别 group_recall / friend_recall 通知。"""
        notice = str(getattr(event, "notice_type", "") or "")
        message_id = getattr(event, "message_id", None)
        if not message_id:
            return None
        if notice == "group_recall":
            return RecallInfo(
                message_id=str(message_id),
                group_id=str(getattr(event, "group_id", "") or "") or None,
                user_id=str(getattr(event, "user_id", "") or "") or None,
                operator_id=str(getattr(event, "operator_id", "") or ""),
            )
        if notice == "friend_recall":
            return RecallInfo(
                message_id=str(message_id),
                user_id=str(getattr(event, "user_id", "") or "") or None,
            )
        return None

    def reply_sender_id(self, event: Any) -> str | None:
        """读取被引用消息的发送者 QQ 号。"""
        sender = getattr(getattr(event, "reply", None), "sender", None)
        user_id = sender.get("user_id") if isinstance(sender, dict) else getattr(sender, "user_id", None)
        return str(user_id) if user_id is not None else None

    async def get_reply_info(self, bot: Any, event: Any) -> ReplyInfo | None:
        """优先使用事件引用，缺失时一次获取原文和作者，避免只拿到正文。"""
        reply, message_id = self.reply_target(event)
        if message_id is None and not self.has_reply_reference(event):
            return None
        message = getattr(reply, "message", None)
        sender_id = self.reply_sender_id(event)
        sender_name = self.reply_sender_name(event)
        if message_id is not None and (message is None or not sender_id or not sender_name):
            try:
                result = await asyncio.wait_for(self.get_message(bot, message_id), timeout=2)
            except UnsupportedCapability:
                result = None
            except Exception:
                logger.opt(exception=True).warning("[adapter] 获取引用原文和作者失败")
                result = None
            if isinstance(result, dict):
                group_id = self.event_group_id(event)
                result_group = result.get("group_id")
                same_message = str(result.get("message_id", message_id)) == str(message_id)
                same_group = result_group is None or str(result_group) == str(group_id)
                same_kind = result.get("message_type") not in {"group", "private"} or result["message_type"] == (
                    "group" if group_id else "private"
                )
                if same_message and same_group and same_kind:
                    sender = result.get("sender") or {}
                    if isinstance(sender, dict):
                        sender_id = sender_id or str(sender.get("user_id") or "") or None
                        sender_name = sender_name or str(sender.get("card") or sender.get("nickname") or "")
                    if message is None:
                        from nonebot.adapters.onebot.v11 import MessageSegment

                        raw_message = result.get("message") or []
                        if isinstance(raw_message, list):
                            raw_message = [
                                MessageSegment(type=str(segment["type"]), data=dict(segment.get("data") or {}))
                                if isinstance(segment, dict)
                                else segment
                                for segment in raw_message
                            ]
                        message = self.build_message(bot, raw_message)
                else:
                    logger.debug("[adapter] 引用查询结果不属于目标消息或会话，忽略该结果")
        return ReplyInfo(
            message_id=message_id,
            sender_id=sender_id,
            sender_name=sender_name,
            image_sources=self.extract_raw_image_sources(message),
            texts=self.extract_raw_texts(message),
        )

    async def get_group_info(self, bot, group_id):
        return await self._api(bot, "get_group_info", group_id=int(group_id))

    async def get_group_member(self, bot, group_id, user_id):
        return await self._api(bot, "get_group_member_info", group_id=int(group_id), user_id=int(user_id))

    async def get_group_member_name(self, bot, group_id, user_id, event=None):
        member = await self.get_group_member(bot, group_id, user_id)
        name = str(member.get("nickname") or "").strip()
        if not name:
            raise RuntimeError("OneBot get_group_member_info 未返回 nickname")
        return name

    async def get_group_member_role(self, bot, group_id, user_id):
        """严格读取目标成员的真实身份，不把缺失身份当作普通成员。"""
        member = await self.get_group_member(bot, group_id, user_id)
        if not isinstance(member, dict):
            return None
        return _member_role(member, user_id)

    async def get_bot_group_role(self, bot, group_id):
        """查询机器人自己，不能拿当前发言人的角色代替。"""
        return await self.get_group_member_role(bot, group_id, str(bot.self_id))

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
            MessageSegment.node_custom(user_id=user_id, nickname=nickname, content=message) for message in messages
        ]

        group_id = getattr(event, "group_id", None)
        if self.event_is_group(event) and group_id is not None:
            await self._api(bot, "send_group_forward_msg", group_id=int(group_id), messages=nodes)
            return True

        user_id_value = getattr(event, "user_id", None)
        if self.event_is_private(event) and user_id_value is not None:
            await self._api(bot, "send_private_forward_msg", user_id=int(user_id_value), messages=nodes)
            return True
        return False


def _segments(content: Any) -> list[Any]:
    """把转发节点的内容归一成消息段列表。"""
    if content is None:
        return []
    if isinstance(content, str):
        return [{"type": "text", "data": {"text": content}}]
    try:
        return list(content)
    except TypeError:
        return []


def _field(segment: Any, key: str) -> Any:
    if isinstance(segment, dict):
        return segment.get(key)
    return getattr(segment, key, None)


def _node_text(segment: Any) -> str:
    kind = str(_field(segment, "type") or "")
    data = _field(segment, "data") or {}
    if not isinstance(data, dict):
        data = {}
    if kind == "text":
        return str(data.get("text") or "")
    if kind == "image":
        return "[图片]"
    if kind in {"record", "voice", "audio"}:
        return "[语音]"
    if kind == "at":
        return f"@{data.get('qq') or ''}"
    if kind in {"face", "mface"}:
        return "[表情]"
    if kind == "forward":
        return "[嵌套转发]"
    return ""


def _parse_forward_nodes(payload: Any) -> list[dict[str, Any]]:
    """把 get_forward_msg 的结果归一成昵称、正文和嵌套转发 ID。"""
    raw_nodes = payload.get("messages") if isinstance(payload, dict) else payload
    if not isinstance(raw_nodes, (list, tuple)):
        return []
    nodes: list[dict[str, Any]] = []
    for item in raw_nodes:
        data = item.get("data") if isinstance(item, dict) and isinstance(item.get("data"), dict) else item
        if not isinstance(data, dict):
            continue
        raw_sender = data.get("sender")
        sender: dict[str, Any] = raw_sender if isinstance(raw_sender, dict) else {}
        content = data.get("message")
        if content is None:
            content = data.get("content")
            if content is None:
                content = data.get("raw_message")
        parts: list[str] = []
        nested: list[str] = []
        for segment in _segments(content):
            if str(_field(segment, "type") or "") == "forward":
                seg_data = _field(segment, "data")
                forward_id = seg_data.get("id") or seg_data.get("forward_id") if isinstance(seg_data, dict) else None
                if isinstance(forward_id, str) and forward_id:
                    nested.append(forward_id)
            parts.append(_node_text(segment))
        nodes.append(
            {
                "name": str(sender.get("card") or sender.get("nickname") or sender.get("user_name") or ""),
                "user_id": str(sender.get("user_id") or ""),
                "text": "".join(parts).strip(),
                "forwards": nested,
            }
        )
    return nodes
