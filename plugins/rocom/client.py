"""远行商人公开页面抓取与解析。"""

from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ...utils.http import get_text_with_browser_fallback
from .config import cfg_merchant

SHANGHAI_TZ = timezone(timedelta(hours=8))


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
    now = datetime.now(SHANGHAI_TZ)
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


def _extract_json_object_after_key(body: str, key: str) -> dict[str, Any] | None:
    """提取页面脚本内指定 key 后面的 JSON 对象。"""
    marker = f'"{key}":'
    start = body.find(marker)
    if start < 0:
        return None
    brace_start = body.find("{", start + len(marker))
    if brace_start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(brace_start, len(body)):
        char = body[idx]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    data = json.loads(body[brace_start : idx + 1])
                except Exception:
                    return None
                return data if isinstance(data, dict) else None
    return None


def _product_from_mapping(item: dict[str, Any], starttime: str = "", endtime: str = "") -> MerchantProduct | None:
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
    windows = {1: ("08:00", "12:00"), 2: ("12:00", "16:00"), 3: ("16:00", "20:00"), 4: ("20:00", "24:00")}
    return windows.get(round_no or 0, ("", ""))


def _extract_initial_products(body: str, fallback_round: int | None) -> tuple[list[MerchantProduct], str, int | None, str]:
    """从页面 initial 数据中读取远行商人商品。"""
    initial = _extract_json_object_after_key(body, "initial")
    if not initial:
        return [], "", None, ""

    round_no = initial.get("round")
    try:
        round_value = int(round_no) if round_no is not None else None
    except Exception:
        round_value = None
    active_round = round_value or fallback_round
    starttime, endtime = _round_window(active_round)

    items = initial.get("items")
    if not isinstance(items, list) or not items:
        rounds = initial.get("rounds")
        if isinstance(rounds, dict) and active_round is not None:
            items = rounds.get(str(active_round)) or rounds.get(active_round) or []
    products: list[MerchantProduct] = []
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            product = _product_from_mapping(item, starttime, endtime)
            if product is not None:
                products.append(product)

    next_refresh = str(initial.get("nextRefreshBeijing") or "").strip()
    updated_at = _parse_iso_time(str(initial.get("fetchedAt") or "").strip())
    return products, next_refresh, active_round, updated_at


def _extract_json_products(body: str) -> list[MerchantProduct]:
    """兜底解析商品 JSON，只匹配带 price/limit/rounds 的商品对象。"""
    products: list[MerchantProduct] = []
    pattern = re.compile(
        r'\{"name"\s*:\s*"(?P<name>[^"]{1,40})"(?:(?!\{).){0,900}?"price(?:Raw)?"\s*:\s*"(?P<price>[^"]*)".{0,900}?"image"\s*:\s*"(?P<image>https?://[^"]+)".{0,900}?"rounds"\s*:\s*\[(?P<rounds>[^\]]*)\]',
        re.S,
    )
    for match in pattern.finditer(body):
        name = html.unescape(match.group("name")).strip()
        if not name or name in {item.name for item in products}:
            continue
        price = html.unescape(match.group("price")).strip()
        detail = f"{name} / 价格 {price}" if price else name
        products.append(MerchantProduct(name=name, image=match.group("image"), starttime="", endtime="", detail=detail))
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
    round_no, remaining_time, fallback_next = _current_round()
    parsed_round = int(round_text) if round_text.isdigit() else round_no or None
    products, initial_next_refresh, initial_round, initial_updated_at = _extract_initial_products(body, parsed_round)
    if initial_round is not None:
        parsed_round = initial_round
    if not products and not (initial_next_refresh or initial_updated_at or initial_round is not None):
        products = _extract_products(body, plain_text)
    product_labels = [f"{item.name}|{item.detail}|{item.image}" for item in products]
    signature_seed = "\n".join([str(parsed_round or ""), modified or initial_updated_at, next_refresh or initial_next_refresh, "\n".join(product_labels)]) or plain_text[:4000]
    signature = hashlib.sha256(signature_seed.encode("utf-8", errors="ignore")).hexdigest()
    return MerchantSnapshot(
        source_url=source_url,
        title=title or "洛克王国世界远行商人",
        round_no=parsed_round,
        updated_at=initial_updated_at or modified,
        next_refresh=next_refresh or fallback_next or initial_next_refresh,
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
    try:
        body = await get_text_with_browser_fallback(
            source_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 FxBot rocom-merchant/1.0"},
        )
    except Exception as exc:
        raise RuntimeError("远行商人数据页面请求失败，请稍后重试") from exc
    return parse_merchant_html(source_url, body)


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
