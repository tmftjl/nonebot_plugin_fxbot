"""鸣潮表情插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "api_base": "https://emoji.wuwa.games/apis/api.random-emoji.wuwa.games/v1alpha1",
    "api_token": "",
    "character_cache_ttl": 86400,
    "alias_path": "",
}


def get_ui_schema() -> dict[str, Any]:
    """返回鸣潮表情配置界面 schema。"""
    return {
        "key": "wuwa_emoji",
        "title": "鸣潮表情",
        "cards": [
            {
                "key": "wuwa_emoji",
                "title": "鸣潮表情设置",
                "schemas": [
                    {
                        "field": "api_base",
                        "label": "接口地址",
                        "component": "Input",
                        "default": DEFAULTS["api_base"],
                        "helpMessage": "随机表情接口的前缀地址，一般无需修改。",
                    },
                    {
                        "field": "api_token",
                        "label": "接口 Token",
                        "component": "InputPassword",
                        "default": DEFAULTS["api_token"],
                        "helpMessage": "留空时匿名请求。匿名接口当前可用，Token 用于提高额度。",
                    },
                    {
                        "field": "character_cache_ttl",
                        "label": "角色列表缓存时长",
                        "component": "InputNumber",
                        "default": DEFAULTS["character_cache_ttl"],
                        "helpMessage": "角色列表缓存的有效秒数，默认 86400（一天）。",
                        "componentProps": {"min": 0, "step": 300},
                    },
                    {
                        "field": "alias_path",
                        "label": "角色别名表路径",
                        "component": "Input",
                        "default": DEFAULTS["alias_path"],
                        "helpMessage": (
                            "留空使用插件内置的别名表。填 XutheringWavesUID 的 char_alias.json 绝对路径"
                            "可跟随其自动维护，文件一改下一条命令即生效；路径不可读时自动退回内置表。"
                        ),
                    },
                ],
            },
        ],
    }
