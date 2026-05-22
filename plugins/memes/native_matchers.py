from __future__ import annotations

import asyncio
import base64
import hashlib
import random
import re
import shlex
from datetime import datetime, timedelta, timezone
from itertools import chain
from typing import Any, Optional

from dateutil.relativedelta import relativedelta
from nonebot import get_driver
from nonebot.adapters import Bot, Event, Message, MessageSegment
from nonebot.log import logger
from nonebot.message import event_preprocessor
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.rule import Rule
from nonebot.typing import T_State
from pypinyin import Style, pinyin

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...adapter.uninfo import QryItrface, Uninfo
from ...utils.paths import cache_dir
from .config import (
    cfg_command_prefixes,
    cfg_list_image_config,
    cfg_notice_prob,
    cfg_random_meme_show_info,
    cfg_use_default_when_no_text,
    cfg_use_sender_when_no_image,
)
from .exception import MemeGeneratorException
from .manager import MemeMode, meme_manager
from .plot import plot_duration_counts, plot_meme_and_duration_counts
from .protection import protection_manager
from .recorder import SessionIdType, get_meme_generation_keys, record_meme_generation
from .recorder import get_meme_generation_records, get_meme_generation_times
from .request import (
    MemeInfo,
    MemeKeyWithProperties,
    generate_meme,
    generate_meme_preview,
    render_meme_list,
)
from .utils import add_timezone, download_url

try:
    from nonebot.adapters.onebot.v11 import Bot as V11Bot
    from nonebot.adapters.onebot.v11 import Message as V11Message
    from nonebot.adapters.onebot.v11 import MessageSegment as V11MessageSegment
except Exception:  # pragma: no cover
    V11Bot = None  # type: ignore[assignment]
    V11Message = None  # type: ignore[assignment]
    V11MessageSegment = None  # type: ignore[assignment]

try:
    from nonebot.adapters.onebot.v12 import Bot as V12Bot
    from nonebot.adapters.onebot.v12 import Message as V12Message
    from nonebot.adapters.onebot.v12 import MessageSegment as V12MessageSegment
except Exception:  # pragma: no cover
    V12Bot = None  # type: ignore[assignment]
    V12Message = None  # type: ignore[assignment]
    V12MessageSegment = None  # type: ignore[assignment]

try:
    from nonebot.adapters.qq import Bot as QQOfficialBot
    from nonebot.adapters.qq import Message as QQOfficialMessage
    from nonebot.adapters.qq import MessageSegment as QQOfficialMessageSegment
except Exception:  # pragma: no cover
    QQOfficialBot = None  # type: ignore[assignment]
    QQOfficialMessage = None  # type: ignore[assignment]
    QQOfficialMessageSegment = None  # type: ignore[assignment]


MEME_TRIGGER_KEY = "_memes_trigger"
MEME_MSG_KEY = "_memes_msg"

memes_cache_dir = cache_dir("nonebot_plugin_memes_api")

_trigger_map: dict[str, list[str]] = {}

P = Plugin(
    "memes",
    display_name="表情包制作",
    enabled=True,
    level=PermLevel.MEMBER,
    scene=PermScene.ALL,
)


def get_user_id(session: Uninfo) -> str:
    return f"{session.scope}_{session.self_id}_{session.scene_path}"


def _is_superuser(session: Uninfo) -> bool:
    return str(session.user.id) in get_driver().config.superusers


def _can_edit(session: Uninfo) -> bool:
    return session.scene.is_private or bool(
        session.member and session.member.role and session.member.role.level > 1
    )


def _split_text(text: str) -> list[str]:
    try:
        return shlex.split(text)
    except Exception:
        return text.split()


def _build_trigger_map() -> dict[str, list[str]]:
    mapping: dict[str, list[str]] = {}
    for meme in meme_manager.get_memes():
        for key in meme.keywords:
            mapping.setdefault(key.lower(), []).append(meme.key)
        for shortcut in meme.shortcuts:
            name = (shortcut.humanized or shortcut.key).strip()
            if name:
                mapping.setdefault(name.lower(), []).append(meme.key)
    return mapping


def _prefixes() -> list[str]:
    prefixes = list(get_driver().config.command_start)
    if (configured := cfg_command_prefixes()) is not None:
        prefixes = configured
    return prefixes


def _match_prefix(text: str) -> Optional[str]:
    for p in _prefixes():
        if p == "":
            return ""
        if text.startswith(p):
            return p
    return None


