"""表情包数据模型。"""

from __future__ import annotations

from sqlmodel import Field, SQLModel, NaiveDatetime


class MemeGenerationRecord(SQLModel, table=True):
    """表情调用记录。"""

    __tablename__ = "meme_generation_records"

    id: int | None = Field(default=None, primary_key=True)
    session_persist_id: int = Field(nullable=False)
    # 记录器将 UTC 时间转换为无时区值后存储，显式声明以匹配该存储语义。
    time: NaiveDatetime = Field(nullable=False)
    meme_key: str = Field(max_length=64, nullable=False)
