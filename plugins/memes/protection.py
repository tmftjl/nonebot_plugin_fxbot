from nonebot import get_driver

from .config import cfg_protected_memes, cfg_whitelist_ids, save_protection_config


class ProtectionManager:
    def add_whitelist(self, user_id: str) -> bool:
        """添加白名单用户。"""
        whitelist_ids = self.get_whitelist()
        if user_id in whitelist_ids:
            return False
        whitelist_ids.append(user_id)
        save_protection_config(whitelist_ids, self.get_protected_memes())
        return True

    def remove_whitelist(self, user_id: str) -> bool:
        """移除白名单用户。"""
        whitelist_ids = self.get_whitelist()
        if user_id not in whitelist_ids:
            return False
        whitelist_ids.remove(user_id)
        save_protection_config(whitelist_ids, self.get_protected_memes())
        return True

    def add_protected_meme(self, meme_key: str) -> bool:
        """添加保护表情。"""
        protected_memes = self.get_protected_memes()
        if meme_key in protected_memes:
            return False
        protected_memes.append(meme_key)
        save_protection_config(self.get_whitelist(), protected_memes)
        return True

    def remove_protected_meme(self, meme_key: str) -> bool:
        """移除保护表情。"""
        protected_memes = self.get_protected_memes()
        if meme_key not in protected_memes:
            return False
        protected_memes.remove(meme_key)
        save_protection_config(self.get_whitelist(), protected_memes)
        return True

    def is_in_whitelist(self, user_id: str) -> bool:
        """检查是否在白名单中，主人默认在白名单。"""
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return True
        return user_id in self.get_whitelist()

    def is_protected(self, meme_key: str) -> bool:
        """检查表情是否需要保护。"""
        return meme_key in self.get_protected_memes()

    def get_whitelist(self) -> list[str]:
        """获取白名单列表。"""
        return cfg_whitelist_ids()

    def get_protected_memes(self) -> list[str]:
        """获取保护表情列表。"""
        return cfg_protected_memes()


protection_manager = ProtectionManager()