@event_preprocessor
async def _move_leading_mention_to_end(event: Event):
    if not _trigger_map:
        return

    try:
        msg = event.get_message()
    except Exception:
        return

    if not msg:
        return

    i = 0
    while i < len(msg) and getattr(msg[i], "type", None) in {"at", "mention"}:
        i += 1
    if i == 0 or i >= len(msg):
        return

    if not msg[i].is_text():
        return

    text = str(msg[i]).lstrip()
    prefix = _match_prefix(text)
    if prefix is None:
        return

    rest = text[len(prefix) :].lstrip()
    trigger = rest.split(maxsplit=1)[0] if rest else ""
    if not trigger or trigger.lower() not in _trigger_map:
        return

    moved: list[MessageSegment] = []
    for _ in range(i):
        moved.append(msg.pop(0))
    for seg in moved:
        msg.append(seg)


def meme_trigger_rule() -> Rule:
    async def checker(event: Event, state: T_State) -> bool:
        if not _trigger_map:
            return False

        try:
            msg = event.get_message()
        except Exception:
            return False
        if not msg:
            return False

        seg: MessageSegment = msg[0]
        if not seg.is_text():
            return False

        seg_text = str(seg).lstrip()
        prefix = _match_prefix(seg_text)
        if prefix is None:
            return False

        rest = seg_text[len(prefix) :].lstrip()
        if not rest:
            return False

        parts = rest.split(maxsplit=1)
        trigger = parts[0].strip()
        if not trigger:
            return False
        if trigger.lower() not in _trigger_map:
            return False

        arg_text = parts[1] if len(parts) > 1 else ""
        new_msg = msg.copy()
        new_msg.pop(0)
        if arg_text:
            head = new_msg.__class__(arg_text.lstrip())
            for s in reversed(head):
                new_msg.insert(0, s)

        state[MEME_TRIGGER_KEY] = trigger
        state[MEME_MSG_KEY] = new_msg
        return True

    return Rule(checker)


async def find_meme(matcher: Matcher, meme_name: str) -> MemeInfo:
    found_memes = meme_manager.find(meme_name)
    found_num = len(found_memes)

    if found_num == 0:
        if searched_memes := meme_manager.search(meme_name, limit=5):
            await matcher.finish(
                f"表情 {meme_name} 不存在，你可能在找：\n"
                + "\n".join(
                    f"* {meme.key} ({'/'.join(meme.keywords)})" for meme in searched_memes
                )
            )
        await matcher.finish(f"表情 {meme_name} 不存在！")

    if found_num == 1:
        return found_memes[0]

    target_name = meme_name.strip().lower()
    for meme in found_memes:
        if meme.key.lower() == target_name:
            logger.warning(f"[memes] 表情别名重复，已优先选择完全同名项: {meme_name} -> {meme.key}")
            return meme

    chosen = found_memes[0]
    logger.warning(
        f"[memes] 表情别名重复，已默认选择首个结果: {meme_name} -> {chosen.key}，候选数={found_num}"
    )
    return chosen


async def _download_image_from_segment(seg: MessageSegment) -> Optional[bytes]:
    url = seg.data.get("url")
    if isinstance(url, str) and url:
        return await download_url(url)

    file = seg.data.get("file") or seg.data.get("path")
    if isinstance(file, str) and file:
        if file.startswith("base64://"):
            try:
                return base64.b64decode(file[len("base64://") :])
            except Exception:
                return None
        if file.startswith("http://") or file.startswith("https://"):
            return await download_url(file)
    return None


async def _avatar_bytes_of(
    matcher: Matcher, session: Uninfo, interface: QryItrface, user_id: str
) -> Optional[bytes]:
    try:
        user = await interface.get_user(user_id)
    except NotImplementedError:
        await matcher.finish("当前平台可能不支持获取用户信息")
    except Exception:
        logger.warning("用户信息获取失败", exc_info=True)
        await matcher.finish("用户信息获取出错，请稍后再试")

    if not user:
        return None
    if not user.avatar:
        return None
    return await download_url(user.avatar)


