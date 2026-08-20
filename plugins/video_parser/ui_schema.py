"""视频解析插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "general": {
        "global_enabled": True,
        "use_base64": False,
        "max_file_mb": 80,
        "max_duration_seconds": 480,
        "request_timeout_seconds": 20,
    },
    "platforms": {
        "douyin": True,
        "kuaishou": True,
        "weibo": True,
        "xiaohongshu": True,
        "bilibili": True,
    },
    "douyin": {
        "use_cookie": False,
        "cookie": "",
    },
    "network": {
        "proxy": "",
    },
}


def get_ui_schema() -> dict[str, Any]:
    """返回视频解析插件配置界面 schema。"""
    return {
        "key": "video_parser",
        "title": "视频解析配置",
        "cards": [
            {
                "key": "video_parser.general",
                "title": "通用",
                "schemas": [
                    {"field": "global_enabled", "label": "全局启用", "component": "Switch", "default": DEFAULTS["general"]["global_enabled"], "helpMessage": "关闭后不自动解析任何链接。"},
                    {"field": "use_base64", "label": "Base64 发送视频", "component": "Switch", "default": DEFAULTS["general"]["use_base64"], "helpMessage": "开启后视频内容直接随消息发送；关闭后发送本地路径，需让 NapCat 能访问同一路径。"},
                    {"field": "max_file_mb", "label": "最大文件 MB", "component": "InputNumber", "default": DEFAULTS["general"]["max_file_mb"], "helpMessage": "超过大小的视频不会下载发送。", "componentProps": {"min": 1, "max": 500}},
                    {"field": "max_duration_seconds", "label": "最大时长秒", "component": "InputNumber", "default": DEFAULTS["general"]["max_duration_seconds"], "helpMessage": "超过时长的视频不会下载发送。", "componentProps": {"min": 1, "max": 7200}},
                    {"field": "request_timeout_seconds", "label": "请求超时秒", "component": "InputNumber", "default": DEFAULTS["general"]["request_timeout_seconds"], "helpMessage": "解析和下载请求的超时时间。", "componentProps": {"min": 5, "max": 120}},
                ],
            },
            {
                "key": "video_parser.platforms",
                "title": "平台开关",
                "schemas": [
                    {"field": "douyin", "label": "抖音", "component": "Switch", "default": DEFAULTS["platforms"]["douyin"], "helpMessage": "启用抖音链接解析。"},
                    {"field": "kuaishou", "label": "快手", "component": "Switch", "default": DEFAULTS["platforms"]["kuaishou"], "helpMessage": "启用快手链接解析。"},
                    {"field": "weibo", "label": "微博", "component": "Switch", "default": DEFAULTS["platforms"]["weibo"], "helpMessage": "启用微博视频解析。"},
                    {"field": "xiaohongshu", "label": "小红书", "component": "Switch", "default": DEFAULTS["platforms"]["xiaohongshu"], "helpMessage": "启用小红书视频解析。"},
                    {"field": "bilibili", "label": "B站", "component": "Switch", "default": DEFAULTS["platforms"]["bilibili"], "helpMessage": "启用 B 站视频解析。"},
                ],
            },
            {
                "key": "video_parser.douyin",
                "title": "抖音",
                "schemas": [
                    {"field": "use_cookie", "label": "使用 Cookie", "component": "Switch", "default": DEFAULTS["douyin"]["use_cookie"], "helpMessage": "开启后请求抖音接口时携带下方 Cookie，可提高接口成功率。"},
                    {"field": "cookie", "label": "抖音 Cookie", "component": "Input", "default": DEFAULTS["douyin"]["cookie"], "helpMessage": "从抖音网页开发者工具的请求头复制 Cookie。Cookie 属于登录凭据，请勿分享。", "componentProps": {"placeholder": "odin_tt=...; sid_guard=..."}},
                ],
            },
            {
                "key": "video_parser.network",
                "title": "网络",
                "schemas": [
                    {"field": "proxy", "label": "代理地址", "component": "Input", "default": DEFAULTS["network"]["proxy"], "helpMessage": "留空表示不使用代理。"},
                ],
            },
        ],
    }
