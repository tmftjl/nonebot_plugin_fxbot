"""联网服务：配置校验与迁移、适配器协议、预览暂存与回查。"""

from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from src.plugins.nonebot_plugin_fxbot.chat.web import (
    ADAPTERS,
    WebRuntimeConfig,
    WebSearchService,
    WebServiceConfig,
    tools as web_tools,
    result_text,
    web_runtime,
)
from src.plugins.nonebot_plugin_fxbot.chat.tools import ToolContext, result as result_module, default_registry
from src.plugins.nonebot_plugin_fxbot.chat.replyer import generator
from src.plugins.nonebot_plugin_fxbot.chat.web.models import SearchHit, PageResult, SearchResult, WebSearchError
from src.plugins.nonebot_plugin_fxbot.chat.tools.store import ToolResultStore
from src.plugins.nonebot_plugin_fxbot.chat.tools.read_result import read_tool_result_tool
from src.plugins.nonebot_plugin_fxbot.config.system_defaults import (
    SYSTEM_DEFAULTS,
    migrate_system_config,
    validate_system_config,
)


def _service(**overrides: object) -> WebServiceConfig:
    value: dict[str, object] = {"name": "s1", "adapter": "deepseek", "api_key": "key"}
    value.update(overrides)
    return WebServiceConfig(**value)  # type: ignore[arg-type]


def _config(service: WebServiceConfig | None = None, preview_chars: int = 4000) -> WebRuntimeConfig:
    service = service or _service()
    return WebRuntimeConfig(preview_chars=preview_chars, services={service.name: service}, selected=service.name)


def _context(session: str = "s-1") -> ToolContext:
    return ToolContext(user_id="u1", group_id="g1", session_id=session, platform="test")


def _mock_service(handler) -> WebSearchService:
    return WebSearchService(httpx.MockTransport(handler))