async def extract_inputs(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    session: Uninfo,
    interface: QryItrface,
    meme: Optional[MemeInfo],
    msg: Message,
) -> tuple[list[str], list[bytes], list[Optional[str]]]:
    texts: list[str] = []
    images: list[bytes] = []
    image_user_ids: list[Optional[str]] = []  # 记录每个图片对应的用户ID

    reply_has_image = False
    try:
        reply = getattr(event, "reply", None)
        if reply:
            reply_msg = getattr(reply, "message", None)
            if isinstance(reply_msg, dict) and "image" in reply_msg:
                for img_seg in reply_msg.get("image") or []:
                    data = await _download_image_from_segment(img_seg)
                    if data:
                        images.append(data)
                        image_user_ids.append(None)  # 回复中的图片没有对应用户ID
                        reply_has_image = True
            elif isinstance(reply_msg, Message):
                for seg in reply_msg:
                    if getattr(seg, "type", None) == "image":
                        data = await _download_image_from_segment(seg)
                        if data:
                            images.append(data)
                            image_user_ids.append(None)  # 回复中的图片没有对应用户ID
                            reply_has_image = True
    except Exception:
        pass

    has_actual_image = reply_has_image or any(
        getattr(seg, "type", None) == "image" for seg in msg
    )

    for seg in msg:
        seg_type = getattr(seg, "type", None)

        if seg_type in {"at", "mention"}:
            if has_actual_image:
                continue
            target = (
                seg.data.get("qq")
                or seg.data.get("user_id")
                or seg.data.get("id")
                or seg.data.get("target")
            )
            if not target:
                continue
            avatar = await _avatar_bytes_of(matcher, session, interface, str(target))
            if avatar:
                images.append(avatar)
                image_user_ids.append(str(target))  # 记录@的用户ID

        elif seg_type == "image":
            data = await _download_image_from_segment(seg)
            if data:
                images.append(data)
                image_user_ids.append(None)  # 直接发送的图片没有对应用户ID

        elif seg.is_text():
            for token in _split_text(str(seg)):
                if token.startswith("@") and token[1:]:
                    avatar = await _avatar_bytes_of(
                        matcher, session, interface, token[1:]
                    )
                    if avatar:
                        images.append(avatar)
                        image_user_ids.append(token[1:])  # 记录@的用户ID
                    continue

                if token == "自己":
                    if session.user.avatar:
                        images.append(await download_url(session.user.avatar))
                        image_user_ids.append(str(session.user.id))  # 记录自己的ID
                    continue

                if token:
                    texts.append(token)

    if meme is not None:
        if (
            meme.params_type.min_images == 2
            and len(images) == 1
            and session.user.avatar
        ):
            images.insert(0, await download_url(session.user.avatar))
            image_user_ids.insert(0, str(session.user.id))  # 发送者的头像

        if (
            cfg_use_sender_when_no_image()
            and meme.params_type.min_images == 1
            and len(images) == 0
            and session.user.avatar
        ):
            images.append(await download_url(session.user.avatar))
            image_user_ids.append(str(session.user.id))  # 发送者的头像

        if (
            cfg_use_default_when_no_text()
            and meme.params_type.min_texts > 0
            and len(texts) == 0
        ):
            texts = list(meme.params_type.default_texts)

    return texts, images, image_user_ids


async def apply_protection(
    meme_key: str,
    images: list[bytes],
    image_user_ids: list[Optional[str]],
    sender_avatar: Optional[str],
) -> list[bytes]:
    """
    应用表情保护逻辑
    - 如果表情在保护列表中，且某个图片对应的用户在白名单中
    - 则将该用户的头像替换为发送者的头像（攻击者的头像）

    Args:
        meme_key: 表情key
        images: 图片列表
        image_user_ids: 每个图片对应的用户ID（可能为None）
        sender_avatar: 发送者头像URL

    Returns:
        处理后的图片列表
    """
    # 只有在保护表情列表中的表情才需要检查
    if not protection_manager.is_protected(meme_key):
        return images

    # 如果没有发送者头像，无法进行保护
    if not sender_avatar:
        return images

    # 边界检查：确保长度一致
    if len(images) != len(image_user_ids):
        logger.warning(
            f"表情保护失败：images 和 image_user_ids 长度不一致 "
            f"({len(images)} vs {len(image_user_ids)})"
        )
        return images

    # 先检查是否有需要保护的用户
    indices_to_replace = [
        i for i, user_id in enumerate(image_user_ids)
        if user_id and protection_manager.is_in_whitelist(user_id)
    ]

    # 如果没有需要保护的用户，直接返回
    if not indices_to_replace:
        return images

    # 只下载一次发送者头像
    try:
        sender_avatar_bytes = await download_url(sender_avatar)
    except Exception as e:
        logger.warning(f"表情保护失败：无法下载发送者头像: {e}")
        return images  # 降级处理，返回原图片列表

    # 替换所有需要保护的用户头像
    protected_images = images.copy()
    for i in indices_to_replace:
        protected_images[i] = sender_avatar_bytes

    return protected_images


async def _send_image(matcher: Matcher, bot: Bot, event: Event, img: bytes, text: str):
    if V11Bot is not None and isinstance(bot, V11Bot):
        assert V11Message is not None and V11MessageSegment is not None
        b64 = base64.b64encode(img).decode()
        await matcher.finish(V11Message(text) + V11MessageSegment.image(f"base64://{b64}"))

    if V12Bot is not None and isinstance(bot, V12Bot):
        assert V12Message is not None and V12MessageSegment is not None
        resp = await bot.upload_file(type="data", name="memes", data=img)
        file_id = resp["file_id"]
        await matcher.finish(V12Message(text) + V12MessageSegment.image(file_id))

    if QQOfficialBot is not None and isinstance(bot, QQOfficialBot):
        assert QQOfficialMessage is not None and QQOfficialMessageSegment is not None
        msg = QQOfficialMessage()
        if text:
            msg += QQOfficialMessageSegment.text(text)
        msg += QQOfficialMessageSegment.file_image(img)
        await matcher.finish(msg)

    if not text:
        text = "已生成图片，但当前适配器不支持发送图片"
    await matcher.finish(text)


