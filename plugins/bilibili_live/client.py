"""B 站直播信息查询。"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any

from ...utils.http import get_shared_async_client

ROOM_INFO_API = "https://api.live.bilibili.com/room/v1/Room/get_info"
USER_INFO_API = "https://api.bilibili.com/x/space/acc/info"
REQUEST_TIMEOUT_SECONDS = 15.0
BILI_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": "https://live.bilibili.com/",
}


class BilibiliLiveError(RuntimeError):
    """B 站直播查询失败。"""


@dataclass(frozen=True, slots=True)
class LiveRoomSnapshot:
    """直播间当前快照。"""

    room_id: int
    uid: int
    name: str
    title: str
    area: str
    cover: str
    live_status: int
    live_time: str

    @property
    def is_live(self) -> bool:
        """返回直播间是否正在直播。"""
        return self.live_status == 1

    @property
    def url(self) -> str:
        """返回直播间地址。"""
        return f"https://live.bilibili.com/{self.room_id}"

    def to_record(self) -> dict[str, Any]:
        """转换为可持久化的直播间记录。"""
        return asdict(self)


def parse_room_id(value: str) -> int:
    """从直播间号或链接中提取展示房间号。"""
    text = value.strip()
    match = re.search(r"live\.bilibili\.com/(?:blanc/)?(\d+)", text, re.I)
    if match is None:
        match = re.fullmatch(r"\s*(\d+)\s*", text)
    if match is None:
        raise BilibiliLiveError("请输入 B 站直播间号或 live.bilibili.com 链接")
    room_id = int(match.group(1))
    if room_id <= 0:
        raise BilibiliLiveError("直播间号必须大于 0")
    return room_id


async def _request_json(url: str, *, params: dict[str, Any]) -> dict[str, Any]:
    """请求 B 站 JSON 接口并校验通用返回结构。"""
    client = await get_shared_async_client()
    try:
        response = await client.get(
            url,
            params=params,
            headers=BILI_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:
        raise BilibiliLiveError(f"B 站接口请求失败：{exc}") from exc

    if not isinstance(payload, dict):
        raise BilibiliLiveError("B 站接口返回了无效数据")
    try:
        code = int(payload.get("code", -1))
    except (TypeError, ValueError) as exc:
        raise BilibiliLiveError("B 站接口返回了无效状态码") from exc
    if code != 0:
        message = str(payload.get("message") or payload.get("msg") or "未知错误")
        raise BilibiliLiveError(f"B 站接口返回错误 {code}：{message}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise BilibiliLiveError("直播间不存在或暂时无法访问")
    return data


async def fetch_anchor_name(uid: int) -> str:
    """查询主播昵称；失败时由调用方使用 UID 兜底。"""
    try:
        data = await _request_json(USER_INFO_API, params={"mid": uid})
    except BilibiliLiveError:
        return ""
    return str(data.get("name") or "").strip()


async def fetch_room(room_id: int, *, known_name: str = "") -> LiveRoomSnapshot:
    """获取直播间当前信息，并将短号解析为真实房间号。"""
    data = await _request_json(ROOM_INFO_API, params={"room_id": room_id})
    uid = int(data.get("uid") or 0)
    canonical_room_id = int(data.get("room_id") or 0)
    if uid <= 0 or canonical_room_id <= 0:
        raise BilibiliLiveError("直播间不存在或没有有效主播")

    name = known_name.strip() or await fetch_anchor_name(uid)
    return LiveRoomSnapshot(
        room_id=canonical_room_id,
        uid=uid,
        name=name or f"UID {uid}",
        title=str(data.get("title") or "未设置直播标题").strip(),
        area=str(data.get("area_name") or data.get("parent_area_name") or "未知分区").strip(),
        cover=str(data.get("keyframe") or data.get("user_cover") or "").strip(),
        live_status=int(data.get("live_status") or 0),
        live_time=str(data.get("live_time") or "").strip(),
    )
