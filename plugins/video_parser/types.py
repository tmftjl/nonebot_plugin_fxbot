"""视频解析数据结构。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class VideoResult:
    """单条视频解析结果。"""

    platform: str
    title: str
    video_url: str | None = None
    cover_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    duration: float | None = None
    source_url: str | None = None
    text: str | None = None
    audio_url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        """返回可展示标题。"""
        title = self.title.strip() if self.title else ""
        return title or f"{self.platform}内容"
