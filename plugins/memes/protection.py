from nonebot import get_driver

from .config import cfg_whitelist_ids, cfg_protected_memes, save_protection_config


class ProtectionManager:
    def add_whitelist(self, user_id: str) -> bool:
        """添加白名单用户。"""
        whitelist_ids = cfg_whitelist_ids()
        if user_id in whitelist_ids:
            return False
        whitelist_ids.append(user_id)
        save_protection_config(whitelist_ids, cfg_protected_memes())
        return True

    def remove_whitelist(self, user_id: str) -> bool:
        """移除白名单用户。"""
        whitelist_ids = cfg_whitelist_ids()
        if user_id not in whitelist_ids:
            return False
        whitelist_ids.remove(user_id)
        save_protection_config(whitelist_ids, cfg_protected_memes())
        return True

    def add_protected_meme(self, meme_key: str) -> bool:
        """添加保护表情。"""
        protected_memes = cfg_protected_memes()
        if meme_key in protected_memes:
            return False
        protected_memes.append(meme_key)
        save_protection_config(cfg_whitelist_ids(), protected_memes)
        return True

    def remove_protected_meme(self, meme_key: str) -> bool:
        """移除保护表情。"""
        protected_memes = cfg_protected_memes()
        if meme_key not in protected_memes:
            return False
        protected_memes.remove(meme_key)
        save_protection_config(cfg_whitelist_ids(), protected_memes)
        return True

    def is_in_whitelist(self, user_id: str) -> bool:
        """检查是否在白名单中，主人默认在白名单。"""
        superusers = get_driver().config.superusers
        if user_id in superusers:
            return True
        return user_id in cfg_whitelist_ids()

    def is_protected(self, meme_key: str) -> bool:
        """检查表情是否需要保护。"""
        return meme_key in cfg_protected_memes()


protection_manager = ProtectionManager()
