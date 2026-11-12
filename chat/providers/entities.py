"""Provider 层基础数据结构。"""

from __future__ import annotations

import json
from enum import Enum
from typing import Any
from dataclasses import field, dataclass


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
                return "".join(item.get("text", "") for item in content if item.get("type") == "text")
        return ""


_PROMPT_KEYS = ("prompt_tokens", "input_tokens", "prompt_token_count", "promptTokenCount")
_COMPLETION_KEYS = ("completion_tokens", "output_tokens", "candidates_token_count", "candidatesTokenCount")
_TOTAL_KEYS = ("total_tokens", "total_token_count", "totalTokenCount")


@dataclass
class TokenUsage:
    """一次或多次 LLM 调用的 token 用量。"""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    def __bool__(self) -> bool:
        return any(value is not None for value in (self.prompt_tokens, self.completion_tokens, self.total_tokens))

    def merge(self, other: TokenUsage) -> None:
        """累计服务端已报告的字段，未知字段不参与求和。"""
        for key, value in other.to_dict().items():
            setattr(self, key, (getattr(self, key) or 0) + value)

    def to_dict(self) -> dict[str, int]:
        """导出已知字段，保留缺失与明确零值的区别。"""
        return {
            key: value
            for key, value in {
                "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens,
                "total_tokens": self.total_tokens,
            }.items()
            if value is not None
        }


def _usage_field(source: Any, keys: tuple[str, ...]) -> int | None:
    for key in keys:
        value = source.get(key) if isinstance(source, dict) else getattr(source, key, None)
        if type(value) is int and value >= 0:
            return value
    return None


def token_usage_of(raw: Any) -> TokenUsage:
    """归一化服务端用量；缺失字段保留未知，不按文本估算。"""
    if raw is None:
        return TokenUsage()
    if isinstance(raw, dict):
        source = raw.get("usageMetadata") or raw.get("usage_metadata") or raw.get("usage")
    else:
        source = getattr(raw, "usage", None) or getattr(raw, "usage_metadata", None)
    if source is None:
        return TokenUsage()
    prompt = _usage_field(source, _PROMPT_KEYS)
    completion = _usage_field(source, _COMPLETION_KEYS)
    total = _usage_field(source, _TOTAL_KEYS)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    return TokenUsage(prompt, completion, total)


@dataclass
class LLMResponse:
    """LLM 响应结构。"""

    role: str = "assistant"
    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_call_ids: list[str] = field(default_factory=list)
    reasoning_content: str = ""
    raw_response: Any = None
    usage: TokenUsage = field(default_factory=TokenUsage)
    is_stream_chunk: bool = False
    provider_data: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """在响应边界归一化原生用量，调用方只读取统一字段。"""
        if not self.usage and self.raw_response is not None:
            self.usage = token_usage_of(self.raw_response)

    @property
    def has_tool_calls(self) -> bool:
        """是否包含工具调用。"""
        return bool(self.tool_calls)

    def to_message_dict(self) -> dict[str, Any]:
        """转换为 OpenAI 风格消息字典，附带可往返的 Provider 原生数据。"""
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        if self.provider_data:
            msg["provider_data"] = self.provider_data
        return msg


class LLMResponseError(RuntimeError):
    """模型返回不可用结果，保留已收到的完整响应。"""

    def __init__(self, response: LLMResponse, message: str) -> None:
        super().__init__(message)
        self.response = response


class JSONResponseError(LLMResponseError):
    """模型返回的正文不是合法 JSON 对象。"""

    def __init__(self, response: LLMResponse) -> None:
        super().__init__(response, "模型输出不是合法 JSON 对象")


@dataclass
class JSONResponse:
    """结构化数据及其完整模型响应。"""

    data: dict[str, Any]
    response: LLMResponse

    @classmethod
    def from_response(cls, response: LLMResponse) -> JSONResponse:
        """解析 JSON 对象并保留正文、原生响应和用量。"""
        text = response.content.strip()
        if text.startswith("```") and text.endswith("```"):
            text = "\n".join(text.splitlines()[1:-1]).strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JSONResponseError(response) from exc
        if not isinstance(data, dict):
            raise JSONResponseError(response)
        return cls(data=data, response=response)
