"""实用工具插件控制台配置。"""

from __future__ import annotations

from typing import Any


def get_ui_schema() -> dict[str, Any]:
    """返回实用工具插件配置界面 schema。"""
    return {
        "key": "useful",
        "title": "实用工具配置",
        "cards": [
            {
                "key": "useful.taffy",
                "title": "Taffy 查询",
                "schemas": [
                    {"field": "api_url", "label": "接口地址", "component": "Input"},
                    {"field": "username", "label": "用户名", "component": "Input"},
                    {"field": "password", "label": "密码", "component": "InputPassword"},
                ],
            },
            {
                "key": "useful.waves_analyze",
                "title": "鸣潮评分",
                "schemas": [
                    {"field": "api_url", "label": "接口地址", "component": "Input"},
                    {"field": "token", "label": "Token", "component": "InputPassword"},
                ],
            },
        ],
    }
