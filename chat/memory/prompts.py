"""读取从 MaiBot 062d45c prompts/zh-CN 迁入的学习模板。"""

from __future__ import annotations

from ...utils.paths import package_root


def learning_prompt(name: str, **context: object) -> str:
    """只渲染随代码提供的命名模板，保留上游提示词内容。"""
    templates = {
        "learn_style",
        "learn_jargon",
        "expression_evaluation",
        "jargon_inference_with_context",
        "jargon_inference_content_only",
        "jargon_compare_inference",
        "person_fact_writeback",
        "context_compaction",
        "context_compaction_focus",
        "emoji_selection",
        "emoji_replace",
        "emoji_content_analysis",
        "emoji_content_filtration",
        "maisaka_chat",
        "maisaka_chat_passive",
        "maisaka_chat_focus",
        "maisaka_replyer",
        "image_description",
    }
    if name not in templates:
        raise ValueError("未知的学习模板")
    text = (package_root() / "chat" / "prompts" / f"{name}.prompt").read_text(encoding="utf-8")
    return text.format(**context)
