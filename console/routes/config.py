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
                    {"field": "enabled", "label": "启用会员门禁", "component": "Switch", "default": SYSTEM_DEFAULTS["membership"]["enabled"]},
                    {"field": "cache_ttl_seconds", "label": "缓存时间（秒）", "component": "InputNumber", "default": SYSTEM_DEFAULTS["membership"]["cache_ttl_seconds"], "componentProps": {"min": 1}},
                    {"field": "schedule_time", "label": "定时任务时间", "component": "Input", "default": SYSTEM_DEFAULTS["membership"]["schedule_time"], "componentProps": {"placeholder": "12:00"}},
                    {"field": "auto_leave_expired_groups", "label": "过期自动退群", "component": "Switch", "default": SYSTEM_DEFAULTS["membership"]["auto_leave_expired_groups"]},
                    {"field": "contact_info", "label": "续费联系信息", "component": "Textarea", "default": SYSTEM_DEFAULTS["membership"]["contact_info"]},
                ],
            },
            {
                "key": "console",
                "title": "控制台",
                "schemas": [
                    {"field": "enabled", "label": "启用控制台", "component": "Switch", "default": SYSTEM_DEFAULTS["console"]["enabled"]},
                    {"field": "mount_path", "label": "挂载路径", "component": "Input", "default": SYSTEM_DEFAULTS["console"]["mount_path"]},
                    {"field": "token", "label": "访问 Token", "component": "InputPassword", "default": SYSTEM_DEFAULTS["console"]["token"]},
                ],
            },
            {
                "key": "chat",
                "title": "AI 对话",
                "schemas": [
                    {"field": "enabled", "label": "启用 AI", "component": "Switch", "default": SYSTEM_DEFAULTS["chat"]["enabled"]},
                    {"field": "provider", "label": "默认 Provider", "component": "Input", "default": SYSTEM_DEFAULTS["chat"]["provider"]},
                    {"field": "max_history", "label": "最大历史轮数", "component": "InputNumber", "default": SYSTEM_DEFAULTS["chat"]["max_history"], "componentProps": {"min": 0}},
                    {"field": "max_tool_rounds", "label": "最大工具轮数", "component": "InputNumber", "default": SYSTEM_DEFAULTS["chat"]["max_tool_rounds"], "componentProps": {"min": 0}},
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