def _first_mention_id(arg: Message) -> Optional[str]:
    for seg in arg:
        seg_type = getattr(seg, "type", None)
        if seg_type in {"at", "mention"}:
            target = (
                seg.data.get("qq")
                or seg.data.get("user_id")
                or seg.data.get("id")
                or seg.data.get("target")
            )
            if target:
                return str(target)
    return None


help_cmd = P.on_command(
    "表情包制作",
    aliases={"表情列表", "头像表情包", "文字表情包"},
    block=True,
    priority=11,
    name="list",
    display_name="表情列表",
    level=PermLevel.MEMBER,
)

usage_help_cmd = P.on_command(
    "表情帮助",
    block=True,
    priority=11,
    name="help",
    display_name="表情帮助",
    level=PermLevel.MEMBER,
)
info_cmd = P.on_command(
    "表情详情",
    aliases={"表情帮助", "表情示例"},
    block=True,
    priority=11,
    name="detail",
    display_name="表情详情",
    level=PermLevel.MEMBER,
)
search_cmd = P.on_command(
    "表情搜索",
    block=True,
    priority=11,
    name="search",
    display_name="表情搜索",
    level=PermLevel.MEMBER,
)

add_whitelist_cmd = P.on_command(
    "添加保护",
    block=True,
    priority=11,
    name="add_whitelist",
    display_name="添加保护白名单",
    level=PermLevel.SUPERUSER,
)
remove_whitelist_cmd = P.on_command(
    "移除保护",
    block=True,
    priority=11,
    name="remove_whitelist",
    display_name="移除保护白名单",
    level=PermLevel.SUPERUSER,
)
protect_meme_cmd = P.on_command(
    "保护表情",
    block=True,
    priority=11,
    name="protect_meme",
    display_name="保护表情",
    level=PermLevel.SUPERUSER,
)
unprotect_meme_cmd = P.on_command(
    "取消保护表情",
    block=True,
    priority=11,
    name="unprotect_meme",
    display_name="取消保护表情",
    level=PermLevel.SUPERUSER,
)
protection_list_cmd = P.on_command(
    "保护列表",
    block=True,
    priority=11,
    name="protection_list",
    display_name="保护列表",
    level=PermLevel.SUPERUSER,
)

statistics_cmd = P.on_command(
    "表情调用统计",
    aliases={"表情使用统计"},
    block=True,
    priority=11,
    name="statistics",
    display_name="表情调用统计",
    level=PermLevel.MEMBER,
)

block_cmd = P.on_command(
    "禁用表情",
    block=True,
    priority=11,
    name="block",
    display_name="禁用表情",
    level=PermLevel.MEMBER,
)
unblock_cmd = P.on_command(
    "启用表情",
    block=True,
    priority=11,
    name="unblock",
    display_name="启用表情",
    level=PermLevel.MEMBER,
)
block_gl_cmd = P.on_command(
    "全局禁用表情",
    block=True,
    priority=11,
    name="global_block",
    display_name="全局禁用表情",
    level=PermLevel.SUPERUSER,
)
unblock_gl_cmd = P.on_command(
    "全局启用表情",
    block=True,
    priority=11,
    name="global_unblock",
    display_name="全局启用表情",
    level=PermLevel.SUPERUSER,
)
black_list_cmd = P.on_command(
    "禁用列表",
    aliases={"全局禁用列表"},
    block=True,
    priority=11,
    name="global_block_list",
    display_name="全局禁用列表",
    level=PermLevel.SUPERUSER,
)

random_cmd = P.on_command(
    "随机表情",
    block=True,
    priority=3,
    name="random",
    display_name="随机表情",
    level=PermLevel.MEMBER,
)
refresh_cmd = P.on_command(
    "更新表情",
    aliases={"刷新表情"},
    block=True,
    priority=3,
    name="refresh",
    display_name="更新表情",
    level=PermLevel.SUPERUSER,
)

meme_msg_matcher = P.on_message(
    rule=meme_trigger_rule(),
    block=False,
    priority=3,
    name="make",
    display_name="制作表情",
    level=PermLevel.MEMBER,
)


