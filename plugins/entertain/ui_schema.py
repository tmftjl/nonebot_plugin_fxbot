"""娱乐插件控制台配置。"""

from __future__ import annotations

from typing import Any


def get_ui_schema() -> dict[str, Any]:
    """返回娱乐插件配置界面 schema。"""
    return {
        "key": "entertain",
        "title": "娱乐配置",
        "cards": [
            {
                "key": "entertain.music",
                "title": "点歌",
                "schemas": [
                    {"field": "api_base", "label": "本地 music-api 地址", "component": "Input"},
                    {
                        "field": "provider_default",
                        "label": "默认平台",
                        "component": "Select",
                        "componentProps": {
                            "options": [
                                {"label": "QQ 音乐", "value": "tencent"},
                                {"label": "网易云音乐", "value": "netease"},
                            ]
                        },
                    },
                    {"field": "search_num", "label": "搜索数量", "component": "InputNumber", "componentProps": {"min": 1, "max": 50}},
                    {
                        "field": "login_mode",
                        "label": "登录模式",
                        "component": "Select",
                        "componentProps": {
                            "options": [
                                {"label": "共享登录池", "value": "shared"},
                                {"label": "仅个人登录", "value": "private"},
                            ]
                        },
                    },
                    {"field": "qq_quality", "label": "QQ 音质档位", "component": "InputNumber", "componentProps": {"min": 1, "max": 8}},
                    {"field": "netease_quality", "label": "网易云音质档位", "component": "InputNumber", "componentProps": {"min": 1, "max": 8}},
                ],
            },
            {
                "key": "entertain.box",
                "title": "开盒",
                "schemas": [
                    {"field": "only_admin", "label": "仅管理员可用", "component": "Switch"},
                    {"field": "increase_box", "label": "启用增强开盒", "component": "Switch"},
                    {"field": "box_blacklist", "label": "黑名单用户", "component": "GArrayInput"},
                    {"field": "auto_box_groups", "label": "自动开盒群", "component": "GArrayInput"},
                    {"field": "avatar_api_url", "label": "头像 API", "component": "Input"},
                ],
            },
            {
                "key": "entertain.reg_time",
                "title": "注册时间",
                "schemas": [
                    {"field": "qq_reg_time_api_url", "label": "接口地址", "component": "Input"},
                    {"field": "qq_reg_time_api_key", "label": "接口密钥", "component": "InputPassword"},
                ],
            },
            {
                "key": "entertain.api_urls",
                "title": "外部接口",
                "schemas": [
                    {"field": "sick_quote_api", "label": "发病语录 API", "component": "Input"},
                    {"field": "doro_api", "label": "Doro API", "component": "Input"},
                    {"field": "background_api", "label": "背景图 API", "component": "Input"},
                ],
            },
        ],
    }
