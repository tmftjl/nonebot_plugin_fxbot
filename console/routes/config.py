"""系统配置路由。"""

from __future__ import annotations

import importlib
from typing import Any

from nonebot import logger
from fastapi import APIRouter, Depends, HTTPException

from ...config import SYSTEM_DEFAULTS, get_manager
from ...utils.paths import built_in_plugins_dir
from ..auth import bearer_auth

router = APIRouter(prefix="/config", tags=["fxbot-config"], dependencies=[Depends(bearer_auth)])


def _system_config_tab() -> dict[str, Any]:
    """返回系统配置表单结构。"""
    return {
        "key": "system",
        "title": "系统配置",
        "cards": [
            {
                "key": "membership",
                "title": "会员系统",
                "schemas": [
                    {"field": "enabled", "label": "启用会员门禁", "component": "Switch", "default": SYSTEM_DEFAULTS["membership"]["enabled"], "helpMessage": "关闭后，群会员门禁不再拦截消息。"},
                    {"field": "free_bot_ids", "label": "免费 Bot", "component": "GTags", "default": SYSTEM_DEFAULTS["membership"]["free_bot_ids"], "helpMessage": "这些 Bot 自编号不执行会员门禁。"},
                    {"field": "expire_notice_days", "label": "到期提示天数", "component": "InputNumber", "default": SYSTEM_DEFAULTS["membership"]["expire_notice_days"], "helpMessage": "会员剩余多少天内，在命令消息中提示续费。", "componentProps": {"min": 0}},
                    {"field": "expire_prompt_text_prefixes", "label": "提示文本前缀", "component": "GTags", "default": SYSTEM_DEFAULTS["membership"]["expire_prompt_text_prefixes"], "helpMessage": "普通消息以这些文本开头时，也触发会员快到期提示。"},
                    {"field": "auto_leave_expired_groups", "label": "过期自动退群", "component": "Switch", "default": SYSTEM_DEFAULTS["membership"]["auto_leave_expired_groups"], "helpMessage": "到期后自动让托管 Bot 退群。"},
                    {"field": "enable_scheduler", "label": "启用定时任务", "component": "Switch", "default": SYSTEM_DEFAULTS["membership"]["enable_scheduler"], "helpMessage": "关闭后不会自动执行到期检查。"},
                    {"field": "schedule_time", "label": "定时任务时间", "component": "Input", "default": SYSTEM_DEFAULTS["membership"]["schedule_time"], "helpMessage": "每日执行会员检查的时间，格式 HH:MM。", "componentProps": {"placeholder": "12:00"}},
                    {"field": "batch_delay_seconds", "label": "批处理延迟（秒）", "component": "InputNumber", "default": SYSTEM_DEFAULTS["membership"]["batch_delay_seconds"], "helpMessage": "群消息和退群操作之间的间隔。", "componentProps": {"min": 0}},
                    {"field": "contact_info", "label": "续费联系信息", "component": "Textarea", "default": SYSTEM_DEFAULTS["membership"]["contact_info"], "helpMessage": "展示在到期查询和提醒消息中的联系方式。"},
                ],
            },
            {
                "key": "console",
                "title": "控制台",
                "schemas": [
                    {"field": "enabled", "label": "启用控制台", "component": "Switch", "default": SYSTEM_DEFAULTS["console"]["enabled"], "helpMessage": "关闭后不会挂载控制台页面和接口。"},
                    {"field": "mount_path", "label": "挂载路径", "component": "Input", "default": SYSTEM_DEFAULTS["console"]["mount_path"], "helpMessage": "控制台页面访问路径。"},
                    {"field": "token", "label": "访问 Token", "component": "InputPassword", "default": SYSTEM_DEFAULTS["console"]["token"], "helpMessage": "留空时会在首次登录时自动生成。"},
                ],
            },
            {
                "key": "chat",
                "title": "AI 对话",
                "schemas": [
                    {"field": "enabled", "label": "启用 AI", "component": "Switch", "default": SYSTEM_DEFAULTS["chat"]["enabled"], "helpMessage": "关闭后 AI 兜底不会接管消息。"},
                    {"field": "command_prefixes", "label": "命令前缀", "component": "GTags", "default": SYSTEM_DEFAULTS["chat"]["command_prefixes"], "helpMessage": "这些前缀开头的消息不会进入 AI 兜底。"},
                    {"field": "group_requires_mention", "label": "群聊需 @bot", "component": "Switch", "default": SYSTEM_DEFAULTS["chat"]["group_requires_mention"], "helpMessage": "开启后群聊必须 @bot 才会进入 AI 兜底。"},
                    {"field": "provider", "label": "默认 Provider", "component": "Input", "default": SYSTEM_DEFAULTS["chat"]["provider"], "helpMessage": "未指定时使用的对话 Provider 名称。"},
                    {"field": "max_history", "label": "最大历史轮数", "component": "InputNumber", "default": SYSTEM_DEFAULTS["chat"]["max_history"], "helpMessage": "保留给 AI 的会话轮数。", "componentProps": {"min": 0}},
                    {"field": "max_tool_rounds", "label": "最大工具轮数", "component": "InputNumber", "default": SYSTEM_DEFAULTS["chat"]["max_tool_rounds"], "helpMessage": "单次对话允许的工具调用轮数。", "componentProps": {"min": 0}},
                ],
            },
            {
                "key": "permission",
                "title": "权限",
                "schemas": [
                    {"field": "bot_admins", "label": "Bot 管理员", "component": "GTags", "default": SYSTEM_DEFAULTS["permission"]["bot_admins"], "helpMessage": "这些账号拥有 Bot 管理员权限。"},
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


@router.get("/tabs")
async def get_config_tabs() -> list[dict[str, Any]]:
    """返回控制台配置表单结构。"""
    return [_system_config_tab(), *_plugin_config_tabs()]


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
