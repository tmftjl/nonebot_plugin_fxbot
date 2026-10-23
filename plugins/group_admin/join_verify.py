"""入群验证：新成员限时回答图片验证码，答错或超时移出群。"""

from __future__ import annotations

import io
import json
import time
import random
import asyncio
from typing import Any
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont
from nonebot import logger, on_notice, on_message
from nonebot.rule import Rule
from nonebot.params import RegexGroup
from nonebot.matcher import Matcher
from nonebot.adapters import Bot, Event

from . import P
from ...adapter import (
    UnsupportedCapability,
    selfBot,
    normalize_id,
    platform_bot,
    build_message,
    event_user_id,
    event_group_id,
    event_plain_text,
    build_message_segment,
)
from ...permission import PermLevel, PermScene
from ...utils.paths import data_dir, package_root
from ...permission.message_policy import should_process_fxbot_message

_CONFIG_FILE = data_dir("group_admin") / "join_verify.json"
_TIMEOUT = 60
_ATTEMPTS = 2
_KICK = True
_SWEEP_INTERVAL = 3.0
_DIGITS = "23456789"
_FONT_PATH = package_root() / "resources" / "fonts" / "MSGothic.ttc"
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９", "0123456789")

_fonts: dict[int, ImageFont.FreeTypeFont] = {}
_sweeper: asyncio.Task[None] | None = None


@dataclass
class _Pending:
    """一个人的待验证状态。"""

    key: tuple[str, str, str, str]
    bot: Any
    group_id: str
    user_id: str
    code: str
    attempts: int
    tries: int
    kick: bool
    deadline: float


_pending: dict[tuple[str, str, str, str], _Pending] = {}


def _load_configs() -> dict[str, bool]:
    """读取各群的开关状态。"""
    try:
        data = json.loads(_CONFIG_FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.opt(exception=True).warning("[JoinVerify] 读取配置失败")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): bool(value.get("enabled")) for key, value in data.items() if isinstance(value, dict)}


def _group_enabled(group_id: str) -> bool:
    return _load_configs().get(str(group_id), False)


def _set_enabled(group_id: str, enabled: bool) -> None:
    configs = _load_configs()
    configs[str(group_id)] = enabled
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {key: {"enabled": value} for key, value in configs.items()}
    _CONFIG_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _font(size: int) -> ImageFont.FreeTypeFont:
    """按字号缓存字体。"""
    font = _fonts.get(size)
    if font is None:
        font = ImageFont.truetype(str(_FONT_PATH), size)
        _fonts[size] = font
    return font


