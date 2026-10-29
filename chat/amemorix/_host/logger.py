"""A_Memorix 日志桥接：统一走 NoneBot 的 loguru 日志。"""

from __future__ import annotations

from nonebot import logger


def get_logger(name: str):
    """返回带模块标记的日志器。"""
    return logger.bind(module=name or "A_Memorix")
