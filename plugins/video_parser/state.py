"""视频解析运行状态。"""

from __future__ import annotations

import json
from pathlib import Path

from ...utils.paths import data_dir

STATE_PATH: Path = data_dir("video_parser") / "groups.json"


def _load() -> dict[str, list[str]]:
    """读取群解析开关状态。"""
    if not STATE_PATH.exists():
        return {"disabled_groups": []}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"disabled_groups": []}
    if not isinstance(data, dict):
        return {"disabled_groups": []}
    groups = data.get("disabled_groups")
    return (
        {"disabled_groups": [str(item) for item in groups]}
        if isinstance(groups, list)
        else {"disabled_groups": []}
    )


def _save(data: dict[str, list[str]]) -> None:
    """保存群解析开关状态。"""
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def is_group_enabled(group_id: str | None) -> bool:
    """判断本群解析是否启用。"""
    if not group_id:
        return True
    return str(group_id) not in set(_load()["disabled_groups"])


def set_group_enabled(group_id: str, enabled: bool) -> None:
    """设置本群解析开关。"""
    data = _load()
    groups = set(data["disabled_groups"])
    if enabled:
        groups.discard(str(group_id))
    else:
        groups.add(str(group_id))
    data["disabled_groups"] = sorted(groups)
    _save(data)
