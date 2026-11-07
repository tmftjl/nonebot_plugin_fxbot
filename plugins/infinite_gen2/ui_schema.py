"""无限二代控制台开关定义。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False}


def get_ui_schema() -> dict[str, Any]:
    """返回可由现有控制台直接渲染的配置表单。"""
    return {
        "key": "infinite_gen2",
        "title": "无限二代",
        "cards": [
            {
                "key": "infinite_gen2",
                "title": "无限二代提示词",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用无限二代",
                        "component": "Switch",
                        "default": DEFAULTS["enabled"],
                        "helpMessage": (
                            "为所有聊天回复追加 DeepSeek 无限二代原版提示词；默认关闭。"
                            "保存后下一次回复生效，不影响内部规划及工具权限。"
                            "可能改变人格、回复风格并增加 token 消耗，不保证突破模型限制。"
                        ),
                    },
                ],
            },
        ],
    }
