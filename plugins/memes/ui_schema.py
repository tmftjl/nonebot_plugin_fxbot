"""表情包插件控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "base_url": "http://127.0.0.1:2233",
    "command_prefixes": None,
    "disabled_list": [],
    "check_resources_on_startup": True,
    "random_meme_show_info": True,
    "notice_prob": 0.1,
    "use_gif": False,
    "use_ban_word": True,
}


def get_ui_schema() -> dict[str, Any]:
    """返回表情包插件配置界面 schema。"""
    return {
        "key": "memes",
        "title": "表情包制作",
        "cards": [
            {
                "key": "memes",
                "title": "表情包制作设置",
                "schemas": [
                    {"field": "base_url", "label": "meme-generator 地址", "component": "Input", "default": DEFAULTS["base_url"], "helpMessage": "表情生成服务地址。"},
                    {"field": "command_prefixes", "label": "命令前缀", "component": "GTags", "default": DEFAULTS["command_prefixes"], "helpMessage": "留空时使用 NoneBot 默认命令前缀。"},
                    {"field": "disabled_list", "label": "全局禁用表情", "component": "GTags", "default": DEFAULTS["disabled_list"], "helpMessage": "填表情 key。"},
                    {"field": "check_resources_on_startup", "label": "启动时检查资源", "component": "Switch", "default": DEFAULTS["check_resources_on_startup"], "helpMessage": "启动时检查表情资源是否可用。"},
                    {"field": "random_meme_show_info", "label": "随机表情显示关键词", "component": "Switch", "default": DEFAULTS["random_meme_show_info"], "helpMessage": "发送随机表情时附带关键词。"},
                    {"field": "notice_prob", "label": "刷屏提醒概率", "component": "InputNumber", "default": DEFAULTS["notice_prob"], "helpMessage": "随机触发提醒消息的概率。", "componentProps": {"min": 0, "max": 1, "step": 0.01}},
                    {"field": "use_gif", "label": "启用 GIF 输出", "component": "Switch", "default": DEFAULTS["use_gif"], "helpMessage": "把表情转换为 GIF 再发送。"},
                    {"field": "use_ban_word", "label": "启用屏蔽词", "component": "Switch", "default": DEFAULTS["use_ban_word"], "helpMessage": "过滤敏感词。"},
                ],
            },
        ],
    }
