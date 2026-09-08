"""AI 对话子系统导出。"""

from .types import ChatRequest, ChatResponse, InboundSegment
from .service import ChatService, chat_service

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ChatService",
    "InboundSegment",
    "chat_service",
]
