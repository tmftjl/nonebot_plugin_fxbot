"""AI 对话子系统导出。"""

from .types import MentionRef, ChatRequest, ChatResponse, InboundSegment
from .service import ChatService, chat_service

__all__ = [
    "ChatRequest",
    "ChatResponse",
    "ChatService",
    "InboundSegment",
    "MentionRef",
    "chat_service",
]