@help_cmd.handle()
async def _help(bot: Bot, event: Event, matcher: Matcher, session: Uninfo):
    user_key = get_user_id(session)
    memes = meme_manager.get_memes()
    list_image_config = cfg_list_image_config()

    sort_by = list_image_config.sort_by
    sort_reverse = list_image_config.sort_reverse
    if sort_by == "key":
        memes = sorted(memes, key=lambda m: m.key, reverse=sort_reverse)
    elif sort_by == "keywords":
        memes = sorted(
            memes,
            key=lambda m: "".join(
                chain.from_iterable(pinyin(m.keywords[0], style=Style.TONE3))
            ),
            reverse=sort_reverse,
        )
    elif sort_by == "date_created":
        memes = sorted(memes, key=lambda m: m.date_created, reverse=sort_reverse)
    elif sort_by == "date_modified":
        memes = sorted(memes, key=lambda m: m.date_modified, reverse=sort_reverse)

    label_new_timedelta = list_image_config.label_new_timedelta
    label_hot_threshold = list_image_config.label_hot_threshold
    label_hot_days = list_image_config.label_hot_days
    meme_generation_keys = await get_meme_generation_keys(
        session,
        SessionIdType.GLOBAL,
        time_start=datetime.now(timezone.utc) - timedelta(days=label_hot_days),
    )

    meme_list: list[MemeKeyWithProperties] = []
    for meme in memes:
        labels: list[str] = []
        if datetime.now() - meme.date_created < label_new_timedelta:
            labels.append("new")
        if meme_generation_keys.count(meme.key) >= label_hot_threshold:
            labels.append("hot")
        disabled = not meme_manager.check(user_key, meme.key)
        meme_list.append(
            MemeKeyWithProperties(meme_key=meme.key, disabled=disabled, labels=labels)
        )

    meme_list_hashable = [
        (
            {
                "key": meme.key,
                "keywords": meme.keywords,
                "shortcuts": [s.humanized or s.key for s in meme.shortcuts],
                "tags": sorted(meme.tags),
            },
            prop,
        )
        for meme, prop in zip(memes, meme_list)
    ]
    meme_list_hash = hashlib.md5(str(meme_list_hashable).encode("utf8")).hexdigest()
    meme_list_cache_file = memes_cache_dir / f"{meme_list_hash}.jpg"
    if not meme_list_cache_file.exists():
        img = await render_meme_list(
            meme_list,
            text_template=list_image_config.text_template,
            add_category_icon=list_image_config.add_category_icon,
        )
        meme_list_cache_file.write_bytes(img)
    else:
        img = meme_list_cache_file.read_bytes()

    prefixes = cfg_command_prefixes() or []
    hint_prefix = prefixes[0] if prefixes else ""
    text = (
        f"触发方式：关键词{prefixes} 表情名 图片/文字/@某人\n"
        f"例：{hint_prefix}卡提举牌 抽我\n"
        "发送【表情详情+关键词】查看预览\n"
        "群管可 启用/禁用表情+表情名\n"
        "目前支持的表情列表："
    )

    await _send_image(matcher, bot, event, img, text)


@usage_help_cmd.handle()
async def _usage_help(matcher: Matcher):
    prefixes = cfg_command_prefixes() or []
    memes_prefix = prefixes[0] if prefixes else ""
    await matcher.finish(
        "- 表情列表\n"
        "发送【表情包制作】查看表情列表\n"
        "- 表情详情\n"
        "发送【表情详情 + 表情名/关键词】查看表情详细信息和表情预览\n"
        "- 表情搜索\n"
        "发送【表情搜索 + 关键词】查找相关的表情\n"
        "- 表情包开关\n"
        "- 群管可以启用或禁用本群的表情\n"
        "发送 启用表情/禁用表情 表情名/关键词，如：禁用表情 摸\n"
        "- 超级用户可以全局禁用/启用表情\n"
        "发送 全局启用表情 表情名/关键词 可全局启用表情；\n"
        "发送 全局禁用表情 表情名/关键词 可全局禁用表情；\n"
        "发送 禁用列表 查看全局禁用的表情列表\n"
        "- 白名单保护（仅超级用户）\n"
        "发送【添加保护@用户】或【添加保护<QQ号>】添加保护白名单\n"
        "发送【移除保护@用户】或【移除保护<QQ号>】移除保护白名单\n"
        "发送【保护表情<表情名>】添加保护表情\n"
        "发送【取消保护表情<表情名>】移除保护表情\n"
        "发送【保护列表】查看保护配置\n"
        "- 表情使用\n"
        f"发送【{memes_prefix}关键词 + 图片/文字】制作表情\n"
        "可使用【自己】、【@某人】获取指定用户的头像作为图片\n"
        "可使用【@ + 用户id】指定任意用户获取头像，如【摸 @114514】\n"
        "- 随机表情\n"
        "发送【随机表情 + 图片/文字】可随机制作表情\n"
        "随机范围为 图片/文字 数量符合要求的表情\n"
        "- 表情调用统计\n"
        "发送【[我的][全局]<时间段>表情调用统计 [表情名]】获取表情调用次数统计图\n"
    )


