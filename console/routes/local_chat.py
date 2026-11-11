"""鉴权后的独立网页聊天接口，仅接受本渠道的 UUID 会话。"""

from __future__ import annotations

from uuid import UUID
from typing import Any

from fastapi import Depends, APIRouter, HTTPException
from nonebot import logger
from pydantic import Field, BaseModel
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from ..auth import bearer_auth
from ..chat_channel import REJECTION_MESSAGES, ChannelRejected, console_chat
from ...chat.personas import load_personas
from ...chat.providers import provider_manager

router = APIRouter(prefix="/local-chat", tags=["fxbot-local-chat"], dependencies=[Depends(bearer_auth)])


class ChatInput(BaseModel):
    """有界文字输入与重试去重 ID，不接受平台、群号或任意租户。"""

    text: str = Field(min_length=1, max_length=8000)
    message_id: UUID
    nickname: str = Field(default="控制台用户", min_length=1, max_length=32)
    persona: str = Field(default="", max_length=100)
    provider: str = Field(default="", max_length=100)


class StopInput(BaseModel):
    """停止操作必须指明本轮消息，避免延迟请求误停新轮次。"""

    message_id: UUID


@router.post("/{conversation}/stop")
async def stop(conversation: UUID, payload: StopInput) -> dict[str, bool]:
    """显式停止指定轮次，保留已经实际发送的内容。"""
    try:
        return {"stopped": await console_chat.cancel(conversation, payload.message_id)}
    except Exception:
        logger.opt(exception=True).warning("[ConsoleChat] 停止失败")
        raise HTTPException(500, "停止对话失败，请稍后重试") from None


@router.get("/options")
async def options() -> dict[str, Any]:
    """返回已有的人格和模型选项，不另建模型配置。"""
    try:
        return {
            "personas": sorted(load_personas()),
            "providers": sorted(provider_manager.list_providers_by_type("chat")),
        }
    except Exception:
        logger.opt(exception=True).warning("[ConsoleChat] 读取选项失败")
        raise HTTPException(500, "加载对话选项失败") from None


@router.get("/{conversation}")
async def history(conversation: UUID) -> dict[str, Any]:
    """恢复该独立会话的可见历史。"""
    try:
        return await console_chat.snapshot(conversation)
    except Exception:
        logger.opt(exception=True).warning("[ConsoleChat] 读取历史失败")
        raise HTTPException(500, "加载对话失败") from None


@router.post("/{conversation}/messages")
async def send(conversation: UUID, payload: ChatInput) -> StreamingResponse:
    """流式返回正式发送链的结果，浏览器断开时取消生成。"""
    try:
        turn = await console_chat.begin(conversation, **payload.model_dump())
        return StreamingResponse(
            console_chat.events(turn),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
            background=BackgroundTask(console_chat.release, turn),
        )
    except ChannelRejected as exc:
        raise HTTPException(409, REJECTION_MESSAGES.get(exc.code, "当前无法接收消息")) from None
    except Exception:
        logger.opt(exception=True).warning("[ConsoleChat] 接收消息失败")
        raise HTTPException(500, "接收消息失败，请稍后重试") from None


@router.delete("/{conversation}")
async def clear(conversation: UUID) -> dict[str, bool]:
    """清空前先取消在途请求，不影响其他聊天或删除长期记忆。"""
    try:
        cleared = await console_chat.clear(conversation)
        return {"success": True, "history_cleared": cleared}
    except ChannelRejected as exc:
        raise HTTPException(409, REJECTION_MESSAGES.get(exc.code, "当前无法清空会话")) from None
    except Exception:
        logger.opt(exception=True).warning("[ConsoleChat] 清空失败")
        raise HTTPException(500, "清空对话失败，请稍后重试") from None
