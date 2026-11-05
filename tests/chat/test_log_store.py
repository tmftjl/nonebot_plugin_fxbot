"""运行日志落库：级别过滤、查询与清空。"""

from __future__ import annotations

import time
import unittest
from uuid import uuid4
from typing import Any

from src.plugins.nonebot_plugin_fxbot.console import log_store


def _record(text: str, level: str = "INFO", ts_ms: int | None = None) -> dict[str, Any]:
    moment = ts_ms if ts_ms is not None else int(time.time() * 1000)
    return {
        "ts": "2026-11-05T10:00:00.000+08:00",
        "ts_ms": moment,
        "boot": "test",
        "level": level,
        "name": "test.log",
        "text": text,
    }


class LogStoreTests(unittest.TestCase):
    """落库走真实写线程，只断言公开的入队与查询行为。"""

    def setUp(self) -> None:
        log_store.clear()

    def test_query_filters_level_and_keyword(self) -> None:
        token = uuid4().hex
        log_store.enqueue(_record(f"info {token}", "INFO"))
        log_store.enqueue(_record(f"warn {token}", "WARNING"))
        log_store.enqueue(_record(f"error {token}", "ERROR"))
        log_store.flush()

        self.assertEqual(log_store.query(level="INFO", keyword=token)["total"], 3)
        self.assertEqual(log_store.query(level="WARNING", keyword=token)["total"], 2)
        errors = log_store.query(level="ERROR", keyword=token)
        self.assertEqual([item["level"] for item in errors["items"]], ["ERROR"])

    def test_debug_is_not_persisted(self) -> None:
        token = uuid4().hex
        log_store.enqueue(_record(f"debug {token}", "DEBUG"))
        log_store.enqueue(_record(f"info {token}", "INFO"))
        log_store.flush()

        self.assertEqual(log_store.query(level="INFO", keyword=token)["total"], 1)

    def test_time_range_and_pagination(self) -> None:
        token = uuid4().hex
        base = int(time.time() * 1000)
        for index in range(5):
            log_store.enqueue(_record(f"row {index} {token}", ts_ms=base + index))
        log_store.flush()

        ranged = log_store.query(level="INFO", keyword=token, since_ms=base + 1, until_ms=base + 3)
        self.assertEqual(ranged["total"], 3)

        page = log_store.query(level="INFO", keyword=token, page=1, page_size=2)
        self.assertEqual(page["total"], 5)
        self.assertEqual(len(page["items"]), 2)

    def test_clear_removes_rows(self) -> None:
        token = uuid4().hex
        log_store.enqueue(_record(token))
        log_store.flush()

        log_store.clear()

        self.assertEqual(log_store.query(level="INFO", keyword=token)["total"], 0)


if __name__ == "__main__":
    unittest.main()
