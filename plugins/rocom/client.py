"""远行商人公开页面抓取与解析。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ...utils.http import get_text_with_browser_fallback

SHANGHAI_TZ = timezone(timedelta(hours=8))
MERCHANT_LIVE_URL = "https://rocokingdomworld.org/api/merchant/live"
MERCHANT_REQUEST_TIMEOUT_SECONDS = 15.0


@dataclass(slots=True)
class MerchantProduct:
    """远行商人商品。"""

    name: str
    image: str
    starttime: str
    endtime: str
    detail: str


@dataclass(slots=True)
class MerchantSnapshot:
    """远行商人当前快照。"""

    source_url: str
    title: str
    round_no: int | None
    updated_at: str
    next_refresh: str
    remaining_time: str
    products: list[MerchantProduct]
    plain_text: str
    signature: str


def _parse_iso_time(value: str) -> str:
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return value


def _current_round() -> tuple[int, str, str]:
    """计算当前北京时间轮次和起止时间。"""
    now = datetime.now(SHANGHAI_TZ)
    windows = [(1, 8, 11), (2, 12, 15), (3, 16, 19), (4, 20, 23)]
    for round_no, start_hour, end_hour in windows:
        if start_hour <= now.hour <= end_hour:
            end_at = now.replace(hour=end_hour, minute=59, second=59, microsecond=0)
            left = max(timedelta(), end_at - now)
            hours = int(left.total_seconds() // 3600)
            minutes = int((left.total_seconds() % 3600) // 60)
            label = f"{hours}时{minutes}分" if hours else f"{minutes}分"
            return (
                round_no,
                label,
                f"{end_hour + 1:02d}:00" if end_hour < 23 else "次日 08:00",
            )
    return 0, "未刷新", "08:00"


def _guess_image(name: str) -> str:
    """为解析不到图片的商品准备空图标占位。"""
    return ""


def _product_from_mapping(
    item: dict[str, Any], starttime: str = "", endtime: str = ""
) -> MerchantProduct | None:
    """将页面商品对象转换为内部商品结构。"""
    name = str(item.get("name") or "").strip()
    if not name:
        return None
    price = str(item.get("priceRaw") or item.get("price") or "").strip()
    limit = str(item.get("limit") or "").strip()
    detail_parts = [name]
    if price:
        detail_parts.append(f"价格 {price}")
    if limit:
        detail_parts.append(f"限购 {limit}")
    return MerchantProduct(
        name=name,
        image=str(item.get("image") or "").strip(),
        starttime=starttime,
        endtime=endtime,
        detail=" / ".join(detail_parts),
    )


def _round_window(round_no: int | None) -> tuple[str, str]:
    """返回轮次起止时间。"""
    windows = {
        1: ("08:00", "12:00"),
        2: ("12:00", "16:00"),
        3: ("16:00", "20:00"),
        4: ("20:00", "24:00"),
    }
    return windows.get(round_no or 0, ("", ""))


def _snapshot_signature(
    status: str, round_no: int | None, started_at: str, products: list[MerchantProduct]
) -> str:
    """生成只反映当前营业状态和商品内容的稳定签名。"""
    product_labels = [f"{item.name}|{item.detail}|{item.image}" for item in products]
    signature_seed = "\n".join([status, str(round_no or ""), started_at, "\n".join(product_labels)])
    return hashlib.sha256(signature_seed.encode("utf-8", errors="ignore")).hexdigest()


def parse_merchant_json(source_url: str, data: dict[str, Any]) -> MerchantSnapshot:
    """从实时 JSON 数据中提取远行商人快照。"""
    status = str(data.get("status") or "").strip().lower()
    round_value = data.get("round")
    try:
        round_no = int(round_value) if round_value is not None else None
    except Exception:
        round_no = None
    starttime, endtime = _round_window(round_no)

    items = data.get("items")
    if (not isinstance(items, list) or not items) and round_no is not None:
        rounds = data.get("rounds")
        if isinstance(rounds, dict):
            items = rounds.get(str(round_no)) or rounds.get(round_no) or []

    products: list[MerchantProduct] = []
    if status == "open" and isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            product = _product_from_mapping(item, starttime, endtime)
            if product is not None:
                products.append(product)

    current_round, remaining_time, fallback_next = _current_round()
    if round_no is None and status == "open":
        round_no = current_round or None
    started_at = str(data.get("startedAtBeijing") or "").strip()
    next_refresh = str(data.get("nextRefreshBeijing") or fallback_next).strip()
    updated_at = _parse_iso_time(str(data.get("fetchedAt") or "").strip())
    signature = _snapshot_signature(
        status or ("open" if products else "closed"), round_no, started_at, products
    )

    return MerchantSnapshot(
        source_url=source_url,
        title="洛克王国世界远行商人",
        round_no=round_no,
        updated_at=updated_at,
        next_refresh=next_refresh,
        remaining_time=remaining_time,
        products=products,
        plain_text=json.dumps(data, ensure_ascii=False),
        signature=signature,
    )


async def fetch_merchant_snapshot() -> MerchantSnapshot:
    """抓取远行商人当前快照。"""
    headers = {"User-Agent": "Mozilla/5.0 FxBot rocom-merchant/1.0"}
    try:
        body = await get_text_with_browser_fallback(
            MERCHANT_LIVE_URL,
            timeout=MERCHANT_REQUEST_TIMEOUT_SECONDS,
            follow_redirects=True,
            headers=headers,
        )
    except Exception as exc:
        raise RuntimeError("远行商人实时数据请求失败，请稍后重试") from exc
    try:
        data = json.loads(body)
    except Exception as exc:
        raise RuntimeError("远行商人实时数据不是有效 JSON") from exc
    if not isinstance(data, dict):
        raise RuntimeError("远行商人实时数据格式异常")
    return parse_merchant_json(MERCHANT_LIVE_URL, data)


def format_snapshot(snapshot: MerchantSnapshot) -> str:
    """格式化远行商人快照。"""
    lines = ["远行商人信息"]
    if snapshot.round_no is not None:
        lines.append(f"当前轮次：{snapshot.round_no}")
    if snapshot.next_refresh:
        lines.append(f"下次刷新：{snapshot.next_refresh}")
    if snapshot.remaining_time:
        lines.append(f"剩余时间：{snapshot.remaining_time}")
    if snapshot.updated_at:
        lines.append(f"页面更新：{snapshot.updated_at}")
    if snapshot.products:
        lines.append("商品：")
        lines.extend(f"- {item.detail or item.name}" for item in snapshot.products[:8])
    else:
        lines.append("商品：页面已更新，但未能稳定解析商品明细")
    lines.append(f"来源：{snapshot.source_url}")
    return "\n".join(lines)