@info_cmd.handle()
async def _info(bot: Bot, event: Event, matcher: Matcher, arg: Message = CommandArg()):
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()

    meme = await find_meme(matcher, meme_name)

    keywords = "、".join([f'"{keyword}"' for keyword in meme.keywords])
    shortcuts = "、".join([f'"{s.humanized or s.key}"' for s in meme.shortcuts])
    tags = "、".join([f'"{tag}"' for tag in meme.tags])

    image_num = f"{meme.params_type.min_images}"
    if meme.params_type.max_images > meme.params_type.min_images:
        image_num += f" ~ {meme.params_type.max_images}"
    text_num = f"{meme.params_type.min_texts}"
    if meme.params_type.max_texts > meme.params_type.min_texts:
        text_num += f" ~ {meme.params_type.max_texts}"
    default_texts = ", ".join([f'"{text}"' for text in meme.params_type.default_texts])

    args_info = ""
    if args_type := meme.params_type.args_type:
        for option in args_type.parser_options:
            opt = option.option()
            alias_text = (
                " ".join(opt.requires)
                + (" " if opt.requires else "")
                + "│".join(sorted(opt.aliases, key=len))
            )
            args_info += f"\n  * {alias_text}{opt.separators[0]}{opt.help_text}"

    info = (
        f"表情名：{meme.key}"
        + f"\n关键词：{keywords}"
        + (f"\n快捷指令：{shortcuts}" if shortcuts else "")
        + (f"\n标签：{tags}" if tags else "")
        + f"\n需要图片数目：{image_num}"
        + f"\n需要文字数目：{text_num}"
        + (f"\n默认文字：[{default_texts}]" if default_texts else "")
        + (f"\n可选参数：{args_info}" if args_info else "")
        + "\n表情预览："
    )
    img = await generate_meme_preview(meme.key)
    await _send_image(matcher, bot, event, img, info + "\n")


@search_cmd.handle()
async def _search(matcher: Matcher, arg: Message = CommandArg()):
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()

    memes = meme_manager.search(meme_name, include_tags=True, limit=30)
    if not memes:
        await matcher.finish("未找到相关表情")
    await matcher.finish(
        f"找到 {len(memes)} 个相关表情：\n"
        + "\n".join(f"* {m.key} ({'/'.join(m.keywords)})" for m in memes)
    )


@add_whitelist_cmd.handle()
async def _add_whitelist(matcher: Matcher, arg: Message = CommandArg()):
    user_id = _first_mention_id(arg) or arg.extract_plain_text().strip()
    if not user_id:
        matcher.block = False
        await matcher.finish()
    if protection_manager.add_whitelist(user_id):
        await matcher.finish(f"已将 {user_id} 添加到保护白名单")
    await matcher.finish(f"{user_id} 已在白名单中")


@remove_whitelist_cmd.handle()
async def _remove_whitelist(matcher: Matcher, arg: Message = CommandArg()):
    user_id = _first_mention_id(arg) or arg.extract_plain_text().strip()
    if not user_id:
        matcher.block = False
        await matcher.finish()
    if protection_manager.remove_whitelist(user_id):
        await matcher.finish(f"已将 {user_id} 从保护白名单移除")
    await matcher.finish(f"{user_id} 不在白名单中")


@protect_meme_cmd.handle()
async def _protect_meme(matcher: Matcher, arg: Message = CommandArg()):
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()
    meme = await find_meme(matcher, meme_name)
    if protection_manager.add_protected_meme(meme.key):
        await matcher.finish(f"表情 {meme.key} 已添加到保护列表")
    await matcher.finish(f"表情 {meme.key} 已在保护列表中")


@unprotect_meme_cmd.handle()
async def _unprotect_meme(matcher: Matcher, arg: Message = CommandArg()):
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()
    meme = await find_meme(matcher, meme_name)
    if protection_manager.remove_protected_meme(meme.key):
        await matcher.finish(f"表情 {meme.key} 已从保护列表移除")
    await matcher.finish(f"表情 {meme.key} 不在保护列表中")


@protection_list_cmd.handle()
async def _protection_list(matcher: Matcher):
    whitelist = protection_manager.get_whitelist()
    protected_memes = protection_manager.get_protected_memes()
    msg = "【保护配置】\n"
    msg += f"\n保护白名单: {', '.join(whitelist) if whitelist else '（空）'}\n"
    msg += f"\n保护表情: {', '.join(protected_memes) if protected_memes else '（空）'}"
    await matcher.finish(msg)


def _parse_statistics_text(text: str) -> tuple[bool, bool, str, Optional[str]]:
    is_my = "我的" in text
    is_global = "全局" in text

    mapped_type: str = "24h"
    type_map = [
        ({"日", "24小时", "1天"}, "24h"),
        ({"本日", "今日"}, "day"),
        ({"周", "一周", "7天"}, "7d"),
        ({"本周"}, "week"),
        ({"月", "30天"}, "30d"),
        ({"本月", "月度"}, "month"),
        ({"年", "一年"}, "1y"),
        ({"本年", "年度"}, "year"),
    ]
    for keys, typ in type_map:
        if any(k in text for k in keys):
            mapped_type = typ
            break

    meme_name = re.sub(
        r"(我的|全局|日|24小时|1天|本日|今日|周|一周|7天|本周|月|30天|本月|月度|年|一年|本年|年度)",
        " ",
        text,
    ).strip()
    return is_my, is_global, mapped_type, (meme_name or None)