class WebConfigTests(unittest.TestCase):
    """chat.web 的校验、兼容迁移与运行时快照。"""

    def _payload(self, **overrides: object) -> dict[str, object]:
        value: dict[str, object] = {
            "preview_chars": 4000,
            "services": [{"name": "ds", "adapter": "deepseek", "api_key": "k", "timeout": 60}],
        }
        value.update(overrides)
        return {"chat": {"web": value, "model_routing": {"web_search": "ds"}}}

    def test_valid_config_passes(self) -> None:
        validate_system_config(self._payload())

    def test_rejects_bad_services(self) -> None:
        cases = (
            ({"preview_chars": 100}, "preview_chars"),
            ({"services": "x"}, "services"),
            ({"services": [{"name": " ", "adapter": "deepseek"}]}, "名称"),
            ({"services": [{"name": "a", "adapter": "deepseek"}, {"name": "a", "adapter": "tavily"}]}, "重复"),
            ({"services": [{"name": "a", "adapter": "bing"}]}, "adapter"),
            ({"services": [{"name": "a", "adapter": "deepseek", "base_url": "ftp://x"}]}, "HTTP"),
            ({"services": [{"name": "a", "adapter": "deepseek", "base_url": "https://x/?q=1"}]}, "查询参数"),
            ({"services": [{"name": "a", "adapter": "deepseek", "timeout": 0}]}, "timeout"),
            ({"services": [{"name": "a", "adapter": "tavily", "search_depth": "deep"}]}, "search_depth"),
            ({"services": [{"name": "a", "adapter": "tavily", "extract_depth": "full"}]}, "extract_depth"),
        )
        for overrides, message in cases:
            with self.subTest(overrides=overrides):
                payload = self._payload(**overrides)
                payload["chat"]["model_routing"] = {"web_search": ""}  # type: ignore[index]
                with self.assertRaisesRegex(ValueError, message):
                    validate_system_config(payload)

    def test_rejects_routing_to_unknown_service(self) -> None:
        payload = self._payload()
        payload["chat"]["model_routing"] = {"web_search": "other"}  # type: ignore[index]
        with self.assertRaisesRegex(ValueError, "不存在的联网服务"):
            validate_system_config(payload)

    def test_migrates_legacy_web_search(self) -> None:
        raw = {"chat": {"web_search": {"base_url": "", "api_key": " old "}, "model_routing": {}}}
        self.assertTrue(migrate_system_config(raw))
        self.assertNotIn("web_search", raw["chat"])
        services = raw["chat"]["web"]["services"]
        self.assertEqual(services[0]["name"], "deepseek")
        self.assertEqual(services[0]["api_key"], "old")
        self.assertEqual(services[0]["adapter"], "deepseek")
        self.assertEqual(raw["chat"]["model_routing"]["web_search"], "deepseek")

    def test_migration_without_key_only_drops_legacy(self) -> None:
        raw = {"chat": {"web_search": {"base_url": "https://x", "api_key": ""}}}
        self.assertTrue(migrate_system_config(raw))
        self.assertNotIn("web_search", raw["chat"])
        self.assertNotIn("services", raw["chat"].get("web", {}))

    def test_migration_is_idempotent(self) -> None:
        self.assertFalse(migrate_system_config({"chat": {}}))
        raw = {"chat": {"web_search": {"api_key": "k"}, "web": {"services": [{"name": "deepseek"}]}}}
        self.assertTrue(migrate_system_config(raw))
        self.assertEqual(len(raw["chat"]["web"]["services"]), 1)

    def test_defaults_have_web_and_routing(self) -> None:
        chat = SYSTEM_DEFAULTS["chat"]
        self.assertEqual(chat["web"], {"preview_chars": 4000, "services": []})
        self.assertEqual(chat["model_routing"]["web_search"], "")
        self.assertNotIn("web_search", chat)

    def test_runtime_snapshot(self) -> None:
        fake = {
            "chat": {
                "web": {
                    "preview_chars": 1200,
                    "services": [{"name": "ds", "adapter": "tavily", "api_key": "k", "timeout": 30}],
                },
                "model_routing": {"web_search": "ds"},
            }
        }
        with patch("src.plugins.nonebot_plugin_fxbot.chat.web.config.get_manager") as manager:
            manager.return_value.get_system.return_value = fake
            config = web_runtime()
        self.assertTrue(config.enabled)
        self.assertEqual(config.preview_chars, 1200)
        self.assertEqual(config.service.adapter, "tavily")  # type: ignore[union-attr]

    def test_unknown_service_fields_are_ignored(self) -> None:
        payload = self._payload()
        payload["chat"]["web"]["services"][0]["category"] = "programming"  # type: ignore[index]
        validate_system_config(payload)
        with patch("src.plugins.nonebot_plugin_fxbot.chat.web.config.get_manager") as manager:
            manager.return_value.get_system.return_value = payload
            config = web_runtime()
        self.assertTrue(config.enabled)
        self.assertFalse(hasattr(config.service, "category"))

    def test_legacy_server_ref_is_ignored(self) -> None:
        payload = self._payload()
        payload["chat"]["web"]["services"][0]["server_ref"] = "mc-a"  # type: ignore[index]
        validate_system_config(payload)
        with patch("src.plugins.nonebot_plugin_fxbot.chat.web.config.get_manager") as manager:
            manager.return_value.get_system.return_value = payload
            config = web_runtime()
        self.assertTrue(config.enabled)
        self.assertFalse(hasattr(config.service, "server_ref"))

    def test_runtime_disabled_on_bad_config(self) -> None:
        with patch("src.plugins.nonebot_plugin_fxbot.chat.web.config.get_manager") as manager:
            manager.return_value.get_system.side_effect = RuntimeError
            self.assertFalse(web_runtime().enabled)
        with patch("src.plugins.nonebot_plugin_fxbot.chat.web.config.get_manager") as manager:
            manager.return_value.get_system.return_value = {"chat": {"web": []}}
            self.assertFalse(web_runtime().enabled)
        with patch("src.plugins.nonebot_plugin_fxbot.chat.web.config.get_manager") as manager:
            manager.return_value.get_system.return_value = {
                "chat": {
                    "web": {"preview_chars": True, "services": [{"name": "ds"}]},
                    "model_routing": {"web_search": "missing"},
                }
            }
            config = web_runtime()
        self.assertFalse(config.enabled)
        self.assertEqual(config.preview_chars, 4000)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    """各适配器的请求形状与错误收敛。"""

    async def test_deepseek_search_and_fetch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["authorization"], "Bearer key")
            self.assertTrue(str(request.url).startswith("https://api.deepseek.com/v1/tools/"))
            payload = json.loads(request.content)
            if request.url.path.endswith("/search"):
                self.assertEqual(payload, {"query": "天气", "count": 3, "need_content": False})
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {"title": "T", "url": "https://a.com", "snippet": "S", "summary": "M", "date": "2026-01-01"}
                        ]
                    },
                )
            self.assertEqual(payload, {"url": "https://a.com"})
            return httpx.Response(200, json={"title": "T", "content": "正文"})

        service = _mock_service(handler)
        config = _config()
        result = await service.search(config, "天气", 3)
        self.assertEqual(result.results[0].summary, "M")
        self.assertEqual(result.results[0].published_at, "2026-01-01")
        page = await service.fetch(config, "https://a.com")
        self.assertEqual((page.title, page.content), ("T", "正文"))

    async def test_tavily_uses_search_and_extract(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            if request.url.path == "/search":
                self.assertEqual(payload["search_depth"], "advanced")
                return httpx.Response(200, json={"results": [{"title": "T", "url": "https://a.com", "content": "S"}]})
            self.assertEqual(request.url.path, "/extract")
            self.assertEqual(payload["urls"], ["https://a.com"])
            return httpx.Response(
                200, json={"results": [{"url": "https://a.com", "title": "T", "raw_content": "正文"}]}
            )

        service = _mock_service(handler)
        config = _config(_service(adapter="tavily", search_depth="advanced"))
        self.assertEqual((await service.search(config, "q")).results[0].snippet, "S")
        self.assertEqual((await service.fetch(config, "https://a.com")).content, "正文")

    async def test_exa_uses_api_key_header(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.headers["x-api-key"], "key")
            self.assertNotIn("authorization", request.headers)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"title": "T", "url": "https://a.com", "highlights": ["一", "二"], "publishedDate": "d"}
                    ],
                    "statuses": [{"id": "https://a.com", "status": "success"}],
                },
            )

        service = _mock_service(handler)
        result = await service.search(_config(_service(adapter="exa")), "q")
        self.assertEqual(result.results[0].snippet, "一\n二")

    async def test_anysearch_and_firecrawl_shapes(self) -> None:
        def anysearch(request: httpx.Request) -> httpx.Response:
            self.assertTrue(str(request.url).startswith("https://api.anysearch.com/v1/"))
            return httpx.Response(
                200, json={"code": 0, "data": {"results": [{"title": "T", "url": "https://a.com", "snippet": "S"}]}}
            )

        result = await _mock_service(anysearch).search(_config(_service(adapter="anysearch")), "q")
        self.assertEqual(result.results[0].title, "T")

        def firecrawl(request: httpx.Request) -> httpx.Response:
            self.assertTrue(str(request.url).startswith("https://api.firecrawl.dev/v2/"))
            return httpx.Response(
                200,
                json={"success": True, "data": {"web": [{"title": "T", "url": "https://a.com", "description": "D"}]}},
            )

        result = await _mock_service(firecrawl).search(_config(_service(adapter="firecrawl")), "q")
        self.assertEqual(result.results[0].snippet, "D")

    async def test_errors_are_normalized(self) -> None:
        def unauthorized(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={})

        with self.assertRaisesRegex(WebSearchError, "HTTP 401"):
            await _mock_service(unauthorized).search(_config(), "q")

        def invalid(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="not json")

        with self.assertRaisesRegex(WebSearchError, "无效 JSON"):
            await _mock_service(invalid).fetch(_config(), "https://a.com")

        def biz_error(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"error": "x"})

        with self.assertRaisesRegex(WebSearchError, "业务错误"):
            await _mock_service(biz_error).search(_config(), "q")

        def timeout(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with self.assertRaisesRegex(WebSearchError, "超时"):
            await _mock_service(timeout).search(_config(), "q")

        with self.assertRaisesRegex(WebSearchError, "API Key"):
            await _mock_service(unauthorized).search(_config(_service(api_key=" ")), "q")

    async def test_service_validates_arguments(self) -> None:
        service = WebSearchService()
        with self.assertRaisesRegex(WebSearchError, "未配置联网服务"):
            await service.search(WebRuntimeConfig(), "q")
        with self.assertRaisesRegex(WebSearchError, "query"):
            await service.search(_config(), "  ")
        with self.assertRaisesRegex(WebSearchError, "max_results"):
            await service.search(_config(), "q", 21)
        with self.assertRaisesRegex(WebSearchError, "url"):
            await service.fetch(_config(), "file:///etc/passwd")
        with self.assertRaisesRegex(WebSearchError, "url"):
            await service.fetch(_config(), "https://user:pw@a.com")

    async def test_deepseek_query_length_limit(self) -> None:
        with self.assertRaisesRegex(WebSearchError, "128"):
            await WebSearchService().search(_config(), "x" * 129)

    async def test_all_adapters_registered(self) -> None:
        self.assertEqual(set(ADAPTERS), {"deepseek", "tavily", "anysearch", "exa", "firecrawl"})


class ResultTextTests(unittest.TestCase):
    """结果序列化保留标题、网址与摘要。"""

    def test_search_and_page(self) -> None:
        search = SearchResult(
            query="天气",
            results=[SearchHit(title="T", url="https://a.com", snippet="S", summary="M")],
        )
        text = result_text(search)
        self.assertIn("搜索：天气", text)
        self.assertIn("T\nhttps://a.com\nM", text)
        page = result_text(PageResult(url="https://a.com", title="T", content="正文"))
        self.assertEqual(page, "T\nhttps://a.com\n正文")


class ToolResultStoreTests(unittest.TestCase):
    """编号按会话递增，超出上限淘汰最早结果。"""

    def test_clear_preserves_counter_and_other_sessions(self) -> None:
        store = ToolResultStore()
        self.assertEqual(store.save("a", "web_search", "old"), "r1")
        self.assertEqual(store.save("b", "web_search", "other"), "r1")

        store.clear("a")
        store.clear("a")
        store.clear("missing")

        self.assertIsNone(store.get("a", "r1"))
        self.assertIsNotNone(store.get("b", "r1"))
        self.assertEqual(store.save("a", "web_search", "new"), "r2")
        self.assertIsNone(store.get("a", "r1"))
        self.assertEqual(store.get("a", "r2").content, "new")  # type: ignore[union-attr]

    def test_ids_and_eviction(self) -> None:
        store = ToolResultStore()
        self.assertEqual(store.save("a", "web_search", "一"), "r1")
        self.assertEqual(store.save("a", "web_fetch", "二"), "r2")
        self.assertEqual(store.save("b", "web_search", "三"), "r1")
        self.assertEqual(store.get("a", "r2").tool_name, "web_fetch")  # type: ignore[union-attr]
        self.assertIsNone(store.get("a", "r9"))
        for index in range(30):
            store.save("a", "web_search", f"x{index}")
        self.assertIsNone(store.get("a", "r1"))
        self.assertIsNotNone(store.get("a", "r32"))


class ReadToolResultTests(unittest.IsolatedAsyncioTestCase):
    """按偏移分段读取与关键词片段。"""

    def setUp(self) -> None:
        self.store = ToolResultStore()
        self.result_id = self.store.save("s-1", "web_search", "甲" * 100 + "关键词" + "乙" * 100)
        patcher = patch("src.plugins.nonebot_plugin_fxbot.chat.tools.read_result.tool_result_store", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_offset_reading(self) -> None:
        text = await read_tool_result_tool(_context(), SimpleNamespace(), self.result_id, max_chars=50)
        self.assertIn("本段 0–50", text)
        self.assertIn("offset=50", text)
        text = await read_tool_result_tool(_context(), SimpleNamespace(), self.result_id, offset=50, max_chars=5000)
        self.assertIn("本段 50–" + str(len("甲" * 100 + "关键词" + "乙" * 100)), text)
        self.assertIn("已到原文末尾", text)
        text = await read_tool_result_tool(_context(), SimpleNamespace(), self.result_id, offset=9999)
        self.assertIn("没有更多内容", text)

    async def test_keyword_reading(self) -> None:
        text = await read_tool_result_tool(_context(), SimpleNamespace(), self.result_id, keyword="关键词")
        self.assertIn("命中 1 处", text)
        self.assertIn("关键词", text)
        self.assertIn("位置", text)
        text = await read_tool_result_tool(_context(), SimpleNamespace(), self.result_id, keyword="不存在")
        self.assertIn("没有这个关键词", text)

    async def test_unknown_id(self) -> None:
        with self.assertRaisesRegex(Exception, "没有找到编号"):
            await read_tool_result_tool(_context(), SimpleNamespace(), "r99")


class WebToolTests(unittest.IsolatedAsyncioTestCase):
    """工具返回预览并暂存原文；搜索有冷却。"""

    async def asyncSetUp(self) -> None:
        self.store = ToolResultStore()
        web_tools._cooldowns.clear()
        self.patches = [
            patch.object(web_tools, "tool_result_store", self.store),
            patch.object(web_tools, "web_runtime", return_value=_config(preview_chars=500)),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    async def test_search_preview_and_store(self) -> None:
        hit = SearchHit(title="T", url="https://a.com", snippet="S" * 1000)
        service = SimpleNamespace(search=AsyncMock(return_value=SearchResult(query="天气", results=[hit])))
        with patch.object(web_tools, "WebSearchService", return_value=service):
            text = await web_tools.web_search_tool(_context(), SimpleNamespace(), "天气", 5)
        self.assertIn("搜索：天气", text)
        self.assertIn("[外部网页资料]", text)
        self.assertIn("原文编号：r1", text)
        self.assertIn("offset=500", text)
        stored = self.store.get("s-1", "r1")
        self.assertEqual(stored.tool_name, "web_search")  # type: ignore[union-attr]
        self.assertGreater(len(stored.content), 500)  # type: ignore[union-attr]

    async def test_search_cooldown_per_session(self) -> None:
        service = SimpleNamespace(
            search=AsyncMock(return_value=SearchResult(query="q", results=[])),
        )
        with patch.object(web_tools, "WebSearchService", return_value=service):
            await web_tools.web_search_tool(_context(), SimpleNamespace(), "q")
            with self.assertRaisesRegex(Exception, "太频繁"):
                await web_tools.web_search_tool(_context(), SimpleNamespace(), "q")
            await web_tools.web_search_tool(_context("s-2"), SimpleNamespace(), "q")

    async def test_fetch_preview(self) -> None:
        page = PageResult(url="https://a.com", title="标题", content="正文" * 400)
        service = SimpleNamespace(fetch=AsyncMock(return_value=page))
        with patch.object(web_tools, "WebSearchService", return_value=service):
            text = await web_tools.web_fetch_tool(_context(), SimpleNamespace(), "https://a.com")
        self.assertIn("标题\nhttps://a.com\n", text)
        self.assertIn("原文编号：r1", text)

    async def test_disabled_runtime_raises(self) -> None:
        with patch.object(web_tools, "web_runtime", return_value=WebRuntimeConfig()):
            with self.assertRaisesRegex(Exception, "未启用"):
                await web_tools.web_search_tool(_context(), SimpleNamespace(), "q")
            with self.assertRaisesRegex(Exception, "未启用"):
                await web_tools.web_fetch_tool(_context(), SimpleNamespace(), "https://a.com")

    async def test_oversized_result_rejected(self) -> None:
        page = PageResult(url="https://a.com", title="T", content="x" * (web_tools.MAX_STORED_CHARS + 1))
        service = SimpleNamespace(fetch=AsyncMock(return_value=page))
        with patch.object(web_tools, "WebSearchService", return_value=service):
            with self.assertRaisesRegex(Exception, "500000"):
                await web_tools.web_fetch_tool(_context(), SimpleNamespace(), "https://a.com")

    def test_tools_registered(self) -> None:
        names = {spec.name for spec in default_registry.list()}
        self.assertTrue({"web_search", "web_fetch", "read_tool_result"} <= names)


class ToolResultContentTests(unittest.IsolatedAsyncioTestCase):
    """超长工具结果写入上下文前只保留预览与编号。"""

    def test_overflow_is_stored(self) -> None:
        store = ToolResultStore()
        with patch.object(result_module, "tool_result_store", store):
            content = result_module.tool_result_content(
                {"ok": True, "result": "字" * 10000}, session_id="s-1", tool_name="web_search"
            )
        payload = json.loads(content)
        self.assertEqual(payload["result_id"], "r1")
        self.assertLessEqual(len(content), result_module.MAX_TOOL_RESULT_CHARS)
        self.assertEqual(len(store.get("s-1", "r1").content), 10000)  # type: ignore[union-attr]

    def test_short_result_passes_through(self) -> None:
        content = result_module.tool_result_content({"ok": True, "result": "短"}, session_id="s-1")
        self.assertEqual(json.loads(content), {"ok": True, "result": "短"})

    def test_overflow_without_session_truncates(self) -> None:
        content = result_module.tool_result_content({"ok": False, "error": "错" * 10000})
        payload = json.loads(content)
        self.assertLessEqual(len(content), result_module.MAX_TOOL_RESULT_CHARS)
        self.assertNotIn("result_id", payload)
        self.assertTrue(payload["error"].endswith("…"))

    async def test_both_web_tools_present_for_proactive_when_enabled(self) -> None:
        specs = [SimpleNamespace(name=name) for name in ("web_search", "web_fetch", "read_tool_result", "like")]
        with (
            patch.object(generator.default_registry, "list", return_value=specs),
            patch.object(
                generator.default_registry,
                "to_openai_tools",
                return_value=[{"function": {"name": spec.name}} for spec in specs],
            ),
            patch.object(generator, "tool_allowed", new=AsyncMock(return_value=True)),
            patch.object(generator, "web_runtime", return_value=SimpleNamespace(enabled=True)),
        ):
            names, _ = await generator.allowed_tools(SimpleNamespace(), proactive=True)
        self.assertEqual(names, {"web_search", "web_fetch", "read_tool_result"})

    async def test_web_tools_filtered_when_disabled(self) -> None:
        specs = [SimpleNamespace(name=name) for name in ("web_search", "web_fetch", "like")]
        with (
            patch.object(generator.default_registry, "list", return_value=specs),
            patch.object(generator.default_registry, "to_openai_tools", return_value=[]),
            patch.object(generator, "tool_allowed", new=AsyncMock(return_value=True)),
            patch.object(generator, "web_runtime", return_value=SimpleNamespace(enabled=False)),
        ):
            names, _ = await generator.allowed_tools(SimpleNamespace(), proactive=True)
        self.assertEqual(names, set())


if __name__ == "__main__":
    unittest.main()
