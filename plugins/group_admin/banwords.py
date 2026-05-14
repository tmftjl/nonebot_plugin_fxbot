"""群违禁词管理和拦截。"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from nonebot import get_driver, logger
from nonebot.adapters import Bot, Event
from nonebot.exception import StopPropagation
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.paths import data_dir

MatchType = Literal["exact", "fuzzy", "regex"]
PenaltyType = Literal["mute", "kick", "recall"]

P = Plugin("group_admin", display_name="群管", enabled=True, level=PermLevel.ADMIN, scene=PermScene.GROUP)

DATA_DIR = data_dir("group_admin") / "banned_words"
DATA_DIR.mkdir(parents=True, exist_ok=True)
_compiled_cache: dict[int, list[tuple[re.Pattern[str], dict[str, Any]]]] = {}
_config_cache: dict[int, dict[str, Any]] = {}


def _gid(event: Event) -> int | None:
    """提取群 ID。"""
    value = getattr(event, "group_id", None)
    if value is None and hasattr(event, "get_group_id"):
        try:
            value = event.get_group_id()
        except Exception:
            value = None
    try:
        return int(value) if value is not None else None
    except Exception:
        return None


def _uid(event: Event) -> int | None:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return int(event.get_user_id())
        except Exception:
            pass
    try:
        return int(getattr(event, "user_id", None))
    except Exception:
        return None


def _plain_text(event: Event) -> str:
    """提取纯文本。"""
    if hasattr(event, "get_plaintext"):
        try:
            return str(event.get_plaintext())
        except Exception:
            pass
    try:
        return str(event.get_message())
    except Exception:
        return ""


class BannedWordsManager:
    """违禁词管理器。"""

    MATCH_TYPE_MAP = {"精确": "exact", "模糊": "fuzzy", "正则": "regex"}
    PENALTY_TYPE_MAP = {"禁": "mute", "踢": "kick", "撤": "recall"}

    @staticmethod
    def _path(group_id: int) -> Path:
        """返回群违禁词配置路径。"""
        return DATA_DIR / f"{group_id}.json"

    @staticmethod
    def _default() -> dict[str, Any]:
        """默认违禁词配置。"""
        return {"banned_words": {}, "config": {"enabled": True, "mute_seconds": 300}}

    @classmethod
    def load_group_data(cls, group_id: int) -> dict[str, Any]:
        """加载群违禁词配置。"""
        path = cls._path(group_id)
        if not path.exists():
            return cls._default()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else cls._default()
        except Exception:
            logger.opt(exception=True).warning(f"[banwords] 加载违禁词配置失败: {group_id}")
            return cls._default()

    @classmethod
    def save_group_data(cls, group_id: int, data: dict[str, Any]) -> None:
        """保存群违禁词配置。"""
        path = cls._path(group_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def invalidate_cache(group_id: int) -> None:
        """清理群违禁词缓存。"""
        _compiled_cache.pop(group_id, None)
        _config_cache.pop(group_id, None)

    @classmethod
    def load_group_config(cls, group_id: int) -> dict[str, Any]:
        """加载群违禁词开关配置。"""
        cached = _config_cache.get(group_id)
        if cached is not None:
            return cached
        data = cls.load_group_data(group_id)
        config = data.get("config", {})
        if not isinstance(config, dict):
            config = {}
        merged = {"enabled": True, "mute_seconds": 300, **config}
        _config_cache[group_id] = merged
        return merged

    @classmethod
    def compile_banned_words(cls, group_id: int) -> list[tuple[re.Pattern[str], dict[str, Any]]]:
        """编译群违禁词规则。"""
        cached = _compiled_cache.get(group_id)
        if cached is not None:
            return cached
        data = cls.load_group_data(group_id)
        banned_words = data.get("banned_words", {})
        compiled: list[tuple[re.Pattern[str], dict[str, Any]]] = []
        if isinstance(banned_words, dict):
            for word, meta in banned_words.items():
                if not isinstance(meta, dict):
                    continue
                match_type = str(meta.get("match_type", "exact"))
                try:
                    if match_type == "exact":
                        pattern = re.compile(f"^{re.escape(str(word))}$")
                    elif match_type == "fuzzy":
                        pattern = re.compile(re.escape(str(word)))
                    elif match_type == "regex":
                        pattern = re.compile(str(word), re.MULTILINE)
                    else:
                        continue
                    compiled.append((pattern, {**meta, "raw_word": str(word)}))
                except re.error:
                    logger.opt(exception=True).warning(f"[banwords] 正则编译失败: {group_id} {word}")
        _compiled_cache[group_id] = compiled
        return compiled

    @classmethod
    async def add_word(
        cls,
        group_id: int,
        word: str,
        match_type: MatchType = "exact",
        penalty_type: PenaltyType = "mute",
        added_by: int = 0,
    ) -> None:
        """添加违禁词。"""
        data = cls.load_group_data(group_id)
        banned_words = data.setdefault("banned_words", {})
        if word in banned_words:
            raise ValueError(f"违禁词已存在: {word}")
        banned_words[word] = {
            "match_type": match_type,
            "penalty_type": penalty_type,
            "added_by": added_by,
            "added_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        cls.save_group_data(group_id, data)
        cls.invalidate_cache(group_id)

    @classmethod
    async def remove_word(cls, group_id: int, word: str) -> None:
        """删除违禁词。"""
        data = cls.load_group_data(group_id)
        banned_words = data.setdefault("banned_words", {})
        if word not in banned_words:
            raise ValueError(f"违禁词不存在: {word}")
        del banned_words[word]
        cls.save_group_data(group_id, data)
        cls.invalidate_cache(group_id)

    @classmethod
    async def clear_words(cls, group_id: int) -> None:
        """清空违禁词。"""
        data = cls.load_group_data(group_id)
        data["banned_words"] = {}
        cls.save_group_data(group_id, data)
        cls.invalidate_cache(group_id)

    @classmethod
    async def set_enabled(cls, group_id: int, enabled: bool) -> None:
        """设置违禁词开关。"""
        data = cls.load_group_data(group_id)
        config = data.setdefault("config", {})
        config["enabled"] = enabled
        cls.save_group_data(group_id, data)
        cls.invalidate_cache(group_id)

    @classmethod
    async def set_mute_seconds(cls, group_id: int, seconds: int) -> None:
        """设置违禁词禁言秒数。"""
        data = cls.load_group_data(group_id)
        config = data.setdefault("config", {})
        config["mute_seconds"] = max(1, seconds)
        cls.save_group_data(group_id, data)
        cls.invalidate_cache(group_id)

    @classmethod
    async def check_message(cls, group_id: int, text: str) -> tuple[str, dict[str, Any]] | None:
        """检查消息是否命中违禁词。"""
        compact_text = re.sub(r"\s+", "", text)
        for pattern, meta in cls.compile_banned_words(group_id):
            try:
                if pattern.search(text) or (compact_text != text and pattern.search(compact_text)):
                    return str(meta.get("raw_word", "")), meta
            except Exception:
                logger.opt(exception=True).warning(f"[banwords] 匹配违禁词异常: {group_id}")
        return None


banword_add = P.on_regex(
    r"^#新增(精确|模糊|正则)?(禁|踢|撤)?违禁词\s*(.+)$",
    name="banword_add",
    display_name="新增违禁词",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

banword_del = P.on_regex(
    r"^#删除违禁词\s*(.+)$",
    name="banword_del",
    display_name="删除违禁词",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

banword_clear = P.on_regex(
    r"^#清空违禁词$",
    name="banword_clear",
    display_name="清空违禁词",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

banword_list = P.on_regex(
    r"^#违禁词列表$",
    name="banword_list",
    display_name="违禁词列表",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

banword_on = P.on_regex(
    r"^#开启违禁词$",
    name="banword_on",
    display_name="开启违禁词",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

banword_off = P.on_regex(
    r"^#关闭违禁词$",
    name="banword_off",
    display_name="关闭违禁词",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)

banword_mute_time = P.on_regex(
    r"^#设置违禁词禁言时间\s*(\d+)$",
    name="banword_mute_time",
    display_name="设置违禁词禁言时间",
    priority=5,
    block=True,
    level=PermLevel.ADMIN,
    scene=PermScene.GROUP,
)


@banword_add.handle()
async def _handle_banword_add(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
    """添加违禁词。"""
    group_id = _gid(event)
    user_id = _uid(event) or 0
    if group_id is None:
        await matcher.finish("请在群聊中使用")
    match_cn = str(groups[0] or "精确")
    penalty_cn = str(groups[1] or "禁")
    word = str(groups[2] or "").strip()
    if not word:
        await matcher.finish("请提供要添加的违禁词")
    try:
        await BannedWordsManager.add_word(
            group_id,
            word,
            BannedWordsManager.MATCH_TYPE_MAP.get(match_cn, "exact"),
            BannedWordsManager.PENALTY_TYPE_MAP.get(penalty_cn, "mute"),
            user_id,
        )
    except ValueError as exc:
        await matcher.finish(str(exc))
    await matcher.finish(f"✅ 已添加违禁词\n词条: {word}\n匹配: {match_cn}\n处罚: {penalty_cn}")


@banword_del.handle()
async def _handle_banword_del(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
    """删除违禁词。"""
    group_id = _gid(event)
    if group_id is None:
        await matcher.finish("请在群聊中使用")
    word = str(groups[0] or "").strip()
    if not word:
        await matcher.finish("请提供要删除的违禁词")
    try:
        await BannedWordsManager.remove_word(group_id, word)
    except ValueError as exc:
        await matcher.finish(str(exc))
    await matcher.finish(f"✅ 已删除违禁词: {word}")


@banword_clear.handle()
async def _handle_banword_clear(matcher: Matcher, event: Event) -> None:
    """清空违禁词。"""
    group_id = _gid(event)
    if group_id is None:
        await matcher.finish("请在群聊中使用")
    await BannedWordsManager.clear_words(group_id)
    await matcher.finish("✅ 已清空所有违禁词")


@banword_list.handle()
async def _handle_banword_list(matcher: Matcher, event: Event) -> None:
    """列出违禁词。"""
    group_id = _gid(event)
    if group_id is None:
        await matcher.finish("请在群聊中使用")
    data = BannedWordsManager.load_group_data(group_id)
    banned_words = data.get("banned_words", {})
    config = {"enabled": True, "mute_seconds": 300, **(data.get("config", {}) or {})}
    enabled = "✅ 开启" if config.get("enabled", True) else "❌ 关闭"
    lines = [f"违禁词检测: {enabled}", f"禁言时长: {config.get('mute_seconds', 300)}秒"]
    if not banned_words:
        lines.append("词条: 暂无")
        await matcher.finish("\n".join(lines))

    match_names = {value: key for key, value in BannedWordsManager.MATCH_TYPE_MAP.items()}
    penalty_names = {value: key for key, value in BannedWordsManager.PENALTY_TYPE_MAP.items()}
    for index, (word, meta) in enumerate(banned_words.items(), 1):
        match_cn = match_names.get(meta.get("match_type", "exact"), "精确")
        penalty_cn = penalty_names.get(meta.get("penalty_type", "mute"), "禁")
        lines.append(f"{index}. [{match_cn}/{penalty_cn}] {word}")
        if index >= 50:
            lines.append(f"... (共{len(banned_words)}条)")
            break
    await matcher.finish("\n".join(lines))


@banword_on.handle()
async def _handle_banword_on(matcher: Matcher, event: Event) -> None:
    """开启违禁词检测。"""
    group_id = _gid(event)
    if group_id is None:
        await matcher.finish("请在群聊中使用")
    await BannedWordsManager.set_enabled(group_id, True)
    await matcher.finish("✅ 已开启违禁词检测")


@banword_off.handle()
async def _handle_banword_off(matcher: Matcher, event: Event) -> None:
    """关闭违禁词检测。"""
    group_id = _gid(event)
    if group_id is None:
        await matcher.finish("请在群聊中使用")
    await BannedWordsManager.set_enabled(group_id, False)
    await matcher.finish("❌ 已关闭违禁词检测")


@banword_mute_time.handle()
async def _handle_banword_mute_time(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
    """设置违禁词禁言时长。"""
    group_id = _gid(event)
    if group_id is None:
        await matcher.finish("请在群聊中使用")
    seconds = int(groups[0] or 300)
    await BannedWordsManager.set_mute_seconds(group_id, seconds)
    await matcher.finish(f"✅ 已设置违禁词禁言时长为 {seconds} 秒")


banword_interceptor = P.on_message(
    name="banword_interceptor",
    display_name="违禁词拦截",
    priority=0,
    block=False,
    level=PermLevel.LOW,
    scene=PermScene.GROUP,
    log=False,
)


async def _is_admin(bot: Bot, event: Event) -> bool:
    """判断消息发送者是否为管理员。"""
    sender = getattr(event, "sender", None)
    role = str(getattr(sender, "role", "") or "")
    if role in {"owner", "admin"}:
        return True
    group_id = _gid(event)
    user_id = _uid(event)
    if group_id is None or user_id is None:
        return False
    try:
        info = await bot.get_group_member_info(group_id=group_id, user_id=user_id)
        return str(info.get("role")) in {"owner", "admin"}
    except Exception:
        return False


def _is_superuser(event: Event) -> bool:
    """判断消息发送者是否为超级用户。"""
    user_id = _uid(event)
    if user_id is None:
        return False
    return str(user_id) in {str(item) for item in get_driver().config.superusers}


@banword_interceptor.handle()
async def _handle_banword_interceptor(bot: Bot, event: Event) -> None:
    """拦截违禁词消息。"""
    group_id = _gid(event)
    user_id = _uid(event)
    if group_id is None or user_id is None:
        return
    config = BannedWordsManager.load_group_config(group_id)
    if not config.get("enabled", True):
        return
    if await _is_admin(bot, event) or _is_superuser(event):
        return
    text = _plain_text(event)
    if not text:
        return
    result = await BannedWordsManager.check_message(group_id, text)
    if not result:
        return

    word, meta = result
    penalty_type = meta.get("penalty_type", "mute")
    mute_seconds = int(config.get("mute_seconds", 300) or 300)
    logger.warning(f"[banwords] 违禁词拦截 [{group_id}] {user_id}: {word} ({penalty_type})")

    message_id = getattr(event, "message_id", None)
    if message_id is not None:
        try:
            await bot.delete_msg(message_id=message_id)
        except Exception:
            logger.opt(exception=True).warning("[banwords] 撤回消息失败")
    if penalty_type == "mute":
        try:
            await bot.set_group_ban(group_id=group_id, user_id=user_id, duration=max(1, mute_seconds))
        except Exception:
            logger.opt(exception=True).warning("[banwords] 禁言失败")
    elif penalty_type == "kick":
        try:
            await bot.set_group_kick(group_id=group_id, user_id=user_id)
        except Exception:
            logger.opt(exception=True).warning("[banwords] 踢出失败")
    raise StopPropagation()