@statistics_cmd.handle()
async def _statistics(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    session: Uninfo,
    arg: Message = CommandArg(),
):
    raw = arg.extract_plain_text().strip()
    if not raw:
        raw = ""
    is_my, is_global, typ, meme_name = _parse_statistics_text(raw)

    meme = await find_meme(matcher, meme_name) if meme_name else None

    if is_my and is_global:
        id_type = SessionIdType.USER
    elif is_my:
        id_type = SessionIdType.GROUP_USER
    elif is_global:
        id_type = SessionIdType.GLOBAL
    else:
        id_type = SessionIdType.GROUP

    now = datetime.now().astimezone()
    if typ == "24h":
        start = now - timedelta(days=1)
        td = timedelta(hours=1)
        fmt = "%H:%M"
        humanized = "24小时"
    elif typ == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        td = timedelta(hours=1)
        fmt = "%H:%M"
        humanized = "本日"
    elif typ == "7d":
        start = now - timedelta(days=7)
        td = timedelta(days=1)
        fmt = "%m/%d"
        humanized = "7天"
    elif typ == "week":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=now.weekday()
        )
        td = timedelta(days=1)
        fmt = "%a"
        humanized = "本周"
    elif typ == "30d":
        start = now - timedelta(days=30)
        td = timedelta(days=1)
        fmt = "%m/%d"
        humanized = "30天"
    elif typ == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        td = timedelta(days=1)
        fmt = "%m/%d"
        humanized = "本月"
    elif typ == "1y":
        start = now - relativedelta(years=1)
        td = relativedelta(months=1)
        fmt = "%y/%m"
        humanized = "一年"
    else:
        start = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)
        td = relativedelta(months=1)
        fmt = "%b"
        humanized = "本年"

    if meme:
        meme_times = await get_meme_generation_times(
            session, id_type, meme_key=meme.key, time_start=start
        )
        meme_keys = [meme.key] * len(meme_times)
    else:
        meme_records = await get_meme_generation_records(session, id_type, time_start=start)
        meme_records = [
            record for record in meme_records if meme_manager.get_meme(record.meme_key)
        ]
        meme_times = [record.time for record in meme_records]
        meme_keys = [record.meme_key for record in meme_records]

    if not meme_times:
        await matcher.finish("暂时没有表情调用记录")

    meme_times = [add_timezone(time) for time in meme_times]
    meme_times.sort()

    def fmt_time(time: datetime) -> str:
        if typ in ["24h", "7d", "30d", "1y"]:
            return (time + td).strftime(fmt)
        return time.strftime(fmt)

    duration_counts: dict[str, int] = {}
    stop = start + td
    count = 0
    key = fmt_time(start)
    for time in meme_times:
        while time >= stop:
            duration_counts[key] = count
            key = fmt_time(stop)
            stop += td
            count = 0
        count += 1
    duration_counts[key] = count
    while stop <= now:
        key = fmt_time(stop)
        stop += td
        duration_counts[key] = 0

    key_counts: dict[str, int] = {}
    for key in meme_keys:
        key_counts[key] = key_counts.get(key, 0) + 1
    key_counts = dict(sorted(key_counts.items(), key=lambda item: item[1]))

    if meme:
        title = (
            f"表情“{'/'.join(meme.keywords)}”{humanized}调用统计"
            f"（总调用次数为 {key_counts.get(meme.key, 0)}）"
        )
        output = await plot_duration_counts(duration_counts, title)
    else:
        title = f"{humanized}表情调用统计（总调用次数为 {sum(key_counts.values())}）"
        meme_counts: dict[str, int] = {}
        for key, count in key_counts.items():
            if m := meme_manager.get_meme(key):
                meme_counts["/".join(m.keywords)] = count
        output = await plot_meme_and_duration_counts(meme_counts, duration_counts, title)

    await _send_image(matcher, bot, event, output, "")


@block_cmd.handle()
async def _block(matcher: Matcher, session: Uninfo, arg: Message = CommandArg()):
    if not _can_edit(session):
        await matcher.finish("权限不足（需要群管或超级用户）")
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()

    meme = await find_meme(matcher, meme_name)
    user_key = get_user_id(session)
    if meme_manager.block(user_key, meme.key):
        await matcher.finish(f"表情 {meme.key} 禁用成功")
    await matcher.finish(f"表情 {meme.key} 已被禁用或已被全局禁用")


