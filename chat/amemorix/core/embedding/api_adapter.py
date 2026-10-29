"""嵌入 API 适配器（FxBot 版）。

保留 MaiBot ``EmbeddingAPIAdapter`` 的公开接口，内部改为调用 FxBot 的
``EmbeddingProvider``。FxBot 的 Provider 不接受请求维度，因此以模型真实输出
维度为准；没有配置嵌入 Provider 时工厂直接抛错，由 A_Memorix 运行时降级。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from ..._host.logger import get_logger
from ..._host.llm import embedding_provider_name
from ....providers.manager import provider_manager

logger = get_logger("A_Memorix.EmbeddingAPIAdapter")


def _fingerprint_hash(payload: Dict[str, Any]) -> str:
    normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()}"


class EmbeddingAPIAdapter:
    """FxBot EmbeddingProvider 的同步/异步包装。"""

    def __init__(
        self,
        batch_size: int = 32,
        max_concurrent: int = 5,
        default_dimension: int = 1024,
        enable_cache: bool = False,
        model_name: str = "auto",
        dimension_request_mode: str = "explicit",
        retry_config: Optional[dict] = None,
    ) -> None:
        self.batch_size = max(1, int(batch_size))
        self.max_concurrent = max(1, int(max_concurrent))
        self.default_dimension = max(1, int(default_dimension))
        self.enable_cache = bool(enable_cache)
        self.model_name = str(model_name or "auto")
        self.dimension_request_mode = str(dimension_request_mode or "explicit")
        self.retry_config = retry_config or {}
        self.max_attempts = max(1, int(self.retry_config.get("max_attempts", 3)))

        self._dimension: Optional[int] = None
        self._dimension_detected = False
        self._total_encoded = 0
        self._total_errors = 0
        self._total_time = 0.0
        self._last_success_model_name = ""
        self._last_success_provider_name = ""
        self._last_success_dimension: Optional[int] = None

        logger.info(
            "Embedding 初始化: "
            f"batch={self.batch_size}, concurrent={self.max_concurrent}, dim={self.default_dimension}"
        )

    async def _provider(self):
        return await provider_manager.get_embedding_provider(embedding_provider_name())

    async def _embed_once(self, texts: List[str]) -> List[List[float]]:
        last_exc: Optional[BaseException] = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                provider = await self._provider()
                vectors = await provider.embed(texts)
                if len(vectors) != len(texts):
                    raise RuntimeError(f"嵌入数量不匹配: {len(vectors)} != {len(texts)}")
                self._last_success_model_name = str(getattr(provider, "model_name", "") or "")
                self._last_success_provider_name = str(getattr(provider, "provider_id", "") or "")
                self._last_success_dimension = len(vectors[0]) if vectors else None
                return vectors
            except Exception as exc:
                last_exc = exc
                if attempt >= self.max_attempts:
                    break
                await asyncio.sleep(min(2.0 * attempt, 5.0))
        raise RuntimeError(f"embedding 请求失败: {last_exc}")

    @staticmethod
    def _validate_vector(vector: Any, *, source: str) -> np.ndarray:
        array = np.asarray(vector, dtype=np.float32)
        if array.ndim != 1:
            raise RuntimeError(f"{source} 返回的 embedding 维度非法: ndim={array.ndim}")
        if array.size <= 0:
            raise RuntimeError(f"{source} 返回了空 embedding")
        if not np.all(np.isfinite(array)):
            raise RuntimeError(f"{source} 返回了非有限 embedding 值")
        return array

    async def _detect_dimension(self) -> int:
        if self._dimension_detected and self._dimension is not None:
            return self._dimension
        try:
            vectors = await self._embed_once(["test"])
            detected = int(self._validate_vector(vectors[0], source="embedding 模型").size)
            if detected != self.default_dimension:
                logger.warning(
                    f"embedding 实际维度 {detected} 与配置维度 {self.default_dimension} 不同，使用实际维度"
                )
            self._dimension = detected
            self._dimension_detected = True
            return detected
        except Exception as exc:
            logger.error(f"嵌入维度检测失败: {exc}，使用 configured_dimension: {self.default_dimension}")
            self._dimension = self.default_dimension
            self._dimension_detected = True
            return self.default_dimension

    async def encode(
        self,
        texts: Union[str, List[str]],
        batch_size: Optional[int] = None,
        show_progress: bool = False,
        normalize: bool = True,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        del show_progress, normalize, dimensions
        start_time = time.time()
        dimension = int(await self._detect_dimension())

        if isinstance(texts, str):
            normalized_texts = [texts]
            single_input = True
        else:
            normalized_texts = [str(item) for item in (texts or [])]
            single_input = False

        if not normalized_texts:
            empty = np.zeros((0, dimension), dtype=np.float32)
            return empty[0] if single_input else empty

        batch_size = max(1, int(batch_size or self.batch_size))
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _encode_batch(batch: List[str]) -> List[np.ndarray]:
            async with semaphore:
                vectors = await self._embed_once(batch)
                return [self._validate_vector(item, source="embedding 模型") for item in vectors]

        try:
            batches = [
                normalized_texts[offset : offset + batch_size]
                for offset in range(0, len(normalized_texts), batch_size)
            ]
            results = await asyncio.gather(*(_encode_batch(batch) for batch in batches))
            embeddings = np.vstack([np.vstack(items) for items in results]) if results else np.zeros((0, dimension))
            self._total_encoded += len(normalized_texts)
            self._total_time += time.time() - start_time
            return embeddings[0] if single_input else embeddings
        except Exception as exc:
            self._total_errors += 1
            logger.error(f"编码失败: {exc}")
            raise RuntimeError(f"embedding encode failed: {exc}") from exc

    async def encode_batch(
        self,
        texts: List[str],
        batch_size: Optional[int] = None,
        num_workers: Optional[int] = None,
        show_progress: bool = False,
        dimensions: Optional[int] = None,
    ) -> np.ndarray:
        if num_workers is not None:
            previous = self.max_concurrent
            self.max_concurrent = max(1, int(num_workers))
            try:
                return await self.encode(
                    texts, batch_size=batch_size, show_progress=show_progress, dimensions=dimensions
                )
            finally:
                self.max_concurrent = previous
        return await self.encode(texts, batch_size=batch_size, show_progress=show_progress, dimensions=dimensions)

    def get_requested_dimension(self) -> int:
        return int(self.default_dimension)

    def get_embedding_dimension(self) -> int:
        if self._dimension is not None:
            return self._dimension
        logger.warning(f"维度尚未检测，返回 configured_dimension: {self.default_dimension}")
        return self.default_dimension

    def get_model_info(self) -> dict:
        effective_dimension = self.get_embedding_dimension()
        return {
            "model_name": self._last_success_model_name or self.model_name,
            "dimension": effective_dimension,
            "configured_dimension": int(self.default_dimension),
            "requested_dimension": int(self.get_requested_dimension()),
            "detected_dimension": int(self._dimension or 0),
            "dimension_detected": self._dimension_detected,
            "dimension_request_mode": self.dimension_request_mode,
            "batch_size": self.batch_size,
            "max_concurrent": self.max_concurrent,
            "total_encoded": self._total_encoded,
            "total_errors": self._total_errors,
            "avg_time_per_text": self._total_time / self._total_encoded if self._total_encoded else 0.0,
        }

    def get_embedding_fingerprint(
        self,
        *,
        dimension: Optional[int] = None,
        model_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """返回当前向量空间的精简指纹。"""
        effective_dimension = max(1, int(dimension or self._last_success_dimension or self.get_embedding_dimension()))
        model_token = str(model_name if model_name is not None else self._last_success_model_name).strip()
        provider_token = self._last_success_provider_name
        source = "observed" if model_name is None and model_token else "configured"
        if not model_token:
            try:
                provider_token = embedding_provider_name()
            except Exception:
                provider_token = ""
            model_token = "auto"
        payload = {
            "model": model_token or "auto",
            "provider": provider_token,
            "dimension": effective_dimension,
            "dimension_request_mode": self.dimension_request_mode,
        }
        return {
            "version": 2,
            "hash": _fingerprint_hash(payload),
            "model": payload["model"],
            "provider": payload["provider"],
            "dimension": effective_dimension,
            "model_identifier": model_token,
            "dimension_request_mode": self.dimension_request_mode,
            "source": source,
        }

    def get_embedding_configuration_key(self) -> str:
        """向量空间身份；等效于当前指纹 hash。"""
        return self.get_embedding_fingerprint()["hash"]


def create_embedding_api_adapter(
    batch_size: int = 32,
    max_concurrent: int = 5,
    default_dimension: int = 1024,
    enable_cache: bool = False,
    model_name: str = "auto",
    dimension_request_mode: str = "explicit",
    retry_config: Optional[dict] = None,
) -> EmbeddingAPIAdapter:
    embedding_provider_name()
    return EmbeddingAPIAdapter(
        batch_size=batch_size,
        max_concurrent=max_concurrent,
        default_dimension=default_dimension,
        enable_cache=enable_cache,
        model_name=model_name,
        dimension_request_mode=dimension_request_mode,
        retry_config=retry_config,
    )
