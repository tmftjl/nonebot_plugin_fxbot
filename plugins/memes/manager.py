from enum import IntEnum
from pathlib import Path
from typing import Any, Optional

import yaml
from nonebot.compat import PYDANTIC_V2, model_dump, type_validate_python
from nonebot.log import logger
from pydantic import BaseModel
from rapidfuzz import process

from ...utils.paths import data_dir
from .config import cfg_disabled_list
from .request import MemeInfo, get_meme_info, get_meme_keys

config_path = data_dir("memes") / "memes_config.yaml"


class MemeMode(IntEnum):
    BLACK = 0
    WHITE = 1


class MemeConfig(BaseModel):
    mode: MemeMode = MemeMode.BLACK
    disabled_groups: list[str] = []  # 禁用该表情的群组列表

    if PYDANTIC_V2:
        from pydantic import field_serializer

        @field_serializer("mode")
        def get_eunm_value(self, v: MemeMode, info) -> int:
            return v.value
    else:

        class Config:
            use_enum_values = True


class MemeManager:
    def __init__(self, path: Path = config_path):
        self.__path = path
        self.__meme_config: dict[str, MemeConfig] = {}
        self.__meme_dict: dict[str, MemeInfo] = {}
        self.__meme_names: dict[str, list[MemeInfo]] = {}
        self.__meme_tags: dict[str, list[MemeInfo]] = {}

    async def init(self):
        disabled_list = set(cfg_disabled_list())
        self.__meme_dict = {
            meme_key: await get_meme_info(meme_key)
            for meme_key in filter(
                lambda meme_key: meme_key not in disabled_list,
                sorted(await get_meme_keys()),
            )
        }
        self.__load()
        self.__dump()
        self.__refresh_names()
        self.__refresh_tags()

    def get_meme(self, meme_key: str) -> Optional[MemeInfo]:
        return self.__meme_dict.get(meme_key, None)

    def get_memes(self) -> list[MemeInfo]:
        return list(self.__meme_dict.values())

    def block(self, user_id: str, meme_key: str):
        """禁用表情（群组级别）"""
        config = self.__meme_config[meme_key]
        # 如果表情已被全局禁用，不允许群组再次禁用
        if config.mode == MemeMode.WHITE:
            return False
        # 添加到禁用列表
        if user_id not in config.disabled_groups:
            config.disabled_groups.append(user_id)
            self.__dump()
            return True
        return False

    def unblock(self, user_id: str, meme_key: str):
        """启用表情（群组级别）"""
        config = self.__meme_config[meme_key]
        # 如果表情已被全局禁用，不允许群组启用
        if config.mode == MemeMode.WHITE:
            return False
        # 从禁用列表移除
        if user_id in config.disabled_groups:
            config.disabled_groups.remove(user_id)
            self.__dump()
            return True
        return False

    def get_black_list(self) -> list[str]:
        """获取全局禁用的表情列表"""
        black_list = []
        for key, config in self.__meme_config.items():
            if config.mode == MemeMode.WHITE:
                black_list.append(key)
        return black_list

    def change_mode(self, mode: MemeMode, meme_key: str):
        config = self.__meme_config[meme_key]
        config.mode = mode
        self.__dump()

    def find(self, meme_name: str) -> list[MemeInfo]:
        meme_name = meme_name.lower()
        if meme_name in self.__meme_names:
            return self.__meme_names[meme_name]
        return []

    def search(
        self,
        meme_name: str,
        include_tags: bool = False,
        limit: Optional[int] = None,
        score_cutoff: float = 80.0,
    ) -> list[MemeInfo]:
        meme_name = meme_name.lower()
        meme_names = process.extract(
            meme_name, self.__meme_names.keys(), limit=limit, score_cutoff=score_cutoff
        )
        result: dict[str, MemeInfo] = {}
        for name, _, _ in meme_names:
            for meme in self.__meme_names[name]:
                result[meme.key] = meme
        if include_tags:
            meme_tags = process.extract(
                meme_name,
                self.__meme_tags.keys(),
                limit=limit,
                score_cutoff=score_cutoff,
            )
            for tag, _, _ in meme_tags:
                for meme in self.__meme_tags[tag]:
                    result[meme.key] = meme
        return list(result.values())

    def check(self, user_id: str, meme_key: str) -> bool:
        """检查表情是否可用"""
        if meme_key not in self.__meme_config:
            return False
        config = self.__meme_config[meme_key]
        # 如果表情被全局禁用，则禁用
        if config.mode == MemeMode.WHITE:
            return False
        # 如果表情在群组禁用列表中，则禁用
        if user_id in config.disabled_groups:
            return False
        return True

    def __load(self):
        raw_list: dict[str, Any] = {}
        if self.__path.exists():
            with self.__path.open("r", encoding="utf-8") as f:
                try:
                    raw_list = yaml.safe_load(f)
                except Exception:
                    logger.warning("表情列表解析失败，将重新生成")
        try:
            meme_list = {
                name: type_validate_python(MemeConfig, config)
                for name, config in raw_list.items()
            }
        except Exception:
            meme_list = {}
            logger.warning("表情列表解析失败，将重新生成")
        self.__meme_config = {
            meme_key: MemeConfig() for meme_key in self.__meme_dict.keys()
        }
        self.__meme_config.update(meme_list)

    def __dump(self):
        self.__path.parent.mkdir(parents=True, exist_ok=True)
        meme_list = {
            name: model_dump(config) for name, config in self.__meme_config.items()
        }
        with self.__path.open("w", encoding="utf-8") as f:
            yaml.dump(meme_list, f, allow_unicode=True)

    def __refresh_names(self):
        self.__meme_names = {}
        for meme in self.__meme_dict.values():
            names = set()
            names.add(meme.key.lower())
            for keyword in meme.keywords:
                names.add(keyword.lower())
            for shortcut in meme.shortcuts:
                names.add((shortcut.humanized or shortcut.key).lower())
            for name in names:
                if name not in self.__meme_names:
                    self.__meme_names[name] = []
                self.__meme_names[name].append(meme)

    def __refresh_tags(self):
        self.__meme_tags = {}
        for meme in self.__meme_dict.values():
            for tag in meme.tags:
                tag = tag.lower()
                if tag not in self.__meme_tags:
                    self.__meme_tags[tag] = []
                self.__meme_tags[tag].append(meme)


meme_manager = MemeManager()
