# -*- coding: utf-8 -*-
"""消息统计的接入点：在哪些方法的调用上认目标、记一次收发。

SEND_PATCHES 声明发送侧的接入点，install_send_patches 一次装上；接收侧只有一处，
走 install_receive_hook。计数交给调用方传进来的回调，本模块不认识缓存。
"""

from __future__ import annotations

import inspect
import importlib
from typing import Any, Dict, Tuple, Mapping, Callable, Optional, Awaitable
from functools import wraps
from dataclasses import dataclass

from nonebot import logger
from nonebot.adapters import Bot

Target = Tuple[str, str]
Classifier = Callable[[Mapping[str, Any]], Optional[Target]]
SendCounter = Callable[[Bot, Target], Awaitable[None]]
ReceiveCounter = Callable[[Bot], Awaitable[None]]


def _get_nested(obj: Any, path: str) -> Any:
    """安全提取嵌套属性。"""
    for part in path.split("."):
        if obj is None:
            return None
        obj = getattr(obj, part, None)
    return obj


def _classify_target(event: Any) -> Optional[Target]:
    """从事件里认出会话目标，返回 (chat_type, target_id) 或 None。"""
    if event is None:
        return None

    # 频道是「群 / 私聊」之外的第三种作用域，统计里没有它的桶
    if hasattr(event, "guild_id") and hasattr(event, "channel_id"):
        return None

    if group_id := getattr(event, "group_id", None):
        return ("group", str(group_id))
    if user_id := getattr(event, "user_id", None):
        return ("private", str(user_id))

    if group_target := (getattr(event, "group_openid", None) or getattr(event, "group_code", None)):
        return ("group", str(group_target))
    if private_target := (getattr(event, "user_openid", None) or _get_nested(event, "author.user_openid")):
        return ("private", str(private_target))

    return None


def _is_message_event(event: Any) -> bool:
    """通知、请求类事件同样带 group_id / user_id，只算消息事件才是「收到一条消息」。"""
    return event.get_type() == "message"


def _classify_ids(arguments: Mapping[str, Any]) -> Optional[Target]:
    if arguments.get("group_id") is not None:
        return ("group", str(arguments["group_id"]))
    if arguments.get("user_id") is not None:
        return ("private", str(arguments["user_id"]))
    return None


# 「往会话里发一条消息」的动作名。故意不含 send_like（点赞）这类名字相似但语义不同的
# 动作，也不含 upload_*_file（传文件不是消息）。
_CALL_API_SEND_ACTIONS = frozenset(
    {
        "send_msg",  # OneBot v11 通用，类型看 message_type
        "send_group_msg",  # OneBot v11 群
        "send_private_msg",  # OneBot v11 私聊
        "send_group_forward_msg",  # OneBot v11 群合并转发
        "send_private_forward_msg",  # OneBot v11 私聊合并转发
        "send_message",  # OneBot v12 / wxauto 通用，类型看 detail_type
    }
)


def _classify_call_api(arguments: Mapping[str, Any]) -> Optional[Target]:
    if arguments.get("api") not in _CALL_API_SEND_ACTIONS:
        return None
    return _classify_ids(arguments)


def _classify_send_event(arguments: Mapping[str, Any]) -> Optional[Target]:
    return _classify_target(arguments.get("event"))


def _classify_qq_group(arguments: Mapping[str, Any]) -> Optional[Target]:
    target = arguments.get("group_openid")
    return ("group", str(target)) if target else None


def _classify_qq_c2c(arguments: Mapping[str, Any]) -> Optional[Target]:
    target = arguments.get("openid")
    return ("private", str(target)) if target else None


@dataclass(frozen=True)
class SendPatch:
    """一条发送统计接入点。

    target 为空表示走官方钩子 ``Bot.on_called_api`` —— 所有适配器的 ``call_api`` 都过
    这里，动作名白名单筛出发送类。非空是适配器 Bot 类的导入路径，补它上面绕开
    ``call_api`` 的原生发送方法：这些方法直接发 HTTP，既不经过 ``bot.send`` 也不经过
    ``bot.call_api``。

    classify 收到的是按参数名组织的实参（``inspect.signature.bind`` 的结果），位置传和
    关键字传都归一到同一个名字，按名取即可。
    """

    target: Optional[str]
    method: str
    classify: Classifier


# 加平台 = 加一行。频道（send_to_channel / send_to_dms）刻意不进表：接收侧就不收频道
# 消息，发送侧跟着一致，否则收和发两个数对不上。
SEND_PATCHES: Tuple[SendPatch, ...] = (
    SendPatch(None, "call_api", _classify_call_api),
    # wxauto 的事件和消息段是 OneBot 12 那套，但它的 Bot 继承的是 nonebot.adapters.Bot，
    # send 直接转 adapter.send_message_to_target —— Matcher.send/finish 都走这条路，
    # 光靠上面的 call_api 钩子收不到。
    SendPatch("nonebot.adapters.wxauto:Bot", "send", _classify_send_event),
    SendPatch("nonebot.adapters.qq:Bot", "send_to_group", _classify_qq_group),
    SendPatch("nonebot.adapters.qq:Bot", "send_to_c2c", _classify_qq_c2c),
)


def _resolve(path: str) -> Any:
    module_name, _, attr = path.partition(":")
    return getattr(importlib.import_module(module_name), attr)


def _wrap_counted_send(original: Any, classify: Classifier, counter: SendCounter) -> Any:
    """包成「原调用成功返回后，按参数名认目标、记一次发送」。"""
    signature = inspect.signature(original)

    @wraps(original)
    async def patched(self: Any, *args: Any, **kwargs: Any) -> Any:
        arguments = signature.bind(self, *args, **kwargs).arguments
        result = await original(self, *args, **kwargs)

        # 原调用抛异常时走不到这里，失败的消息不计
        if target := classify(arguments):
            await counter(self, target)

        return result

    return patched


def _install_api_hook(classify: Classifier, counter: SendCounter) -> None:
    async def on_called_api(
        bot: Bot,
        exception: Optional[Exception],
        api: str,
        data: Dict[str, Any],
        result: Any,
    ) -> None:
        if exception is None and (target := classify({"api": api, **data})):
            await counter(bot, target)

    Bot.on_called_api(on_called_api)


def install_receive_hook(bot: Bot, counter: ReceiveCounter) -> None:
    """接收方向不做目标级拆分，只在 Bot 上加一，所以挂在实例的 handle_event 上。"""
    original = getattr(type(bot), "handle_event", None)
    if original is None:
        return

    @wraps(original)
    async def patched(event: Any) -> Any:
        result = await original(bot, event)

        if _is_message_event(event) and _classify_target(event):
            await counter(bot)

        return result

    bot.handle_event = patched


_installed = False


def install_send_patches(counter: SendCounter) -> None:
    """装上发送统计接入点，全进程一次。"""
    global _installed
    if _installed:
        return
    _installed = True

    installed = 0
    for patch in SEND_PATCHES:
        if patch.target is None:
            _install_api_hook(patch.classify, counter)
            installed += 1
            continue

        try:
            owner = _resolve(patch.target)
        except Exception as e:
            logger.debug(f"[bot_status] 适配器不可用，跳过发送统计接入点 {patch.target}: {e}")
            continue

        original = getattr(owner, patch.method, None)
        if original is None:
            continue

        setattr(owner, patch.method, _wrap_counted_send(original, patch.classify, counter))
        installed += 1

    logger.info(f"[bot_status] 已装上 {installed} 个发送统计接入点")
