"""按 MaiBot 062d45c 的原版模板构造普通及 Focus Planner 提示。"""

from __future__ import annotations

from typing import Any

from ..planner import planner_config
from ..personas import DEFAULT_PERSONA, _split_sections, get_persona_text
from ..capabilities import capabilities_config
from ..memory.prompts import learning_prompt


def planner_prompt(name: str, text: str, behavior: dict[str, Any], *, is_group: bool, focus: bool = False) -> str:
    """人格、行为和任务规则按宿主配置映射到上游模板参数。"""
    parsed = _split_sections(text)
    intro, sections = parsed if parsed is not None else (text, {})
    aliases = [str(alias).strip() for alias in behavior.get("persona_aliases", []) if str(alias).strip()]
    personality = sections.get("personality") or intro.removeprefix(f"你的名字是{name}。").strip() or "是人类。"
    attention = str(behavior.get("group_chat_prompt" if is_group else "private_chat_prompts") or "").strip()
    query_rule = (
        "- query_memory()：只有回复明显依赖群内过去对话、共同经历、公开约定、任务进展或近期线索时使用；"
        "不要为了寒暄、即时情绪回应、轻松接话、只看最近消息就能回答的内容而检索。不要把私聊或个人隐私记忆带到群聊里。"
        if is_group
        else "- query_memory()：当对方提到“之前”“上次”“最近”“还记得吗”“我喜欢”“我说过”等信号，"
        "或回复依赖长期偏好、先前承诺、共同经历、人物长期信息时，可以更积极检索。"
    )
    if focus:
        # 共享主体可能同时处理群聊与私聊，不能用单一群聊规则覆盖所有成员。
        attention = ""
        query_rule = (
            "- query_memory()：按目标 session_id 的真实会话类型检索。"
            "群聊仅在回复依赖过去对话、共同经历或进展时检索；私聊可根据长期偏好、承诺或回忆信号检索。"
            "不得把共享上下文或检索结果当作目标聊天的在场人物与经历，不向其他聊天泄露私聊或个人隐私。"
        )
    capabilities = capabilities_config()
    subagent_rule = ""
    if capabilities.get("planner_subagent"):
        subagent_rule = (
            "- subagent()：遇到需要查资料才能回答或判断的事（查文档、搜网页、查版本或插件信息、找图、核实某个说法等），"
            "优先交给子代理：写清楚要查什么，把用得上的工具授权给它；它查完只把结论交给你。只有一步就能完成的事才直接调用对应工具。"
        )
        if capabilities.get("advanced_sub_agent"):
            subagent_rule += "子代理在后台运行，派发后不用等它，可以继续聊天，结论稍后会送回给你。"
    template = (
        "maisaka_chat_focus"
        if focus
        else "maisaka_chat_passive"
        if planner_config().get("passive_replyer")
        else "maisaka_chat"
    )
    return learning_prompt(
        template,
        bot_name=name,
        bot_alias_notice=f"，也有人叫你{','.join(aliases)}" if aliases else "",
        bot_nickname_notice="",
        personality=personality,
        behavior_style=sections.get("behavior_style", ""),
        group_chat_attention_block=f"在该聊天中的注意事项：\n{attention}" if attention else "",
        query_memory_rule="\n" + query_rule,
        subagent_rule="\n" + subagent_rule if subagent_rule else "",
    ).strip()


def focus_planner_prompt(behavior: dict[str, Any], *, name: str = DEFAULT_PERSONA, is_group: bool = True) -> str:
    """共享 Planner 保留同一份人格和上游共享决策规则。"""
    return planner_prompt(name, get_persona_text(name), behavior, is_group=is_group, focus=True)
