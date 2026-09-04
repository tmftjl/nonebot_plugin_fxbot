"""统一平台 Bot 外观（Facade）。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextvars import ContextVar
from typing import Any

from nonebot.exception import IgnoredException
from nonebot.log import logger


class PlatformError(IgnoredException):
    """平台调用失败。"""


class UnsupportedCapability(PlatformError):
    """当前平台不具备指定能力。"""


class PlatformAdapter(ABC):
    """所有平台适配器必须实现的能力边界。"""

    @abstractmethod
    def match(self, bot: Any) -> bool:
        """判断当前适配器是否支持指定 Bot。"""

    @abstractmethod
    def build_segment(self, bot: Any, segment_type: str, data: Any = None) -> Any:
        """构造平台消息段。"""

    @abstractmethod
    def build_message(self, bot: Any, segments: list[Any]) -> Any:
        """构造平台消息对象。"""

    def message_segment_class(self) -> type | None:
        return None

    @abstractmethod
    async def send_message_to_target(self, bot: Any, target: dict[str, Any], message: Any) -> Any:
        """向持久化目标发送消息。"""

    async def send_group_message(self, bot: Any, group_id: str, message: Any) -> Any:
        return await self.send_message_to_target(
            bot, {"group_id": group_id, "group_openid": group_id}, message
        )

    async def send_private_message(self, bot: Any, user_id: str, message: Any) -> Any:
        return await self.send_message_to_target(
            bot, {"user_id": user_id, "user_openid": user_id}, message
        )

    async def send_text_to_target(self, bot: Any, target: dict[str, Any], text: str) -> Any:
        message = self.build_message(bot, [self.build_segment(bot, "text", text)])
        return await self.send_message_to_target(bot, target, message)

    async def send_forward_messages(
        self, bot: Any, event: Any, messages: list[Any], *, nickname: str = "FxBot"
    ) -> bool:
        return await self._unsupported("转发消息")

    async def get_replied_message(self, bot: Any, message_id: int) -> Any:
        result = await self.get_message(bot, message_id)
        return result.get("message") if isinstance(result, dict) else None

    def extract_image_sources(self, message: Any) -> list[str]:
        return [
            source
            for segment in list(message or [])
            if getattr(segment, "type", "") == "image"
            and isinstance(
                source := (getattr(segment, "data", {}) or {}).get("url")
                or (getattr(segment, "data", {}) or {}).get("file"),
                str,
            )
            and source
            and not source.startswith("base64://")
        ]

    def extract_reply_message_id(self, message: Any) -> int | None:
        for segment in list(message or []):
            if getattr(segment, "type", "") != "reply":
                continue
            try:
                return int((getattr(segment, "data", {}) or {}).get("id"))
            except (TypeError, ValueError):
                return None
        return None

    def is_mention_segment(self, segment: Any) -> bool:
        return False

    def mention_target(self, segment: Any) -> str | None:
        return None

    def mention_targets(self, message: Any, ignored_targets: set[str] | None = None) -> list[str]:
        ignored = {str(target) for target in (ignored_targets or set())}
        return [
            target
            for segment in list(message or [])
            if (target := self.mention_target(segment)) and target not in ignored
        ]

    async def _unsupported(self, capability: str) -> Any:
        logger.info(f"[adapter] 当前适配器不支持能力: {capability}")
        raise UnsupportedCapability(capability)

    async def delete_message(self, bot: Any, message_id: int) -> Any:
        return await self._unsupported("撤回消息")

    async def get_message(self, bot: Any, message_id: int) -> Any:
        return await self._unsupported("获取消息")

    async def get_group_info(self, bot: Any, group_id: str) -> Any:
        return await self._unsupported("获取群信息")

    async def get_group_member(self, bot: Any, group_id: str, user_id: str) -> Any:
        return await self._unsupported("获取成员信息")

    async def get_group_member_role(self, bot: Any, group_id: str, user_id: str) -> str | None:
        member = await self.get_group_member(bot, group_id, user_id)
        return str(member.get("role") or member.get("member_role") or "") or None

    async def get_group_members(self, bot: Any, group_id: str) -> Any:
        return await self._unsupported("获取群成员列表")

    async def get_muted_members(self, bot: Any, group_id: str) -> list[dict[str, Any]]:
        return await self._unsupported("获取禁言列表")

    async def get_group_list(self, bot: Any) -> Any:
        return await self._unsupported("获取群列表")

    async def get_user(self, bot: Any, user_id: str) -> Any:
        return await self._unsupported("获取用户信息")

    def user_avatar(self, bot: Any, user_id: str) -> str | None:
        return None

    def group_avatar(self, bot: Any, group_id: str) -> str | None:
        return None

    async def ban(self, bot: Any, group_id: str, user_id: str, duration: int) -> Any:
        return await self._unsupported("禁言")

    async def kick(
        self, bot: Any, group_id: str, user_id: str, reject_add_request: bool = False
    ) -> Any:
        return await self._unsupported("踢人")

    async def whole_ban(self, bot: Any, group_id: str, enable: bool) -> Any:
        return await self._unsupported("全体禁言")

    async def get_group_mute_setting(self, bot: Any, group_id: str) -> Any:
        return await self._unsupported("获取群禁言状态")

    async def set_group_members_mute(self, bot: Any, group_id: str, members: list[Any]) -> Any:
        return await self._unsupported("设置群成员禁言")

    async def set_admin(self, bot: Any, group_id: str, user_id: str, enable: bool) -> Any:
        return await self._unsupported("管理员")

    async def leave_group(self, bot: Any, group_id: str) -> Any:
        return await self._unsupported("退群")

    async def set_special_title(self, bot: Any, group_id: str, user_id: str, title: str) -> Any:
        return await self._unsupported("群头衔")

    async def set_essence(self, bot: Any, message_id: int, enable: bool) -> Any:
        return await self._unsupported("精华消息")

    async def like(self, bot: Any, user_id: str, times: int = 1) -> Any:
        return await self._unsupported("点赞")

    async def upload_file(self, bot: Any, **kwargs: Any) -> Any:
        return await self._unsupported("文件上传")

    async def send_event(self, bot: Any, event: Any, message: Any) -> Any:
        return await bot.send(event, message)


class PlatformBot:
    """面向业务层的统一 Bot 对象。"""

    def __init__(self, bot: Any):
        from .registry import get_platform_adapter

        self.raw = bot
        self.adapter = get_platform_adapter(bot)

    def __getattr__(self, name: str):
        if name in {"raw", "adapter"}:
            raise AttributeError(name)
        return getattr(self.raw, name)

    def build_segment(self, segment_type: str, data: Any = None) -> Any:
        return self.adapter.build_segment(self.raw, segment_type, data)

    def build_message(self, *segments: Any) -> Any:
        return self.adapter.build_message(
            self.raw, [segment for segment in segments if segment is not None]
        )

    def message_segment_class(self) -> type | None:
        return self.adapter.message_segment_class()

    def is_mention_segment(self, segment: Any) -> bool:
        return self.adapter.is_mention_segment(segment)

    def mention_target(self, segment: Any) -> str | None:
        return self.adapter.mention_target(segment)

    async def send_group_message(self, group_id: str, message: Any):
        return await self.adapter.send_group_message(self.raw, str(group_id), message)

    async def send_private_message(self, user_id: str, message: Any):
        return await self.adapter.send_private_message(self.raw, str(user_id), message)

    async def send(self, event: Any, message: Any):
        """按当前事件上下文发送消息。"""
        return await self.adapter.send_event(self.raw, event, message)

    async def send_message_to_target(self, target: dict[str, Any], message: Any):
        return await self.adapter.send_message_to_target(self.raw, target, message)

    async def send_text_to_target(self, target: dict[str, Any], text: str):
        return await self.adapter.send_text_to_target(self.raw, target, text)

    async def send_forward_messages(
        self, event: Any, messages: list[Any], *, nickname: str = "FxBot"
    ) -> bool:
        return await self.adapter.send_forward_messages(
            self.raw, event, messages, nickname=nickname
        )

    async def get_replied_message(self, message_id: int) -> Any:
        return await self.adapter.get_replied_message(self.raw, message_id)

    def extract_image_sources(self, message: Any) -> list[str]:
        return self.adapter.extract_image_sources(message)

    def extract_reply_message_id(self, message: Any) -> int | None:
        return self.adapter.extract_reply_message_id(message)

    async def delete_message(self, message_id: int):
        return await self.adapter.delete_message(self.raw, message_id)

    async def get_message(self, message_id: int):
        return await self.adapter.get_message(self.raw, message_id)

    async def get_group_info(self, group_id: str):
        return await self.adapter.get_group_info(self.raw, str(group_id))

    async def get_group_member(self, group_id: str, user_id: str):
        return await self.adapter.get_group_member(self.raw, str(group_id), str(user_id))

    async def get_group_member_role(self, group_id: str, user_id: str):
        return await self.adapter.get_group_member_role(self.raw, str(group_id), str(user_id))

    async def get_group_members(self, group_id: str):
        return await self.adapter.get_group_members(self.raw, str(group_id))

    async def get_muted_members(self, group_id: str):
        return await self.adapter.get_muted_members(self.raw, str(group_id))

    async def get_group_list(self):
        return await self.adapter.get_group_list(self.raw)

    async def get_user(self, user_id: str):
        return await self.adapter.get_user(self.raw, str(user_id))

    def user_avatar(self, user_id: str):
        return self.adapter.user_avatar(self.raw, str(user_id))

    def group_avatar(self, group_id: str):
        return self.adapter.group_avatar(self.raw, str(group_id))

    async def ban(self, group_id: str, user_id: str, duration: int):
        return await self.adapter.ban(self.raw, str(group_id), str(user_id), duration)

    async def kick(self, group_id: str, user_id: str, reject_add_request: bool = False):
        return await self.adapter.kick(self.raw, str(group_id), str(user_id), reject_add_request)

    async def whole_ban(self, group_id: str, enable: bool):
        return await self.adapter.whole_ban(self.raw, str(group_id), enable)

    async def get_group_mute_setting(self, group_id: str):
        return await self.adapter.get_group_mute_setting(self.raw, str(group_id))

    async def set_group_members_mute(self, group_id: str, members: list[Any]):
        return await self.adapter.set_group_members_mute(self.raw, str(group_id), members)

    async def set_admin(self, group_id: str, user_id: str, enable: bool):
        return await self.adapter.set_admin(self.raw, str(group_id), str(user_id), enable)

    async def leave_group(self, group_id: str):
        return await self.adapter.leave_group(self.raw, str(group_id))

    async def set_special_title(self, group_id: str, user_id: str, title: str):
        return await self.adapter.set_special_title(self.raw, str(group_id), str(user_id), title)

    async def like(self, user_id: str, times: int = 1):
        return await self.adapter.like(self.raw, str(user_id), times)

    async def upload_file(self, **kwargs: Any):
        return await self.adapter.upload_file(self.raw, **kwargs)

    async def set_essence(self, message_id: int, enable: bool):
        return await self.adapter.set_essence(self.raw, message_id, enable)

    def mention_targets(self, message: Any, ignored_targets: set[str] | None = None):
        return self.adapter.mention_targets(message, ignored_targets)

    def first_mention_target(self, message: Any, ignored_targets: set[str] | None = None):
        values = self.mention_targets(message, ignored_targets)
        return values[0] if values else None


def platform_bot(bot: Any) -> PlatformBot:
    return PlatformBot(bot)


_current: ContextVar[PlatformBot | None] = ContextVar("fxbot_platform_bot", default=None)


def bind_bot(bot: Any) -> PlatformBot:
    client = PlatformBot(bot)
    _current.set(client)
    return client


def current_bot() -> PlatformBot:
    client = _current.get()
    if client is not None:
        return client
    try:
        from nonebot.internal.matcher import current_bot as nonebot_current_bot

        return PlatformBot(nonebot_current_bot.get())
    except LookupError:
        pass
    raise RuntimeError("当前事件未绑定 Bot；请仅在事件处理上下文中使用 selfBot")


class _SelfBot:
    """当前事件 Bot 代理，业务层直接使用 selfBot。"""

    def _client(self) -> PlatformBot:
        return current_bot()

    def __getattr__(self, name: str):
        return getattr(self._client(), name)


selfBot = _SelfBot()
