"""实用工具插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "taffy": {
        "api_url": "http://127.0.0.1:8899/stats/api",
        "username": "",
        "password": "",
    },
    "waves_analyze": {
        "api_url": "https://scoreecho.loping151.site/score",
        "token": "f3e6d1d382925f0c63bd296e3e92a314",
    },
}


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
                    {"field": "api_url", "label": "接口地址", "component": "Input", "default": DEFAULTS["taffy"]["api_url"], "helpMessage": "Taffy 统计接口。"},
                    {"field": "username", "label": "用户名", "component": "Input", "default": DEFAULTS["taffy"]["username"], "helpMessage": "接口认证用户名。"},
                    {"field": "password", "label": "密码", "component": "InputPassword", "default": DEFAULTS["taffy"]["password"], "helpMessage": "接口认证密码。"},
                ],
            },
            {
                "key": "useful.waves_analyze",
                "title": "鸣潮评分",
                "schemas": [
                    {"field": "api_url", "label": "接口地址", "component": "Input", "default": DEFAULTS["waves_analyze"]["api_url"], "helpMessage": "鸣潮评分服务地址。"},
                    {"field": "token", "label": "Token", "component": "InputPassword", "default": DEFAULTS["waves_analyze"]["token"], "helpMessage": "评分服务的鉴权令牌。"},
                ],
            },
        ],
    }
