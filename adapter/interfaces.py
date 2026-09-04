"""平台适配器的统一领域接口。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from nonebot.exception import IgnoredException
from nonebot.log import logger


class PlatformError(IgnoredException):
    """平台调用失败。"""


class UnsupportedCapability(PlatformError):
    """当前平台不具备指定能力。"""


class PlatformAdapter(ABC):
    """所有平台适配器必须实现的能力边界。"""

    # 基础识别与消息构造
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
        """返回平台消息段类型（供平台扩展使用）。"""
        return None

    @abstractmethod
    async def send_message_to_target(self, bot: Any, target: dict[str, Any], message: Any) -> Any:
        """向持久化目标发送消息。"""

    # 常用发送封装
    async def send_group_message(self, bot: Any, group_id: str, message: Any) -> Any:
        """发送群消息。"""
        target = {"group_id": group_id, "group_openid": group_id}
        return await self.send_message_to_target(bot, target, message)

    async def send_private_message(self, bot: Any, user_id: str, message: Any) -> Any:
        """发送私聊消息。"""
        target = {"user_id": user_id, "user_openid": user_id}
        return await self.send_message_to_target(bot, target, message)

    async def send_text_to_target(self, bot: Any, target: dict[str, Any], text: str) -> Any:
        """发送纯文本消息。"""
        message = self.build_message(bot, [self.build_segment(bot, "text", text)])
        return await self.send_message_to_target(bot, target, message)

    async def send_forward_messages(
        self,
        bot: Any,
        event: Any,
        messages: list[Any],
        *,
        nickname: str = "FxBot",
    ) -> bool:
        """发送转发消息。"""
        return await self._unsupported("转发消息")

    # 消息解析
    async def get_replied_message(self, bot: Any, message_id: int) -> Any:
        """获取被回复消息内容。"""
        result = await self.get_message(bot, message_id)
        return result.get("message") if isinstance(result, dict) else None

    def extract_image_sources(self, message: Any) -> list[str]:
        """提取消息中的远程图片来源。"""
        sources: list[str] = []
        for segment in list(message or []):
            if getattr(segment, "type", "") != "image":
                continue
            data = getattr(segment, "data", {}) or {}
            source = data.get("url") or data.get("file")
            if isinstance(source, str) and source and not source.startswith("base64://"):
                sources.append(source)
        return sources

    def extract_reply_message_id(self, message: Any) -> int | None:
        """提取消息中的回复 ID。"""
        for segment in list(message or []):
            if getattr(segment, "type", "") != "reply":
                continue
            try:
                return int((getattr(segment, "data", {}) or {}).get("id"))
            except (TypeError, ValueError):
                return None
        return None

    def is_mention_segment(self, segment: Any) -> bool:
        """判断消息段是否为 @。"""
        return False

    def mention_target(self, segment: Any) -> str | None:
        """提取 @ 消息段的目标 ID。"""
        return None

    def mention_targets(self, message: Any, ignored_targets: set[str] | None = None) -> list[str]:
        """提取消息中的所有 @ 目标。"""
        ignored = {str(target) for target in (ignored_targets or set())}
        return [
            target
            for segment in list(message or [])
            if (target := self.mention_target(segment)) and target not in ignored
        ]

    # 平台查询与消息管理
    async def _unsupported(self, capability: str) -> Any:
        logger.info(f"[adapter] 当前适配器不支持能力: {capability}")
        raise UnsupportedCapability(capability)

    async def delete_message(self, bot: Any, message_id: int) -> Any:
        """撤回消息。"""
        return await self._unsupported("撤回消息")

    async def get_message(self, bot: Any, message_id: int) -> Any:
        """获取消息。"""
        return await self._unsupported("获取消息")

    async def get_group_info(self, bot: Any, group_id: str) -> Any:
        """获取群信息。"""
        return await self._unsupported("获取群信息")

    async def get_group_member(self, bot: Any, group_id: str, user_id: str) -> Any:
        """获取群成员信息。"""
        return await self._unsupported("获取成员信息")

    async def get_group_members(self, bot: Any, group_id: str) -> Any:
        """获取群成员列表。"""
        return await self._unsupported("获取群成员列表")

    async def get_group_list(self, bot: Any) -> Any:
        """获取群列表。"""
        return await self._unsupported("获取群列表")

    async def get_user(self, bot: Any, user_id: str) -> Any:
        """获取用户信息。"""
        return await self._unsupported("获取用户信息")

    def user_avatar(self, bot: Any, user_id: str) -> str | None:
        """生成用户头像地址。"""
        return None

    def group_avatar(self, bot: Any, group_id: str) -> str | None:
        """生成群头像地址。"""
        return None

    async def ban(self, bot: Any, group_id: str, user_id: str, duration: int) -> Any:
        """禁言成员。"""
        return await self._unsupported("禁言")

    async def kick(
        self,
        bot: Any,
        group_id: str,
        user_id: str,
        reject_add_request: bool = False,
    ) -> Any:
        """踢出成员。"""
        return await self._unsupported("踢人")

    async def whole_ban(self, bot: Any, group_id: str, enable: bool) -> Any:
        """设置全体禁言。"""
        return await self._unsupported("全体禁言")

    async def set_admin(self, bot: Any, group_id: str, user_id: str, enable: bool) -> Any:
        """设置或取消管理员。"""
        return await self._unsupported("管理员")

    async def leave_group(self, bot: Any, group_id: str) -> Any:
        """退出群聊。"""
        return await self._unsupported("退群")

    async def set_special_title(
        self,
        bot: Any,
        group_id: str,
        user_id: str,
        title: str,
    ) -> Any:
        """设置群成员头衔。"""
        return await self._unsupported("群头衔")

    async def set_essence(self, bot: Any, message_id: int, enable: bool) -> Any:
        """设置或取消精华消息。"""
        return await self._unsupported("精华消息")

    async def like(self, bot: Any, user_id: str, times: int = 1) -> Any:
        """给用户点赞。"""
        return await self._unsupported("点赞")

    async def upload_file(self, bot: Any, **kwargs: Any) -> Any:
        """上传文件。"""
        return await self._unsupported("文件上传")

    async def send_event(self, bot: Any, event: Any, message: Any) -> Any:
        """按事件上下文发送消息。"""
        return await bot.send(event, message)
