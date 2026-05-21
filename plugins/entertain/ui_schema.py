"""娱乐插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "music": {
        "api_base": "http://127.0.0.1:3000",
        "provider_default": "tencent",
        "search_num": 20,
        "login_mode": "shared",
        "qq_quality": 2,
        "netease_quality": 4,
    },
    "box": {
        "only_admin": False,
        "box_blacklist": [],
        "increase_box": False,
        "auto_box_groups": [],
        "avatar_api_url": "https://q4.qlogo.cn/headimg_dl?dst_uin={user_id}&spec=640",
    },
    "reg_time": {
        "qq_reg_time_api_url": "https://api.s01s.cn/API/zcsj/",
        "qq_reg_time_api_key": "",
    },
    "api_urls": {
        "sick_quote_api": "https://oiapi.net/API/SickL/",
        "doro_api": "https://doro-api.hxxn.cc/get",
        "background_api": "",
    },
}


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
                    {"field": "api_base", "label": "本地 music-api 地址", "component": "Input", "default": DEFAULTS["music"]["api_base"], "helpMessage": "本地点歌服务地址。"},
                    {
                        "field": "provider_default",
                        "label": "默认平台",
                        "component": "Select",
                        "default": DEFAULTS["music"]["provider_default"],
                        "helpMessage": "未指定平台时默认搜索哪个音乐源。",
                        "componentProps": {
                            "options": [
                                {"label": "QQ 音乐", "value": "tencent"},
                                {"label": "网易云音乐", "value": "netease"},
                            ]
                        },
                    },
                    {"field": "search_num", "label": "搜索数量", "component": "InputNumber", "default": DEFAULTS["music"]["search_num"], "helpMessage": "每次点歌搜索返回的结果数量。", "componentProps": {"min": 1, "max": 50}},
                    {
                        "field": "login_mode",
                        "label": "登录模式",
                        "component": "Select",
                        "default": DEFAULTS["music"]["login_mode"],
                        "helpMessage": "shared 表示共享登录池，private 表示每个用户独立登录。",
                        "componentProps": {
                            "options": [
                                {"label": "共享登录池", "value": "shared"},
                                {"label": "仅个人登录", "value": "private"},
                            ]
                        },
                    },
                    {"field": "qq_quality", "label": "QQ 音质档位", "component": "InputNumber", "default": DEFAULTS["music"]["qq_quality"], "helpMessage": "QQ 音乐播放质量档位。", "componentProps": {"min": 1, "max": 8}},
                    {"field": "netease_quality", "label": "网易云音质档位", "component": "InputNumber", "default": DEFAULTS["music"]["netease_quality"], "helpMessage": "网易云播放质量档位。", "componentProps": {"min": 1, "max": 8}},
                ],
            },
            {
                "key": "entertain.box",
                "title": "开盒",
                "schemas": [
                    {"field": "only_admin", "label": "仅管理员可用", "component": "Switch", "default": DEFAULTS["box"]["only_admin"], "helpMessage": "开启后只有群管理员才能使用开盒。"},
                    {"field": "increase_box", "label": "启用增强开盒", "component": "Switch", "default": DEFAULTS["box"]["increase_box"], "helpMessage": "入群时自动触发开盒。"},
                    {"field": "box_blacklist", "label": "黑名单用户", "component": "GArrayInput", "default": DEFAULTS["box"]["box_blacklist"], "helpMessage": "这些 QQ 号不会被开盒。"},
                    {"field": "auto_box_groups", "label": "自动开盒群", "component": "GArrayInput", "default": DEFAULTS["box"]["auto_box_groups"], "helpMessage": "仅这些群会触发入群自动开盒。"},
                    {"field": "avatar_api_url", "label": "头像 API", "component": "Input", "default": DEFAULTS["box"]["avatar_api_url"], "helpMessage": "获取 QQ 头像的接口模板。"},
                ],
            },
            {
                "key": "entertain.reg_time",
                "title": "注册时间",
                "schemas": [
                    {"field": "qq_reg_time_api_url", "label": "接口地址", "component": "Input", "default": DEFAULTS["reg_time"]["qq_reg_time_api_url"], "helpMessage": "QQ 注册时间查询接口。"},
                    {"field": "qq_reg_time_api_key", "label": "接口密钥", "component": "InputPassword", "default": DEFAULTS["reg_time"]["qq_reg_time_api_key"], "helpMessage": "注册时间接口的访问密钥。"},
                ],
            },
            {
                "key": "entertain.api_urls",
                "title": "外部接口",
                "schemas": [
                    {"field": "sick_quote_api", "label": "发病语录 API", "component": "Input", "default": DEFAULTS["api_urls"]["sick_quote_api"], "helpMessage": "发病语录接口。"},
                    {"field": "doro_api", "label": "Doro API", "component": "Input", "default": DEFAULTS["api_urls"]["doro_api"], "helpMessage": "Doro 结果接口。"},
                    {"field": "background_api", "label": "背景图 API", "component": "Input", "default": DEFAULTS["api_urls"]["background_api"], "helpMessage": "运势背景图接口，留空则使用内置背景。"},
                ],
            },
        ],
    }
