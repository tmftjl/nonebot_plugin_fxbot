"""本地 music-api 点歌命令。"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import re
import time
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.exception import FinishedException
from nonebot.matcher import Matcher
from nonebot.params import RegexGroup
from nonebot.rule import Rule
from nonebot.typing import T_State
from PIL import Image, ImageDraw

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...adapter import build_message, build_message_segment
from ...utils.fonts import load_font
from ...utils.http import get_shared_async_client
from ...utils.paths import data_dir
from .config import cfg_music

Platform = Literal["qq", "netease"]

P = Plugin("entertain", display_name="娱乐", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

_CACHE_TTL = 600
_LOGIN_TTL = 7 * 24 * 60 * 60
_music_cache: dict[str, tuple[float, tuple[Platform, list["Song"]]]] = {}
_login_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_LOGIN_WATCH_TASKS: dict[str, asyncio.Task[None]] = {}
_AUTH_POOL_CURSORS: dict[str, int] = {}
_LOGIN_SESSION_FILE = data_dir("entertain") / "music_login_sessions.json"
_SELECT_INDEX_STATE = "musicshare_select_index"


@dataclass
class Song:
    """音乐搜索结果。"""

    id: str
    mid: str | None
    vid: str
    song: str
    subtitle: str
    album: str
    singer: str
    cover: str
    pay: str
    time: str
    type: int
    bpm: int
    quality: str
    grp: list["Song"]
    index: int = 0
    link: str | None = None
    interval: str | None = None
    size: str | None = None
    kbps: str | None = None
    url: str | None = None
    search_id: str | None = None
    auth: str | None = None
    auth_owner: str | None = None
    media_id: str | None = None


class MusicLoginRequired(Exception):
    """music-api 提示当前平台需要登录。"""


class MusicPlayUnavailable(Exception):
    """music-api 返回播放失败原因。"""

    def __init__(self, message: str, reason: str = "", detail: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason
        self.detail = detail or {}

    def log_text(self) -> str:
        """生成日志文本。"""
        parts = [self.message]
        if self.reason:
            parts.append(f"reason={self.reason}")
        if self.detail:
            parts.append(f"detail={self.detail}")
        return "; ".join(parts)

    @property
    def is_login_related(self) -> bool:
        """是否像登录态失效。"""
        text = f"{self.reason} {self.message}".lower()
        keys = ("login", "auth", "token", "session", "cookie", "uin", "登录", "登陆", "未登录", "失效")
        return any(key in text for key in keys)


def _load_login_sessions() -> dict[str, dict[str, Any]]:
    """加载本地 music-api 登录会话。"""
    if not _LOGIN_SESSION_FILE.exists():
        return {}
    try:
        data = json.loads(_LOGIN_SESSION_FILE.read_text(encoding="utf-8"))
    except Exception:
        logger.opt(exception=True).warning("[musicshare] 读取音乐登录会话失败")
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(key): value for key, value in data.items() if isinstance(value, dict)}


_LOGIN_SESSIONS: dict[str, dict[str, Any]] = _load_login_sessions()


def _uid(event: Event) -> str:
    """提取用户 ID。"""
    if hasattr(event, "get_user_id"):
        try:
            return str(event.get_user_id())
        except Exception:
            pass
    return str(getattr(event, "user_id", "") or "")


def _normalize_platform(alias: str | None) -> Platform:
    """归一化音乐平台名称。"""
    if not alias:
        default = str(cfg_music()["provider_default"]).lower()
        return "qq" if default == "tencent" else "netease"
    return "qq" if alias.lower() == "qq" else "netease"


async def _has_active_select_session(event: Event, state: T_State) -> bool:
    """仅在当前用户有未过期点歌结果时匹配 #序号。"""
    matched = re.fullmatch(r"[#＃](\d+)", event.get_plaintext().strip())
    if not matched:
        return False

    user_id = _uid(event)
    if not user_id:
        return False
    item = _music_cache.get(user_id)
    if item is None:
        return False

    expires_at, cached = item
    if expires_at < time.time():
        _music_cache.pop(user_id, None)
        return False

    _, songs = cached
    index = int(matched.group(1)) - 1
    if not (0 <= index < len(songs)):
        return False

    state[_SELECT_INDEX_STATE] = index
    return True


