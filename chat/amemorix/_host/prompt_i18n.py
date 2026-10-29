"""A_Memorix 提示词加载。

提示词原样取自 MaiBot ``prompts/zh-CN``，放在 ``chat/amemorix/prompts/<locale>``。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"
DEFAULT_LOCALE = "zh-CN"


def normalize_prompt_name(name: str) -> str:
    token = str(name or "").strip()
    if token.endswith(".prompt"):
        token = token[: -len(".prompt")]
    return token


def resolve_prompt_path(name: str, locale: str | None = None) -> Path:
    token = normalize_prompt_name(name)
    target_locale = str(locale or DEFAULT_LOCALE).strip() or DEFAULT_LOCALE
    for candidate_locale in (target_locale, DEFAULT_LOCALE):
        path = PROMPTS_ROOT / candidate_locale / f"{token}.prompt"
        if path.is_file():
            return path
    raise FileNotFoundError(f"未找到提示词 {token}（locale={target_locale}）")


def load_prompt(
    name: str,
    locale: str | None = None,
    category: str | None = None,
    prompts_root: Path | None = None,
    custom_prompts_root: Path | None = None,
    **kwargs: Any,
) -> str:
    del category, prompts_root, custom_prompts_root
    template = resolve_prompt_path(name, locale).read_text(encoding="utf-8")
    if not kwargs:
        return template
    return template.format(**kwargs)
