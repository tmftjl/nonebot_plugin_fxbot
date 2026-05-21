from pathlib import Path
from typing import Any

import yaml
from nonebot import get_driver
from nonebot.compat import model_dump, type_validate_python
from nonebot.log import logger
from pydantic import BaseModel

from ...utils.paths import data_dir

protection_config_path = data_dir("memes") / "protection_config.yaml"


class ProtectionConfig(BaseModel):
    whitelist_ids: list[str] = []  # 白名单用户 QQ 号列表
    protected_memes: list[str] = []  # 需要保护的表情 key 列表


class ProtectionManager:
    def __init__(self, path: Path = protection_config_path):
        self.__path = path
        self.__config = ProtectionConfig()
        self.__load()

    def add_whitelist(self, user_id: str) -> bool:
        """添加白名单用户"""
        if user_id not in self.__config.whitelist_ids:
            self.__config.whitelist_ids.append(user_id)
            self.__dump()
            return True
        return False

    def remove_whitelist(self, user_id: str) -> bool:
        """移除白名单用户"""
        if user_id in self.__config.whitelist_ids:
            self.__config.whitelist_ids.remove(user_id)
            self.__dump()
            return True
        return False

    def add_protected_meme(self, meme_key: str) -> bool:
        """添加保护表情"""
        if meme_key not in self.__config.protected_memes:
            self.__config.protected_memes.append(meme_key)
            self.__dump()
            return True
        return False

    def remove_protected_meme(self, meme_key: str) -> bool:
        """移除保护表情"""
        if meme_key in self.__config.protected_memes:
            self.__config.protected_memes.remove(meme_key)
            self.__dump()
            return True
        return False

    def is_in_whitelist(self, user_id: str) -> bool:
        """检查是否在白名单中（主人默认在白名单）"""
        # 主人默认在白名单中
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return True
        return user_id in self.__config.whitelist_ids

    def is_protected(self, meme_key: str) -> bool:
        """检查表情是否需要保护"""
        return meme_key in self.__config.protected_memes

    def get_whitelist(self) -> list[str]:
        """获取白名单列表"""
        return self.__config.whitelist_ids.copy()

    def get_protected_memes(self) -> list[str]:
        """获取保护表情列表"""
        return self.__config.protected_memes.copy()

    def __load(self):
        """从文件加载配置"""
        if self.__path.exists():
            with self.__path.open("r", encoding="utf-8") as f:
                try:
                    raw_config = yaml.safe_load(f) or {}
                    self.__config = type_validate_python(ProtectionConfig, raw_config)
                except Exception as e:
                    logger.warning(f"保护配置解析失败，将使用默认配置: {e}")
                    self.__config = ProtectionConfig()
        else:
            self.__config = ProtectionConfig()
            self.__dump()

    def __dump(self):
        """保存配置到文件"""
        self.__path.parent.mkdir(parents=True, exist_ok=True)
        config_dict = model_dump(self.__config)
        with self.__path.open("w", encoding="utf-8") as f:
            yaml.dump(config_dict, f, allow_unicode=True)


protection_manager = ProtectionManager()