def _render_captcha(code: str) -> bytes:
    """把验证码画成 PNG：逐字旋转，叠加噪点和横穿折线。"""
    rng = random.SystemRandom()
    width, height = 160, 60
    image = Image.new("RGB", (width, height), (246, 247, 249))
    draw = ImageDraw.Draw(image)
    for _ in range(140):
        gray = rng.randrange(150, 225)
        draw.point((rng.randrange(width), rng.randrange(height)), fill=(gray, gray, gray))
    slot = width // len(code)
    for index, char in enumerate(code):
        layer = Image.new("RGBA", (slot + 12, height), (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(
            (6, 4),
            char,
            font=_font(rng.randrange(36, 46)),
            fill=(rng.randrange(0, 105), rng.randrange(0, 105), rng.randrange(0, 105), 255),
        )
        layer = layer.rotate(rng.uniform(-26, 26), resample=Image.BICUBIC)
        image.paste(layer, (index * slot - 4, rng.randint(-5, 5)), layer)
    for _ in range(3):
        points = [(x, rng.randrange(height)) for x in range(0, width + 10, 8)]
        draw.line(points, fill=(rng.randrange(120, 185),) * 3, width=rng.choice([1, 2]))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _platform(bot: Any) -> str:
    adapter = getattr(bot, "adapter", None)
    get_name = getattr(adapter, "get_name", None)
    return str(get_name() or "unknown") if callable(get_name) else "unknown"


def _pending_key(bot: Any, event: Any) -> tuple[str, str, str, str] | None:
    group_id = normalize_id(event_group_id(event))
    user_id = normalize_id(event_user_id(event))
    if not group_id or not user_id:
        return None
    return (_platform(bot), str(getattr(bot, "self_id", "") or ""), group_id, user_id)


def _is_pending(bot: Bot, event: Event) -> bool:
    key = _pending_key(bot, event)
    return key is not None and key in _pending


async def _is_group_admin(bot: Any, group_id: str, user_id: str) -> bool:
    """群主和管理员不验证；查不到身份时按普通成员处理。"""
    try:
        role = await platform_bot(bot).get_group_member_role(group_id, user_id)
    except Exception:
        return False
    return str(role or "") in {"admin", "owner", "administrator"}


async def _kick(bot: Any, group_id: str, user_id: str) -> bool:
    try:
        await platform_bot(bot).kick(group_id, user_id)
        return True
    except UnsupportedCapability:
        logger.info(f"[JoinVerify] 当前平台不支持自动移出: group={group_id}")
        return False
    except Exception as exc:
        logger.opt(exception=True).warning(f"[JoinVerify] 移出成员失败: group={group_id} user={user_id} err={exc}")
        return False


async def _notify(bot: Any, group_id: str, user_id: str, text: str) -> None:
    """在群里 @ 某人发一条提示。"""
    try:
        await platform_bot(bot).send_group_message(
            group_id,
            build_message(
                bot,
                build_message_segment(bot, "at", user_id),
                build_message_segment(bot, "text", f" {text}"),
            ),
        )
    except Exception as exc:
        logger.opt(exception=True).warning(f"[JoinVerify] 发送提示失败: group={group_id} err={exc}")


def _ensure_sweeper() -> None:
    global _sweeper
    if _sweeper is None:
        _sweeper = asyncio.create_task(_sweep_loop())


async def _sweep_loop() -> None:
    """待验证为空时自动退出，下次入群再拉起。"""
    global _sweeper
    try:
        while _pending:
            await asyncio.sleep(_SWEEP_INTERVAL)
            now = time.monotonic()
            for record in [item for item in _pending.values() if item.deadline <= now]:
                if _pending.pop(record.key, None) is not None:
                    await _expire(record)
    finally:
        _sweeper = None


async def _expire(record: _Pending) -> None:
    if record.kick and await _kick(record.bot, record.group_id, record.user_id):
        await _notify(record.bot, record.group_id, record.user_id, "验证超时，已移出本群")
    else:
        await _notify(record.bot, record.group_id, record.user_id, "验证超时，请管理员手动处理")


join_notice = on_notice(priority=10, block=False, rule=Rule(should_process_fxbot_message))


@join_notice.handle()
async def _handle_join(bot: Bot, event: Event) -> None:
    """新成员入群时发验证码。"""
    join_info = selfBot.extract_group_member_add(event)
    if not join_info:
        return
    group_id = join_info["group_id"]
    user_id = join_info["user_id"]
    if not group_id or not user_id or user_id == str(getattr(bot, "self_id", "") or ""):
        return
    if not _group_enabled(group_id):
        return
    key = (_platform(bot), str(getattr(bot, "self_id", "") or ""), group_id, user_id)
    if key in _pending:
        return
    if await _is_group_admin(bot, group_id, user_id):
        return
    code = "".join(random.SystemRandom().choice(_DIGITS) for _ in range(4))
    image = await asyncio.to_thread(_render_captcha, code)
    _pending[key] = _Pending(
        key=key,
        bot=bot,
        group_id=group_id,
        user_id=user_id,
        code=code,
        attempts=_ATTEMPTS,
        tries=0,
        kick=_KICK,
        deadline=time.monotonic() + _TIMEOUT,
    )
    _ensure_sweeper()
    hint = f" 请在 {_TIMEOUT} 秒内回复图中 4 位数字，答错 {_ATTEMPTS} 次或超时将被移出本群。"
    await selfBot.send(
        event,
        build_message(
            bot,
            build_message_segment(bot, "at", user_id),
            build_message_segment(bot, "text", hint),
            build_message_segment(bot, "image", image),
        ),
    )


answer_matcher = on_message(priority=1, block=True, rule=Rule(_is_pending) & Rule(should_process_fxbot_message))


@answer_matcher.handle()
async def _handle_answer(bot: Bot, event: Event, matcher: Matcher) -> None:
    """验证期间该成员的所有消息都在这里消费，不再进入命令和 AI。"""
    key = _pending_key(bot, event)
    record = _pending.get(key) if key else None
    if record is None:
        return
    answer = "".join(str(event_plain_text(event) or "").translate(_FULLWIDTH_DIGITS).split())
    if not answer:
        await _notify(bot, record.group_id, record.user_id, "请在60s内回复图中的 4 位数字")
        return
    if answer == record.code:
        _pending.pop(key, None)
        await _notify(bot, record.group_id, record.user_id, "验证通过，欢迎加入！")
        await matcher.finish()
    record.tries += 1
    remaining = record.attempts - record.tries
    if remaining > 0:
        await _notify(bot, record.group_id, record.user_id, f"答案不对，还有 {remaining} 次机会")
        await matcher.finish()
    _pending.pop(key, None)
    if record.kick and await _kick(bot, record.group_id, record.user_id):
        await _notify(bot, record.group_id, record.user_id, "验证未通过，已移出本群")
    else:
        await _notify(bot, record.group_id, record.user_id, "验证未通过，可能是人机，请管理员手动处理")
    await matcher.finish()


verify_cmd = P.on_regex(
    r"^[#＃](开启|关闭)进群验证\s*$",
    name="join_verify",
    display_name="进群验证",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)


@verify_cmd.handle()
async def _handle_verify_cmd(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
    group_id = normalize_id(event_group_id(event))
    if not group_id:
        await matcher.finish("请在群聊中使用")
    enabled = bool(groups) and groups[0] == "开启"
    _set_enabled(group_id, enabled)
    await matcher.finish(f"已{'开启' if enabled else '关闭'}进群验证")
