"""带默认值、深度合并、校验和热重载的配置代理。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable
from dataclasses import field, dataclass

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

    def ensure(self) -> None:
        if not self.storage.exists():
            self.save(deepcopy(self.defaults))
            return
        if self.storage.read() == {} and self.defaults:
            self.save(deepcopy(self.defaults))

    def reload(self) -> dict[str, Any]:
        self.ensure()
        raw = self.storage.read()
        if self.validator is not None:
            self.validator(raw)
        merged = deep_merge(self.defaults, raw, clean_extra=self.clean_extra)
        mtime = self.storage.mtime()
        cache.set(self._cache_key, (mtime, deepcopy(raw), merged))
        return deepcopy(raw)

    def _read(self, mtime: float) -> tuple[float, dict[str, Any], dict[str, Any]]:
        self.ensure()
        raw = self.storage.read()
        if self.validator is not None:
            self.validator(raw)
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
        raw = self.storage.read()
        merged = deep_merge(self.defaults, raw, clean_extra=self.clean_extra)
        if merged != raw:
            self.save(merged)
        return merged

    def save(self, data: dict[str, Any]) -> None:
        if self.validator is not None:
            self.validator(data)
        self.storage.write(data)
        mtime = self.storage.mtime()
        merged = deep_merge(self.defaults, data, clean_extra=self.clean_extra)
        cache.set(self._cache_key, (mtime, deepcopy(data), merged))

    def reload_and_validate(self) -> tuple[bool, dict[str, Any], str | None]:
        try:
            return True, self.reload(), None
        except Exception as exc:
            return False, deepcopy(self.defaults), str(exc)
