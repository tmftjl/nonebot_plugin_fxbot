"""控制台元信息路由。"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, APIRouter
from nonebot import logger

from ..auth import bearer_auth
from ...chat.personas import DEFAULT_PERSONA, list_personas, delete_persona, save_persona_text
from ...plugin.builder import get_plugin_display_names, get_command_display_names
from ...chat.providers.usage import get_model_task_usage
from ...system_control.bot_status import get_today_stats, get_stats_history

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


@router.get("/stats/history")
async def get_stats_history_route(days: int = 14) -> dict[str, Any]:
    """获取最近若干天的消息发送汇总（本地 JSON，滚动保留 30 天）。"""
    return await get_stats_history(days)


@router.get("/stats/model_usage")
async def get_model_usage(hours: int = 24) -> dict[str, Any]:
    """获取近 N 小时的模型调用次数、失败率与平均耗时（汇总保留 7 天）。"""
    return await get_model_task_usage(hours)


@router.get("/ai_chat/personas")
async def get_ai_personas() -> dict[str, Any]:
    """获取 AI 人格列表与不可删除的默认人格名。"""
    return {"items": list_personas(), "default": DEFAULT_PERSONA}


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
    if key.strip() not in list_personas():
        return {"success": False, "message": "人格不存在"}
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
    except OSError:
        logger.opt(exception=True).error(f"[Console] 删除人格失败: key={key}")
        return {"success": False, "message": "删除失败，请稍后再试"}
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
