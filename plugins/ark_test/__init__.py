"""ARK 消息测试插件。"""

from __future__ import annotations

from nonebot import logger
from nonebot.adapters import Bot, Event
from nonebot.matcher import Matcher

from ...permission import PermLevel, PermScene
from ...plugin import Plugin
from ...utils.compat import send_ark_message

P = Plugin("ark_test", display_name="ARK测试", enabled=True, level=PermLevel.LOW, scene=PermScene.ALL)

ark_test_cmd = P.on_regex(
    r"^#ark测试$",
    name="ark_test",
    display_name="ARK测试",
    priority=1,
    block=True,
    level=PermLevel.LOW,
    scene=PermScene.ALL,
)


@ark_test_cmd.handle()
async def _handle_ark_test(matcher: Matcher, bot: Bot, event: Event) -> None:
    """发送旧版 ARK 模板 23 测试消息。"""
    adapter_name = bot.adapter.get_name()
    await matcher.send("ARK测试开始...")
    await matcher.send(f"当前适配器: {adapter_name}")

    ark_data = {
        "template_id": 23,
        "kv": [
            {"key": "#DESC#", "value": "这是一个测试ARK消息"},
            {"key": "#PROMPT#", "value": "点击下方链接"},
            {
                "key": "#LIST#",
                "obj": [
                    {"obj_kv": [{"key": "desc", "value": "🔗 访问百度"}, {"key": "link", "value": "https://www.baidu.com"}]},
                    {"obj_kv": [{"key": "desc", "value": "🔗 访问GitHub"}, {"key": "link", "value": "https://github.com"}]},
                    {"obj_kv": [{"key": "desc", "value": "📺 访问B站"}, {"key": "link", "value": "https://www.bilibili.com"}]},
                    {"obj_kv": [{"key": "desc", "value": "这是普通文本（没有链接）"}]},
                ],
            },
        ],
    }
    try:
        await send_ark_message(bot, event, ark_data)
        logger.info("[ARK测试] ARK消息发送成功")
    except Exception as exc:
        logger.opt(exception=True).warning("[ARK测试] ARK消息发送失败")
        await matcher.finish(f"ARK消息发送失败\n错误: {exc}\n适配器: {adapter_name}")
