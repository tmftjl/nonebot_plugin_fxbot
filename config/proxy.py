"""带默认值、深度合并、校验和热重载的配置代理。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable
from dataclasses import field, dataclass

from nonebot import logger

from .storage import ConfigStorage
from ..utils.cache import cache

Validator = Callable[[dict[str, Any]], None]


def deep_merge(
    defaults: dict[str, Any],
    overrides: dict[str, Any],
    *,
    clean_extra: bool = True,
) -> dict[str, Any]:
    """将用户配置合并到默认配置上。

    默认值中的空字典会保留用户的任意嵌套键，适合 provider 定义等动态映射。
    """

    def _merge(base: dict[str, Any], user: dict[str, Any]) -> dict[str, Any]:
        keys = set(base) if clean_extra else set(base) | set(user)
        out: dict[str, Any] = {}
        for key in keys:
            base_value = base.get(key)
            user_value = user.get(key)
            if clean_extra and base_value == {} and isinstance(user_value, dict):
                out[key] = deepcopy(user_value)
            elif isinstance(base_value, dict) and isinstance(user_value, dict):
                out[key] = _merge(base_value, user_value)
            elif key in user:
                out[key] = deepcopy(user_value)
            else:
                out[key] = deepcopy(base_value)
        return out

    return _merge(defaults or {}, overrides or {})


@dataclass
class ConfigProxy:
    namespace: str
    defaults: dict[str, Any] = field(default_factory=dict)
    filename: str = "config.json"
    validator: Validator | None = None
    clean_extra: bool = True
    _storage: ConfigStorage | None = None
    _last_valid: dict[str, Any] | None = field(default=None, init=False, repr=False)
    _last_error: str | None = field(default=None, init=False, repr=False)

    @property
    def _cache_key(self) -> tuple[str, str, str]:
        return ("config", self.namespace, self.filename)

    @property
    def storage(self) -> ConfigStorage:
        if self._storage is None:
            self._storage = ConfigStorage(self.namespace, self.filename)
        return self._storage

    @property
    def path(self):
        return self.storage.path

    @property
    def history_storage(self) -> ConfigStorage:
        """返回与主配置相邻的有效配置快照。"""
        storage = ConfigStorage(self.namespace, f"{self.filename}.last-valid")
        storage._path = self.path.with_name(f"{self.path.name}.last-valid")
        return storage

    def ensure(self) -> None:
        if not self.storage.exists() and not self.history_storage.exists() and self._last_valid is None:
            self.save(deepcopy(self.defaults))

    def _validated(self, raw: dict[str, Any]) -> dict[str, Any]:
        """同时校验原始值与合并后的配置。"""
        if self.validator is not None:
            self.validator(raw)
            self.validator(deep_merge(self.defaults, raw, clean_extra=self.clean_extra))
        return raw

    def _remember(self, raw: dict[str, Any]) -> None:
        """独立于可淘汰缓存保留有效值，并原子保存校验快照。"""
        if self.validator is not None:
            try:
                self.history_storage.write(raw)
            except Exception as exc:
                logger.warning(f"[Config] {self.namespace}/{self.filename} 有效配置快照保存失败: {exc}")
                raise
        self._last_valid = deepcopy(raw)
        self._last_error = None

    def _fallback(self, exc: Exception) -> dict[str, Any]:
        """失败时优先使用内存或磁盘有效历史，无历史才用默认值。"""
        self._last_error = str(exc)
        if self._last_valid is None and self.history_storage.exists():
            try:
                self._last_valid = deepcopy(self._validated(self.history_storage.read(strict=True)))
            except Exception as history_exc:
                logger.warning(f"[Config] {self.namespace}/{self.filename} 有效配置快照不可用: {history_exc}")
                raise RuntimeError("有效配置快照不可用，拒绝回退默认值") from history_exc
        source = "上一份有效配置" if self._last_valid is not None else "默认值（无有效历史）"
        logger.warning(f"[Config] {self.namespace}/{self.filename} 配置读取或校验失败，使用{source}: {exc}")
        return deepcopy(self._last_valid if self._last_valid is not None else self.defaults)

    def reload(self) -> dict[str, Any]:
        return deepcopy(self._read(self.storage.mtime())[1])

    def _read(self, mtime: float) -> tuple[float, dict[str, Any], dict[str, Any]]:
        self.ensure()
        try:
            if not self.storage.exists():
                raise FileNotFoundError(f"配置文件不存在: {self.path}")
            raw = self._validated(self.storage.read(strict=True))
        except Exception as exc:
            raw = self._fallback(exc)
        else:
            self._remember(raw)
        merged = deep_merge(self.defaults, raw, clean_extra=self.clean_extra)
        entry = (mtime, deepcopy(raw), merged)
        cache.set(self._cache_key, entry)
        return entry

    def load(self) -> dict[str, Any]:
        mtime = self.storage.mtime()
        entry = cache.get(self._cache_key)
        if entry is None or entry[0] != mtime:
            entry = self._read(mtime)
        return deepcopy(entry[2])

    def merge_and_save(self) -> dict[str, Any]:
        """补齐并保存有效配置，坏文件保留原样并使用有效历史。"""
        self.ensure()
        try:
            if not self.storage.exists():
                raise FileNotFoundError(f"配置文件不存在: {self.path}")
            raw = self._validated(self.storage.read(strict=True))
        except Exception as exc:
            return deep_merge(self.defaults, self._fallback(exc), clean_extra=self.clean_extra)
        merged = deep_merge(self.defaults, raw, clean_extra=self.clean_extra)
        if merged != raw:
            self.save(merged)
        else:
            self._remember(raw)
        return merged

    def save(self, data: dict[str, Any]) -> None:
        self._validated(data)
        self.storage.write(data)
        self._remember(data)
        mtime = self.storage.mtime()
        merged = deep_merge(self.defaults, data, clean_extra=self.clean_extra)
        cache.set(self._cache_key, (mtime, deepcopy(data), merged))

    def reload_and_validate(self) -> tuple[bool, dict[str, Any], str | None]:
        raw = self.reload()
        return self._last_error is None, raw, self._last_error
