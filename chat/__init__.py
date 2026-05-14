"""AI 对话子系统导出。"""

from .service import ChatService, chat_service
from .types import ChatRequest, ChatResponse, InboundSegment

__all__ = ["ChatRequest", "ChatResponse", "ChatService", "InboundSegment", "chat_service"]
