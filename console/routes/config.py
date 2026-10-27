"""系统配置路由。"""

from __future__ import annotations

import importlib
from typing import Any

from fastapi import Depends, APIRouter, HTTPException
from nonebot import logger

from ..auth import bearer_auth
from ...config import SYSTEM_DEFAULTS, get_manager
from ...chat.tools import default_registry
from ...utils.paths import built_in_plugins_dir
from ...chat.personas import load_personas

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


def _tool_choices() -> list[dict[str, str]]:
    """返回已注册的 AI 工具，供配置页勾选。"""
    specs = sorted(default_registry.list(), key=lambda item: item.name)
    return [{"label": spec.title or spec.name, "value": spec.name} for spec in specs]


def _system_config_tab() -> dict[str, Any]:
    """返回系统配置表单结构。"""
    personas, providers = _chat_choices()
    tool_options = _tool_choices()
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
                    },
                    {
                        "field": "group_allowlist",
                        "label": "群聊白名单",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["group_allowlist"],
                        "helpMessage": "留空表示所有群在 @Bot 时可用；填写后仅这些群 ID 可用（QQ 官方群填 openid）。",
                    },
                    {
                        "field": "command_prefixes",
                        "label": "忽略命令前缀",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["command_prefixes"],
                        "helpMessage": "以这些前缀开头的消息不会进入 AI 对话，也不会记录成群聊上下文。",
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
                        "field": "max_history",
                        "label": "历史条数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["max_history"],
                        "helpMessage": "群聊和私聊保留给 AI 的消息条数，群聊包含未 @Bot 的消息；填 0 表示不记录。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "image_description",
                        "label": "图片转文字",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["image_description"],
                        "helpMessage": "收到图片时额外调用一次模型生成文字描述并写入历史；关闭后只记 [图片]。",
                    },
                    {
                        "field": "enabled_tools",
                        "label": "开启的 AI 工具",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["enabled_tools"],
                        "helpMessage": "留空表示启用全部工具；勾选后模型只能使用选中的工具。",
                        "componentProps": {
                            "multiple": True,
                            "options": tool_options,
                            "placeholder": "启用全部工具",
                        },
                    },
                ],
            },
            {
                "key": "chat.memory",
                "title": "对话记忆",
                "schemas": [
                    {
                        "field": "history_enabled",
                        "label": "持久化对话历史",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["memory"]["history_enabled"],
                        "helpMessage": "关闭后只保留进程内上下文，重启或清空后不恢复历史。",
                    },
                    {
                        "field": "learn_enabled",
                        "label": "长期记忆学习",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["memory"]["learn_enabled"],
                        "helpMessage": "开启后从对话中抽取事实与画像；当前阶段仅保存开关。",
                    },
                ],
            },
            {
                "key": "chat.privacy",
                "title": "群风格与表达学习",
                "schemas": [
                    {
                        "field": "style_learn",
                        "label": "学习群风格与黑话",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["privacy"]["style_learn"],
                        "helpMessage": (
                            "开启后每周或新增 200 条人类消息学习一次本群说话风格；黑话只在当前消息命中时注入。"
                        ),
                    },
                    {
                        "field": "expression_learn",
                        "label": "学习群表达",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["privacy"]["expression_learn"],
                        "helpMessage": "开启后每 40 条人类消息学习一次情境说法；新条目复审通过后才会注入。",
                    },
                ],
            },
            {
                "key": "chat.jargon",
                "title": "群黑话",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "注入黑话释义",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["jargon"]["enabled"],
                        "helpMessage": "关闭后仍会学习，但回复时不注入词条释义。",
                    },
                    {
                        "field": "inject_max",
                        "label": "每次最多注入",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["jargon"]["inject_max"],
                        "helpMessage": "当前消息命中多个词条时，最多注入几条释义。",
                        "componentProps": {"min": 0, "max": 20},
                    },
                ],
            },
            {
                "key": "chat.expression",
                "title": "群表达",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "注入表达参考",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["enabled"],
                        "helpMessage": "关闭后仍会学习，但回复时不注入情境说法。",
                    },
                    {
                        "field": "learn",
                        "label": "学习表达",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["learn"],
                        "helpMessage": "关闭后停止学习新表达；需要同时开启隐私页的“学习群表达”。",
                    },
                    {
                        "field": "llm_select",
                        "label": "模型精排表达",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["llm_select"],
                        "helpMessage": "开启后每轮多一次模型调用选择表达；失败自动回退本地模糊匹配。",
                    },
                    {
                        "field": "sample_size",
                        "label": "精排候选数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["sample_size"],
                        "helpMessage": "模型精排时最多查看多少条表达。",
                        "componentProps": {"min": 1, "max": 200},
                    },
                    {
                        "field": "inject_top_k",
                        "label": "每次最多注入",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["expression"]["inject_top_k"],
                        "helpMessage": "每轮最多注入几条表达参考。",
                        "componentProps": {"min": 1, "max": 20},
                    },
                ],
            },
            {
                "key": "chat.planner",
                "title": "对话规划",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用 Planner",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["planner"]["enabled"],
                        "helpMessage": "开启后每轮先让模型选择一个动作；关闭时保持当前单次回复。",
                    },
                    {
                        "field": "mode",
                        "label": "规划模式",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["planner"]["mode"],
                        "helpMessage": "simple 保持现有单次回复；complex 才调用 Planner。",
                        "componentProps": {
                            "options": [
                                {"label": "简单（不规划）", "value": "simple"},
                                {"label": "复杂（先规划）", "value": "complex"},
                            ],
                        },
                    },
                    {
                        "field": "wait_seconds",
                        "label": "wait 动作等待",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["planner"]["wait_seconds"],
                        "helpMessage": "Planner 选择 wait 时等待的秒数；期间新消息会进入历史。",
                        "componentProps": {"min": 30, "max": 120},
                    },
                ],
            },
            {
                "key": "chat.mood",
                "title": "情绪与熟悉度",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "注入状态提示",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["mood"]["enabled"],
                        "helpMessage": "关闭后不再向 Planner 提供心情、熟悉度和行为参考；门控不受影响。",
                    },
                    {
                        "field": "decay_per_day",
                        "label": "每日回落",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["mood"]["decay_per_day"],
                        "helpMessage": "心情每天向平静回落的幅度；熟悉度每天按 0.99 衰减。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.05},
                    },
                ],
            },
            {
                "key": "chat.typo",
                "title": "错字模拟",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用错字",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["enabled"],
                        "helpMessage": "只改发送文本，历史仍保存干净内容；默认关闭。",
                    },
                    {
                        "field": "error_rate",
                        "label": "同音错字率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["error_rate"],
                        "helpMessage": "每个字被同音字替换的概率。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.001},
                    },
                    {
                        "field": "tone_error_rate",
                        "label": "声调错字率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["tone_error_rate"],
                        "helpMessage": "每个字被同音不同调的字替换的概率。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.005},
                    },
                    {
                        "field": "word_replace_rate",
                        "label": "整词错写率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["word_replace_rate"],
                        "helpMessage": "常见词被同音错写替换的概率。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.001},
                    },
                    {
                        "field": "correction_quote",
                        "label": "发送后自纠",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["typo"]["correction_quote"],
                        "helpMessage": "OneBot 平台有错字时，一半概率追加“（打错了，是 X）”。",
                    },
                ],
            },
            {
                "key": "chat.emoji",
                "title": "表情包",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用表情包",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["enabled"],
                        "helpMessage": "正文发送后按文本匹配补一条表情；冷却 5 分钟。",
                    },
                    {
                        "field": "max_per_reply",
                        "label": "每次最多表情数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["max_per_reply"],
                        "helpMessage": "按设计每种回复最多 1 条；填 0 等于关闭发送。",
                        "componentProps": {"min": 0, "max": 3},
                    },
                    {
                        "field": "collect_from_group",
                        "label": "采集群图片",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["emoji"]["collect_from_group"],
                        "helpMessage": "默认关闭；开启时只处理允许对话的群。",
                    },
                ],
            },
            {
                "key": "chat.knowledge",
                "title": "知识库",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用知识库",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["knowledge"]["enabled"],
                        "helpMessage": "开启后 replyer 可用 search_knowledge 检索本租户上传的资料。",
                    },
                    {
                        "field": "chunk_chars",
                        "label": "切片字数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["knowledge"]["chunk_chars"],
                        "helpMessage": "每块 400 字、相邻重叠 50 字；修改后需重新导入文档。",
                        "componentProps": {"min": 100, "max": 2000, "step": 50},
                    },
                    {
                        "field": "top_k",
                        "label": "检索条数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["knowledge"]["top_k"],
                        "helpMessage": "每次检索返回的切片数量。",
                        "componentProps": {"min": 1, "max": 10},
                    },
                ],
            },
            {
                "key": "chat.reminder",
                "title": "提醒",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用提醒",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["reminder"]["enabled"],
                        "helpMessage": "开启后模型可以创建提醒，后台每分钟发送到期项。",
                    },
                    {
                        "field": "max_pending_per_user",
                        "label": "每用户上限",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["reminder"]["max_pending_per_user"],
                        "helpMessage": "未结束提醒的最大条数。",
                        "componentProps": {"min": 1, "max": 100},
                    },
                    {
                        "field": "require_confirmation",
                        "label": "需要用户确认",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["reminder"]["require_confirmation"],
                        "helpMessage": "开启时先创建待确认提醒，用户回复 #确认提醒 <id> 后才生效。",
                    },
                ],
            },
            {
                "key": "chat.proactive",
                "title": "主动接话",
                "schemas": [
                    {
                        "field": "enabled",
                        "label": "启用主动接话",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["enabled"],
                        "helpMessage": "只影响普通群消息；@、人格名和私聊始终直接回复。",
                    },
                    {
                        "field": "gate_mode",
                        "label": "门控模式",
                        "component": "Select",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["gate_mode"],
                        "helpMessage": "observe 只记录不拦截；enforce 才按概率和窗口预算拦截。",
                        "componentProps": {
                            "options": [
                                {"label": "观察（只记录）", "value": "observe"},
                                {"label": "启用（会拦截）", "value": "enforce"},
                            ],
                        },
                    },
                    {
                        "field": "talk_value",
                        "label": "回复频率",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["talk_value"],
                        "helpMessage": "0 到 1；1 表示窗口内预计的回复全部保留。",
                        "componentProps": {"min": 0, "max": 1, "step": 0.1},
                    },
                    {
                        "field": "group_allowlist",
                        "label": "启用群",
                        "component": "GTags",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["group_allowlist"],
                        "helpMessage": "填写群号；留空表示没有群启用主动接话。",
                    },
                    {
                        "field": "enable_talk_value_rules",
                        "label": "分时段回复频率",
                        "component": "Switch",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["enable_talk_value_rules"],
                        "helpMessage": "规则在 config.json 的 talk_value_rules 中配置；此开关即时生效。",
                    },
                    {
                        "field": "no_action_backoff_base_seconds",
                        "label": "退避基数（秒）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["no_action_backoff_base_seconds"],
                        "helpMessage": "连续空闲轮次从第 2 次起指数退避。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "no_action_backoff_cap_seconds",
                        "label": "退避上限（秒）",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["no_action_backoff_cap_seconds"],
                        "helpMessage": "单次退避最多等待的秒数。",
                        "componentProps": {"min": 0},
                    },
                    {
                        "field": "no_action_backoff_start_count",
                        "label": "开始退避轮次",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["no_action_backoff_start_count"],
                        "helpMessage": "第几次连续空闲开始退避。",
                        "componentProps": {"min": 1},
                    },
                    {
                        "field": "no_action_backoff_bypass_pending_count",
                        "label": "绕过退避的消息数",
                        "component": "InputNumber",
                        "default": SYSTEM_DEFAULTS["chat"]["proactive"]["no_action_backoff_bypass_pending_count"],
                        "helpMessage": "待处理消息达到这个数量时不再退避。",
                        "componentProps": {"min": 0},
                    },
                ],
            },
            {
                "key": "chat.web_search",
                "title": "联网搜索",
                "schemas": [
                    {
                        "field": "base_url",
                        "label": "搜索 API 地址",
                        "component": "Input",
                        "default": SYSTEM_DEFAULTS["chat"]["web_search"]["base_url"],
                        "helpMessage": "DeepSeek 官方地址；中转站填到域名根即可，不要带 /v1。",
                    },
                    {
                        "field": "api_key",
                        "label": "Search API Key",
                        "component": "InputPassword",
                        "default": SYSTEM_DEFAULTS["chat"]["web_search"]["api_key"],
                        "helpMessage": "独立填写，不会复用对话 Provider 的 key；留空时搜索工具提示不可用。",
                    },
                ],
            },
        ],
    }


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


@router.get("")
async def get_config() -> dict[str, Any]:
    """读取控制台配置。"""
    return get_manager().get_console_configs()


@router.put("")
async def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    """保存控制台配置。"""
    try:
        get_manager().save_console_configs(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}
