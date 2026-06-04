"""洛克王国插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "resources": {
        "enabled": True,
        "base_url": "",
        "concurrency": 12,
    },
    "merchant": {
        "enabled": True,
        "source_url": "https://rocokingdomworld.org/zh/merchant/",
        "check_interval_seconds": 300,
        "request_timeout_seconds": 15,
        "push_on_start": False,
    },
}


def get_ui_schema() -> dict[str, Any]:
    """返回洛克王国插件配置界面 schema。"""
    resources = DEFAULTS["resources"]
    merchant = DEFAULTS["merchant"]
    return {
        "key": "rocom",
        "title": "洛克王国配置",
        "cards": [
            {
                "key": "rocom.resources",
                "title": "资源下载",
                "schemas": [
                    {"field": "enabled", "label": "启动检查资源", "component": "Switch", "default": resources["enabled"], "helpMessage": "缺少立绘、技能图标、特性图标时自动从 GsCore 资源站下载。"},
                    {"field": "base_url", "label": "指定资源站", "component": "Input", "default": resources["base_url"], "helpMessage": "留空自动测速选择资源站；也可填写如 https://gscore.focalors.com。"},
                    {"field": "concurrency", "label": "下载并发数", "component": "InputNumber", "default": resources["concurrency"], "helpMessage": "资源首次下载较多，建议 8-16。"},
                ],
            },
            {
                "key": "rocom.merchant",
                "title": "远行商人",
                "schemas": [
                    {"field": "enabled", "label": "启用后台推送", "component": "Switch", "default": merchant["enabled"], "helpMessage": "关闭后仍可手动发送“远行商人”查询。"},
                    {"field": "source_url", "label": "数据页面", "component": "Input", "default": merchant["source_url"], "helpMessage": "用于抓取远行商人信息的公开页面。"},
                    {"field": "check_interval_seconds", "label": "检查间隔秒", "component": "InputNumber", "default": merchant["check_interval_seconds"], "helpMessage": "后台轮询间隔，建议不低于 300 秒。"},
                    {"field": "request_timeout_seconds", "label": "请求超时秒", "component": "InputNumber", "default": merchant["request_timeout_seconds"], "helpMessage": "抓取数据页面的 HTTP 超时时间。"},
                    {"field": "push_on_start", "label": "启动时推送", "component": "Switch", "default": merchant["push_on_start"], "helpMessage": "首次启动发现当前商品时是否立即推送。"},
                ],
            },
        ],
    }