@unblock_cmd.handle()
async def _unblock(matcher: Matcher, session: Uninfo, arg: Message = CommandArg()):
    if not _can_edit(session):
        await matcher.finish("权限不足（需要群管或超级用户）")
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()

    meme = await find_meme(matcher, meme_name)
    user_key = get_user_id(session)
    if meme_manager.unblock(user_key, meme.key):
        await matcher.finish(f"表情 {meme.key} 启用成功")
    await matcher.finish(f"表情 {meme.key} 已被全局禁用，请联系超级用户启用")


@block_gl_cmd.handle()
async def _block_gl(matcher: Matcher, arg: Message = CommandArg()):
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()
    meme = await find_meme(matcher, meme_name)
    meme_manager.change_mode(MemeMode.WHITE, meme.key)
    await matcher.finish(f"表情 {meme.key} 已全局禁用")


@unblock_gl_cmd.handle()
async def _unblock_gl(matcher: Matcher, arg: Message = CommandArg()):
    meme_name = arg.extract_plain_text().strip()
    if not meme_name:
        matcher.block = False
        await matcher.finish()
    meme = await find_meme(matcher, meme_name)
    meme_manager.change_mode(MemeMode.BLACK, meme.key)
    await matcher.finish(f"表情 {meme.key} 已全局启用")


@black_list_cmd.handle()
async def _black_list(matcher: Matcher):
    black_list = meme_manager.get_black_list()
    if not black_list:
        await matcher.finish("当前没有全局禁用的表情")
    await matcher.finish("当前全局禁用的表情列表：\n" + "\n".join(black_list))


@refresh_cmd.handle()
async def _refresh(matcher: Matcher):
    await meme_manager.init()
    global _trigger_map
    _trigger_map = _build_trigger_map()
    await matcher.finish("表情更新成功")


@random_cmd.handle()
async def _random(
    bot: Bot,
    event: Event,
    matcher: Matcher,
    session: Uninfo,
    interface: QryItrface,
    arg: Message = CommandArg(),
):
    base_texts, base_images, _ = await extract_inputs(
        bot, event, matcher, session, interface, None, arg
    )

    candidates: list[MemeInfo] = []
    user_key = get_user_id(session)
    for meme in meme_manager.get_memes():
        if not meme_manager.check(user_key, meme.key):
            continue
        images_num = len(base_images)
        texts_num = len(base_texts)
        if (
            session.user.avatar
            and cfg_use_sender_when_no_image()
            and images_num == 0
            and meme.params_type.min_images == 1
        ):
            images_num = 1
        if session.user.avatar and meme.params_type.min_images == 2 and images_num == 1:
            images_num = 2
        if (
            meme.params_type.min_images <= images_num <= meme.params_type.max_images
            and meme.params_type.min_texts <= texts_num <= meme.params_type.max_texts
        ):
            candidates.append(meme)

    if not candidates:
        await matcher.finish("没有找到符合条件的表情")

    meme = random.choice(candidates)
    texts, images, image_user_ids = await extract_inputs(bot, event, matcher, session, interface, meme, arg)

    # 应用表情保护逻辑
    images = await apply_protection(
        meme.key, images, image_user_ids, session.user.avatar
    )

    try:
        result = await generate_meme(meme.key, images, texts, args={})
        await record_meme_generation(session, meme.key)
    except MemeGeneratorException as e:
        await matcher.finish(e.message)

    text = ""
    if cfg_random_meme_show_info():
        text = f"随机表情：{meme.key} ({'/'.join(meme.keywords)})\n"
    if random.random() < cfg_notice_prob():
        text += "注意避免群聊刷屏哦~群管可启用禁用表情\n"
    await _send_image(matcher, bot, event, result, text)


@meme_msg_matcher.handle()
async def _meme(
    bot: Bot,
    event: Event,
    state: T_State,
    matcher: Matcher,
    session: Uninfo,
    interface: QryItrface,
):
    trigger = state.get(MEME_TRIGGER_KEY, "")
    msg = state.get(MEME_MSG_KEY)
    if not trigger or msg is None:
        matcher.block = False
        await matcher.finish()

    meme = await find_meme(matcher, str(trigger))

    user_key = get_user_id(session)
    if not meme_manager.check(user_key, meme.key):
        await matcher.finish("表情已被禁用")

    texts, images, image_user_ids = await extract_inputs(
        bot, event, matcher, session, interface, meme, msg
    )

    # 应用表情保护逻辑
    images = await apply_protection(
        meme.key, images, image_user_ids, session.user.avatar
    )

    try:
        result = await generate_meme(meme.key, images, texts, args={})
        await record_meme_generation(session, meme.key)
    except MemeGeneratorException as e:
        await matcher.finish(e.message)

    text = ""
    if random.random() < cfg_notice_prob():
        text += "注意避免群聊刷屏哦~群管可启用禁用表情\n"
    await _send_image(matcher, bot, event, result, text)


driver = get_driver()


async def _init():
    await meme_manager.init()
    global _trigger_map
    _trigger_map = _build_trigger_map()


@driver.on_startup
async def _():
    asyncio.create_task(_init())
