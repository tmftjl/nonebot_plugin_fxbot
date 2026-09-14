import asyncio
from datetime import datetime

import httpx
from nonebot.log import logger

from ...utils.tz import as_shanghai, to_naive_utc


class NetworkError(Exception):
    pass


async def download_url(url: str) -> bytes:
    async with httpx.AsyncClient() as client:
        for i in range(3):
            try:
                resp = await client.get(url, timeout=20)
                resp.raise_for_status()
                return resp.content
            except Exception as e:
                logger.warning(f"Error downloading {url}, retry {i}/3: {e}")
                await asyncio.sleep(3)
    raise NetworkError(f"{url} 下载失败！")


def remove_timezone(dt: datetime) -> datetime:
    """移除时区，转为无时区的 UTC 用于存储。"""
    return to_naive_utc(dt)


def add_timezone(dt: datetime) -> datetime:
    """添加时区，归一到北京时间用于展示与分桶。"""
    return as_shanghai(dt)
