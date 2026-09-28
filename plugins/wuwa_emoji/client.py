"""鸣潮表情接口客户端。"""

from __future__ import annotations

from typing import Any

from .config import cfg_api_base, cfg_api_token
from ...utils.http import get_shared_async_client, get_bytes_with_browser_fallback

REQUEST_TIMEOUT_SECONDS = 15.0
MEDIA_DOWNLOAD_TIMEOUT_SECONDS = 60.0
MAX_IMAGE_BYTES = 16 * 1024 * 1024
CHARACTER_EMPTY_CODE = "CHARACTER_EMPTY"


class WuwaEmojiError(RuntimeError):
    """鸣潮表情接口请求失败。"""


class CharacterEmptyError(WuwaEmojiError):
    """角色不存在或没有所需格式的公开表情。"""


def _headers() -> dict[str, str]:
    """构造请求头，仅在配置了 Token 时附带鉴权。"""
    token = cfg_api_token()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _error_code(response: Any) -> str:
    """读取错误响应体中的业务错误码，解析失败时返回空串。"""
    try:
        payload = response.json()
    except Exception:
        return ""
    return str(payload.get("code") or "") if isinstance(payload, dict) else ""


async def _request_json(path: str, *, params: dict[str, Any] | None = None) -> Any:
    """请求鸣潮表情 JSON 接口。"""
    url = f"{cfg_api_base()}/{path}"
    client = await get_shared_async_client()
    try:
        response = await client.get(url, params=params, headers=_headers(), timeout=REQUEST_TIMEOUT_SECONDS)
    except Exception as exc:
        raise WuwaEmojiError(f"鸣潮表情接口请求失败：{exc}") from exc

    # 角色无可用表情是预期的业务分支，与真正的请求失败区分开
    if response.status_code == 404 and _error_code(response) == CHARACTER_EMPTY_CODE:
        raise CharacterEmptyError("该角色没有所需格式的公开表情")

    try:
        response.raise_for_status()
        return response.json()
    except Exception as exc:
        raise WuwaEmojiError(f"鸣潮表情接口请求失败：{exc}") from exc


async def fetch_characters() -> list[dict[str, str]]:
    """获取全部角色列表。"""
    payload = await _request_json("characters")
    items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(items, list):
        raise WuwaEmojiError("鸣潮角色列表接口返回了无效数据")

    characters: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if name:
            characters.append({"name": name, "slug": str(item.get("slug") or "").strip()})
    return characters


async def fetch_random_image(character: str | None = None) -> str:
    """随机获取一张表情的图片地址，character 为空时随机任意角色。"""
    payload = await _request_json("random", params={"character": character} if character else None)
    if not isinstance(payload, dict):
        raise WuwaEmojiError("鸣潮表情接口返回了无效数据")

    url = str(payload.get("url") or "").strip()
    if not url:
        raise WuwaEmojiError("鸣潮表情接口未返回图片地址")
    # 返回的是带 ticket 的临时地址，有效期仅 15 分钟，调用方不可缓存
    return url


async def download_image(url: str) -> bytes:
    """下载表情图片数据。

    仅用于平台无法自行下载该地址的场景：媒体站点对非浏览器客户端一律返回
    挑战页，所以必须走浏览器 TLS 指纹，且不能缓存（图片本身也不可复用）。
    """
    try:
        return await get_bytes_with_browser_fallback(
            url,
            timeout=MEDIA_DOWNLOAD_TIMEOUT_SECONDS,
            max_bytes=MAX_IMAGE_BYTES,
        )
    except Exception as exc:
        raise WuwaEmojiError(f"鸣潮表情图片下载失败：{exc}") from exc
