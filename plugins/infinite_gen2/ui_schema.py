"""无限二代控制台开关定义。"""

from __future__ import annotations

from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "direct_llm": False}


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
                            "为普通规划、Focus 共享规划、聊天回复及纯人格直聊注入无限二代系统段；默认关闭。"
                            "保存后下一次组装提示词生效，不改变工具权限。"
                            "可能改变人格、回复风格并增加 token 消耗，不保证突破模型限制。"
                        ),
                    },
                    {
                        "field": "direct_llm",
                        "label": "纯人格直聊",
                        "component": "Switch",
                        "default": DEFAULTS["direct_llm"],
                        "helpMessage": (
                            "将人格原文、短期对话历史和本条消息直接发给回复模型；上面的提示词开关开启时仍注入系统段。"
                            "不带长期记忆、工具或框架任务提示词，跳过 Planner/Focus、分段、错字、语音及表情补发。"
                            "历史条数沿用群聊/私聊上下文配置，设为 0 则不带历史。"
                            "群聊仅响应 @ 或人格名触发，权限和发送校验仍生效。"
                        ),
                    },
                ],
            },
        ],
    }
