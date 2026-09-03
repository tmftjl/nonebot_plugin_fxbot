"""配置管理器编排。"""

from __future__ import annotations

from typing import Any

from nonebot import logger

from ..utils.paths import config_dir
from .proxy import ConfigProxy
from .system_defaults import SYSTEM_DEFAULTS


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
    ) -> ConfigProxy:
        key = (namespace, filename)
        proxy = self._proxies.get(key)
        if proxy is None:
            proxy = ConfigProxy(
                namespace=namespace,
                filename=filename,
                defaults=defaults or {},
                clean_extra=clean_extra,
            )
            self._proxies[key] = proxy
        return proxy

    def bootstrap(self) -> None:
        if self._bootstrapped:
            return
        config_dir()
        self.register("system", SYSTEM_DEFAULTS, clean_extra=True)
        for proxy in list(self._proxies.values()):
            proxy.merge_and_save()
        self._bootstrapped = True
        logger.info("[FxBot] Config initialized")

    def get_system(self) -> dict[str, Any]:
        return self.register("system", SYSTEM_DEFAULTS).load()

    def get_all(self) -> dict[str, Any]:
        return {
            f"{namespace}/{filename}": proxy.load()
            for (namespace, filename), proxy in self._proxies.items()
        }

    def get_console_configs(self) -> dict[str, Any]:
        """返回控制台使用的配置数据。"""
        configs: dict[str, Any] = {}
        system = self.get_system()
        configs.update(system)
        for (namespace, filename), proxy in self._proxies.items():
            if namespace == "system" or filename != "config.json":
                continue
            configs[namespace] = proxy.load()
        return configs

    def save_console_configs(self, payload: dict[str, Any]) -> None:
        """保存控制台提交的配置数据。"""
        system_proxy = self.register("system", SYSTEM_DEFAULTS)
        system_keys = set(system_proxy.load())
        system_data = {
            key: payload[key]
            for key in system_keys
            if key in payload and key not in self._registered_plugin_names()
        }
        if system_data:
            system_proxy.save(system_data)

        for (namespace, filename), proxy in list(self._proxies.items()):
            if namespace == "system" or filename != "config.json":
                continue
            value = payload.get(namespace)
            if isinstance(value, dict):
                proxy.save(value)

    def _registered_plugin_names(self) -> set[str]:
        """返回已注册插件配置命名空间。"""
        return {
            namespace
            for (namespace, filename) in self._proxies
            if namespace != "system" and filename == "config.json"
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
