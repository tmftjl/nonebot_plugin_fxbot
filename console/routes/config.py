"""系统配置路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from ...config import get_manager
from ..auth import bearer_auth

router = APIRouter(prefix="/config", tags=["fxbot-config"], dependencies=[Depends(bearer_auth)])


@router.get("/tabs")
async def get_config_tabs() -> list[dict[str, Any]]:
    """返回控制台配置表单结构。"""
    return [
        {
            "key": "system",
            "title": "系统配置",
            "cards": [
                {
                    "key": "membership",
                    "title": "会员系统",
                    "schemas": [
                        {"field": "enabled", "label": "启用会员门禁", "component": "Switch"},
                        {"field": "cache_ttl_seconds", "label": "缓存时间（秒）", "component": "InputNumber", "componentProps": {"min": 1}},
                        {"field": "schedule_time", "label": "定时任务时间", "component": "Input", "componentProps": {"placeholder": "12:00"}},
                        {"field": "auto_leave_expired_groups", "label": "过期自动退群", "component": "Switch"},
                        {"field": "contact_info", "label": "续费联系信息", "component": "Textarea"},
                    ],
                },
                {
                    "key": "console",
                    "title": "控制台",
                    "schemas": [
                        {"field": "enabled", "label": "启用控制台", "component": "Switch"},
                        {"field": "mount_path", "label": "挂载路径", "component": "Input"},
                        {"field": "token", "label": "访问 Token", "component": "InputPassword"},
                    ],
                },
                {
                    "key": "chat",
                    "title": "AI 对话",
                    "schemas": [
                        {"field": "enabled", "label": "启用 AI", "component": "Switch"},
                        {"field": "provider", "label": "默认 Provider", "component": "Input"},
                        {"field": "max_history", "label": "最大历史轮数", "component": "InputNumber", "componentProps": {"min": 0}},
                        {"field": "max_tool_rounds", "label": "最大工具轮数", "component": "InputNumber", "componentProps": {"min": 0}},
                    ],
                },
            ],
        }
    ]


@router.get("")
async def get_config() -> dict[str, Any]:
    """读取系统配置。"""
    return get_manager().get_system()


@router.put("")
async def update_config(payload: dict[str, Any]) -> dict[str, Any]:
    """保存系统配置。"""
    try:
        proxy = get_manager().register("system", payload)
        proxy.save(payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"success": True}
