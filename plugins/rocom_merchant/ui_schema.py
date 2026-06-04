"""远行商人推送控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "merchant": {
        "enabled": True,
        "source_url": "https://rocokingdomworld.org/zh/merchant/",
        "check_interval_seconds": 300,
        "request_timeout_seconds": 15,
        "default_keywords": ["国王球", "棱镜球", "炫彩精灵蛋"],
        "push_on_start": False,
    },
}


def get_ui_schema() -> dict[str, Any]:
    """返回远行商人推送配置界面 schema。"""
    merchant = DEFAULTS["merchant"]
    return {
        "key": "rocom_merchant",
        "title": "远行商人推送配置",
        "cards": [
            {
                "key": "rocom_merchant.merchant",
                "title": "远行商人",
                "schemas": [
                    {"field": "enabled", "label": "启用后台推送", "component": "Switch", "default": merchant["enabled"], "helpMessage": "关闭后仍可手动发送“远行商人”查询。"},
                    {"field": "source_url", "label": "数据页面", "component": "Input", "default": merchant["source_url"], "helpMessage": "用于抓取远行商人信息的公开页面。"},
                    {"field": "check_interval_seconds", "label": "检查间隔秒", "component": "InputNumber", "default": merchant["check_interval_seconds"], "helpMessage": "后台轮询间隔，建议不低于 300 秒。"},
                    {"field": "request_timeout_seconds", "label": "请求超时秒", "component": "InputNumber", "default": merchant["request_timeout_seconds"], "helpMessage": "抓取数据页面的 HTTP 超时时间。"},
                    {"field": "default_keywords", "label": "默认关键词", "component": "Tags", "default": merchant["default_keywords"], "helpMessage": "订阅命令不带关键词时使用；留空表示任意刷新都推送。"},
                    {"field": "push_on_start", "label": "启动时推送", "component": "Switch", "default": merchant["push_on_start"], "helpMessage": "首次启动发现当前商品时是否立即推送。"},
                ],
            },
        ],
    }
