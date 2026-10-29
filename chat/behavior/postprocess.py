"""回复后处理：去掉模型带出的发言人前缀、答语、Markdown 围栏和结束语。"""

from __future__ import annotations

import re

_SPEAKER = re.compile(r"^\s*(?:[^\n:：]{1,32}[（(]\d{5,}[)）]|用户\d{5,})\s*[:：]\s*")
_ANSWER = re.compile(r"^\s*(?:答|回复)\s*[:：]\s*")
_FENCE = re.compile(r"^[ \t]*```[^\n]*\n?", re.M)
_BLANK = re.compile(r"\n{3,}")
_TAIL = re.compile(r"\s*[（(]\s*以上\s*[)）]\s*$")


def postprocess_reply(text: str) -> str:
    """清理模型原始输出；空结果由调用方决定不发送。"""
    value = str(text or "").strip()
    value = _SPEAKER.sub("", value)
    value = _ANSWER.sub("", value)
    value = _FENCE.sub("", value)
    value = _BLANK.sub("\n\n", value)
    value = _TAIL.sub("", value)
    return value.strip()
