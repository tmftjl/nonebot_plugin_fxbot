"""洛克王国插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "resources": {
        "enabled": True,
        "base_url": "",
        "concurrency": 12,
    },
}


def get_ui_schema() -> dict[str, Any]:
    """返回洛克王国插件配置界面 schema。"""
    resources = DEFAULTS["resources"]
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
        ],
    }
