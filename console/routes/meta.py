"""控制台元信息路由。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...plugin.builder import get_command_display_names, get_plugin_display_names
from ...chat.personas import delete_persona, list_personas, save_persona_text
from ..auth import bearer_auth

router = APIRouter(tags=["fxbot-meta"], dependencies=[Depends(bearer_auth)])


@router.get("/plugins")
async def get_plugins() -> dict[str, str]:
    """获取插件展示名。"""
    return get_plugin_display_names()


@router.get("/commands")
async def get_commands() -> dict[str, dict[str, str]]:
    """获取命令展示名。"""
    return get_command_display_names()


@router.get("/stats/today")
async def get_stats_today() -> dict[str, Any]:
    """返回消息统计占位数据。"""
    return {"bots": {}}


@router.get("/ai_chat/personas")
async def get_ai_personas() -> dict[str, str]:
    """获取 AI 人格列表。"""
    return list_personas()


@router.post("/ai_chat/persona")
async def create_ai_persona(payload: dict[str, Any]) -> dict[str, bool]:
    """创建 AI 人格。"""
    key = str(payload.get("key") or "").strip()
    desc = str(payload.get("desc") or "").strip()
    if not key:
        return {"success": False}
    save_persona_text(key, desc)
    return {"success": True}


@router.put("/ai_chat/persona/{key}")
async def update_ai_persona(key: str, payload: dict[str, Any]) -> dict[str, bool]:
    """更新 AI 人格。"""
    desc = str(payload.get("desc") or "").strip()
    save_persona_text(key, desc)
    return {"success": True}


@router.delete("/ai_chat/persona/{key}")
async def delete_ai_persona(key: str) -> dict[str, bool]:
    """删除 AI 人格。"""
    delete_persona(key)
    return {"success": True}


@router.get("/ai_chat/knowledge/{persona_name}/stats")
async def get_ai_knowledge_stats(persona_name: str) -> dict[str, Any]:
    """返回知识库统计占位数据。"""
    return {"persona_name": persona_name, "count": 0}


@router.post("/ai_chat/knowledge/{persona_name}/text")
async def import_ai_knowledge_text(persona_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """导入知识库文本占位接口。"""
    return {"success": False, "message": "知识库功能未启用", "count": 0}


@router.post("/ai_chat/knowledge/{persona_name}/file")
async def import_ai_knowledge_file(persona_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """导入知识库文件占位接口。"""
    return {"success": False, "message": "知识库功能未启用", "count": 0}


@router.delete("/ai_chat/knowledge/{persona_name}")
async def clear_ai_knowledge(persona_name: str) -> dict[str, Any]:
    """清空知识库占位接口。"""
    return {"success": False, "message": "知识库功能未启用"}


@router.get("/ai_chat/tools")
async def get_ai_tools() -> dict[str, Any]:
    """返回工具列表。"""
    try:
        from ...chat.tools import default_registry

        tools = [spec.name for spec in default_registry.list()]
        return {"success": True, "data": [{"label": name, "value": name} for name in sorted(tools)]}
    except Exception:
        return {"success": True, "data": [], "fallback": True}
