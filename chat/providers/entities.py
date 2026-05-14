"""Provider 层基础数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class ProviderType(Enum):
    """Provider 能力类型。"""

    CHAT = "chat"
    EMBEDDING = "embedding"
    TTS = "tts"
    STT = "stt"
    RERANK = "rerank"


@dataclass
class ProviderMeta:
    """Provider 实例元数据。"""

    id: str
    model: str | None
    type: str
    provider_type: ProviderType = ProviderType.CHAT


@dataclass
class ProviderMetadata(ProviderMeta):
    """Provider 注册元数据。"""

    desc: str = ""
    cls_type: Any = None
    default_config: dict[str, Any] | None = None
    display_name: str | None = None


@dataclass
class LLMRequest:
    """LLM 请求结构。"""

    messages: list[dict[str, Any]] = field(default_factory=list)
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int | None = None
    tools: list[dict[str, Any]] | None = None
    image_urls: list[str] = field(default_factory=list)
    system_prompt: str = ""

    def get_plain_text(self) -> str:
        """提取最后一条用户消息的纯文本。"""
        for msg in reversed(self.messages):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return "".join(
                    item.get("text", "")
                    for item in content
                    if item.get("type") == "text"
                )
        return ""


@dataclass
class LLMResponse:
    """LLM 响应结构。"""

    role: str = "assistant"
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_ids: list[str] = field(default_factory=list)
    reasoning_content: str = ""
    raw_response: Any = None
    is_stream_chunk: bool = False
    provider_data: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用。"""
        return bool(self.tool_calls)

    def to_message_dict(self) -> dict[str, Any]:
        """转换为 OpenAI 风格消息字典。"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg
