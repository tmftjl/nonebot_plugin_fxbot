"""群聊日报控制台配置。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {
    "basic": {
        "enabled": True,
        "groups": [],
        "days": 1,
        "max_days": 7,
        "max_messages": 1000,
        "min_messages": 30,
        "filter_self": True,
    },
    "auto": {
        "auto_groups": [],
        "auto_hour": 8,
        "auto_minute": 0,
    },
}


def get_ui_schema() -> dict[str, Any]:
    """返回群聊日报配置界面 schema。"""
    return {
        "key": "group_analysis",
        "title": "群聊日报",
        "cards": [
            {
                "key": "group_analysis.basic",
                "title": "基础设置",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用群聊日报",
                        "component": "Switch",
                        "default": DEFAULTS["basic"]["enabled"],
                        "helpMessage": "关闭后手动命令和自动日报都不再执行。",
                    },
                    {
                        "field": "groups",
                        "label": "允许分析的群",
                        "component": "GArrayInput",
                        "default": DEFAULTS["basic"]["groups"],
                        "helpMessage": "填写群号；留空表示所有群都可以使用 /群聊分析。",
                    },
                    {
                        "field": "days",
                        "label": "默认分析天数",
                        "component": "InputNumber",
                        "default": DEFAULTS["basic"]["days"],
                        "helpMessage": "自动日报固定分析前一天；手动命令不带天数时使用该值。",
                        "componentProps": {"min": 1, "max": 30},
                    },
                    {
                        "field": "max_days",
                        "label": "手动命令最大天数",
                        "component": "InputNumber",
                        "default": DEFAULTS["basic"]["max_days"],
                        "helpMessage": "/群聊分析 3 中的天数不能超过该值。",
                        "componentProps": {"min": 1, "max": 30},
                    },
                    {
                        "field": "max_messages",
                        "label": "单次最多消息数",
                        "component": "InputNumber",
                        "default": DEFAULTS["basic"]["max_messages"],
                        "helpMessage": "按时间倒序回溯，最多取这么多条参与分析。",
                        "componentProps": {"min": 50, "max": 5000, "step": 50},
                    },
                    {
                        "field": "min_messages",
                        "label": "最少消息数",
                        "component": "InputNumber",
                        "default": DEFAULTS["basic"]["min_messages"],
                        "helpMessage": "不足该条数时提示消息太少，不调用模型。",
                        "componentProps": {"min": 0, "max": 1000},
                    },
                    {
                        "field": "filter_self",
                        "label": "排除机器人发言",
                        "component": "Switch",
                        "default": DEFAULTS["basic"]["filter_self"],
                        "helpMessage": "开启后机器人自己的消息不计入统计和分析。",
                    },
                ],
            },
            {
                "key": "group_analysis.auto",
                "title": "自动日报",
                "schemas": [
                    {
                        "field": "auto_groups",
                        "label": "自动推送的群",
                        "component": "GArrayInput",
                        "default": DEFAULTS["auto"]["auto_groups"],
                        "helpMessage": "留空表示不自动推送；日报只分析前一天，群号需在允许分析的群中（留空时除外）。",
                    },
                    {
                        "field": "auto_hour",
                        "label": "推送小时",
                        "component": "InputNumber",
                        "default": DEFAULTS["auto"]["auto_hour"],
                        "helpMessage": "按北京时间每天在这个小时生成并发送前一天的日报；修改后需重载插件生效。",
                        "componentProps": {"min": 0, "max": 23},
                    },
                    {
                        "field": "auto_minute",
                        "label": "推送分钟",
                        "component": "InputNumber",
                        "default": DEFAULTS["auto"]["auto_minute"],
                        "helpMessage": "每小时内的分钟数。",
                        "componentProps": {"min": 0, "max": 59},
                    },
                ],
            },
        ],
    }
