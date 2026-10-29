"""表达向量：索引维护、余弦召回、模型筛选与配置。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.memory import (
    config as memory_config,
    expression as expression_module,
    expression_vector as vector_module,
)
from src.plugins.nonebot_plugin_fxbot.chat.providers import LLMResponse
from src.plugins.nonebot_plugin_fxbot.chat.memory.models import ChatExpression
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    validate_system_config,
)

TENANT = "test:expression-vector"


def _row(row_id: int, situation: str, style: str) -> ChatExpression:
    return ChatExpression(
        id=row_id,
        tenant=TENANT,
        situation=situation,
        situation_hash=str(row_id),
        style=style,
        style_hash=str(row_id),
        count=1.0,
        checked=True,
    )


class _EmbeddingProvider:
    provider_id = "fake"
    model_name = "fake-embed"

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self.vectors = vectors
        self.calls: list[list[str]] = []

    def get_dimension(self) -> int:
        return 2

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return [self.vectors.get(text, [0.0, 0.0]) for text in texts]


class _Lease:
    def __init__(self, provider: object) -> None:
        self.provider = provider

    async def __aenter__(self) -> object:
        return self.provider

    async def __aexit__(self, *exc: object) -> None:
        return None


class ConfigTests(unittest.TestCase):
    """默认值、校验与按路径解析。"""

    def test_defaults_and_accessor(self) -> None:
        vector = SYSTEM_DEFAULTS["chat"]["expression"]["vector"]
        self.assertEqual(
            vector,
            {
                "enabled": False,
                "index_path": "data/expression_selection/expression_vector_index.json",
                "candidate_pool_size": 50,
            },
        )
        with patch.object(memory_config, "expression_config", return_value={"vector": {"enabled": True}}):
            merged = memory_config.expression_vector_config()
        self.assertTrue(merged["enabled"])
        self.assertEqual(merged["candidate_pool_size"], 50)

    def test_validation(self) -> None:
        validate_system_config({"chat": {"expression": {"vector": {"enabled": True, "candidate_pool_size": 10}}}})
        for vector, error in (
            ({"enabled": "yes"}, "enabled"),
            ({"index_path": ""}, "index_path"),
            ({"candidate_pool_size": 0}, "candidate_pool_size"),
            ({"candidate_pool_size": 51}, "candidate_pool_size"),
            ("x", "必须是对象"),
        ):
            with self.subTest(vector=vector):
                with self.assertRaisesRegex(ValueError, error):
                    validate_system_config({"chat": {"expression": {"vector": vector}}})

    def test_index_path_resolution(self) -> None:
        self.assertEqual(vector_module.resolve_index_path(str(Path.cwd() / "a.json")), Path.cwd() / "a.json")
        self.assertTrue(str(vector_module.resolve_index_path("data/expression_selection/x.json")).endswith("x.json"))
        relative = vector_module.package_root() / "other/x.json"
        self.assertEqual(vector_module.resolve_index_path("other/x.json"), relative)


class SelectorTextTests(unittest.TestCase):
    """余弦、候选提示与编号解析。"""

    def test_cosine(self) -> None:
        self.assertAlmostEqual(vector_module.cosine([1, 0], [1, 0]), 1.0)
        self.assertAlmostEqual(vector_module.cosine([1, 0], [0, 1]), 0.0)
        self.assertEqual(vector_module.cosine([0, 0], [1, 0]), 0.0)
        self.assertEqual(vector_module.cosine([1], [1, 0]), 0.0)

    def test_prompt_lists_candidates(self) -> None:
        prompt = vector_module.build_selector_prompt("在问好", [_row(3, "打招呼", "早上好")])
        self.assertIn("3: 情景=打招呼 | 风格=早上好", prompt)
        self.assertIn("在问好", prompt)

    def test_selected_ids_are_filtered(self) -> None:
        rows = [_row(1, "a", "b"), _row(2, "c", "d")]
        self.assertEqual(vector_module.parse_selected_ids('{"selected_ids":[2,2,9,"1",true]}', rows), [2])
        self.assertEqual(vector_module.parse_selected_ids("没有 JSON", rows), [])
        self.assertEqual(vector_module.parse_selected_ids('{"selected_ids":[1,2,1]}', rows), [1, 2])
        self.assertEqual(vector_module.parse_selected_ids('{"selected_ids":[1,2,1,1,1,2]}', rows), [1, 2])


class VectorCandidatesTests(unittest.IsolatedAsyncioTestCase):
    """索引按文本增量维护，召回只返回相似度大于 0 的候选。"""

    def setUp(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.path = Path(directory) / "index.json"
            self.directory = directory
            vector_module._indexes.clear()
            vector_module._index_lock = None
            provider = _EmbeddingProvider(
                {
                    "问候 | 早上好": [1.0, 0.0],
                    "告别 | 拜拜": [0.0, 1.0],
                    "早上好呀": [1.0, 0.0],
                }
            )
            self.provider = provider
            patcher = patch.object(
                vector_module.provider_manager, "get_embedding_provider", AsyncMock(return_value=provider)
            )
            patcher.start()
            self.addCleanup(patcher.stop)
            self.rows = [_row(1, "问候", "早上好"), _row(2, "告别", "拜拜")]
            self.index_path = str(self.path)

    async def test_index_is_built_then_reused(self) -> None:
        first = await vector_module.vector_candidates("早上好呀", self.rows, index_path=self.index_path, pool_size=50)
        self.assertEqual([row.id for row in first], [1])
        saved = json.loads(Path(self.index_path).read_text(encoding="utf-8"))
        self.assertEqual(saved["version"], vector_module.INDEX_VERSION)
        self.assertEqual(saved["identity"], "fake:fake-embed:2")
        await vector_module.vector_candidates("早上好呀", self.rows, index_path=self.index_path, pool_size=50)
        self.assertEqual(len(self.provider.calls), 3)

    async def test_pool_size_and_empty_query(self) -> None:
        first = await vector_module.vector_candidates("早上好呀", self.rows, index_path=self.index_path, pool_size=1)
        self.assertEqual([row.id for row in first], [1])
        self.assertEqual(
            await vector_module.vector_candidates("", self.rows, index_path=self.index_path, pool_size=5), []
        )

    async def test_identity_change_rebuilds_whole_index(self) -> None:
        """换嵌入模型后整代重建，不混用两种向量空间。"""
        await vector_module.vector_candidates("早上好呀", self.rows, index_path=self.index_path, pool_size=50)
        switched = _EmbeddingProvider(self.provider.vectors)
        switched.model_name = "other-embed"
        with patch.object(vector_module.provider_manager, "get_embedding_provider", AsyncMock(return_value=switched)):
            await vector_module.vector_candidates("早上好呀", self.rows, index_path=self.index_path, pool_size=50)

        self.assertEqual(switched.calls, [["问候 | 早上好", "告别 | 拜拜"], ["早上好呀"]])
        saved = json.loads(Path(self.index_path).read_text(encoding="utf-8"))
        self.assertEqual(saved["identity"], "fake:other-embed:2")

    async def test_text_change_reembeds_only_changed(self) -> None:
        await vector_module.vector_candidates("早上好呀", self.rows, index_path=self.index_path, pool_size=50)
        changed = [_row(1, "问候", "早安"), _row(2, "告别", "拜拜")]
        await vector_module.vector_candidates("早上好呀", changed, index_path=self.index_path, pool_size=50)
        self.assertEqual(self.provider.calls[2], ["问候 | 早安"])


class SelectByVectorsTests(unittest.IsolatedAsyncioTestCase):
    """召回后由模型筛选；失败时的回退行为。"""

    def setUp(self) -> None:
        self.rows = [_row(1, "问候", "早上好"), _row(2, "告别", "拜拜")]

    async def test_model_selection(self) -> None:
        provider = AsyncMock(return_value=LLMResponse(content='{"selected_ids":[2]}'))
        with (
            patch.object(vector_module, "vector_candidates", AsyncMock(return_value=self.rows)),
            patch.object(vector_module, "fallback_provider", lambda *a, **k: _Lease(SimpleProvider(provider))),
            patch.object(vector_module, "routed_task", return_value="utils"),
        ):
            selected = await vector_module.select_by_vectors(
                TENANT, "再见", self.rows, index_path="x.json", pool_size=50
            )
        self.assertEqual([row.id for row in selected], [2])  # type: ignore[union-attr]

    async def test_empty_selection_stays_empty(self) -> None:
        provider = AsyncMock(return_value=LLMResponse(content='{"selected_ids":[]}'))
        with (
            patch.object(vector_module, "vector_candidates", AsyncMock(return_value=self.rows)),
            patch.object(vector_module, "fallback_provider", lambda *a, **k: _Lease(SimpleProvider(provider))),
            patch.object(vector_module, "routed_task", return_value="utils"),
        ):
            self.assertEqual(
                await vector_module.select_by_vectors(TENANT, "再见", self.rows, index_path="x.json", pool_size=50),
                [],
            )

    async def test_recall_failure_falls_back_to_fuzzy(self) -> None:
        with patch.object(vector_module, "vector_candidates", AsyncMock(side_effect=RuntimeError("no model"))):
            self.assertIsNone(
                await vector_module.select_by_vectors(TENANT, "再见", self.rows, index_path="x.json", pool_size=50)
            )

    async def test_selector_failure_uses_candidates(self) -> None:
        with (
            patch.object(vector_module, "vector_candidates", AsyncMock(return_value=self.rows)),
            patch.object(vector_module, "fallback_provider", side_effect=RuntimeError("boom")),
        ):
            selected = await vector_module.select_by_vectors(
                TENANT, "再见", self.rows, index_path="x.json", pool_size=50
            )
        self.assertEqual([row.id for row in selected], [1, 2])  # type: ignore[union-attr]


class SimpleProvider:
    def __init__(self, chat: AsyncMock) -> None:
        self.chat = chat


class SelectExpressionsTests(unittest.IsolatedAsyncioTestCase):
    """普通模式使用计数抽样，向量模式使用召回结果。"""

    def setUp(self) -> None:
        self.rows = [_row(1, "问候", "早上好")]

    async def test_disabled_requires_ten_candidates(self) -> None:
        with (
            patch.object(expression_module, "expression_vector_config", return_value={"enabled": False}),
        ):
            selected = await expression_module.select_expressions(TENANT, "早上好", self.rows)
        self.assertEqual(selected, [])

    async def test_enabled_prefers_vector(self) -> None:
        with (
            patch.object(
                expression_module,
                "expression_vector_config",
                return_value={"enabled": True, "index_path": "x.json", "candidate_pool_size": 10},
            ),
            patch.object(expression_module, "select_by_vectors", AsyncMock(return_value=[self.rows[0]])) as vector,
        ):
            selected = await expression_module.select_expressions(TENANT, "早上好", self.rows)
        self.assertEqual(selected, [self.rows[0]])
        vector.assert_awaited_once()

    async def test_vector_none_falls_back(self) -> None:
        with (
            patch.object(expression_module, "expression_vector_config", return_value={"enabled": True}),
            patch.object(expression_module, "select_by_vectors", AsyncMock(return_value=None)),
        ):
            selected = await expression_module.select_expressions(TENANT, "早上好", self.rows)
        self.assertEqual(selected, [])


if __name__ == "__main__":
    unittest.main()
