"""鸣潮角色别名表。

别名数据的默认来源是同目录的 ``char_alias.json``，键是接口返回的正式角色名，值是
社群常用称呼（昵称、梗名、拼音缩写）。可以改为读 gsuid_core 的 XutheringWavesUID
那份自动维护的表（配置项 ``alias_path``），格式相同。

别名表允许超前于接口：对不上当前角色列表的键会被忽略，等接口收录该角色后自动生效。
"""

from __future__ import annotations

import json
from pathlib import Path

from nonebot import logger

from .config import cfg_alias_path

BUILTIN_ALIAS_PATH = Path(__file__).with_name("char_alias.json")

# 图库插件 plugins/cultured 的命令名是精确匹配，同名别名必须让给它，否则两边抢同一条消息
GALLERY_RESERVED = {"jk"}

# 正式名里带间隔号（如「秧秧·玄翎」），用户不一定会打出来，比较前统一去掉
_SEPARATORS = "·・‧.．"

_unusable_paths: set[str] = set()


def normalize(text: str) -> str:
    """归一化角色称呼：去掉间隔号与全部空白后小写。"""
    for separator in _SEPARATORS:
        text = text.replace(separator, "")
    return "".join(text.split()).casefold()


def resolve_alias_path() -> Path:
    """决定别名表来源：配置的路径优先，未配置或不可读时用内置表。"""
    configured = cfg_alias_path()
    if configured:
        path = Path(configured).expanduser()
        if path.is_file():
            return path
        # 别名表在每条未命中的消息上都会查一次，坏路径只提醒一次，别刷日志
        if configured not in _unusable_paths:
            _unusable_paths.add(configured)
            logger.warning(f"[wuwa_emoji] 配置的别名表不可读，改用内置别名表：{configured}")
    return BUILTIN_ALIAS_PATH


def load_alias_map(path: Path) -> dict[str, list[str]]:
    """读取别名表，文件缺失或损坏时返回空表并留下日志。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        logger.opt(exception=True).warning(f"[wuwa_emoji] 角色别名表读取失败，本次只按正式名与 slug 匹配：{path}")
        return {}
    if not isinstance(data, dict):
        logger.warning(f"[wuwa_emoji] 角色别名表结构异常，本次只按正式名与 slug 匹配：{path}")
        return {}
    return {
        name: [str(alias) for alias in aliases]
        for name, aliases in data.items()
        if isinstance(name, str) and isinstance(aliases, list)
    }


def build_alias_index(characters: list[dict[str, str]], alias_map: dict[str, list[str]]) -> dict[str, str]:
    """构造「别名 -> 正式角色名」索引。

    别名撞车（多个角色都认领同一个词，如 ``qx`` 同时属于千咲和清宵）时整条丢弃：
    静默发错角色比不回应更糟。对不上角色列表的键直接忽略，因此别名表可以超前于
    接口，也不会因为接口删角色而对陌生名字乱发图。
    """
    names = [str(character.get("name") or "") for character in characters]
    canonical = {normalize(name): name for name in names if name}
    reserved = {normalize(word) for word in GALLERY_RESERVED}
    claims: dict[str, set[str]] = {}
    for name, aliases in alias_map.items():
        target = canonical.get(normalize(name))
        if not target:
            continue
        for alias in aliases:
            key = normalize(alias)
            if key and key not in reserved:
                claims.setdefault(key, set()).add(target)

    ambiguous = sorted(key for key, targets in claims.items() if len(targets) > 1)
    if ambiguous:
        logger.debug(f"[wuwa_emoji] 角色别名存在歧义，已丢弃 {len(ambiguous)} 条：{ambiguous}")
    return {alias: next(iter(targets)) for alias, targets in claims.items() if len(targets) == 1}
