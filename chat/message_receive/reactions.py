"""关键词与正则反应提示，对齐 MaiBot（commit 062d45c，`src/config/official_configs.py` 的 KeywordReactionConfig）。"""

from __future__ import annotations

import re
from typing import Any


def _rule_reactions(rules: Any, text: str, *, regex: bool) -> list[str]:
    """逐条规则匹配，命中后取反应提示。"""
    reactions: list[str] = []
    for rule in rules if isinstance(rules, list) else []:
        if not isinstance(rule, dict):
            continue
        reaction = str(rule.get("reaction") or "").strip()
        if not reaction:
            continue
        if regex:
            for pattern in rule.get("regex") or []:
                try:
                    match = re.search(str(pattern), text)
                except re.error:
                    continue
                if match is not None:
                    reactions.append(_replace_groups(reaction, match))
                    break
        else:
            keywords = [str(keyword).strip() for keyword in rule.get("keywords") or [] if str(keyword).strip()]
            if keywords and any(keyword in text for keyword in keywords):
                reactions.append(reaction)
    return reactions


def _replace_groups(reaction: str, match: re.Match[str]) -> str:
    """把 reaction 里的 [名字] 换成同名捕获组内容。"""
    for name, value in match.groupdict().items():
        reaction = reaction.replace(f"[{name}]", value or "")
    return reaction


def reaction_block(chat_cfg: dict[str, Any], text: str) -> str:
    """命中预设规则时返回追加给模型的反应提示；未命中返回空串。"""
    settings = chat_cfg.get("message_receive")
    if not isinstance(settings, dict) or not text:
        return ""
    reactions = _rule_reactions(settings.get("keyword_rules"), text, regex=False)
    reactions.extend(_rule_reactions(settings.get("regex_rules"), text, regex=True))
    if not reactions:
        return ""
    lines = "\n".join(f"- {reaction}" for reaction in reactions)
    return f"【关键词反应】\n最新消息命中了预设反应规则，请在回复时优先参考以下要求：\n{lines}"
