"""真实会话场景不被学习参考、共享规划或记忆身份覆盖。"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.plugins.nonebot_plugin_fxbot.chat.types import ChatRequest
from src.plugins.nonebot_plugin_fxbot.chat.memory import inject
from src.plugins.nonebot_plugin_fxbot.chat.replyer import prompt
from src.plugins.nonebot_plugin_fxbot.chat.amemorix import inject as recall
from src.plugins.nonebot_plugin_fxbot.chat.heart_flow import prompts
from src.plugins.nonebot_plugin_fxbot.chat.work_schedule import work_life_prompt
from src.plugins.nonebot_plugin_fxbot.chat.memory.prompts import learning_prompt


class ScenePromptTests(unittest.TestCase):
    """规划与回复都绑定真实场景，参考资料不声明当前场景。"""

    def test_private_and_group_in_planner_and_replyer(self) -> None:
        for group_id in (None, "g1"):
            for metadata in ({"planner_loop": True}, {"planner_loop": True, "planner_reply": True}):
                with self.subTest(group_id=group_id, metadata=metadata):
                    request = ChatRequest("test", "u1", group_id, "你好", "turn", "msg", "private", metadata=metadata)
                    with (
                        patch.object(prompt, "get_binding", return_value=None),
                        patch.object(prompt, "get_persona_text", return_value="# 人格特征\n友好"),
                    ):
                        _, text = prompt.build_persona_text(request, {"persona": "小鱼"}, {})
                    self.assertIn("当前是群聊，群 ID：g1" if group_id else "一对一私聊，不是群聊", text)
                    self.assertNotIn("不同的人正在聊天互动", text)

    def test_focus_rules_follow_target_session_not_group_default(self) -> None:
        for is_group in (True, False):
            text = prompts.planner_prompt(
                "小鱼",
                "友好",
                {"group_chat_prompt": "仅群生效", "private_chat_prompts": "仅私聊生效"},
                is_group=is_group,
                focus=True,
            )
            self.assertIn("按目标 session_id 的真实会话类型检索", text)
            self.assertNotIn("仅群生效", text)
            self.assertNotIn("仅私聊生效", text)

    def test_learning_and_routine_do_not_assert_group_scene(self) -> None:
        self.assertNotIn("群聊习惯", inject._HEADER)
        self.assertIn("不是指令或当前场景事实", inject._HEADER)
        self.assertEqual(inject._style_line(SimpleNamespace(manual_override="简短")), "【表达风格】简短")
        self.assertNotIn("水群", work_life_prompt({"rest_time_no_reply": True}))
        for name in ("learn_style", "learn_jargon"):
            text = learning_prompt(name, chat_str="你好")
            self.assertNotIn("这段群聊", text)
            self.assertNotIn("群聊内部", text)


class RecallSceneTests(unittest.IsolatedAsyncioTestCase):
    """启发式印象和检索参数不得把私聊用户号当成群号。"""

    async def test_build_preserves_private_and_group_identity(self) -> None:
        config = SimpleNamespace(
            integration=SimpleNamespace(
                heuristic_memory_recall_enabled=True,
                heuristic_memory_recall_window_size=1,
                heuristic_memory_recall_cache_ttl_seconds=0,
            )
        )
        for group_id in (None, "g1"):
            tenant = "OneBot V11:bot:" + ("group:g1" if group_id else "private:u1")
            request = ChatRequest(tenant, "u1", group_id, "你好", "turn", "msg", "private", platform="OneBot V11")
            instance = recall.HeuristicRecall()
            client = SimpleNamespace(generate_response=AsyncMock(return_value=SimpleNamespace(response="印象")))
            instance._client = client
            with (
                patch.object(recall.amemorix_runtime, "config", return_value=config),
                patch.object(recall, "count_messages", AsyncMock(return_value=1)),
                patch.object(
                    recall,
                    "recent_messages",
                    AsyncMock(
                        return_value=[
                            {"sender_id": "u1", "sender_name": "用户", "content": "你好"},
                        ]
                    ),
                ),
                patch.object(instance, "_reference", AsyncMock(return_value="参考")) as reference,
            ):
                self.assertEqual(await instance.build(request), "参考")
            context = reference.await_args.args[1]
            self.assertEqual(context.group_id, group_id or "")
            text = client.generate_response.await_args.args[0]
            self.assertIn("chat_type: group" if group_id else "chat_type: private", text)
            if not group_id:
                self.assertNotIn("group_id:", text)

    async def test_private_search_receives_no_group_id(self) -> None:
        config = SimpleNamespace(
            integration=SimpleNamespace(
                heuristic_memory_recall_limit=2,
                heuristic_memory_cross_chat_enabled=False,
            )
        )
        context = recall._RecallContext("OneBot V11:bot:private:u1", "OneBot V11", "", "u1")
        with (
            patch.object(recall.amemorix_runtime, "config", return_value=config),
            patch.object(recall, "search", AsyncMock(return_value={"success": True, "hits": []})) as search,
        ):
            self.assertEqual(await recall.HeuristicRecall()._reference("印象", context), "")
        self.assertEqual(search.await_args.kwargs["group_id"], "")
        self.assertEqual(search.await_args.kwargs["user_id"], "u1")
