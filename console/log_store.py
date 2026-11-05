"""运行日志落库：独立 SQLite、有界队列、后台批量写入。

日志入口在 loguru 处理器线程里，不能碰 ORM、事件循环或 logger 本身；
写库失败只落 stderr，避免递归回日志系统。
"""

from __future__ import annotations

import sys
import time
import queue
import atexit
import sqlite3
import threading
import traceback
from typing import Any
from pathlib import Path

from ..utils.tz import now as shanghai_now
from ..utils.paths import data_dir

_QUEUE_SIZE = 10000
_BATCH_SIZE = 200
_FLUSH_SECONDS = 1.0
_RETENTION_DAYS = 7
_PRUNE_SECONDS = 3600
_MIN_LEVEL = "INFO"
_LEVEL_RANK = {
    "TRACE": 5,
    "DEBUG": 10,
    "INFO": 20,
    "SUCCESS": 22,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50,
}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    ts_ms INTEGER NOT NULL,
    boot TEXT NOT NULL,
    level TEXT NOT NULL,
    name TEXT NOT NULL,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_log_ts_ms ON log(ts_ms);
CREATE INDEX IF NOT EXISTS idx_log_level_ts ON log(level, ts_ms);
"""

_queue: queue.Queue[dict[str, Any] | None] = queue.Queue(maxsize=_QUEUE_SIZE)
_drop_lock = threading.Lock()
_start_lock = threading.Lock()
_writer: _Writer | None = None
_dropped = 0
_stopped = False


def log_db_path() -> Path:
    """日志库路径：和主库同目录但独立成文件，避免和聊天写入抢锁。"""
    return data_dir("db") / "logs.db"


def _connect() -> sqlite3.Connection:
    """打开日志库并确保表结构存在。"""
    conn = sqlite3.connect(log_db_path(), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def _take_dropped() -> int:
    global _dropped
    with _drop_lock:
        count, _dropped = _dropped, 0
    return count


def _drop_oldest(item: dict[str, Any]) -> None:
    """队列满时丢最旧的一条保住最新可见，并累计丢弃数。"""
    global _dropped
    lost = 0
    try:
        _queue.get_nowait()
        _queue.task_done()
        lost += 1
    except queue.Empty:
        pass
    try:
        _queue.put_nowait(item)
    except queue.Full:
        lost += 1
    if lost:
        with _drop_lock:
            _dropped += lost


class _Writer(threading.Thread):
    """独立写线程：自带连接，攒批写入，全部异常只落 stderr。"""

    def __init__(self) -> None:
        super().__init__(name="fxbot-log-db", daemon=True)
        self._conn: sqlite3.Connection | None = None
        self._last_prune = 0.0

    def run(self) -> None:
        stop = False
        while not stop:
            item = _queue.get()
            if item is None:
                _queue.task_done()
                break
            batch = [item]
            deadline = time.monotonic() + _FLUSH_SECONDS
            while len(batch) < _BATCH_SIZE:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break
                try:
                    nxt = _queue.get(timeout=timeout)
                except queue.Empty:
                    break
                if nxt is None:
                    stop = True
                    _queue.task_done()
                    break
                batch.append(nxt)
            try:
                self._write(batch)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                self._close()
            finally:
                for _ in batch:
                    _queue.task_done()

    def _ensure_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = _connect()
        return self._conn

    def _close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass
            self._conn = None

    def _write(self, batch: list[dict[str, Any]]) -> None:
        conn = self._ensure_conn()
        rows = [
            (
                str(item.get("ts") or ""),
                int(item.get("ts_ms") or 0),
                str(item.get("boot") or ""),
                str(item.get("level") or ""),
                str(item.get("name") or ""),
                str(item.get("text") or ""),
            )
            for item in batch
        ]
        dropped = _take_dropped()
        if dropped:
            moment = shanghai_now()
            rows.append(
                (
                    moment.isoformat(timespec="milliseconds"),
                    int(moment.timestamp() * 1000),
                    "",
                    "WARNING",
                    "fxbot.log_store",
                    f"日志落库队列已满，丢弃 {dropped} 条",
                )
            )
        conn.executemany("INSERT INTO log (ts, ts_ms, boot, level, name, text) VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.commit()
        self._prune(conn)

    def _prune(self, conn: sqlite3.Connection) -> None:
        moment = time.monotonic()
        if moment - self._last_prune < _PRUNE_SECONDS:
            return
        self._last_prune = moment
        cutoff = int((time.time() - _RETENTION_DAYS * 86400) * 1000)
        conn.execute("DELETE FROM log WHERE ts_ms < ?", (cutoff,))
        conn.commit()


def _ensure_writer() -> None:
    global _writer
    if _writer is not None or _stopped:
        return
    with _start_lock:
        if _writer is not None or _stopped:
            return
        writer = _Writer()
        writer.start()
        _writer = writer
        atexit.register(shutdown)


def enqueue(item: dict[str, Any]) -> None:
    """日志处理器调用入口：只入队，绝不阻塞、绝不抛异常。"""
    if _LEVEL_RANK.get(str(item.get("level") or "").upper(), 0) < _LEVEL_RANK[_MIN_LEVEL]:
        return
    try:
        _ensure_writer()
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return
    try:
        _queue.put_nowait(item)
    except queue.Full:
        _drop_oldest(item)


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def query(
    *,
    level: str = _MIN_LEVEL,
    keyword: str = "",
    since_ms: int | None = None,
    until_ms: int | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """按级别阈值、关键字和时间范围分页查询历史日志。"""
    page = max(1, int(page))
    page_size = min(200, max(1, int(page_size)))
    rank = _LEVEL_RANK.get(str(level or "").upper(), _LEVEL_RANK[_MIN_LEVEL])
    allowed = [name for name, value in _LEVEL_RANK.items() if value >= rank]
    conditions = [f"level IN ({', '.join('?' for _ in allowed)})"]
    params: list[Any] = list(allowed)
    text = str(keyword or "").strip()
    if text:
        like = f"%{_escape_like(text)}%"
        conditions.append(r"(text LIKE ? ESCAPE '\' OR name LIKE ? ESCAPE '\')")
        params.extend([like, like])
    if since_ms is not None:
        conditions.append("ts_ms >= ?")
        params.append(int(since_ms))
    if until_ms is not None:
        conditions.append("ts_ms <= ?")
        params.append(int(until_ms))
    where = f"WHERE {' AND '.join(conditions)}"
    empty = {"items": [], "total": 0, "page": page, "page_size": page_size}
    if not log_db_path().exists():
        return empty
    try:
        conn = _connect()
        try:
            total = int(conn.execute(f"SELECT COUNT(*) FROM log {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT id, ts, ts_ms, boot, level, name, text FROM log {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, page_size, (page - 1) * page_size],
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        traceback.print_exc(file=sys.stderr)
        return empty
    columns = ("id", "ts", "ts_ms", "boot", "level", "name", "text")
    items = [dict(zip(columns, row)) for row in rows]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


def clear() -> None:
    """清空历史日志。"""
    if not log_db_path().exists():
        return
    conn = _connect()
    try:
        conn.execute("DELETE FROM log")
        conn.commit()
    finally:
        conn.close()


def flush() -> None:
    """等待已入队记录写完，测试和退出时使用。"""
    if _writer is not None:
        _queue.join()


def shutdown() -> None:
    """排空队列后停止写线程。"""
    global _writer, _stopped
    writer = _writer
    _stopped = True
    if writer is None:
        return
    try:
        _queue.put_nowait(None)
    except queue.Full:
        try:
            _queue.get_nowait()
            _queue.task_done()
        except queue.Empty:
            pass
        try:
            _queue.put_nowait(None)
        except queue.Full:
            return
    writer.join(timeout=5.0)
    _writer = None
