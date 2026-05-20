"""图库插件控制台配置。"""

from __future__ import annotations

from typing import Any


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
                    {"field": "random_picture_open", "label": "启用随机图片", "component": "Switch"},
                    {"field": "poke_repo", "label": "戳一戳图库仓库", "component": "Input"},
                    {"field": "fallback_api", "label": "兜底图片 API", "component": "Input"},
                    {
                        "field": "custom_commands",
                        "label": "自定义图库命令",
                        "component": "GSubForm",
                        "componentProps": {
                            "modalProps": {"title": "自定义图库命令"},
                            "schemas": [
                                {"field": "url", "label": "接口地址", "component": "Input", "required": True},
                                {"field": "description", "label": "说明", "component": "Input"},
                            ],
                        },
                    },
                ],
            },
        ],
    }
