"""系统配置路由。"""

from __future__ import annotations

import importlib
from typing import Any

from fastapi import Depends, APIRouter, HTTPException
from nonebot import logger

from ..auth import bearer_auth
from ...config import SYSTEM_DEFAULTS, get_manager
from ...utils.paths import built_in_plugins_dir
from ...chat.personas import load_personas
from ...config.manager import ConfigConflictError

router = APIRouter(prefix="/config", tags=["fxbot-config"], dependencies=[Depends(bearer_auth)])


def _chat_choices() -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    """返回已有的人格和对话 Provider，供配置页下拉选择。"""
    chat = get_manager().get_system()["chat"]
    personas = [{"label": name, "value": name} for name in sorted(load_personas())]
    providers = chat.get("providers")
    names = sorted(
        str(name)
        for name, value in (providers.items() if isinstance(providers, dict) else ())
        if isinstance(value, dict) and str(value.get("provider_type") or "") == "chat"
    )
    return personas, [{"label": name, "value": name} for name in names]


_CHAT_TYPE_FIELD = {
    "field": "type",
    "label": "聊天类型",
    "component": "Select",
    "componentProps": {
        "options": [{"label": "群聊", "value": "group"}, {"label": "私聊", "value": "private"}],
        "defaultValue": "group",
    },
}


def _input_field(field: str, label: str, placeholder: str = "", *, required: bool = False) -> dict[str, Any]:
    """规则列表里的文本字段。"""
    schema: dict[str, Any] = {
        "field": field,
        "label": label,
        "component": "Input",
        "componentProps": {"placeholder": placeholder},
    }
    if required:
        schema["required"] = True
    return schema


def _platform_field(placeholder: str) -> dict[str, Any]:
    """规则列表里的平台选择。"""
    return {
        "field": "platform",
        "label": "平台",
        "component": "GPlatformSelect",
        "componentProps": {"placeholder": placeholder},
    }


def _target_field(placeholder: str) -> dict[str, Any]:
    """规则列表里的会话选择，按同行平台与聊天类型过滤。"""
    return {
        "field": "item_id",
        "label": "会话 ID",
        "component": "GChatTarget",
        "componentProps": {"platformField": "platform", "typeField": "type", "placeholder": placeholder},
    }


def _switch_field(field: str, label: str, default: bool = True) -> dict[str, Any]:
    """规则列表里的开关字段。"""
    return {"field": field, "label": label, "component": "Switch", "componentProps": {"defaultValue": default}}


def _learning_fields(defaults_key: str) -> list[dict[str, Any]]:
    """学习范围：按聊天流覆盖「使用 / 学习」，共享组让多个聊天共用已学内容。"""
    chat = SYSTEM_DEFAULTS["chat"]
    return [
        {"component": "Divider", "label": "学习范围"},
        {
            "field": "learning_list",
            "label": "学习配置",
            "component": "GListForm",
            "default": chat[defaults_key]["learning_list"],
            "helpMessage": "按聊天流覆盖；平台和 ID 都留空表示全局默认，只填平台表示平台兜底。",
            "componentProps": {
                "schemas": [
                    _platform_field("留空表示任意平台"),
                    _target_field("留空表示该平台默认"),
                    _CHAT_TYPE_FIELD,
                    _switch_field("use", "使用已学内容"),
                    _switch_field("learn", "继续学习"),
                ],
                "titleField": "item_id",
                "addLabel": "添加学习配置",
            },
        },
        {
            "field": "groups",
            "label": "共享组",
            "component": "GListForm",
            "default": chat[defaults_key]["groups"],
            "helpMessage": "同一组里的聊天共享已学到的内容。",
            "componentProps": {
                "schemas": [
                    {
                        "field": "targets",
                        "label": "共享聊天",
                        "component": "GListForm",
                        "componentProps": {
                            "schemas": [
                                _platform_field("* 表示任意平台"),
                                _target_field("* 表示任意聊天"),
                                _CHAT_TYPE_FIELD,
                            ],
                            "titleField": "item_id",
                            "addLabel": "添加聊天",
                        },
                    },
                ],
                "addLabel": "添加共享组",
            },
        },
    ]


