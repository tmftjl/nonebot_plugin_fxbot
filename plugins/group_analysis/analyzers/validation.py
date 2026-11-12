"""模型输出的二次校验，规则与原版一致。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator


def _is_list(value: object) -> bool:
    return isinstance(value, list)


class TopicItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str
    contributors: list[str]
    detail: str

    @field_validator("topic", "detail", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return str(value).strip()

    @field_validator("contributors", mode="before")
    @classmethod
    def _normalize_contributors(cls, value: object) -> list[str]:
        if not _is_list(value):
            return []
        return [str(item).strip() for item in value if str(item).strip()]


class UserTitleItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    user_id: str
    title: str
    mbti: str
    reason: str

    @field_validator("name", "user_id", "title", "mbti", "reason", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return str(value).strip()


class GoldenQuoteItemModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str
    sender: str
    reason: str

    @field_validator("content", "sender", "reason", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return str(value).strip()


class QualityDimensionModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    percentage: float
    comment: str

    @field_validator("name", "comment", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return str(value).strip()


class QualityReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    subtitle: str
    dimensions: list[QualityDimensionModel]
    summary: str

    @field_validator("title", "subtitle", "summary", mode="before")
    @classmethod
    def _normalize_text(cls, value: object) -> str:
        return str(value).strip()


def _validate(model: type[BaseModel], items: Any) -> tuple[bool, Any, str | None]:
    try:
        if not isinstance(items, list):
            return False, None, "响应不是数组"
        return True, [model.model_validate(item).model_dump() for item in items], None
    except ValidationError as exc:
        return False, None, str(exc)


def validate_topic_items(data_list: Any) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    return _validate(TopicItemModel, data_list)


def validate_user_title_items(data_list: Any) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    return _validate(UserTitleItemModel, data_list)


def validate_golden_quote_items(data_list: Any) -> tuple[bool, list[dict[str, Any]] | None, str | None]:
    return _validate(GoldenQuoteItemModel, data_list)


def validate_quality_review_item(data: Any) -> tuple[bool, dict[str, Any] | None, str | None]:
    try:
        if not isinstance(data, dict):
            return False, None, "响应不是对象"
        return True, QualityReviewModel.model_validate(data).model_dump(), None
    except ValidationError as exc:
        return False, None, str(exc)
