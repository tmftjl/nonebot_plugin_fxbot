"""话题分析，流程与提示词取自原版 topic_analyzer。"""

from __future__ import annotations

from typing import Any

from .base import BaseAnalyzer
from .prompts import TOPIC_PROMPT, render
from .schemas import build_topics_schema
from .json_utils import extract_topics_with_regex
from .validation import validate_topic_items


class TopicAnalyzer(BaseAnalyzer):
    data_type = "话题"
    items_key = "topics"

    def build_prompt(self, data: list[dict[str, Any]]) -> str:
        if not data:
            return ""
        messages_text = "\n".join(f"[{msg['time']}] [{msg['user_id']}]: {msg['content']}" for msg in data)
        return render(TOPIC_PROMPT, max_topics=self.max_count, messages_text=messages_text)

    def build_schema(self) -> dict[str, Any]:
        return build_topics_schema(self.max_count)

    def extract_with_regex(self, result_text: str) -> list[dict[str, Any]]:
        return extract_topics_with_regex(result_text, self.max_count)

    def validate(self, data_list: Any) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return validate_topic_items(data_list)

    def create_data_objects(self, data_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        topics: list[dict[str, Any]] = []
        for item in data_list[: self.max_count]:
            topic = str(item.get("topic") or "").strip()
            detail = str(item.get("detail") or "").strip()
            if not topic or not detail:
                continue
            contributors = [str(value).strip() for value in item.get("contributors") or [] if str(value).strip()]
            topics.append(
                {
                    "topic": topic,
                    "contributors": contributors[:5] or ["群友"],
                    "detail": detail,
                    "contributor_ids": [],
                }
            )
        return topics

    async def analyze(self, data: list[dict[str, Any]]) -> list[dict[str, Any]]:
        topics = await super().analyze(data)
        id_to_nickname = {str(msg["user_id"]): str(msg.get("sender") or "") for msg in data if msg.get("user_id")}
        known_ids = set(id_to_nickname)
        for topic in topics:
            valid_ids: list[str] = []
            for raw_id in topic["contributors"]:
                user_id = raw_id.strip().strip("[]")
                if user_id in known_ids and user_id not in valid_ids:
                    valid_ids.append(user_id)
            topic["contributor_ids"] = valid_ids
            topic["contributors"] = [id_to_nickname.get(user_id, user_id) for user_id in valid_ids]
        return topics
