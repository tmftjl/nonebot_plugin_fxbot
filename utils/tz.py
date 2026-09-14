"""统一时区（UTC+8）辅助函数。"""

from __future__ import annotations

from datetime import date, datetime, timezone, timedelta

SHANGHAI_TZ = timezone(timedelta(hours=8))


def now() -> datetime:
    """返回当前北京时间。"""
    return datetime.now(SHANGHAI_TZ)


def today() -> date:
    """返回当前北京日期。"""
    return now().date()


def today_str(fmt: str = "%Y-%m-%d") -> str:
    """按北京时区格式化当天。"""
    return now().strftime(fmt)


def now_str(fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """按北京时区格式化当前时刻。"""
    return now().strftime(fmt)


def as_shanghai(value: datetime) -> datetime:
    """归一到北京时间：naive 视为 UTC，aware 则转换。

    适用于 naive 值本身就是 UTC 场景。若 naive 值表达的是本地墙钟时间
    （例如手写的日历日期），用 as_shanghai_local，否则会平白偏移 8 小时。
    """
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc).astimezone(SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def as_shanghai_local(value: datetime) -> datetime:
    """归一到北京时间：naive 视为北京时间（墙钟），aware 则转换。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=SHANGHAI_TZ)
    return value.astimezone(SHANGHAI_TZ)


def to_naive_utc(value: datetime) -> datetime:
    """转为无时区的 UTC，供存储使用。"""
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)
