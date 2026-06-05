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
        "retry_interval_seconds": 30,
        "retry_times": 20,
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
                    {"field": "retry_interval_seconds", "label": "重试间隔秒", "component": "InputNumber", "default": merchant["retry_interval_seconds"], "helpMessage": "刷新点后暂未获取到商品时的重试间隔。"},
                    {"field": "retry_times", "label": "重试次数", "component": "InputNumber", "default": merchant["retry_times"], "helpMessage": "每个刷新点最多重试次数。"},
                    {"field": "request_timeout_seconds", "label": "请求超时秒", "component": "InputNumber", "default": merchant["request_timeout_seconds"], "helpMessage": "抓取远行商人实时数据的 HTTP 超时时间。"},
                    {"field": "push_on_start", "label": "启动时推送", "component": "Switch", "default": merchant["push_on_start"], "helpMessage": "首次启动发现当前商品时是否立即推送。"},
                ],
            },
        ],
    }
