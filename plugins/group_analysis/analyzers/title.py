"""用户称号分析，流程与提示词取自原版 user_title_analyzer。"""

from __future__ import annotations

from typing import Any

from .base import BaseAnalyzer
from .prompts import USER_TITLE_PROMPT, render
from .schemas import build_user_titles_schema
from .json_utils import extract_user_titles_with_regex
from .validation import validate_user_title_items


class UserTitleAnalyzer(BaseAnalyzer):
    data_type = "用户称号"
    items_key = "titles"

    def build_prompt(self, data: list[dict[str, Any]]) -> str:
        if not data:
            return ""
        users_text = "\n".join(
            f"- {user['name']} (ID:{user['user_id']}): "
            f"发言{user['message_count']}条, 平均{user['avg_chars']}字, "
            f"表情比例{user['emoji_ratio']}, 夜间发言比例{user['night_ratio']}, "
            f"回复比例{user['reply_ratio']}"
            for user in data
        )
        return render(USER_TITLE_PROMPT, users_text=users_text)

    def build_schema(self) -> dict[str, Any]:
        return build_user_titles_schema(self.max_count)

    def extract_with_regex(self, result_text: str) -> list[dict[str, Any]]:
        return extract_user_titles_with_regex(result_text, self.max_count)

    def validate(self, data_list: Any) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
        return validate_user_title_items(data_list)

    def create_data_objects(self, data_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
        titles: list[dict[str, Any]] = []
        for item in data_list[: self.max_count]:
            name = str(item.get("name") or "").strip()
            user_id = str(item.get("user_id") or "").strip()
            title = str(item.get("title") or "").strip()
            mbti = str(item.get("mbti") or "").strip()
            reason = str(item.get("reason") or "").strip()
            if not name or not user_id or not title or not mbti or not reason:
                continue
            titles.append({"name": name, "user_id": user_id, "title": title, "mbti": mbti, "reason": reason})
        return titles
