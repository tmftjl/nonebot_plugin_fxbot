"""宿主网络异常类型别名。"""

from __future__ import annotations


class NetworkConnectionError(ConnectionError):
    """与 MaiBot 同名的网络异常，供重试逻辑识别。"""
