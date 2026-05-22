"""表情包数据模型。"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import Field, SQLModel


class MemeGenerationRecord(SQLModel, table=True):
    """表情调用记录。"""

    __tablename__ = "nonebot_plugin_memes_api_memegenerationrecord_v2"

    id: int | None = Field(default=None, primary_key=True)
    session_persist_id: int = Field(nullable=False)
    time: datetime = Field(nullable=False)
    meme_key: str = Field(max_length=64, nullable=False)
