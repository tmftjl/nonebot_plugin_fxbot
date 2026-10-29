"""A_Memorix 内核在 FxBot 里的生命周期。"""

from __future__ import annotations

import asyncio

from nonebot import logger

from .paths import package_root
from ._host.host_config import load_amemorix_config
from .core.runtime.sdk_memory_kernel import SDKMemoryKernel


class AMemorixRuntime:
    def __init__(self) -> None:
        self._kernel: SDKMemoryKernel | None = None
        self._lock = asyncio.Lock()
        self._started = False
        self._error = ""
        self._stopping = False

    @property
    def available(self) -> bool:
        return self._kernel is not None and not self._stopping and not self._error

    @property
    def error(self) -> str:
        return self._error

    def config(self):
        return load_amemorix_config()

    async def start(self) -> bool:
        """按配置启动内核；关闭或启动失败都只降级记忆。"""
        async with self._lock:
            if self._started:
                return self.available
            self._started = True
            cfg = load_amemorix_config()
            if not cfg.plugin.enabled:
                logger.info("[A_Memorix] 未启用，跳过长期记忆内核")
                return False
            try:
                kernel = SDKMemoryKernel(plugin_root=package_root(), config=cfg.model_dump())
                await kernel.initialize()
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                logger.opt(exception=True).error("[A_Memorix] 内核启动失败，记忆功能降级")
                return False
            self._kernel = kernel
            self._error = ""
            logger.info("[A_Memorix] 内核已启动")
            return True

    async def get_kernel(self) -> SDKMemoryKernel | None:
        if self._stopping or self._error:
            return None
        if self._kernel is None and not self._started:
            await self.start()
        return self._kernel

    async def stop(self) -> None:
        async with self._lock:
            kernel = self._kernel
            if kernel is None:
                self._started = False
                self._error = ""
                return
            self._stopping = True
            try:
                await kernel.shutdown()
            except Exception as exc:
                self._error = f"{type(exc).__name__}: {exc}"
                logger.opt(exception=True).warning("[A_Memorix] 内核关闭失败")
            else:
                self._kernel = None
                self._started = False
                self._error = ""
            finally:
                self._stopping = False


amemorix_runtime = AMemorixRuntime()
