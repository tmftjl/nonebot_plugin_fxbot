"""图库插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "random_picture_open": True,
    "poke_repo": "https://cnb.cool/denfenglai/poke.git",
    "fallback_api": "https://ciallo.hxxn.cc/?name={name}",
    "custom_commands": {},
}


def get_ui_schema() -> dict[str, Any]:
    """返回图库插件配置界面 schema。"""
    return {
        "key": "cultured",
        "title": "图库配置",
        "cards": [
            {
                "key": "cultured",
                "title": "图库",
                "schemas": [
                    {"field": "random_picture_open", "label": "启用随机图片", "component": "Switch", "default": DEFAULTS["random_picture_open"]},
                    {"field": "poke_repo", "label": "戳一戳图库仓库", "component": "Input", "default": DEFAULTS["poke_repo"]},
                    {"field": "fallback_api", "label": "兜底图片 API", "component": "Input", "default": DEFAULTS["fallback_api"]},
                    {
                        "field": "custom_commands",
                        "label": "自定义图库命令",
                        "component": "GSubForm",
                        "default": DEFAULTS["custom_commands"],
                        "componentProps": {
                            "modalProps": {"title": "自定义图库命令"},
                            "schemas": [
                                {"field": "url", "label": "接口地址", "component": "Input", "required": True},
                                {"field": "method", "label": "获取方式", "component": "Input", "default": "direct"},
                                {"field": "regex", "label": "提取正则", "component": "Input", "default": r"https?://[^ ]+"},
                                {"field": "response_text", "label": "回复文本", "component": "Input", "default": ""},
                                {"field": "description", "label": "说明", "component": "Input"},
                            ],
                        },
                    },
                ],
            },
        ],
    }
