# -*- coding: utf-8 -*-
"""系统状态后台监控服务

功能：
1. 定期采集网络速度（上传/下载）
2. 定期采集磁盘IO速度（读/写）
3. 记录历史数据用于绘制趋势图
4. 计算瞬时速度（通过差值）
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Deque, Optional

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore

from nonebot import get_driver, logger


@dataclass
class NetworkSnapshot:
    """网络快照"""

    timestamp: float
    bytes_sent: int
    bytes_recv: int


@dataclass
class DiskIOSnapshot:
    """磁盘IO快照"""

    timestamp: float
    read_bytes: int
    write_bytes: int


@dataclass
class MonitorData:
    """监控数据"""

    # 当前网速（字节/秒）
    upload_speed: float
    download_speed: float

    # 累计流量
    total_upload: int
    total_download: int

    # 磁盘IO速度（字节/秒）
    disk_read_speed: float
    disk_write_speed: float

    # 历史数据用于绘制图表（最近60个采样点）
    network_history: list[tuple[float, float, float]]  # [(timestamp, upload, download), ...]
    disk_io_history: list[tuple[float, float, float]]  # [(timestamp, read, write), ...]
    cpu_history: list[tuple[float, float]]  # [(timestamp, usage), ...]
    memory_history: list[tuple[float, float]]  # [(timestamp, percent), ...]


class StatusMonitor:
    """系统状态监控服务（单例）"""

    def __init__(self):
        self._running = False
        self._task: Optional[asyncio.Task] = None

        # 采样间隔（秒）
        self.interval = 2.0

        # 历史数据最大保留数量
        self.max_history = 60

        # 上一次快照
        self._last_network_snapshot: Optional[NetworkSnapshot] = None
        self._last_disk_snapshot: Optional[DiskIOSnapshot] = None

        # 当前监控数据
        self._current_data: Optional[MonitorData] = None

        # 历史数据队列
        self._network_history: Deque[tuple[float, float, float]] = deque(maxlen=self.max_history)
        self._disk_io_history: Deque[tuple[float, float, float]] = deque(maxlen=self.max_history)
        self._cpu_history: Deque[tuple[float, float]] = deque(maxlen=self.max_history)
        self._memory_history: Deque[tuple[float, float]] = deque(maxlen=self.max_history)

        # 进程监控配置
        self.top_process_count = 30  # 监控TOP30进程
        self.process_refresh_interval = 10  # 每10次采样刷新一次监控列表
        self._process_cache: dict[int, Any] = {}  # 缓存进程对象用于 CPU 采样
        self._sample_counter = 0  # 采样计数器
        self._process_data: dict[str, Any] = {
            "top_cpu": [],
            "top_mem": [],
            "status_counts": {},
            "total_count": 0,
        }

    async def start(self):
        """启动监控服务"""
        if self._running:
            logger.warning("[status_monitor] 监控服务已在运行")
            return

        if psutil is None:
            logger.warning("[status_monitor] psutil 未安装，监控服务无法启动")
            return

        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("[status_monitor] 监控服务已启动")

    async def stop(self):
        """停止监控服务"""
        if not self._running:
            return

        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("[status_monitor] 监控服务已停止")

    async def _monitor_loop(self):
        """监控循环"""
        try:
            # 首次采样（建立基线）
            await asyncio.to_thread(self._take_initial_snapshot)

            while self._running:
                await asyncio.sleep(self.interval)

                # 在线程中执行采样
                try:
                    await asyncio.to_thread(self._collect_data)

                except Exception as e:
                    logger.exception(f"[status_monitor] 数据采集失败: {e}")

        except asyncio.CancelledError:
            logger.debug("[status_monitor] 监控循环被取消")
            raise
        except Exception as e:
            logger.exception(f"[status_monitor] 监控循环异常退出: {e}")

    def _take_initial_snapshot(self):
        """建立初始快照（同步方法，在线程中运行）"""
        if psutil is None:
            return

        now = time.monotonic()  # 使用 monotonic 避免时间校准影响

        # CPU预热（建立baseline）
        psutil.cpu_percent(interval=None)

        # 网络快照
        net_io = psutil.net_io_counters()
        self._last_network_snapshot = NetworkSnapshot(
            timestamp=now,
            bytes_sent=net_io.bytes_sent,
            bytes_recv=net_io.bytes_recv,
        )

        # 磁盘IO快照
        disk_io = psutil.disk_io_counters()
        if disk_io:
            self._last_disk_snapshot = DiskIOSnapshot(
                timestamp=now,
                read_bytes=disk_io.read_bytes,
                write_bytes=disk_io.write_bytes,
            )

        # 进程初始化：全量扫描并选出TOP进程
        self._refresh_process_cache()

    def _collect_data(self):
        """收集监控数据（同步方法，在线程中运行）"""
        if psutil is None:
            return

        now = time.monotonic()  # 使用 monotonic 避免时间校准影响
        now_real = time.time()  # 真实时间用于记录

        # === 网络数据 ===
        net_io = psutil.net_io_counters()
        upload_speed = 0.0
        download_speed = 0.0

        if self._last_network_snapshot:
            time_delta = now - self._last_network_snapshot.timestamp
            if time_delta > 0:
                # 计算差值，避免计数器回退
                sent_diff = max(net_io.bytes_sent - self._last_network_snapshot.bytes_sent, 0)
                recv_diff = max(net_io.bytes_recv - self._last_network_snapshot.bytes_recv, 0)

                upload_speed = sent_diff / time_delta
                download_speed = recv_diff / time_delta

        # 更新快照
        self._last_network_snapshot = NetworkSnapshot(
            timestamp=now,
            bytes_sent=net_io.bytes_sent,
            bytes_recv=net_io.bytes_recv,
        )

        # === 磁盘IO数据 ===
        disk_read_speed = 0.0
        disk_write_speed = 0.0

        disk_io = psutil.disk_io_counters()
        if disk_io:
            if self._last_disk_snapshot:
                time_delta = now - self._last_disk_snapshot.timestamp
                if time_delta > 0:
                    # 计算差值，避免计数器回退
                    read_diff = max(disk_io.read_bytes - self._last_disk_snapshot.read_bytes, 0)
                    write_diff = max(disk_io.write_bytes - self._last_disk_snapshot.write_bytes, 0)

                    disk_read_speed = read_diff / time_delta
                    disk_write_speed = write_diff / time_delta

            # 更新快照
            self._last_disk_snapshot = DiskIOSnapshot(
                timestamp=now,
                read_bytes=disk_io.read_bytes,
                write_bytes=disk_io.write_bytes,
            )

        # === CPU和内存 ===
        cpu_percent = psutil.cpu_percent(interval=0)
        memory_percent = psutil.virtual_memory().percent

        # === 更新历史数据（使用真实时间戳用于展示） ===
        self._network_history.append((now_real, upload_speed, download_speed))
        if disk_io:
            self._disk_io_history.append((now_real, disk_read_speed, disk_write_speed))
        self._cpu_history.append((now_real, cpu_percent))
        self._memory_history.append((now_real, memory_percent))

        # === 进程数据采集 ===
        self._collect_process_data()

        # === 构建当前数据 ===
        self._current_data = MonitorData(
            upload_speed=upload_speed,
            download_speed=download_speed,
            total_upload=net_io.bytes_sent,
            total_download=net_io.bytes_recv,
            disk_read_speed=disk_read_speed,
            disk_write_speed=disk_write_speed,
            network_history=list(self._network_history),
            disk_io_history=list(self._disk_io_history),
            cpu_history=list(self._cpu_history),
            memory_history=list(self._memory_history),
        )

    def _refresh_process_cache(self):
        """全量刷新进程缓存（同步方法）"""
        if psutil is None:
            return

        try:
            # 收集所有进程的基本信息
            all_processes = []
            for proc in psutil.process_iter(["pid", "name", "status", "memory_info"]):
                try:
                    pinfo = proc.info
                    # 第一次调用 cpu_percent() 建立baseline
                    proc.cpu_percent(interval=None)

                    mem_bytes = pinfo["memory_info"].rss if pinfo["memory_info"] else 0
                    all_processes.append(
                        {
                            "proc": proc,
                            "pid": pinfo["pid"],
                            "mem_bytes": mem_bytes,
                        }
                    )
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

            # 按内存排序，选出TOP进程
            all_processes.sort(key=lambda x: x["mem_bytes"], reverse=True)
            top_processes = all_processes[: self.top_process_count]

            # 更新缓存
            self._process_cache.clear()
            for p in top_processes:
                self._process_cache[p["pid"]] = p["proc"]

            logger.debug(f"[status_monitor] 刷新进程缓存: {len(self._process_cache)} 个进程")
        except Exception as e:
            logger.exception(f"[status_monitor] 刷新进程缓存失败: {e}")

    def _collect_process_data(self):
        """采集进程数据并写入cache（同步方法）"""
        if psutil is None:
            return

        try:
            # 增加采样计数
            self._sample_counter += 1

            # 每N次采样，全量刷新进程列表
            if self._sample_counter % self.process_refresh_interval == 0:
                self._refresh_process_cache()
                return  # 刷新时跳过本次数据采集（CPU数据还未准备好）

            # 收集缓存中进程的数据
            all_processes = []
            status_counts = {}
            pids_to_remove = []

            for pid, proc in self._process_cache.items():
                try:
                    # 获取进程信息
                    pinfo = proc.as_dict(attrs=["name", "status", "memory_info"])
                    cpu_percent = proc.cpu_percent(interval=None)
                    mem_bytes = pinfo["memory_info"].rss if pinfo["memory_info"] else 0

                    all_processes.append(
                        {
                            "name": pinfo["name"],
                            "cpu_percent": cpu_percent,
                            "mem_bytes": mem_bytes,
                            "mem_str": self._format_bytes(mem_bytes),
                        }
                    )

                    # 统计状态
                    status = pinfo["status"]
                    status_counts[status] = status_counts.get(status, 0) + 1

                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    # 进程已退出，标记待删除
                    pids_to_remove.append(pid)

            # 清理已退出的进程
            for pid in pids_to_remove:
                self._process_cache.pop(pid, None)

            # 排序获取TOP5
            top_cpu = sorted(all_processes, key=lambda x: x["cpu_percent"], reverse=True)[:5]
            top_mem = sorted(all_processes, key=lambda x: x["mem_bytes"], reverse=True)[:5]

            # 状态中文映射
            status_map = {
                "running": "运行中",
                "sleeping": "休眠",
                "disk-sleep": "磁盘休眠",
                "stopped": "已停止",
                "zombie": "僵尸进程",
                "dead": "已终止",
                "idle": "空闲",
                "locked": "已锁定",
                "waiting": "等待中",
                "suspended": "已暂停",
            }
            status_counts_cn = {status_map.get(k, k): v for k, v in status_counts.items()}

            # 获取总进程数
            total_count = len(list(psutil.process_iter()))

            self._process_data = {
                "top_cpu": top_cpu,
                "top_mem": top_mem,
                "status_counts": status_counts_cn,
                "total_count": total_count,
            }

        except Exception as e:
            logger.exception(f"[status_monitor] 采集进程数据失败: {e}")

    @staticmethod
    def _format_bytes(bytes_value: float) -> str:
        """格式化字节数"""
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if bytes_value < 1024.0:
                return f"{bytes_value:.2f} {unit}"
            bytes_value /= 1024.0
        return f"{bytes_value:.2f} PB"

    def get_current_data(self) -> Optional[MonitorData]:
        """获取当前监控数据"""
        return self._current_data

    def get_process_data(self) -> dict[str, Any]:
        """获取最近一次进程采样数据。"""
        return {
            "top_cpu": list(self._process_data.get("top_cpu", [])),
            "top_mem": list(self._process_data.get("top_mem", [])),
            "status_counts": dict(self._process_data.get("status_counts", {})),
            "total_count": int(self._process_data.get("total_count", 0) or 0),
        }

    @property
    def is_running(self) -> bool:
        """监控服务是否运行中"""
        return self._running


# 全局单例
_monitor = StatusMonitor()


def get_monitor() -> StatusMonitor:
    """获取监控服务单例"""
    return _monitor


# 在 NoneBot 启动/关闭时自动启动/停止监控
_driver = get_driver()


@_driver.on_startup
async def _start_monitor():
    """NoneBot 启动时启动监控"""
    try:
        await _monitor.start()
    except Exception as e:
        logger.exception(f"[status_monitor] 启动监控服务失败: {e}")


@_driver.on_shutdown
async def _stop_monitor():
    """NoneBot 关闭时停止监控"""
    try:
        await _monitor.stop()
    except Exception as e:
        logger.exception(f"[status_monitor] 停止监控服务失败: {e}")


def _start_if_loaded_during_startup() -> None:
    """确保内置插件在 FxBot 启动流程中被加载时立即启动。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    if loop.is_running():
        loop.create_task(_monitor.start())


_start_if_loaded_during_startup()