def _all_config_tab() -> dict[str, Any]:
    """返回系统配置表单结构，含 chat 卡片。"""
    personas, providers = _chat_choices()
    return {
        "key": "system",
        "title": "系统配置",
        "order": 0,
        "dataKey": "system",
        "cards": [
            {
                "key": "membership",
                "title": "会员系统",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用会员门禁",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["membership"]["enabled"],
                        "helpMessage": "关闭后，群会员门禁不再拦截消息。",
                    },
                    {
                        "field": "free_bot_ids",
                        "label": "免费 Bot",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["membership"]["free_bot_ids"],
                        "helpMessage": "这些 Bot 自编号不执行会员门禁。",
                    },
                    {
                        "field": "expire_notice_days",
                        "label": "到期提示天数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["membership"]["expire_notice_days"],
                        "helpMessage": "会员剩余多少天内，在命令消息中提示续费。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "expire_prompt_text_prefixes",
                        "label": "提示文本前缀",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["membership"]["expire_prompt_text_prefixes"],
                        "helpMessage": "普通消息以这些文本开头时，也触发会员快到期提示。",
                    },
                    {
                        "field": "auto_leave_expired_groups",
                        "label": "过期自动退群",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["membership"]["auto_leave_expired_groups"],
                        "helpMessage": "到期后自动让托管 Bot 退群。",
                    },
                    {
                        "field": "enable_scheduler",
                        "label": "启用定时任务",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["membership"]["enable_scheduler"],
                        "helpMessage": "关闭后不会自动执行到期检查。",
                    },
                    {
                        "field": "schedule_time",
                        "label": "定时任务时间",
                        "component": "Input",
                        "default": SYSTEM_DEFAULTS["membership"]["schedule_time"],
                        "helpMessage": "每日执行会员检查的时间，格式 HH:MM。",
                        "componentProps": {"placeholder": "00:00"},
                    },
                    {
                        "field": "batch_delay_seconds",
                        "label": "批处理延迟（秒）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["membership"]["batch_delay_seconds"],
                        "helpMessage": "群消息和退群操作之间的间隔。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "renewal_contact",
                        "label": "续费联系人",
                        "component": "Input",
                        "default": SYSTEM_DEFAULTS["membership"]["renewal_contact"],
                        "helpMessage": "填写 qq123456 或 wx123456。",
                    },
                ],
            },
            {
                "key": "system",
                "title": "系统",
                "schemas": [
                    {
                        "field": "token",
                        "label": "访问 Token",
                        "component": "InputPassword",
                        "default": SYSTEM_DEFAULTS["system"]["token"],
                        "helpMessage": "留空时会在首次登录时自动生成。",
                    },
                    {
                        "field": "ignored_mention_bot_ids",
                        "label": "忽略 @Bot",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["system"]["ignored_mention_bot_ids"],
                        "helpMessage": "消息中 @ 到这些 Bot QQ 时，本机器人不处理该消息。",
                    },
                    {
                        "field": "bot_admins",
                        "label": "Bot 管理员",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["system"]["bot_admins"],
                        "helpMessage": "这些账号拥有 Bot 管理员权限。",
                    },
                ],
            },
            {
                "key": "chat",
                "title": "AI 对话",
                "category": "基础",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用 AI",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["enabled"],
                        "helpMessage": "关闭后 AI 兜底不会接管消息。",
                    },
                    {
                        "field": "private_enabled",
                        "label": "启用私聊 AI",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["private_enabled"],
                        "helpMessage": "关闭后私聊消息不会进入 AI 兜底；群聊仍需 @Bot。",
                    },
                    {
                        "field": "private_allowlist",
                        "label": "私聊白名单",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["private_allowlist"],
                        "helpMessage": "留空表示所有私聊用户可用；填写后仅这些用户 ID 可用。",
                        "componentProps": {"suggestScene": "private"},
                    },
                    {
                        "field": "group_allowlist",
                        "label": "群聊白名单",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["group_allowlist"],
                        "helpMessage": "留空表示所有群在 @Bot 时可用；填写后仅这些群 ID 可用（QQ 官方群填 openid）。",
                        "componentProps": {"suggestScene": "group"},
                    },
                    {
                        "field": "persona",
                        "label": "人格",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["persona"],
                        "helpMessage": "全局默认人格；群主/管理员可用 /人格 <名称> 覆盖本群，私聊用户可覆盖自己的。",
                        "componentProps": {"options": personas},
                    },
                    {
                        "field": "provider",
                        "label": "默认 Provider",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["provider"],
                        "helpMessage": "未指定时用第一个对话 Provider；Bot 主人可用 /provider <名称> 覆盖本群或私聊。",
                        "componentProps": {
                            "options": [{"label": "自动（第一个对话 Provider）", "value": ""}, *providers],
                        },
                    },
                    {
                        "field": "max_context_size",
                        "label": "群聊上下文",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["max_context_size"],
                        "helpMessage": (
                            "群聊回复时参考的最近消息数量，包含未 @Bot 的消息；"
                            "0 表示不记录该类型会话的历史，也不参与记忆学习。"
                        ),
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "max_private_context_size",
                        "label": "私聊上下文",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["max_private_context_size"],
                        "helpMessage": (
                            "私聊回复时参考的最近消息数量；0 表示不记录该类型会话的历史，也不参与记忆学习。"
                        ),
                        "componentProps": {"min": 0},
                    },
                ],
            },
            {
                "key": "chat.message_receive",
                "title": "消息接收",
                "category": "消息接收",
                "schemas": [
                    {
                        "field": "image_parse_threshold",
                        "label": "单条识图上限",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["message_receive"]["image_parse_threshold"],
                        "helpMessage": "一条消息的图片数超过该值时只记 [图片]，不再调用视觉模型。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "ban_words",
                        "label": "屏蔽词",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["message_receive"]["ban_words"],
                        "helpMessage": "消息包含任一屏蔽词时不回复，也不写入历史。",
                    },
                    {
                        "field": "ban_msgs_regex",
                        "label": "屏蔽正则",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["message_receive"]["ban_msgs_regex"],
                        "helpMessage": "按正则过滤消息；每项一条，保存时会校验语法。",
                    },
                    {
                        "field": "keyword_rules",
                        "label": "关键词反应",
                        "component": "GListForm",
                        "default": SYSTEM_DEFAULTS["chat"]["message_receive"]["keyword_rules"],
                        "helpMessage": "消息包含任一关键词时，把反应提示追加给模型。",
                        "componentProps": {
                            "schemas": [
                                {"field": "keywords", "label": "关键词", "component": "GTags"},
                                {
                                    "field": "reaction",
                                    "label": "反应提示",
                                    "component": "Textarea",
                                    "componentProps": {"rows": 3},
                                },
                            ],
                            "titleField": "reaction",
                            "addLabel": "添加关键词规则",
                        },
                    },
                    {
                        "field": "regex_rules",
                        "label": "正则反应",
                        "component": "GListForm",
                        "default": SYSTEM_DEFAULTS["chat"]["message_receive"]["regex_rules"],
                        "helpMessage": "命中任一正则时追加反应提示；反应里的 [名字] 会替换成同名捕获组。",
                        "componentProps": {
                            "schemas": [
                                {
                                    "field": "regex",
                                    "label": "正则",
                                    "component": "GTags",
                                    "componentProps": {"placeholder": "输入后按回车添加"},
                                },
                                {
                                    "field": "reaction",
                                    "label": "反应提示",
                                    "component": "Textarea",
                                    "componentProps": {"rows": 3},
                                },
                            ],
                            "titleField": "reaction",
                            "addLabel": "添加正则规则",
                        },
                    },
                ],
            },
            {
                "key": "chat.capabilities",
                "title": "能力开关",
                "category": "能力",
                "schemas": [
                    {
                        "field": "tool_result_condense",
                        "label": "超长工具结果整理",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["capabilities"]["tool_result_condense"],
                        "helpMessage": (
                            "工具结果超过阈值时先交给子模型按调用意图整理成结论再写入上下文，"
                            "减少后续每轮的上下文占用；会多一次模型请求。"
                        ),
                    },
                    {
                        "field": "tool_result_condense_threshold",
                        "label": "整理阈值（字）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["capabilities"]["tool_result_condense_threshold"],
                        "helpMessage": "工具结果达到多少字时才触发整理，仅在开启整理时生效。",
                        "componentProps": {"min": 500, "max": 30000, "step": 100},
                    },
                    {
                        "field": "planner_subagent",
                        "label": "子代理任务派发",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["capabilities"]["planner_subagent"],
                        "helpMessage": (
                            "给规划器和回复模型提供 subagent 工具：把自包含的查询或整理任务交给子代理，"
                            "子代理在独立上下文里多轮调用被授权的工具，只把结论交回来。"
                        ),
                    },
                    {
                        "field": "advanced_sub_agent",
                        "label": "高级子代理",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["capabilities"]["advanced_sub_agent"],
                        "helpMessage": (
                            "子代理改为后台运行：派发后立刻返回任务 id，结论结束时写入会话，下一轮对话可见；"
                            "同时提供查看和终止后台任务的工具。需要先开启子代理任务派发。"
                        ),
                    },
                ],
            },
            {
                "key": "chat.expression",
                "title": "群表达",
                "category": "学习",
                "schemas": [
                    {
                        "field": "checked_only",
                        "label": "只用人工确认表达",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["checked_only"],
                        "helpMessage": "开启后只注入在控制台人工通过过的表达；关闭时复审通过的表达都可用。",
                    },
                    {
                        "field": "self_reflect",
                        "label": "写入前 AI 复审",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["self_reflect"],
                        "helpMessage": "关闭后学习结果不再经过复审模型，直接作为可用表达。",
                    },
                    {
                        "field": "max_expression_learner",
                        "label": "表达学习最大并发",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["max_expression_learner"],
                        "helpMessage": "同时处理的学习任务数量；太高会占用更多模型并发。",
                        "componentProps": {"min": 1, "max": 20},
                    },
                    {
                        "field": "vector.enabled",
                        "label": "表达向量选择",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["vector"]["enabled"],
                        "helpMessage": (
                            "用嵌入召回相近表达，再由 utils 模型按语境筛选；"
                            "需要配置嵌入 Provider，召回失败时回退关键词相似度。"
                        ),
                    },
                    {
                        "field": "vector.index_path",
                        "label": "向量索引路径",
                        "component": "Input",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["vector"]["index_path"],
                        "helpMessage": "表达向量的缓存文件；相对路径按插件目录解析，以 data/ 开头时放在运行数据目录。",
                    },
                    {
                        "field": "vector.candidate_pool_size",
                        "label": "向量候选上限",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["vector"]["candidate_pool_size"],
                        "helpMessage": "召回后最多交给选择模型多少条候选；上限 50。",
                        "componentProps": {"min": 1, "max": 50},
                    },
                    *_learning_fields("expression"),
                ],
            },
            {
                "key": "chat.jargon",
                "title": "黑话",
                "category": "学习",
                "schemas": [*_learning_fields("jargon")],
            },
            {
                "key": "chat.planner",
                "title": "对话规划",
                "category": "能力",
                "schemas": [
                    {
                        "field": "context_summary_max_chars",
                        "label": "状态摘要上限（字）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["planner"]["context_summary_max_chars"],
                        "helpMessage": (
                            "聊天历史达到裁切阈值时，将较早的对话压缩成状态摘要，"
                            "之后随每一轮上下文一起发送；摘要最多多少字。"
                        ),
                        "componentProps": {"min": 1000, "max": 8000, "step": 500},
                    },
                    {
                        "field": "passive_replyer",
                        "label": "被动式 replyer",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["planner"]["passive_replyer"],
                        "helpMessage": (
                            "开启后，Planner 不调用工具而输出正文时，将正文作为指导交给回复模型；"
                            "关闭时通过 reply 工具发言。普通对话始终先进入 Planner。"
                        ),
                    },
                ],
            },
            {
                "key": "chat.typo",
                "title": "错字模拟",
                "category": "聊天",
                "schemas": [
                    {
                        "field": "enable",
                        "label": "启用错别字",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["enable"],
                        "helpMessage": "只改发送文本，历史仍保存干净内容。",
                    },
                    {
                        "field": "error_rate",
                        "label": "单字错字概率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["error_rate"],
                        "helpMessage": "每个字被同音字替换的概率。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.001},
                    },
                    {
                        "field": "min_freq",
                        "label": "最小字频",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["min_freq"],
                        "helpMessage": "只对常见程度达到该值的字造错字；0 表示不限制。",
                        "componentProps": {"min": 0, "max": 1000, "step": 1},
                    },
                    {
                        "field": "tone_error_rate",
                        "label": "声调错字概率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["tone_error_rate"],
                        "helpMessage": "每个字被同音不同调的字替换的概率。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.005},
                    },
                    {
                        "field": "word_replace_rate",
                        "label": "整词替换概率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["word_replace_rate"],
                        "helpMessage": "常见词被同音错写替换的概率。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.001},
                    },
                    {
                        "field": "enable_correction_quote",
                        "label": "错别字纠正时引用原消息",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["enable_correction_quote"],
                        "helpMessage": "有错字时，发出的纠正消息是否引用上一条错字消息。",
                    },
                    {
                        "field": "correction_quote_probability",
                        "label": "错别字纠正引用概率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["correction_quote_probability"],
                        "helpMessage": "生成纠正消息时，引用上一条错字消息的概率。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.1},
                    },
                ],
            },
            {
                "key": "chat.reply_timing",
                "title": "回复时机",
                "category": "聊天",
                "schemas": [
                    {
                        "field": "message_debounce_mode",
                        "label": "消息防抖",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["message_debounce_mode"],
                        "helpMessage": "off 不等待；其余按安静、活跃、同人续发、换人的秒数合并消息。",
                        "componentProps": {
                            "options": [
                                {"label": "关闭", "value": "off"},
                                {"label": "快速", "value": "fast"},
                                {"label": "自然", "value": "natural"},
                                {"label": "沉稳", "value": "steady"},
                            ]
                        },
                    },
                    {
                        "field": "talk_value",
                        "label": "群聊频率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["talk_value"],
                        "helpMessage": "群聊里主动说话的频率，越小越安静；群级开关用 #主动 开/关。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.1},
                    },
                    {
                        "field": "mentioned_bot_reply",
                        "label": "提及必回复",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["mentioned_bot_reply"],
                        "helpMessage": "关闭后，叫出人格名的消息只记入上下文。",
                    },
                    {
                        "field": "inevitable_at_reply",
                        "label": "At 必回复",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["inevitable_at_reply"],
                        "helpMessage": "关闭后，@ Bot 的消息只记入上下文。",
                    },
                    {
                        "field": "planner_message_steering",
                        "label": "快速反应模式",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["planner_message_steering"],
                        "helpMessage": (
                            "规划期间同一用户发来新消息时立即取消本次规划，按新消息重新规划；"
                            "关闭时这次规划会先跑完再丢弃结果。"
                        ),
                    },
                    {
                        "field": "no_action_backoff_base_seconds",
                        "label": "空闲退避基准（秒）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["no_action_backoff_base_seconds"],
                        "helpMessage": "连续决定不回复后，下一次检查前先等多久。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "no_action_backoff_cap_seconds",
                        "label": "空闲退避上限（秒）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["no_action_backoff_cap_seconds"],
                        "helpMessage": "不回复退避等待的最长时间。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "no_action_backoff_start_count",
                        "label": "空闲退避起点",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["no_action_backoff_start_count"],
                        "helpMessage": "连续几次不回复后开始放慢检查。",
                        "componentProps": {"min": 1},
                    },
                    {
                        "field": "no_action_backoff_bypass_pending_count",
                        "label": "空闲退避绕过消息数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["no_action_backoff_bypass_pending_count"],
                        "helpMessage": "等待期间新消息达到多少条就立刻重新处理；0 表示不按条数打断等待。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "enable_talk_value_rules",
                        "label": "启用动态发言频率规则",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["enable_talk_value_rules"],
                        "helpMessage": "开启后按下方规则覆盖群聊频率；私聊不使用频率规则。",
                    },
                    {
                        "field": "talk_value_rules",
                        "label": "动态发言频率规则",
                        "component": "GListForm",
                        "default": SYSTEM_DEFAULTS["chat"]["reply_timing"]["talk_value_rules"],
                        "helpMessage": (
                            "按平台、聊天类型和会话匹配；平台和会话留空表示该层级默认，全部留空即全局。时间段不选按全天。"
                        ),
                        "componentProps": {
                            "schemas": [
                                _platform_field("留空表示任意平台"),
                                _target_field("留空表示整个平台"),
                                _CHAT_TYPE_FIELD,
                                {
                                    "field": "time",
                                    "label": "时间段",
                                    "component": "GTimeRange",
                                    "componentProps": {"placeholder": "不选按全天"},
                                },
                                {
                                    "field": "value",
                                    "label": "发言频率",
                                    "component": "InputNumber",
                                    "componentProps": {"min": 0, "max": 1, "step": 0.1, "defaultValue": 0.5},
                                },
                            ],
                            "titleField": "item_id",
                            "addLabel": "添加频率规则",
                        },
                    },
                ],
            },
            {
                "key": "chat.behavior",
                "title": "对话行为",
                "category": "聊天",
                "schemas": [
                    {
                        "field": "group_quote_frequency",
                        "label": "群聊引用频率",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["group_quote_frequency"],
                        "helpMessage": "按连发条数、发言间隔与目标位置随机决定；经常引用的概率为偶尔引用的两倍。",
                        "componentProps": {
                            "options": [
                                {"label": "不引用", "value": "never"},
                                {"label": "偶尔引用", "value": "occasionally"},
                                {"label": "经常引用", "value": "often"},
                            ]
                        },
                    },
                    {
                        "field": "private_quote_frequency",
                        "label": "私聊引用频率",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["private_quote_frequency"],
                        "helpMessage": "结合用户连发条数、发言间隔和回复目标位置随机决定。",
                        "componentProps": {
                            "options": [
                                {"label": "不引用", "value": "never"},
                                {"label": "偶尔引用", "value": "occasionally"},
                                {"label": "经常引用", "value": "often"},
                            ]
                        },
                    },
                    {
                        "field": "persona_aliases",
                        "label": "人格别名",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["persona_aliases"],
                        "helpMessage": "别人可能用来称呼 Bot 的名字；群消息以别名开头时免 @ 触发回复。",
                    },
                    {
                        "field": "enable_reply_at",
                        "label": "回复时 @ 用户",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["enable_reply_at"],
                        "helpMessage": "模型可在回复开头写 @昵称 来 @ 本条消息的发送者或被 @ 的人。",
                    },
                    {"component": "Divider", "label": "场景提示词"},
                    {
                        "field": "group_chat_prompt",
                        "label": "群聊提示词",
                        "component": "Textarea",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["group_chat_prompt"],
                        "helpMessage": "群聊回复的通用注意事项，随系统提示发给模型。",
                        "componentProps": {"rows": 6},
                    },
                    {
                        "field": "private_chat_prompts",
                        "label": "私聊提示词",
                        "component": "Textarea",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["private_chat_prompts"],
                        "helpMessage": "私聊回复的通用注意事项，随系统提示发给模型。",
                        "componentProps": {"rows": 4},
                    },
                    {"component": "Divider", "label": "临时风格"},
                    {
                        "field": "multiple_reply_style",
                        "label": "备用表达风格",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["multiple_reply_style"],
                        "helpMessage": "命中概率时从这些风格里随机取一条，只作用于本轮回复。",
                    },
                    {
                        "field": "multiple_probability",
                        "label": "临时风格概率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["multiple_probability"],
                        "helpMessage": "0 表示不随机切换；1 表示每轮都抽一条备用风格。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.1},
                    },
                    {"component": "Divider", "label": "额外 Prompt"},
                    {
                        "field": "chat_prompts",
                        "label": "指定聊天要求",
                        "component": "GChatPrompts",
                        "default": SYSTEM_DEFAULTS["chat"]["behavior"]["chat_prompts"],
                        "helpMessage": "按平台、聊天类型和会话选择，只对匹配的聊天生效；提示词只追加给这些聊天。",
                    },
                ],
            },
            {
                "key": "chat.attention_drift",
                "title": "注意力漂移",
                "category": "聊天",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用漂移",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["attention_drift"]["enabled"],
                        "helpMessage": "允许被最近消息里的新鲜细节短暂吸引，但仍要能被上下文解释。",
                    },
                    {
                        "field": "drift_level",
                        "label": "漂移档位",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["attention_drift"]["drift_level"],
                        "helpMessage": "从轻微联想到明显拐弯；每轮最多一次明显跳跃。",
                        "componentProps": {
                            "options": [
                                {"label": "轻微", "value": "subtle"},
                                {"label": "活跃", "value": "active"},
                                {"label": "发散", "value": "scattered"},
                                {"label": "跳跃", "value": "wild"},
                            ]
                        },
                    },
                    {
                        "field": "anchor_policy",
                        "label": "回钩策略",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["attention_drift"]["anchor_policy"],
                        "helpMessage": "漂移后多快回到当前话题。",
                        "componentProps": {
                            "options": [
                                {"label": "严格", "value": "strict"},
                                {"label": "自然", "value": "balanced"},
                                {"label": "宽松", "value": "loose"},
                            ]
                        },
                    },
                    {
                        "field": "reaction_style",
                        "label": "短反应风格",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["attention_drift"]["reaction_style"],
                        "helpMessage": "用短句、吐槽或语气词接话的积极程度。",
                        "componentProps": {
                            "options": [
                                {"label": "克制", "value": "reserved"},
                                {"label": "自然", "value": "natural"},
                                {"label": "活泼", "value": "lively"},
                            ]
                        },
                    },
                ],
            },
            {
                "key": "chat.visual",
                "title": "图片",
                "category": "视觉",
                "schemas": [
                    {
                        "field": "image_quality",
                        "label": "图片画质",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["visual"]["image_quality"],
                        "helpMessage": "发给模型的图片最长边：普通 512，超清 1024；不放大原图。",
                        "componentProps": {
                            "options": [
                                {"label": "普通", "value": "normal"},
                                {"label": "超清", "value": "high"},
                            ]
                        },
                    },
                    {
                        "field": "max_image_num",
                        "label": "多模态最大图片数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["visual"]["max_image_num"],
                        "helpMessage": "一次多模态请求最多带多少张图，太大可能更慢更贵；0 表示不接收图片。",
                        "componentProps": {"min": 0, "max": 64},
                    },
                    {
                        "field": "max_image_size_mb",
                        "label": "图片大小上限（MB）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["visual"]["max_image_size_mb"],
                        "helpMessage": "超过上限的图片按下方方式处理；0 表示不限。",
                        "componentProps": {"min": 0, "max": 64, "step": 1},
                    },
                    {
                        "field": "wait_image_recognize_max_time",
                        "label": "识图最长等待时间（秒）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["visual"]["wait_image_recognize_max_time"],
                        "helpMessage": "回复生成后等图片描述写回历史的最长秒数；0 表示不等待。",
                        "componentProps": {"min": 0, "max": 120, "step": 1},
                    },
                    {
                        "field": "handle_oversized_images",
                        "label": "处理过大图片",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["visual"]["handle_oversized_images"],
                        "helpMessage": "关闭后超过大小上限的图片直接跳过。",
                    },
                    {
                        "field": "oversized_image_handle_method",
                        "label": "过大图片处理方式",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["visual"]["oversized_image_handle_method"],
                        "helpMessage": "compress 压到上限内继续用；discard 直接丢弃。",
                        "componentProps": {
                            "options": [
                                {"label": "压缩", "value": "compress"},
                                {"label": "丢弃", "value": "discard"},
                            ]
                        },
                    },
                ],
            },
            {
                "key": "chat.voice",
                "title": "语音",
                "category": "消息接收",
                "schemas": [
                    {
                        "field": "enable_asr",
                        "label": "语音识别",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["voice"]["enable_asr"],
                        "helpMessage": "开启后用语音识别 Provider 把语音消息转成文字再处理。",
                    },
                    {
                        "field": "enable_tts",
                        "label": "语音合成",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["voice"]["enable_tts"],
                        "helpMessage": "开启后每条文字回复再补发一条同内容的语音，失败时只保留文字。",
                    },
                ],
            },
            {
                "key": "chat.response_post_process",
                "title": "回复后处理",
                "category": "后处理",
                "schemas": [
                    {
                        "field": "enable_response_post_process",
                        "label": "启用后处理",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["response_post_process"]["enable_response_post_process"],
                        "helpMessage": "关闭后跳过括号清理、断句与兜底，整段原文直接发送。",
                    },
                    {
                        "field": "typing_speed",
                        "label": "打字速度",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["response_post_process"]["typing_speed"],
                        "helpMessage": "每段发送前按字数模拟打字；0 表示不等待。",
                        "componentProps": {"min": 0, "max": 2, "step": 0.1},
                    },
                ],
            },
            {
                "key": "chat.response_splitter",
                "title": "回复断句",
                "category": "后处理",
                "schemas": [
                    {
                        "field": "mode",
                        "label": "断句方式",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["response_splitter"]["mode"],
                        "helpMessage": (
                            "rule 按标点断句；llm 使用快速模型（留空用通用工具模型）按语义断句，失败回退规则。"
                        ),
                        "componentProps": {
                            "options": [
                                {"label": "规则", "value": "rule"},
                                {"label": "模型", "value": "llm"},
                            ],
                        },
                    },
                    {
                        "field": "enable",
                        "label": "启用断句",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["response_splitter"]["enable"],
                        "helpMessage": "关闭后整段回复作为一条发送。",
                    },
                    {
                        "field": "max_length",
                        "label": "单段字数上限",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["response_splitter"]["max_length"],
                        "helpMessage": "中文长回复超过该字数时改用兜底句。",
                        "componentProps": {"min": 1, "max": 4096, "step": 64},
                    },
                    {
                        "field": "max_sentence_num",
                        "label": "最大句子数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["response_splitter"]["max_sentence_num"],
                        "helpMessage": "断句结果超过该数量时按兜底处理。",
                        "componentProps": {"min": 1, "max": 50},
                    },
                    {
                        "field": "max_split_num",
                        "label": "最大消息数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["response_splitter"]["max_split_num"],
                        "helpMessage": "最终最多合并成几条消息。",
                        "componentProps": {"min": 1, "max": 10},
                    },
                    {
                        "field": "enable_kaomoji_protection",
                        "label": "保护颜文字",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["response_splitter"]["enable_kaomoji_protection"],
                        "helpMessage": "把颜文字替换成占位符，避免断句时从中间拆开。",
                    },
                    {
                        "field": "enable_overflow_return_all",
                        "label": "超限整段返回",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["response_splitter"]["enable_overflow_return_all"],
                        "helpMessage": "超过最大句子数时整段返回，而不是使用兜底句。",
                    },
                ],
            },
            {
                "key": "chat.emoji",
                "title": "表情包",
                "category": "学习",
                "schemas": [
                    {
                        "field": "use_new_send_logic",
                        "label": "采用新表情包发送逻辑",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["use_new_send_logic"],
                        "helpMessage": (
                            "开启后由规划模型直接查看表情包拼图挑选，在文字回复后发送，需要规划模型支持视觉；"
                            "关闭时沿用按回复内容匹配的旧方式。"
                        ),
                    },
                    {
                        "field": "emoji_send_num",
                        "label": "单次发送候选数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["emoji_send_num"],
                        "helpMessage": "从多少个匹配表情里随机挑一个，不是一次发这么多。",
                        "componentProps": {"min": 1, "max": 64},
                    },
                    {
                        "field": "steal_emoji",
                        "label": "收集聊天表情",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["steal_emoji"],
                        "helpMessage": "自动收集群友发送的表情包，识别后进入素材库；关闭只保留手动上传。",
                    },
                    {
                        "field": "max_reg_num",
                        "label": "最大注册数量",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["max_reg_num"],
                        "helpMessage": "可发送表情上限；0 表示不再自动注册新表情。",
                        "componentProps": {"min": 0, "max": 10000, "step": 8},
                    },
                    {
                        "field": "do_replace",
                        "label": "满额后替换旧表情",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["do_replace"],
                        "helpMessage": "名额已满时让文本模型挑一个少用的旧表情取消注册。",
                    },
                    {
                        "field": "check_interval",
                        "label": "检查间隔（分钟）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["check_interval"],
                        "helpMessage": "定期补识别上次失败或没处理完的表情。",
                        "componentProps": {"min": 1, "max": 1440, "step": 1},
                    },
                    {
                        "field": "max_emoji_size_mb",
                        "label": "收集大小上限（MB）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["max_emoji_size_mb"],
                        "helpMessage": "超过上限的收集表情跳过；0 表示不限。",
                        "componentProps": {"min": 0, "max": 64, "step": 1},
                    },
                    {
                        "field": "content_filtration",
                        "label": "启用内容过滤",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["content_filtration"],
                        "helpMessage": "额外让视觉模型审核，不合适的内容不注册。",
                    },
                    {
                        "field": "cache_cleanup.enabled",
                        "label": "自动清理缓存",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["cache_cleanup"]["enabled"],
                        "helpMessage": "删除长期未注册表情；已注册表情不会被清理。",
                    },
                    {
                        "field": "cache_cleanup.check_interval_hours",
                        "label": "清理检查间隔（小时）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["cache_cleanup"]["check_interval_hours"],
                        "helpMessage": "每隔多少小时检查一次旧表情缓存。",
                        "componentProps": {"min": 0.1, "max": 168, "step": 1},
                    },
                    {
                        "field": "cache_cleanup.emoji_file_retention_days",
                        "label": "未注册文件保留天数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["cache_cleanup"]["emoji_file_retention_days"],
                        "helpMessage": "未注册表情文件超过该天数后删除。",
                        "componentProps": {"min": 1, "max": 365},
                    },
                    {
                        "field": "cache_cleanup.no_file_record_retention_days",
                        "label": "无文件记录保留天数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["cache_cleanup"]["no_file_record_retention_days"],
                        "helpMessage": "文件已删除后，未注册表情的记录还能保留多久。",
                        "componentProps": {"min": 1, "max": 365},
                    },
                ],
            },
        ],
    }


