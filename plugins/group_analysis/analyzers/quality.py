"""聊天质量分析，流程与提示词取自原版 chat_quality_analyzer。"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from .base import BaseAnalyzer
from .prompts import QUALITY_PROMPT, render
from .schemas import wrap_object, build_chat_quality_schema
from .json_utils import extract_quality_with_regex, parse_json_object_response
from .validation import validate_quality_review_item
from ....chat.providers.entities import JSONResponseError

MAX_DIMENSIONS = 8


class ChatQualityAnalyzer(BaseAnalyzer):
    data_type = "聊天质量"
    items_key = "quality"

    def build_prompt(self, data: list[dict[str, Any]]) -> str:
        if not data:
            return ""
        messages_text = "\n".join(
            f"[{message['time']}] [{message['sender']}]: {message['content']}" for message in data[:1000]
        )
        return render(QUALITY_PROMPT, messages_text=messages_text)

    def build_schema(self) -> dict[str, Any]:
        return build_chat_quality_schema(MAX_DIMENSIONS)

    def extract_with_regex(self, result_text: str) -> list[dict[str, Any]]:
        return []

    def validate(self, data: Any) -> tuple[bool, Any, str | None]:
        return validate_quality_review_item(data)

    def create_data_objects(self, data_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return []

    def _build_review(self, data: dict[str, Any]) -> dict[str, Any]:
        dimensions = []
        total = 0.0
        for item in data.get("dimensions") or []:
            percentage = max(0.0, min(100.0, float(item.get("percentage") or 0)))
            total += percentage
            dimensions.append({**item, "percentage": percentage})
        factor = 100.0 / total if total > 100 else 1.0
        for item in dimensions:
            item["percentage"] = round(item["percentage"] * factor, 1)
        return {
            "title": str(data.get("title") or "聊天质量锐评"),
            "subtitle": str(data.get("subtitle") or "今天的群里发生了什么？"),
            "dimensions": dimensions,
            "summary": str(data.get("summary") or "今天也是充满活力的一天。"),
        }

    def _try_parse_object_with_fallback(self, result_text: str) -> tuple[bool, dict[str, Any] | None, str | None]:
        success, parsed_data, error_msg = parse_json_object_response(result_text, self.data_type)
        if success and parsed_data:
            valid, normalized, validation_error = validate_quality_review_item(parsed_data)
            if valid and normalized:
                return True, normalized, None
            error_msg = validation_error or error_msg
        regex_data = extract_quality_with_regex(result_text)
        if regex_data:
            valid, normalized, validation_error = validate_quality_review_item(regex_data)
            if valid and normalized:
                logger.warning("聊天质量首轮结构化失败后，正则提取成功")
                return True, normalized, None
            error_msg = validation_error or error_msg
        return False, None, error_msg

    async def analyze(self, data: list[dict[str, Any]]) -> dict[str, Any] | None:
        prompt = self.build_prompt(data)
        if not prompt.strip():
            return None
        messages = self.build_messages(prompt)
        schema = wrap_object(self.items_key, self.build_schema())
        raw = ""
        try:
            result = await self.model.chat_json(messages=messages, schema=schema)
            raw = result.response.content
            review = result.data.get(self.items_key)
            if isinstance(review, dict):
                valid, normalized, _ = validate_quality_review_item(review)
                if valid and normalized:
                    return self._build_review(normalized)
        except JSONResponseError as exc:
            raw = exc.response.content
            logger.warning(f"{self.data_type}结构化解析失败，复用原输出降级解析")
        except Exception as exc:
            logger.warning(f"{self.data_type}结构化请求失败，改用文本解析: {exc}")

        error_msg = ""
        try:
            if not raw:
                response = await self.model.chat(messages=messages, temperature=0.1)
                raw = str(response.content or "")
            success, parsed_data, error_msg = self._try_parse_object_with_fallback(raw)
            if success and parsed_data:
                return self._build_review(parsed_data)
        except Exception as exc:
            logger.warning(f"{self.data_type}模型调用失败: {exc}")

        for attempt, temperature in enumerate((0.1, 0.0), start=1):
            if not raw:
                break
            logger.warning(f"聊天质量结构化解析失败，触发修复重试 (attempt={attempt}, temperature={temperature})")
            retry_prompt = self.build_schema_retry_prompt(prompt, raw, error_msg, attempt)
            try:
                response = await self.model.chat(
                    messages=[{"role": "user", "content": retry_prompt}], temperature=temperature
                )
            except Exception as exc:
                logger.warning(f"{self.data_type}修复重试调用失败: {exc}")
                continue
            raw = str(response.content or "")
            success, parsed_data, error_msg = self._try_parse_object_with_fallback(raw)
            if success and parsed_data:
                return self._build_review(parsed_data)

        logger.error(f"{self.data_type}分析失败: JSON解析和正则提取均未成功 ({error_msg or '未产出有效内容'})")
        return None
