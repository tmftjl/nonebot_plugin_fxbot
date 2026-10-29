"""Focus 共享时间线与检查点。

每个条目是一个入站消息或一个完整的模型—工具批次，压缩时整段保留或整段丢弃。
"""

from __future__ import annotations

import os
import json
from uuid import uuid4
from typing import Any
from hashlib import sha256
from pathlib import Path
from dataclasses import field, dataclass

from nonebot import logger

from ...utils.paths import data_dir

CHECKPOINT_VERSION = 1


@dataclass
class FocusEntry:
    """共享组时间线上的一段原子内容。"""

    messages: list[dict[str, Any]]
    session_ids: set[str]
    sequence: int = 0
    inputs: dict[str, list[list[Any]]] = field(default_factory=dict)
    is_at: bool = False
    # 每个会话对应的平台消息 ID；撤回时据此整条丢弃，不随 inputs 被剪掉
    sources: dict[str, list[str]] = field(default_factory=dict)

    @property
    def progress(self) -> dict[str, int]:
        """这一条对应的各会话输入进度。"""
        return dict.fromkeys(self.session_ids, self.sequence) if self.sequence else {}

    def prune_inputs(self, considered: dict[str, int]) -> None:
        """丢弃已被确认的输入编号。"""
        self.inputs = {
            sid: [record for record in records if int(record[0]) > considered.get(sid, 0)]
            for sid, records in self.inputs.items()
        }


class FocusCheckpointStore:
    """按共享组保存时间线；写入临时文件后原子替换。"""

    def __init__(self, scope_id: str, root: Path | None = None) -> None:
        self.scope_id = scope_id
        self.root = root or data_dir("ai_chat") / "focus"
        self.path = self.root / f"{sha256(scope_id.encode('utf-8')).hexdigest()[:32]}.json"

    def load(self) -> dict[str, Any]:
        """读取检查点；缺失或损坏时返回空状态。"""
        if not self.path.is_file():
            return {}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if payload.get("version") != CHECKPOINT_VERSION or payload.get("scope_id") != self.scope_id:
                raise ValueError("检查点版本或共享组身份不匹配")
            return payload
        except Exception as exc:
            logger.warning(f"[ChatFocus] 检查点读取失败，按空上下文启动: {self.scope_id} ({type(exc).__name__}: {exc})")
            return {}

    def save(self, entries: list[FocusEntry], state: dict[str, Any]) -> None:
        """保存时间线与等待状态；entries 必须已按顺序排列。"""
        payload = {
            "version": CHECKPOINT_VERSION,
            "scope_id": self.scope_id,
            **state,
            "entries": [
                {
                    "messages": entry.messages,
                    "session_ids": sorted(entry.session_ids),
                    "sequence": entry.sequence,
                    "inputs": entry.inputs,
                    "sources": entry.sources,
                }
                for entry in entries
            ],
        }
        self._save_payload(payload)

    def _save_payload(self, payload: dict[str, Any]) -> None:
        """通过原子替换保存完整检查点。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)

    @classmethod
    def purge_unloaded_session(cls, session_id: str, loaded_scopes: set[str], root: Path | None = None) -> None:
        """清理未加载组的会话痕迹，不依赖当前 Focus 分组配置。"""
        root = root or data_dir("ai_chat") / "focus"
        for path in root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                scope_id = payload.get("scope_id")
                if payload.get("version") != CHECKPOINT_VERSION or not isinstance(scope_id, str):
                    raise ValueError("检查点版本或共享组身份不匹配")
                store = cls(scope_id, root)
                if store.path != path:
                    raise ValueError("检查点路径与共享组身份不匹配")
                if scope_id in loaded_scopes:
                    continue
                entries = payload.get("entries") or []
                kept = [
                    entry
                    for entry in entries
                    if not any(session_id in (entry.get(key) or []) for key in ("session_ids", "inputs", "sources"))
                ]
                considered = dict(payload.get("considered") or {})
                considered.pop(session_id, None)
                seen = [key for key in (payload.get("seen") or []) if not key.startswith(session_id + "\0")]
                if (
                    kept == entries
                    and considered == (payload.get("considered") or {})
                    and seen == (payload.get("seen") or [])
                    and payload.get("wait_session_id") != session_id
                ):
                    continue
                updated = {**payload, "entries": kept, "considered": considered, "seen": seen}
                if payload.get("wait_session_id") == session_id:
                    updated.update(wait_until=None, wait_session_id=None)
            except Exception as exc:
                logger.warning(f"[ChatFocus] 检查点清理跳过: {path.name} ({type(exc).__name__}: {exc})")
                continue
            store._save_payload(updated)


def restore_entries(payload: dict[str, Any]) -> list[FocusEntry]:
    """把检查点里的条目还原成对象；坏条目整条跳过。"""
    entries: list[FocusEntry] = []
    for item in payload.get("entries") or []:
        try:
            entries.append(
                FocusEntry(
                    [message for message in item["messages"] if isinstance(message, dict)],
                    set(item.get("session_ids") or []),
                    int(item.get("sequence") or 0),
                    {
                        str(sid): [list(record) for record in records]
                        for sid, records in (item.get("inputs") or {}).items()
                    },
                    sources={
                        str(sid): [str(key) for key in keys]
                        for sid, keys in (item.get("sources") or {}).items()
                        if isinstance(keys, list)
                    },
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return entries
