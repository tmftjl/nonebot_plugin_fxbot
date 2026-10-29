"""配置管理器编排。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any
from hashlib import sha1
from collections.abc import Iterable

from nonebot import logger

from .proxy import Validator, ConfigProxy
from ..utils.cache import cache
from ..utils.paths import config_dir
from .system_defaults import SYSTEM_DEFAULTS, validate_system_config


class ConfigConflictError(ValueError):
    """控制台提交的配置修订号已过期。"""


class ConfigManager:
    def __init__(self) -> None:
        self._proxies: dict[tuple[str, str], ConfigProxy] = {}
        self._bootstrapped = False

    def register(
        self,
        namespace: str,
        defaults: dict[str, Any] | None = None,
        *,
        filename: str = "config.json",
        clean_extra: bool = True,
        validator: Validator | None = None,
    ) -> ConfigProxy:
        if namespace == "system" and filename == "config.json":
            defaults = SYSTEM_DEFAULTS if defaults is None else defaults
            validator = validate_system_config
        key = (namespace, filename)
        proxy = self._proxies.get(key)
        if proxy is None:
            proxy = ConfigProxy(
                namespace=namespace,
                filename=filename,
                defaults=defaults or {},
                clean_extra=clean_extra,
                validator=validator,
            )
            self._proxies[key] = proxy
        elif validator is not None:
            proxy.validator = validator
        return proxy

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        config_dir()
        self.register("system", SYSTEM_DEFAULTS, clean_extra=True, validator=validate_system_config)
        for proxy in list(self._proxies.values()):
            proxy.merge_and_save()
        self._bootstrapped = True
        logger.info("[FxBot] Config initialized")

    def get_system(self) -> dict[str, Any]:
        return self.register("system", SYSTEM_DEFAULTS, validator=validate_system_config).load()

    def get_all(self) -> dict[str, Any]:
        return {f"{namespace}/{filename}": proxy.load() for (namespace, filename), proxy in self._proxies.items()}

    def get_console_configs(self) -> dict[str, Any]:
        """返回控制台使用的配置数据。"""
        configs: dict[str, Any] = {"system": self.get_system()}
        for (namespace, filename), proxy in self._proxies.items():
            if namespace == "system" or filename != "config.json":
                continue
            configs[namespace] = proxy.load()
        configs["revisions"] = self.revisions(configs)
        return configs

    def revisions(self, namespaces: Iterable[str]) -> dict[str, str]:
        """返回控制台可写命名空间的文件内容修订号。"""
        revisions = {}
        for (namespace, filename), proxy in self._proxies.items():
            if filename != "config.json" or namespace not in namespaces:
                continue
            try:
                revisions[namespace] = sha1(proxy.path.read_bytes()).hexdigest()
            except FileNotFoundError:
                revisions[namespace] = ""
        return revisions

    def save_console_configs(self, payload: dict[str, Any], *, base_revisions: dict[str, str] | None = None) -> None:
        """保存控制台提交的配置数据。"""
        self.register("system", SYSTEM_DEFAULTS, validator=validate_system_config)
        if base_revisions is not None:
            conflicts = [
                namespace
                for namespace, revision in self.revisions(payload).items()
                if base_revisions.get(namespace) != revision
            ]
            if conflicts:
                raise ConfigConflictError(f"配置已被修改: {', '.join(conflicts)}")
        pending: list[tuple[ConfigProxy, dict[str, Any]]] = []
        for (namespace, filename), proxy in list(self._proxies.items()):
            if filename != "config.json" or namespace not in payload:
                continue
            value = payload[namespace]
            if not isinstance(value, dict):
                raise ValueError(f"{namespace} 配置必须是对象")
            proxy._validated(value)
            pending.append((proxy, value))
        snapshots = []
        for proxy, _ in pending:
            files = []
            for path in (proxy.path, proxy.history_storage.path):
                try:
                    content = path.read_bytes()
                except FileNotFoundError:
                    content = None
                files.append((path, content))
            snapshots.append((files, deepcopy(proxy._last_valid), cache.get(proxy._cache_key)))
        attempted = []
        try:
            for (proxy, value), snapshot in zip(pending, snapshots):
                attempted.append((proxy, snapshot))
                proxy.save(value)
        except Exception:
            for proxy, (files, last_valid, cached) in reversed(attempted):
                for path, content in files:
                    try:
                        if content is None:
                            path.unlink(missing_ok=True)
                        else:
                            path.write_bytes(content)
                    except Exception as exc:
                        logger.error(f"[Config] {path} 配置回滚失败: {exc}")
                proxy._last_valid = last_valid
                try:
                    if cached is None:
                        cache.pop(proxy._cache_key)
                    else:
                        cache.set(proxy._cache_key, cached)
                except Exception as exc:
                    logger.error(f"[Config] {proxy.namespace}/{proxy.filename} 缓存回滚失败: {exc}")
            raise
        try:
            from ..chat.memory.tasks import setup_memory_tasks

            setup_memory_tasks()
        except Exception as exc:
            logger.warning(f"[Config] 聊天后台任务重配置失败: {exc}")
        try:
            from ..membership.tasks import setup_membership_tasks

            setup_membership_tasks()
        except Exception as exc:
            logger.warning(f"[Config] 会员定时任务重配置失败: {exc}")

    def _registered_plugin_names(self) -> set[str]:
        """返回已注册插件配置命名空间。"""
        return {
            namespace for (namespace, filename) in self._proxies if namespace != "system" and filename == "config.json"
        }

    def reload_all(self) -> tuple[bool, dict[str, Any]]:
        result: dict[str, Any] = {}
        ok_all = True
        for (namespace, filename), proxy in list(self._proxies.items()):
            ok, data, error = proxy.reload_and_validate()
            ok_all = ok_all and ok
            result[f"{namespace}/{filename}"] = {
                "ok": ok,
                "error": error,
                "data": data,
            }
        return ok_all, result


_manager = ConfigManager()


def get_manager() -> ConfigManager:
    return _manager
