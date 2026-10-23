"""控制台元信息路由。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, APIRouter
from nonebot import logger

from ..auth import bearer_auth
from ...chat.personas import list_personas, delete_persona, save_persona_text
from ...plugin.builder import get_plugin_display_names, get_command_display_names
from ...system_control.bot_status import get_today_stats

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
    """获取今日消息统计，直接读本进程内的统计缓存，不依赖任何外部服务。"""
    return await get_today_stats()


@router.get("/ai_chat/personas")
async def get_ai_personas() -> dict[str, str]:
    """获取 AI 人格列表。"""
    return list_personas()


@router.post("/ai_chat/persona")
async def create_ai_persona(payload: dict[str, Any]) -> dict[str, Any]:
    """创建 AI 人格。"""
    key = str(payload.get("key") or "").strip()
    desc = str(payload.get("desc") or "").strip()
    if not key:
        return {"success": False, "message": "人格名称不能为空"}
    try:
        save_persona_text(key, desc, overwrite=False)
    except FileExistsError:
        return {"success": False, "message": "已存在同名人格"}
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    except OSError:
        logger.opt(exception=True).error(f"[Console] 创建人格失败: key={key}")
        return {"success": False, "message": "保存失败，请稍后再试"}
    return {"success": True, "message": ""}


@router.put("/ai_chat/persona/{key}")
async def update_ai_persona(key: str, payload: dict[str, Any]) -> dict[str, Any]:
    """更新 AI 人格。"""
    desc = str(payload.get("desc") or "").strip()
    try:
        save_persona_text(key, desc)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
    except OSError:
        logger.opt(exception=True).error(f"[Console] 更新人格失败: key={key}")
        return {"success": False, "message": "保存失败，请稍后再试"}
    return {"success": True, "message": ""}


@router.delete("/ai_chat/persona/{key}")
async def delete_ai_persona(key: str) -> dict[str, bool | str]:
    """删除 AI 人格。"""
    try:
        delete_persona(key)
    except ValueError as exc:
        return {"success": False, "message": str(exc)}
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
        return {
            "success": True,
            "data": [{"label": name, "value": name} for name in sorted(tools)],
        }
    except Exception:
        return {"success": True, "data": [], "fallback": True}