def _platform_name_cn(platform: Platform) -> str:
    """返回平台中文名。"""
    return {"qq": "QQ音乐", "netease": "网易云音乐"}[platform]


def _music_api_base() -> str:
    """读取本地 music-api 地址。"""
    return str(cfg_music()["api_base"]).strip().rstrip("/")


def _login_mode() -> str:
    """读取登录账号使用模式。"""
    mode = str(cfg_music()["login_mode"]).strip().lower()
    return "per_user" if mode in {"per_user", "private", "user", "own"} else "shared"


def _login_hint(platform: Platform) -> str:
    """生成登录提示。"""
    provider_hint = "qq" if platform == "qq" else "网易云"
    if _login_mode() == "per_user":
        return f"请先发送 #音乐登录{provider_hint} 登录自己的 {_platform_name_cn(platform)} 账号。"
    return f"当前没有可共用的 {_platform_name_cn(platform)} 登录账号，请先发送 #音乐登录{provider_hint} 扫码登录。"


def _login_cache_key(owner: str, platform: Platform) -> str:
    """生成登录缓存键。"""
    return f"music_login:{platform}:{owner}"


def _login_session_key(owner: str, platform: Platform) -> str:
    """生成登录持久化键。"""
    return f"{platform}:{owner}"


def _save_login_sessions() -> None:
    """只保存已登录账号会话。"""
    try:
        persist_data: dict[str, dict[str, Any]] = {}
        for key, bucket in _LOGIN_SESSIONS.items():
            if not isinstance(bucket, dict):
                continue
            accounts = [
                account
                for account in bucket.get("accounts", [])
                if isinstance(account, dict) and str(account.get("auth") or "").strip()
            ]
            if accounts:
                persist_data[key] = {
                    "provider": bucket.get("provider"),
                    "owner": bucket.get("owner"),
                    "accounts": accounts,
                }
        _LOGIN_SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = _LOGIN_SESSION_FILE.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(persist_data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(_LOGIN_SESSION_FILE)
    except Exception:
        logger.opt(exception=True).warning("[musicshare] 保存音乐登录会话失败")


def _cache_set(key: str, value: dict[str, Any], ttl: int = _LOGIN_TTL) -> None:
    """写入内存缓存。"""
    _login_cache[key] = (time.time() + ttl, value)


def _cache_get(key: str) -> dict[str, Any] | None:
    """读取内存缓存。"""
    item = _login_cache.get(key)
    if item is None:
        return None
    expires_at, value = item
    if expires_at < time.time():
        _login_cache.pop(key, None)
        return None
    return value


def _normalize_login_bucket(owner: str, platform: Platform, data: Any = None) -> dict[str, Any]:
    """规范化登录桶数据。"""
    if not isinstance(data, dict):
        data = {}

    accounts_raw = data.get("accounts")
    accounts = accounts_raw if isinstance(accounts_raw, list) else []
    normalized_accounts: list[dict[str, Any]] = []
    seen_auths: set[str] = set()
    for item in accounts:
        if not isinstance(item, dict):
            continue
        auth = str(item.get("auth") or "").strip()
        if not auth or auth in seen_auths:
            continue
        seen_auths.add(auth)
        normalized_accounts.append({**item, "auth": auth, "owner": owner, "provider": platform})

    pending_raw = data.get("pending")
    pending = pending_raw if isinstance(pending_raw, list) else []
    normalized_pending: list[dict[str, Any]] = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        login_token = str(item.get("loginToken") or "").strip()
        if login_token:
            normalized_pending.append({**item, "loginToken": login_token, "owner": owner, "provider": platform})

    return {
        "provider": platform,
        "owner": owner,
        "accounts": normalized_accounts,
        "pending": normalized_pending,
    }


async def _get_login_session_data(owner: str, platform: Platform) -> dict[str, Any]:
    """获取登录会话桶。"""
    cache_key = _login_cache_key(owner, platform)
    cached = _cache_get(cache_key)
    if cached is not None:
        bucket = _normalize_login_bucket(owner, platform, cached)
    else:
        bucket = _normalize_login_bucket(owner, platform, _LOGIN_SESSIONS.get(_login_session_key(owner, platform)))
    _cache_set(cache_key, bucket)
    return bucket


async def _save_login_bucket(owner: str, platform: Platform, bucket: dict[str, Any]) -> None:
    """保存登录会话桶。"""
    bucket = _normalize_login_bucket(owner, platform, bucket)
    session_key = _login_session_key(owner, platform)
    if bucket.get("accounts"):
        _LOGIN_SESSIONS[session_key] = bucket
    else:
        _LOGIN_SESSIONS.pop(session_key, None)
    _save_login_sessions()
    _cache_set(_login_cache_key(owner, platform), bucket)


async def _add_pending_login(owner: str, platform: Platform, login_token: str) -> dict[str, Any]:
    """新增待确认登录。"""
    bucket = await _get_login_session_data(owner, platform)
    pending = {
        "id": uuid.uuid4().hex,
        "provider": platform,
        "owner": owner,
        "loginToken": login_token,
        "createdAt": time.time(),
    }
    bucket["pending"].append(pending)
    await _save_login_bucket(owner, platform, bucket)
    return pending


async def _update_pending_login(owner: str, platform: Platform, pending_id: str, login_token: str) -> None:
    """更新待确认登录 token。"""
    bucket = await _get_login_session_data(owner, platform)
    for item in bucket["pending"]:
        if item.get("id") == pending_id:
            item["loginToken"] = login_token
            item["updatedAt"] = time.time()
            break
    await _save_login_bucket(owner, platform, bucket)


async def _remove_pending_login(owner: str, platform: Platform, pending_id: str) -> None:
    """删除待确认登录。"""
    bucket = await _get_login_session_data(owner, platform)
    bucket["pending"] = [item for item in bucket["pending"] if item.get("id") != pending_id]
    await _save_login_bucket(owner, platform, bucket)


async def _add_auth_account(
    owner: str,
    platform: Platform,
    auth: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """新增可用登录账号。"""
    bucket = await _get_login_session_data(owner, platform)
    account: dict[str, Any] = {
        "id": uuid.uuid4().hex,
        "provider": platform,
        "owner": owner,
        "auth": auth,
        "createdAt": time.time(),
    }
    if payload:
        for key in ("nickname", "uin", "code"):
            if payload.get(key) is not None:
                account[key] = payload[key]
    bucket["accounts"] = [item for item in bucket["accounts"] if item.get("auth") != auth]
    bucket["accounts"].append(account)
    await _save_login_bucket(owner, platform, bucket)
    return account


async def _remove_auth_account(owner: str, platform: Platform, auth: str) -> None:
    """移除失效登录账号。"""
    bucket = await _get_login_session_data(owner, platform)
    bucket["accounts"] = [item for item in bucket["accounts"] if item.get("auth") != auth]
    await _save_login_bucket(owner, platform, bucket)


def _owners_with_platform(platform: Platform) -> list[str]:
    """列出拥有该平台登录账号的 owner。"""
    owners: list[str] = []
    prefix = f"{platform}:"
    for key in _LOGIN_SESSIONS:
        if key.startswith(prefix):
            owners.append(key.split(":", 1)[1])
    return list(dict.fromkeys(owners))


async def _auth_candidates(user_id: str, platform: Platform) -> list[tuple[str, dict[str, Any]]]:
    """获取可用登录账号候选。"""
    owners = [user_id] if _login_mode() == "per_user" else _owners_with_platform(platform)
    if _login_mode() == "shared" and user_id not in owners:
        owners.append(user_id)

    candidates: list[tuple[str, dict[str, Any]]] = []
    for owner in owners:
        bucket = await _get_login_session_data(owner, platform)
        for account in bucket.get("accounts", []):
            auth = str(account.get("auth") or "").strip()
            if auth:
                candidates.append((owner, account))
    return candidates


async def _next_auth_account(
    user_id: str,
    platform: Platform,
    *,
    exclude: set[str] | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """轮询选择一个登录账号。"""
    candidates = [
        (owner, account)
        for owner, account in await _auth_candidates(user_id, platform)
        if str(account.get("auth") or "") not in (exclude or set())
    ]
    if not candidates:
        return None
    cursor_key = f"{_login_mode()}:{platform}" if _login_mode() == "shared" else f"{user_id}:{platform}"
    index = _AUTH_POOL_CURSORS.get(cursor_key, 0) % len(candidates)
    _AUTH_POOL_CURSORS[cursor_key] = index + 1
    return candidates[index]


def _decode_data_image(image: str) -> bytes | str:
    """解码 music-api 返回的二维码图片。"""
    if image.startswith("data:image/"):
        _, encoded = image.split(",", 1)
        return base64.b64decode(encoded)
    if image.startswith("base64://"):
        return base64.b64decode(image[9:])
    return image


async def _send_login_text(bot: Bot, event: Event, text: str) -> None:
    """发送登录状态文本。"""
    await bot.send(event, build_message(bot, build_message_segment(bot, "text", text)))


async def _music_api_get(path: str, params: dict[str, Any], allow_error_body: bool = False) -> dict[str, Any]:
    """请求本地 music-api。"""
    url = f"{_music_api_base()}{path}"
    client = await get_shared_async_client()
    response = await client.get(url, params=params)
    if response.status_code in {400, 401}:
        try:
            error_text = str((response.json() or {}).get("error") or "")
        except Exception:
            error_text = response.text
        lowered = error_text.lower()
        login_keys = ("auth", "token", "session", "login", "logged")
        if response.status_code == 401 or any(key in lowered for key in login_keys):
            raise MusicLoginRequired()
    response.raise_for_status()

    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("music-api 返回格式异常")
    if data.get("error") and not allow_error_body:
        raise RuntimeError(str(data["error"]))
    return data


async def _watch_login_status(
    bot: Bot,
    event: Event,
    owner: str,
    platform: Platform,
    pending_id: str,
    login_token: str,
) -> None:
    """后台轮询扫码登录状态。"""
    task_key = f"{platform}:{owner}:{pending_id}"
    provider_name = _platform_name_cn(platform)
    provider_hint = "qq" if platform == "qq" else "网易云"
    try:
        for _ in range(60):
            await asyncio.sleep(2)
            bucket = await _get_login_session_data(owner, platform)
            pending = next((item for item in bucket.get("pending", []) if item.get("id") == pending_id), None)
            if not pending:
                return
            login_token = str(pending.get("loginToken") or login_token)
            try:
                data = await _music_api_get("/api/login/poll", {"provider": platform, "loginToken": login_token})
            except Exception as exc:
                logger.debug(f"[musicshare] 自动检查音乐登录状态失败: {exc}")
                continue

            if data.get("loggedIn"):
                auth = str(data.get("auth") or "").strip()
                if not auth:
                    logger.warning("[musicshare] 音乐登录成功但 music-api 未返回 auth")
                    return
                await _remove_pending_login(owner, platform, pending_id)
                await _add_auth_account(owner, platform, auth, data)
                nickname = data.get("nickname")
                suffix = f"：{nickname}" if nickname else ""
                await _send_login_text(bot, event, f"{provider_name} 登录成功{suffix}，现在可以点歌了。")
                return

            next_token = str(data.get("loginToken") or "").strip()
            if next_token and next_token != login_token:
                await _update_pending_login(owner, platform, pending_id, next_token)
                login_token = next_token
            status = str(data.get("status") or "pending")
            if status == "expired" or data.get("refresh"):
                await _remove_pending_login(owner, platform, pending_id)
                await _send_login_text(bot, event, f"{provider_name} 登录二维码已过期，请重新发送 #音乐登录{provider_hint}")
                return
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.opt(exception=True).warning("[musicshare] 自动提示音乐登录状态失败")
    finally:
        if _LOGIN_WATCH_TASKS.get(task_key) is asyncio.current_task():
            _LOGIN_WATCH_TASKS.pop(task_key, None)


def _start_login_watcher(
    bot: Bot,
    event: Event,
    owner: str,
    platform: Platform,
    pending_id: str,
    login_token: str,
) -> None:
    """启动登录状态后台轮询。"""
    task_key = f"{platform}:{owner}:{pending_id}"
    old_task = _LOGIN_WATCH_TASKS.get(task_key)
    if old_task and not old_task.done():
        return
    _LOGIN_WATCH_TASKS[task_key] = asyncio.create_task(
        _watch_login_status(bot, event, owner, platform, pending_id, login_token)
    )


def _format_duration(seconds: Any) -> str:
    """秒数转 mm:ss。"""
    try:
        value = int(seconds)
    except (TypeError, ValueError):
        return ""
    if value <= 0:
        return ""
    return f"{value // 60:02d}:{value % 60:02d}"


def _quality_for_api(platform: Platform) -> str:
    """转换本地 music-api 音质参数。"""
    config = cfg_music()
    if platform == "qq":
        qualities = ["m4a", "128", "320", "flac", "ape"]
        value = config["qq_quality"]
    else:
        qualities = ["standard", "higher", "exhigh", "lossless", "hires", "jyeffect", "sky", "jymaster"]
        value = config["netease_quality"]
    level = int(value)
    level = max(1, min(level, len(qualities)))
    return qualities[level - 1]


async def _search_songs_api(
    platform: Platform,
    keyword: str,
    auth: str | None = None,
    auth_owner: str | None = None,
) -> list[Song]:
    """通过本地 music-api 搜索歌曲。"""
    if not auth:
        raise MusicLoginRequired()
    limit = int(cfg_music()["search_num"])
    data = await _music_api_get(
        "/api/search",
        {"provider": platform, "key": keyword, "limit": limit, "auth": auth},
    )
    search_id = data.get("searchId")
    items = data.get("songs", [])
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        items = []

    results: list[Song] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        song_id = str(item.get("id") or item.get("songid") or item.get("songmid") or "")
        mid = item.get("songmid") or item.get("mid")
        song_type = int(item.get("type", 0) or 0)
        media_id = item.get("mediaId") or item.get("media_id")
        link = item.get("link")
        if not link:
            if platform == "qq" and mid:
                link = f"https://i.y.qq.com/v8/playsong.html?songmid={mid}&type={song_type}"
            elif platform == "netease" and song_id:
                link = f"https://music.163.com/#/song?id={song_id}"
        results.append(
            Song(
                id=song_id,
                mid=str(mid) if mid else None,
                vid=str(item.get("vid", "") or ""),
                song=str(item.get("name") or item.get("song") or "未知歌曲"),
                subtitle=str(item.get("subtitle", "") or ""),
                album=str(item.get("album", "") or ""),
                singer=str(item.get("singer", "未知歌手") or "未知歌手"),
                cover=str(item.get("cover", "") or ""),
                pay=str(item.get("pay", "") or ""),
                time=str(item.get("time") or _format_duration(item.get("duration"))),
                type=song_type,
                bpm=int(item.get("bpm", 0) or 0),
                quality=str(item.get("quality", "") or ""),
                grp=[],
                index=int(item.get("index") or len(results)),
                link=str(link) if link else None,
                search_id=str(search_id) if search_id else None,
                auth=auth,
                auth_owner=auth_owner,
                media_id=str(media_id) if media_id else None,
            )
        )
    return results


def _format_play_error(data: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """格式化播放失败响应。"""
    message = str(data.get("error") or "").strip()
    reason = str(data.get("reason") or "").strip()
    detail_raw = data.get("detail")
    detail = detail_raw if isinstance(detail_raw, dict) else {}
    return message, reason, detail


async def _get_song_url_api(platform: Platform, song: Song, auth: str | None = None) -> str | None:
    """通过本地 music-api 获取播放链接。"""
    token = auth or song.auth
    if not token:
        raise MusicLoginRequired()
    params: dict[str, Any] = {
        "provider": platform,
        "quality": _quality_for_api(platform),
        "auth": token,
    }
    if song.search_id is not None:
        params["searchId"] = song.search_id
        params["index"] = song.index
    elif platform == "qq":
        params["songmid"] = song.mid or song.id
        if song.media_id:
            params["mediaId"] = song.media_id
    else:
        params["id"] = song.id

    data = await _music_api_get("/api/play", params, allow_error_body=True)
    url = data.get("url")
    if url:
        return str(url)
    if data.get("error") or data.get("reason") or data.get("detail"):
        message, reason, detail = _format_play_error(data)
        raise MusicPlayUnavailable(message or "music-api 未返回播放链接", reason=reason, detail=detail)
    return None


async def _search_songs_with_pool(user_id: str, platform: Platform, keyword: str) -> list[Song]:
    """用登录账号池搜索歌曲。"""
    tried: set[str] = set()
    while True:
        selected = await _next_auth_account(user_id, platform, exclude=tried)
        if not selected:
            raise MusicLoginRequired()
        owner, account = selected
        auth = str(account.get("auth") or "")
        tried.add(auth)
        try:
            return await _search_songs_api(platform, keyword, auth=auth, auth_owner=owner)
        except MusicLoginRequired:
            await _remove_auth_account(owner, platform, auth)


async def _get_song_url_with_pool(user_id: str, platform: Platform, song: Song) -> str | None:
    """用登录账号池获取播放链接。"""
    tried: set[str] = set()
    last_play_error: MusicPlayUnavailable | None = None
    if song.auth:
        tried.add(song.auth)
        try:
            url = await _get_song_url_api(platform, song, auth=song.auth)
            if url:
                return url
        except MusicLoginRequired:
            if song.auth_owner:
                await _remove_auth_account(song.auth_owner, platform, song.auth)
        except MusicPlayUnavailable as exc:
            last_play_error = exc
            if exc.is_login_related and song.auth_owner:
                await _remove_auth_account(song.auth_owner, platform, song.auth)

    while True:
        selected = await _next_auth_account(user_id, platform, exclude=tried)
        if not selected:
            if last_play_error:
                if last_play_error.is_login_related:
                    raise MusicLoginRequired()
                raise last_play_error
            if tried:
                return None
            raise MusicLoginRequired()
        owner, account = selected
        auth = str(account.get("auth") or "")
        tried.add(auth)
        fallback_song = replace(song, search_id=None, auth=auth, auth_owner=owner)
        try:
            url = await _get_song_url_api(platform, fallback_song, auth=auth)
            if url:
                return url
        except MusicLoginRequired:
            await _remove_auth_account(owner, platform, auth)
        except MusicPlayUnavailable as exc:
            last_play_error = exc
            if exc.is_login_related:
                await _remove_auth_account(owner, platform, auth)


def _draw_music_list(platform: Platform, keyword: str, songs: list[Song]) -> bytes:
    """按旧版双列卡片排版绘制搜索结果。"""
    bg_color = (240, 242, 245)
    header_color = (64, 84, 180)
    card_bg = (255, 255, 255)
    text_main = (30, 30, 30)
    text_sub = (100, 100, 100)
    accent = (64, 84, 180)

    padding = 30
    columns = 2
    gap_x = 20
    gap_y = 15
    card_h = 70
    col_w = 400
    font_path = Path(__file__).parent / "resource" / "font.ttf"

    font_title = load_font(font_path, 36)
    font_sub = load_font(font_path, 22)
    font_song = load_font(font_path, 26)
    font_artist = load_font(font_path, 20)
    font_badge = load_font(font_path, 20)
    font_footer = load_font(font_path, 18)

    count = min(len(songs), 20)
    rows = (count + columns - 1) // columns
    header_h = 120
    list_h = rows * card_h + max(rows - 1, 0) * gap_y
    footer_h = 50
    width = padding * 2 + col_w * columns + gap_x * (columns - 1)
    height = header_h + list_h + footer_h + padding

    image = Image.new("RGB", (width, height), bg_color)
    draw = ImageDraw.Draw(image)
    draw.rectangle([(0, 0), (width, header_h)], fill=header_color)
    draw.text((padding, 25), f"搜索结果: {keyword}", font=font_title, fill=(255, 255, 255))
    draw.text(
        (padding, 75),
        f"来源: {_platform_name_cn(platform)} | 共找到 {len(songs)} 首歌曲",
        font=font_sub,
        fill=(220, 220, 255),
    )

    start_y = header_h + 20
    for index, song in enumerate(songs[:20]):
        row = index // columns
        col = index % columns
        x = padding + col * (col_w + gap_x)
        y = start_y + row * (card_h + gap_y)
        draw.rounded_rectangle([(x, y), (x + col_w, y + card_h)], radius=8, fill=card_bg)

        badge_size = 36
        bx = x + 15
        by = y + (card_h - badge_size) // 2
        draw.ellipse([(bx, by), (bx + badge_size, by + badge_size)], fill=bg_color)
        idx_str = str(index + 1)
        bbox = draw.textbbox((0, 0), idx_str, font=font_badge)
        draw.text(
            (bx + (badge_size - (bbox[2] - bbox[0])) / 2, by + (badge_size - (bbox[3] - bbox[1])) / 2 - 2),
            idx_str,
            fill=accent,
            font=font_badge,
        )

        text_x = bx + badge_size + 15
        content_w = col_w - (text_x - x) - 10
        song_name = song.song
        while draw.textlength(song_name, font=font_song) > content_w and len(song_name) > 1:
            song_name = song_name[:-2] + "…"
        draw.text((text_x, y + 12), song_name, fill=text_main, font=font_song)
        draw.text((text_x, y + 42), song.singer, fill=text_sub, font=font_artist)

    footer = "发送 #序号 (如 #1) 即可播放"
    bbox = draw.textbbox((0, 0), footer, font=font_footer)
    draw.text(((width - (bbox[2] - bbox[0])) / 2, height - 30), footer, fill=(150, 150, 150), font=font_footer)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


login_matcher = P.on_regex(
    r"^[#＃](?:音乐登录|点歌登录)\s*(qq|网易云|netease)?\s*$",
    name="music_login",
    display_name="音乐登录",
    priority=4,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

login_poll_matcher = P.on_regex(
    r"^[#＃](?:音乐登录状态|点歌登录状态)\s*(qq|网易云|netease)?\s*$",
    name="music_login_poll",
    display_name="音乐登录状态",
    priority=4,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

search_matcher = P.on_regex(
    r"^[#＃]点歌(?:(qq|网易云|netease))?\s*(.*)",
    name="search",
    display_name="点歌",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

select_matcher = P.on_regex(
    r"^[#＃](\d+)\s*$",
    rule=Rule(_has_active_select_session),
    name="select",
    display_name="选择歌",
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@login_matcher.handle()
async def _handle_login(matcher: Matcher, bot: Bot, event: Event, groups: tuple = RegexGroup()) -> None:
    """创建 music-api 登录二维码。"""
    user_id = _uid(event)
    if not user_id:
        await matcher.finish("无法获取用户 ID")
    platform = _normalize_platform(str(groups[0] if groups else "") or None)
    try:
        data = await _music_api_get("/api/login/qr", {"provider": platform})
    except Exception as exc:
        logger.opt(exception=True).warning("[musicshare] 创建音乐登录二维码失败")
        await matcher.finish(f"创建登录二维码失败: {exc}")

    login_token = str(data.get("loginToken") or "")
    image = str(data.get("image") or "")
    if not login_token or not image:
        await matcher.finish("创建登录二维码失败：music-api 返回数据不完整。")
    pending = await _add_pending_login(user_id, platform, login_token)
    _start_login_watcher(bot, event, user_id, platform, str(pending["id"]), login_token)

    provider_hint = "qq" if platform == "qq" else "网易云"
    mode_text = "共用账号" if _login_mode() == "shared" else "独立账号"
    text = (
        f"{_platform_name_cn(platform)} 登录二维码（{mode_text}）\n"
        f"扫码确认后会自动提示，也可发送 #音乐登录状态{provider_hint} 手动检查"
    )
    try:
        await matcher.finish(
            build_message(
                bot,
                build_message_segment(bot, "text", text),
                build_message_segment(bot, "image", _decode_data_image(image)),
            )
        )
    except FinishedException:
        raise
    except Exception:
        logger.opt(exception=True).warning("[musicshare] 登录二维码发送失败")
        await matcher.finish("登录二维码生成成功，但发送失败，请查看后台日志。")


@login_poll_matcher.handle()
async def _handle_login_poll(matcher: Matcher, event: Event, groups: tuple = RegexGroup()) -> None:
    """手动检查 music-api 登录状态。"""
    user_id = _uid(event)
    if not user_id:
        await matcher.finish("无法获取用户 ID")
    platform = _normalize_platform(str(groups[0] if groups else "") or None)
    provider_hint = "qq" if platform == "qq" else "网易云"
    bucket = await _get_login_session_data(user_id, platform)
    if not bucket.get("pending"):
        account_count = len(bucket.get("accounts", []))
        if account_count:
            await matcher.finish(f"{_platform_name_cn(platform)} 已登录 {account_count} 个账号。")
        await matcher.finish(f"还没有待确认的 {_platform_name_cn(platform)} 登录二维码，请先发送 #音乐登录{provider_hint}")

    scanned = False
    errors: list[str] = []
    for pending in list(bucket.get("pending", [])):
        pending_id = str(pending.get("id") or "")
        login_token = str(pending.get("loginToken") or "")
        if not pending_id or not login_token:
            continue
        try:
            data = await _music_api_get("/api/login/poll", {"provider": platform, "loginToken": login_token})
        except Exception as exc:
            logger.opt(exception=True).warning("[musicshare] 轮询音乐登录状态失败")
            errors.append(str(exc))
            continue

        if data.get("loggedIn"):
            auth = str(data.get("auth") or "").strip()
            if not auth:
                await matcher.finish("登录成功但 music-api 未返回 auth。")
            await _remove_pending_login(user_id, platform, pending_id)
            await _add_auth_account(user_id, platform, auth, data)
            nickname = data.get("nickname")
            suffix = f"：{nickname}" if nickname else ""
            await matcher.finish(f"{_platform_name_cn(platform)} 登录成功{suffix}")

        next_token = str(data.get("loginToken") or "").strip()
        if next_token and next_token != login_token:
            await _update_pending_login(user_id, platform, pending_id, next_token)
        status = str(data.get("status") or "pending")
        if status == "expired" or data.get("refresh"):
            await _remove_pending_login(user_id, platform, pending_id)
        elif status == "scanned":
            scanned = True

    if scanned:
        await matcher.finish(f"{_platform_name_cn(platform)} 已扫码，请在手机上确认登录。")
    if errors:
        await matcher.finish(f"检查登录状态失败: {errors[-1]}")
    await matcher.finish(f"{_platform_name_cn(platform)} 尚未登录，请扫码后再试。")


@search_matcher.handle()
async def _handle_search(matcher: Matcher, bot: Bot, event: Event, groups: tuple = RegexGroup()) -> None:
    """搜索歌曲。"""
    alias = str(groups[0] or "") if groups else ""
    keyword = str(groups[1] or "").strip() if groups and len(groups) > 1 else ""
    if not keyword:
        await matcher.finish("请提供关键词，例如：#点歌 晴天")
    platform = _normalize_platform(alias or None)
    user_id = _uid(event)
    if not user_id:
        await matcher.finish("无法获取用户 ID")

    try:
        songs = await _search_songs_with_pool(user_id, platform, keyword)
    except MusicLoginRequired:
        await matcher.finish(_login_hint(platform))
    except Exception as exc:
        logger.opt(exception=True).warning("[musicshare] 搜索歌曲失败")
        await matcher.finish(f"搜索出错: {exc}")
    if not songs:
        await matcher.finish(f"在 {_platform_name_cn(platform)} 未找到相关歌曲")

    _music_cache[user_id] = (time.time() + _CACHE_TTL, (platform, songs))
    image = _draw_music_list(platform, keyword, songs)
    await matcher.finish(build_message(bot, build_message_segment(bot, "image", image)))


@select_matcher.handle()
async def _handle_select(matcher: Matcher, bot: Bot, event: Event, state: T_State) -> None:
    """播放搜索结果中的歌曲。"""
    user_id = _uid(event)
    item = _music_cache.get(user_id)
    if item is None:
        await matcher.skip()
    expires_at, cached = item
    if expires_at < time.time():
        _music_cache.pop(user_id, None)
        await matcher.skip()
    platform, songs = cached
    index = int(state.get(_SELECT_INDEX_STATE, -1))
    if not (0 <= index < len(songs)):
        await matcher.skip()
    song = songs[index]

    try:
        audio_url = await _get_song_url_with_pool(user_id, platform, song)
    except MusicLoginRequired:
        await matcher.finish(_login_hint(platform))
    except MusicPlayUnavailable as exc:
        logger.error(f"[musicshare] 无法播放音乐: {song.song}, 平台: {platform}, 错误: {exc.log_text()}")
        await matcher.finish(f"播放失败：{song.song} - {song.singer}\n{exc}")
    if not audio_url:
        logger.error(f"[musicshare] 无法获取播放链接: {song.song}, 平台: {platform}")
        await matcher.finish(f"播放失败：{song.song} - {song.singer}")

    segment = build_message_segment(bot, "record", audio_url)
    await matcher.finish(build_message(bot, segment))


async def _cleanup_cache_loop() -> None:
    """定期清理过期点歌缓存。"""
    while True:
        await asyncio.sleep(300)
        now = time.time()
        for key, (expires_at, _) in list(_music_cache.items()):
            if expires_at < now:
                _music_cache.pop(key, None)
