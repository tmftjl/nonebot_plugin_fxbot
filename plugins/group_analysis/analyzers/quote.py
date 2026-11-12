"""金句分析，流程与提示词取自原版 golden_quote_analyzer。"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from .base import BaseAnalyzer
from .prompts import GOLDEN_QUOTE_PROMPT, render
from .schemas import build_golden_quotes_schema
from .json_utils import extract_golden_quotes_with_regex
from .validation import validate_golden_quote_items


class GoldenQuoteAnalyzer(BaseAnalyzer):
    data_type = "金句"
    items_key = "quotes"

    def build_prompt(self, data: list[dict[str, Any]]) -> str:
        if not data:
            return ""
        messages_text = "\n".join(f"[{msg['time']}] [{msg['user_id']}]: {msg['content']}" for msg in data)
        return render(GOLDEN_QUOTE_PROMPT, max_golden_quotes=self.max_count, messages_text=messages_text)

    def build_schema(self) -> dict[str, Any]:
        return build_golden_quotes_schema(self.max_count)

    def extract_with_regex(self, result_text: str) -> list[dict[str, Any]]:
        return extract_golden_quotes_with_regex(result_text, self.max_count)

    def validate(self, data_list: Any) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return validate_golden_quote_items(data_list)

    def create_data_objects(self, data_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        quotes: list[dict[str, Any]] = []
        for item in data_list[: self.max_count]:
            content = str(item.get("content") or "").strip()
            sender = str(item.get("sender") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not content or not sender or not reason:
                continue
            quotes.append({"content": content, "sender": sender, "reason": reason})
        return quotes

    async def analyze(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        quotes = await super().analyze(data)
        id_to_nickname = {str(msg["user_id"]): str(msg.get("sender") or "") for msg in data if msg.get("user_id")}
        for quote in quotes:
            potential_id = quote["sender"].strip().strip("[]")
            if potential_id in id_to_nickname:
                quote["user_id"] = potential_id
                quote["sender"] = id_to_nickname[potential_id]
            else:
                logger.warning(f"[金句分析] 无法匹配 User ID: {potential_id}，金句将无法显示真实头像。")
        return quotes
