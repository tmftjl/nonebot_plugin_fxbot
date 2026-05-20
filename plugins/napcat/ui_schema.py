"""NapCat 插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "image_display": {
        "enabled": True,
        "type": 2,
        "text": "Ciallo~",
        "list": [
            "你干嘛~",
            "我喜欢你",
            "[图片]",
        ],
        "api": "https://v1.hitokoto.cn/?encode=text",
    },
}


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
                    {"field": "enabled", "label": "启用图片外显", "component": "Switch", "default": DEFAULTS["image_display"]["enabled"]},
                    {"field": "type", "label": "外显类型", "component": "InputNumber", "default": DEFAULTS["image_display"]["type"], "componentProps": {"min": 0}},
                    {"field": "text", "label": "固定外显文本", "component": "Input", "default": DEFAULTS["image_display"]["text"]},
                    {"field": "list", "label": "随机外显文本", "component": "GArrayInput", "default": DEFAULTS["image_display"]["list"]},
                    {"field": "api", "label": "外显文本 API", "component": "Input", "default": DEFAULTS["image_display"]["api"]},
                ],
            },
        ],
    }
