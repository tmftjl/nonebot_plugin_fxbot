"""NapCat 插件控制台配置。"""

from __future__ import annotations

from typing import Any


def get_ui_schema() -> dict[str, Any]:
    """返回 NapCat 插件配置界面 schema。"""
    return {
        "key": "napcat",
        "title": "NapCat 配置",
        "cards": [
            {
                "key": "napcat.image_display",
                "title": "图片外显",
                "schemas": [
                    {"field": "enabled", "label": "启用图片外显", "component": "Switch"},
                    {"field": "type", "label": "外显类型", "component": "InputNumber", "componentProps": {"min": 0}},
                    {"field": "text", "label": "固定外显文本", "component": "Input"},
                    {"field": "list", "label": "随机外显文本", "component": "GArrayInput"},
                    {"field": "api", "label": "外显文本 API", "component": "Input"},
                ],
            },
        ],
    }
