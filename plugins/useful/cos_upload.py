"""COS 图片本地收集。"""

from __future__ import annotations

import hashlib
from pathlib import Path
from datetime import datetime

from nonebot import logger
from nonebot.matcher import Matcher
from nonebot.adapters import Event

from ...plugin import Plugin
from ...adapter import selfBot, event_message
from ...utils.tz import SHANGHAI_TZ, today_str
from ...permission import PermLevel, PermScene
from ...utils.http import get_shared_async_client
from ...utils.paths import data_dir

DATA_DIR = data_dir("useful") / "cos_images"
DATA_DIR.mkdir(parents=True, exist_ok=True)

P = Plugin(
    "useful",
    display_name="实用工具",
    enabled=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


def _image_filename(url: str) -> str:
    """根据图片 URL 生成文件名。"""
    digest = hashlib.md5(url.encode("utf-8")).hexdigest()
    ext = ".jpg"
    suffix = url.split("?", 1)[0].rsplit(".", 1)[-1].lower() if "." in url else ""
    if suffix in {"jpg", "jpeg", "png", "gif", "webp"}:
        ext = f".{suffix}"
    return f"{digest}{ext}"


async def _download_image(url: str, save_path: Path) -> bool:
    """下载图片到本地。"""
    try:
        client = await get_shared_async_client()
        response = await client.get(url)
        response.raise_for_status()
        save_path.parent.mkdir(parents=True, exist_ok=True)
        save_path.write_bytes(response.content)
        return True
    except Exception as exc:
        logger.warning(f"[cos_upload] 下载图片失败 {url}: {exc}")
        return False


cos_upload_cmd = P.on_regex(
    r"[#＃]上传cos",
    name="cos_upload",
    display_name="上传COS图片",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)

cos_list_cmd = P.on_regex(
    r"^[#＃]cos列表",
    name="cos_list",
    display_name="COS图片列表",
    priority=5,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@cos_upload_cmd.handle()
async def _handle_cos_upload(matcher: Matcher, event: Event) -> None:
    """上传消息或回复中的 COS 图片。"""
    sources: list[str | bytes] = selfBot.extract_image_sources(event_message(event))
    sources += await selfBot.reply_image_sources(event)
    # 该命令走 HTTP 下载并按 URL 命名，跳过 base64 内联图
    image_urls = [
        source for source in dict.fromkeys(sources) if isinstance(source, str) and not source.startswith("base64://")
    ]
    if not image_urls:
        await matcher.finish("❌ 未找到图片\n请发送带图消息或回复包含图片的消息")

    success_count = 0
    fail_count = 0
    date_dir = today_str("%Y%m%d")
    for url in image_urls:
        save_path = DATA_DIR / date_dir / _image_filename(url)
        if await _download_image(url, save_path):
            success_count += 1
        else:
            fail_count += 1

    lines = ["📸 COS图片上传完成", f"成功: {success_count} 张"]
    if fail_count:
        lines.append(f"失败: {fail_count} 张")
    await matcher.finish("\n".join(lines))


@cos_list_cmd.handle()
async def _handle_cos_list(matcher: Matcher) -> None:
    """查看 COS 图片库统计。"""
    if not DATA_DIR.exists():
        await matcher.finish("暂无COS图片")
    date_dirs = sorted([path for path in DATA_DIR.iterdir() if path.is_dir()], reverse=True)
    if not date_dirs:
        await matcher.finish("暂无COS图片")

    lines = ["📸 COS图片库"]
    total = 0
    for date_path in date_dirs[:10]:
        count = len([path for path in date_path.iterdir() if path.is_file()])
        total += count
        try:
            label = datetime.strptime(date_path.name, "%Y%m%d").replace(tzinfo=SHANGHAI_TZ).strftime("%Y-%m-%d")
        except Exception:
            label = date_path.name
        lines.append(f"{label}: {count} 张")
    lines.append(f"总计: {total} 张图片")
    await matcher.finish("\n".join(lines))
