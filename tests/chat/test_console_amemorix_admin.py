"""控制台长期记忆管理接口的租户边界与参数校验。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from src.plugins.nonebot_plugin_fxbot.console.routes import amemorix


class _Store:
    def __init__(self, *, owns_fact: bool = False, owns_paragraph: bool = False) -> None:
        self.owns_fact = owns_fact
        self.owns_paragraph = owns_paragraph
        self.occurrence: dict[str, str] | None = None

    def query(self, sql: str, params: tuple) -> list[dict[str, str]]:
        if sql.startswith("SELECT f.claim_id"):
            return [{"claim_id": "fact-1"}] if self.owns_fact else []
        if "SELECT p.hash" in sql and "FROM paragraphs p" in sql:
            return [{"hash": "para-1"}] if self.owns_paragraph else []
        if "SELECT DISTINCT pr.relation_hash" in sql:
            return []
        return []

    def get_image_occurrence(self, occurrence_id: str) -> dict[str, str] | None:
        return self.occurrence


class _Kernel:
    def __init__(self, store: _Store) -> None:
        self.metadata_store = store
        self.image_memory_runtime = type("_ImageRuntime", (), {"metadata_store": store})()
        self.data_dir = Path(tempfile.mkdtemp(prefix="fxbot-console-amx-"))
        self.restore_calls: list[dict] = []
        self.rollback_calls: list[dict] = []
        self.correction_calls: list[dict] = []
        self.correction_plan: dict = {
            "target_chat_id": "other",
            "preview": {"candidates": [{"target_type": "paragraph", "hash": "para-1"}]},
        }

    async def memory_delete_admin(self, *, action: str, **kwargs) -> dict:
        if action == "get_operation":
            return {
                "success": True,
                "operation": {
                    "operation_id": kwargs.get("operation_id"),
                    "status": "committed",
                    "items": [{"item_type": "paragraph", "item_hash": "para-1"}],
                },
            }
        if action == "restore":
            self.restore_calls.append(kwargs)
            return {"success": True}
        return {"success": True}

    async def memory_fact_admin(self, *, action: str, **kwargs) -> dict:
        self.restore_calls.append({"action": action, **kwargs})
        return {"success": True}

    async def memory_feedback_admin(self, *, action: str, **kwargs) -> dict:
        if action == "get":
            return {"success": True, "task": {"session_id": "other", "task_id": kwargs.get("task_id")}}
        if action == "rollback":
            self.rollback_calls.append(kwargs)
            return {"success": True}
        return {"success": True}

    async def memory_correction_admin(self, *, action: str, **kwargs) -> dict:
        if action == "get":
            return {"success": True, "plan": {**self.correction_plan, "plan_id": kwargs.get("plan_id")}}
        if action == "rollback":
            self.rollback_calls.append(kwargs)
            return {"success": True}
        if action in {"preview", "execute"}:
            self.correction_calls.append({"action": action, **kwargs})
            return {"success": True, "plan_id": "plan-1"}
        return {"success": True}


class ConsoleAmemorixAdminTests(unittest.IsolatedAsyncioTestCase):
    def _patched(self, kernel: _Kernel):
        return (
            patch.object(amemorix, "_kernel", AsyncMock(return_value=kernel)),
            patch.object(amemorix, "_persons", AsyncMock(return_value=[{"person_id": "person-1"}])),
        )

    async def test_restore_rejects_operation_from_another_tenant(self) -> None:
        kernel = _Kernel(_Store(owns_paragraph=False))
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.restore_deleted({"tenant": "tenant-a", "kind": "operation", "id": "op-1"})
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(kernel.restore_calls, [])

    async def test_restore_accepts_owned_operation(self) -> None:
        kernel = _Kernel(_Store(owns_paragraph=True))
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch:
            result = await amemorix.restore_deleted({"tenant": "tenant-a", "kind": "operation", "id": "op-1"})
        self.assertTrue(result["success"])
        self.assertEqual(kernel.restore_calls[0]["operation_id"], "op-1")

    async def test_restore_rejects_fact_from_another_tenant(self) -> None:
        kernel = _Kernel(_Store(owns_fact=False))
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.restore_deleted({"tenant": "tenant-a", "kind": "fact", "id": "fact-1"})
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(kernel.restore_calls, [])

    async def test_rollback_rejects_feedback_from_another_tenant(self) -> None:
        kernel = _Kernel(_Store())
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.rollback_correction({"tenant": "tenant-a", "kind": "feedback", "id": "12"})
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(kernel.rollback_calls, [])

    async def test_rollback_rejects_plan_from_another_tenant(self) -> None:
        kernel = _Kernel(_Store())
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.rollback_correction({"tenant": "tenant-a", "kind": "plan", "id": "plan-1"})
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(kernel.rollback_calls, [])

    async def test_upload_import_rejects_unsupported_suffix(self) -> None:
        kernel = _Kernel(_Store())
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.upload_import(
                {"tenant": "tenant-a", "file_name": "payload.exe", "content_base64": "aGVsbG8="}
            )
        self.assertEqual(caught.exception.status_code, 400)

    async def test_safe_file_name_keeps_only_the_basename(self) -> None:
        self.assertEqual(amemorix._safe_file_name("../../evil.txt", ".txt"), "evil.txt")
        self.assertEqual(amemorix._safe_file_name("a/b/c.md", ".md"), "c.md")

    async def test_delete_image_rejects_occurrence_from_another_tenant(self) -> None:
        store = _Store()
        store.occurrence = {"occurrence_id": "occ-1", "chat_id": "tenant-b", "asset_id": "asset-1"}
        kernel = _Kernel(store)
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.delete_image("occ-1", tenant="tenant-a")
        self.assertEqual(caught.exception.status_code, 404)

    async def test_correction_preview_rejects_target_from_another_tenant(self) -> None:
        kernel = _Kernel(_Store(owns_paragraph=False))
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.preview_correction(
                {
                    "tenant": "tenant-a",
                    "request_text": "改成别的说法",
                    "targets": [{"type": "paragraph", "id": "para-1"}],
                }
            )
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(kernel.correction_calls, [])

    async def test_correction_preview_passes_owned_targets(self) -> None:
        kernel = _Kernel(_Store(owns_paragraph=True))
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch:
            result = await amemorix.preview_correction(
                {
                    "tenant": "tenant-a",
                    "request_text": "改成别的说法",
                    "targets": [{"type": "paragraph", "id": "para-1"}],
                }
            )
        self.assertTrue(result["success"])
        call = kernel.correction_calls[0]
        self.assertEqual(call["action"], "preview")
        self.assertEqual(call["scope"], "memory")
        self.assertEqual(call["targets"], [{"type": "paragraph", "id": "para-1"}])

    async def test_correction_execute_rejects_plan_with_foreign_candidates(self) -> None:
        kernel = _Kernel(_Store(owns_paragraph=False))
        kernel.correction_plan = {
            "target_chat_id": "other",
            "preview": {"candidates": [{"target_type": "paragraph", "hash": "para-9"}]},
        }
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch, self.assertRaises(HTTPException) as caught:
            await amemorix.execute_correction({"tenant": "tenant-a", "plan_id": "plan-1"})
        self.assertEqual(caught.exception.status_code, 404)
        self.assertEqual(kernel.correction_calls, [])

    async def test_correction_execute_accepts_owned_candidates(self) -> None:
        kernel = _Kernel(_Store(owns_paragraph=True))
        kernel_patch, persons_patch = self._patched(kernel)
        with kernel_patch, persons_patch:
            result = await amemorix.execute_correction({"tenant": "tenant-a", "plan_id": "plan-1"})
        self.assertTrue(result["success"])
        call = kernel.correction_calls[0]
        self.assertEqual(call["action"], "execute")
        self.assertTrue(call["confirmed"])
