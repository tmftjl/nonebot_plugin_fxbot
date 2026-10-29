"""系统运行状态路由。"""

from __future__ import annotations

import time
import platform
from typing import Any

from fastapi import Depends, APIRouter

from ..auth import bearer_auth
from ...system_control.status_monitor import get_monitor

router = APIRouter(prefix="/system", tags=["fxbot-system"], dependencies=[Depends(bearer_auth)])


def _percent(value: float | None) -> float | None:
    return round(float(value), 1) if value is not None else None


@router.get("/status")
async def get_system_status() -> dict[str, Any]:
    """返回本机 CPU、内存、磁盘、网络和当前进程状态。

    监控服务每 2 秒采样一次，这里优先读采样数据；服务未启动或 psutil 缺失时
    退化为一次性读取，前端按 available=false 显示不可用。
    """
    try:
        import psutil
    except ImportError:
        return {"available": False}

    monitor = get_monitor()
    data = monitor.get_current_data()

    cpu_percent = data.cpu_history[-1][1] if data and data.cpu_history else psutil.cpu_percent(interval=None)
    cpu_history = [round(value, 1) for _, value in (data.cpu_history if data else [])][-40:]
    memory_history = [round(value, 1) for _, value in (data.memory_history if data else [])][-40:]

    memory = psutil.virtual_memory()
    swap = psutil.swap_memory()

    disks: list[dict[str, Any]] = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            # Windows 上光驱、未挂载盘符会拒绝访问，跳过即可。
            continue
        if not usage.total:
            continue
        disks.append(
            {
                "mount": part.mountpoint,
                "percent": _percent(usage.percent),
                "used": int(usage.used),
                "total": int(usage.total),
            }
        )
    disks.sort(key=lambda item: item["total"], reverse=True)

    try:
        process = psutil.Process()
        process_info = {
            "rss": int(process.memory_info().rss),
            "threads": int(process.num_threads()),
            "uptime": round(time.time() - process.create_time(), 1),
        }
    except (psutil.Error, OSError):
        process_info = {"rss": 0, "threads": 0, "uptime": 0}

    try:
        freq = psutil.cpu_freq()
    except Exception:
        freq = None
    try:
        net_io = psutil.net_io_counters()
        total_upload, total_download = int(net_io.bytes_sent), int(net_io.bytes_recv)
    except Exception:
        total_upload, total_download = 0, 0
    physical = psutil.cpu_count(logical=False)

    return {
        "available": True,
        "cpu": {
            "percent": _percent(cpu_percent),
            "count": psutil.cpu_count(logical=True) or 0,
            "physical": physical or 0,
            "freq_mhz": round(freq.max or freq.current, 0) if freq else None,
            "history": cpu_history,
        },
        "memory": {
            "percent": _percent(memory.percent),
            "used": int(memory.used),
            "total": int(memory.total),
            "history": memory_history,
        },
        "swap": {
            "percent": _percent(swap.percent),
            "used": int(swap.used),
            "total": int(swap.total),
        },
        "disks": disks,
        "network": {
            "upload": round(data.upload_speed if data else 0.0, 1),
            "download": round(data.download_speed if data else 0.0, 1),
            "total_upload": total_upload,
            "total_download": total_download,
        },
        "process": process_info,
        "system": {
            "platform": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "uptime": round(time.time() - psutil.boot_time(), 1),
        },
    }
