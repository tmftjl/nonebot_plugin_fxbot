"""按逻辑轮次汇总规划、回复生成及交付阶段。"""

from __future__ import annotations

from copy import deepcopy
from time import perf_counter
from typing import Any
from dataclasses import field, dataclass

from .types import ChatRequest


@dataclass
class TurnTrace:
    """共享给本轮子回复的阶段记录，持久化只由所属轮次负责。"""

    turn_id: str
    started: float = field(default_factory=perf_counter)
    stages: list[dict[str, Any]] = field(default_factory=list)
    saved: bool = False

    @property
    def elapsed_ms(self) -> int:
        """返回本轮实际经过时间，子阶段耗时不重复累加。"""
        return max(0, int((perf_counter() - self.started) * 1000))

    def add_stage(self, kind: str, label: str, duration_ms: int, *, provider: str = "", **details: Any) -> None:
        """添加实际执行阶段，保留执行顺序与每次回复明细。"""
        self.stages.append(
            {
                "kind": kind,
                "label": label,
                "duration_ms": max(0, duration_ms),
                "provider": provider,
                **details,
            }
        )

    def snapshot(self) -> dict[str, Any]:
        """生成阶段快照与交付汇总，总耗时按经过时间统计。"""
        timings = {
            f"{kind}_ms": sum(stage["duration_ms"] for stage in self.stages if stage["kind"] == kind)
            for kind in ("planner", "replyer", "send")
        }
        timings["total_ms"] = self.elapsed_ms
        timings["other_ms"] = max(
            0, timings["total_ms"] - sum(timings[f"{kind}_ms"] for kind in ("planner", "replyer", "send"))
        )
        result: dict[str, Any] = {"stages": deepcopy(self.stages), "timings": timings}
        deliveries = [stage["send"] for stage in self.stages if stage["kind"] == "send"]
        if deliveries:
            failed_index = None
            offset = 0
            for delivery in deliveries:
                if failed_index is None and delivery.get("failed_index") is not None:
                    failed_index = offset + delivery["failed_index"]
                offset += delivery["total_parts"]
            result["send"] = {
                "sent_parts": sum(item["sent_parts"] for item in deliveries),
                "total_parts": offset,
                "failed_index": failed_index,
                "cancelled": any(item["cancelled"] for item in deliveries),
                "send_ms": timings["send_ms"],
            }
        return result


def turn_trace(request: ChatRequest) -> TurnTrace | None:
    """读取当前请求所属的逻辑轮次追踪。"""
    trace = request.metadata.get("_turn_trace")
    return trace if isinstance(trace, TurnTrace) else None