def _system_config_tab() -> dict[str, Any]:
    """插件配置页只保留会员与系统卡片。"""
    tab = _all_config_tab()
    tab["cards"] = [card for card in tab["cards"] if not str(card["key"]).startswith("chat")]
    return tab


def _chat_config_tab() -> dict[str, Any]:
    """AI 对话设置页的卡片；模型分配与联网服务在模型配置页维护。"""
    tab = _all_config_tab()
    tab.update(key="chat-settings", title="对话设置", order=5, dataKey="chat")
    tab["cards"] = [
        card for card in tab["cards"] if str(card["key"]).startswith("chat") and card["key"] != "chat.model_routing"
    ]
    return tab


def _plugin_config_tabs() -> list[dict[str, Any]]:
    """扫描内置插件的控制台配置 schema。"""
    tabs: list[dict[str, Any]] = []
    package_root = __package__.rsplit(".console.routes", 1)[0]
    for plugin_path in sorted(built_in_plugins_dir().iterdir()):
        if not plugin_path.is_dir() or plugin_path.name.startswith("_"):
            continue
        if not (plugin_path / "ui_schema.py").exists():
            continue
        module_name = f"{package_root}.plugins.{plugin_path.name}.ui_schema"
        try:
            module = importlib.import_module(module_name)
            schema = module.get_ui_schema()
        except Exception as exc:
            logger.warning(f"[Config] 加载插件配置界面失败: {plugin_path.name} err={exc}")
            continue
        if isinstance(schema, dict):
            tabs.append(schema)
    return tabs


def _tab_sort_key(tab: dict[str, Any]) -> tuple[int, str]:
    """按 schema 声明的顺序排列配置页。"""
    try:
        order = int(tab.get("order", 1000))
    except (TypeError, ValueError):
        order = 1000
    return order, str(tab.get("key") or "")


@router.get("/tabs")
async def get_config_tabs() -> list[dict[str, Any]]:
    """返回控制台配置表单结构。"""
    return sorted([_system_config_tab(), *_plugin_config_tabs()], key=_tab_sort_key)


@router.get("/chat")
async def get_chat_config_tab() -> dict[str, Any]:
    """返回 AI 对话设置页的表单结构。"""
    return _chat_config_tab()


@router.get("")
async def get_config() -> dict[str, Any]:
    """读取控制台配置。"""
    return get_manager().get_console_configs()


@router.put("")
async def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    """保存控制台配置。"""
    try:
        base_revisions = payload.pop("base_revisions", None)
        get_manager().save_console_configs(payload, base_revisions=base_revisions)
    except ConfigConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.opt(exception=True).error("[Console] 保存配置失败")
        raise HTTPException(status_code=500, detail="保存失败，请稍后再试") from exc
    return {"success": True}
