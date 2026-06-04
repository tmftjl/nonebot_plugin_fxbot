"""远行商人公开页面抓取与解析。"""

from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ...utils.http import get_shared_async_client
from .config import cfg_merchant


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


def _clean_text(text: str) -> str:
    text = re.sub(r"(?is)<script\b.*?</script>", "\n", text)
    text = re.sub(r"(?is)<style\b.*?</style>", "\n", text)
    text = re.sub(r"(?is)<svg\b.*?</svg>", "\n", text)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</(p|div|li|tr|td|th|h[1-6]|section|article|span)>", "\n", text)
    text = re.sub(r"(?s)<[^>]+>", "\n", text)
    text = html.unescape(text)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _first(patterns: list[str], text: str) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    return ""


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
    now = datetime.now(ZoneInfo("Asia/Shanghai"))
    windows = [(1, 8, 11), (2, 12, 15), (3, 16, 19), (4, 20, 23)]
    for round_no, start_hour, end_hour in windows:
        if start_hour <= now.hour <= end_hour:
            end_at = now.replace(hour=end_hour, minute=59, second=59, microsecond=0)
            left = max(timedelta(), end_at - now)
            hours = int(left.total_seconds() // 3600)
            minutes = int((left.total_seconds() % 3600) // 60)
            label = f"{hours}时{minutes}分" if hours else f"{minutes}分"
            return round_no, label, f"{end_hour + 1:02d}:00" if end_hour < 23 else "次日 08:00"
    return 0, "未刷新", "08:00"


def _guess_image(name: str) -> str:
    """为解析不到图片的商品准备空图标占位。"""
    return ""


def _extract_json_products(body: str) -> list[MerchantProduct]:
    products: list[MerchantProduct] = []
    pattern = re.compile(
        r'"name"\s*:\s*"(?P<name>[^"]{1,40})"(?:(?!\{).){0,900}?"(?:icon|image|imageUrl|icon_url|url)"\s*:\s*"(?P<image>https?://[^"]+)"',
        re.S,
    )
    for match in pattern.finditer(body):
        name = html.unescape(match.group("name")).strip()
        if not name or name in {item.name for item in products}:
            continue
        products.append(MerchantProduct(name=name, image=match.group("image"), starttime="", endtime="", detail=name))
        if len(products) >= 8:
            break
    return products


def _extract_products(body: str, plain_text: str) -> list[MerchantProduct]:
    json_products = _extract_json_products(body)
    if json_products:
        return json_products

    lines = plain_text.splitlines()
    products: list[MerchantProduct] = []
    for idx, line in enumerate(lines):
        if len(products) >= 8:
            break
        if not re.search(r"(限购|价格|洛克贝|钻石|金币|售价)", line):
            continue
        window = [item for item in lines[max(0, idx - 3) : min(len(lines), idx + 4)] if item]
        label = re.sub(r"\s+", " ", " / ".join(window)).strip(" /")
        name = window[0] if window else label[:12]
        if label and name not in {item.name for item in products}:
            products.append(MerchantProduct(name=name, image=_guess_image(name), starttime="", endtime="", detail=label))
    if products:
        return products

    marker = "当前商品"
    pos = plain_text.find(marker)
    if pos < 0:
        pos = plain_text.find("在售商品")
    if pos < 0:
        return []
    chunk = plain_text[pos : pos + 1200]
    stop = re.search(r"\n(刷新|下一次|说明|常见问题|本站)", chunk)
    if stop:
        chunk = chunk[: stop.start()]
    names = [line for line in chunk.splitlines()[1:] if 2 <= len(line) <= 80][:8]
    return [MerchantProduct(name=name, image=_guess_image(name), starttime="", endtime="", detail=name) for name in names]


def parse_merchant_html(source_url: str, body: str) -> MerchantSnapshot:
    """从页面 HTML 中提取远行商人快照。"""
    title = html.unescape(_first([r"<title>(.*?)</title>", r'"name":"([^"]*Traveling Merchant[^"]*)"'], body))
    modified = _parse_iso_time(_first([r'"dateModified"\s*:\s*"([^"]+)"', r'"updatedAt"\s*:\s*"([^"]+)"'], body))
    round_text = _first([r'"name"\s*:\s*"Current round"\s*,\s*"value"\s*:\s*"?(\d+)"?', r"当前轮次\D+(\d+)"], body)
    next_refresh = _first([r"下一次刷新[^0-9]*(\d{1,2}:\d{2})", r"Next refresh[^0-9]*(\d{1,2}:\d{2})"], body)
    plain_text = _clean_text(body)
    products = _extract_products(body, plain_text)
    round_no, remaining_time, fallback_next = _current_round()
    product_labels = [f"{item.name}|{item.detail}|{item.image}" for item in products]
    signature_seed = "\n".join([round_text, modified, next_refresh, "\n".join(product_labels)]) or plain_text[:4000]
    signature = hashlib.sha256(signature_seed.encode("utf-8", errors="ignore")).hexdigest()
    return MerchantSnapshot(
        source_url=source_url,
        title=title or "洛克王国世界远行商人",
        round_no=int(round_text) if round_text.isdigit() else round_no or None,
        updated_at=modified,
        next_refresh=next_refresh or fallback_next,
        remaining_time=remaining_time,
        products=products,
        plain_text=plain_text,
        signature=signature,
    )


async def fetch_merchant_snapshot() -> MerchantSnapshot:
    """抓取远行商人当前快照。"""
    cfg = cfg_merchant()
    source_url = str(cfg.get("source_url") or "").strip()
    if not source_url:
        raise RuntimeError("未配置远行商人数据页面")
    timeout = max(3.0, float(cfg.get("request_timeout_seconds") or 15))
    client = await get_shared_async_client()
    response = await client.get(
        source_url,
        timeout=timeout,
        follow_redirects=True,
        headers={"User-Agent": "FxBot rocom-merchant/1.0"},
    )
    response.raise_for_status()
    return parse_merchant_html(source_url, response.text)


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


def snapshot_matches(snapshot: MerchantSnapshot, keywords: list[str]) -> bool:
    """判断快照是否命中订阅关键词。"""
    cleaned = [item.strip() for item in keywords if item and item.strip()]
    if not cleaned:
        return True
    haystack = "\n".join([snapshot.plain_text, *[item.detail or item.name for item in snapshot.products]])
    return any(keyword in haystack for keyword in cleaned)
