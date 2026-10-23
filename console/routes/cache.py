"""缓存统计与清理路由。"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, APIRouter, HTTPException

from ..auth import bearer_auth
from ...utils.cache import cache, key_label, estimate_size

router = APIRouter(prefix="/cache", tags=["fxbot-cache"], dependencies=[Depends(bearer_auth)])


def _process_rss() -> int | None:
    """获取进程常驻内存，不可用时返回空值。"""
    try:
        import psutil

        return int(psutil.Process().memory_info().rss)
    except ImportError:
        return None
    except Exception:
        return None


@router.get("")
async def get_cache_stats() -> dict[str, Any]:
    """获取进程内存及按占用降序排列的缓存域统计。"""
    domains: list[dict[str, Any]] = [dict(domain) for domain in cache.snapshot()]
    for domain in domains:
        accesses = domain["hits"] + domain["misses"]
        domain["hit_rate"] = round(domain["hits"] / accesses, 4) if accesses else 0.0
    domains.sort(key=lambda domain: domain["bytes"], reverse=True)
    return {
        "process": {
            "rss": _process_rss(),
            "cache_bytes": sum(domain["bytes"] for domain in domains),
            "entries": sum(domain["entries"] for domain in domains),
        },
        "domains": domains,
    }


@router.get("/keys")
async def get_cache_keys(domain: str, limit: int = 200) -> dict[str, Any]:
    """获取指定缓存域中按占用降序排列的键及时间信息。"""
    limit = max(1, min(limit, 1000))
    keys = [
        {
            "key": key_label(key),
            "bytes": estimate_size(key) + estimate_size(value),
            "ttl": None if expires_at is None else max(0, expires_at - time.monotonic()),
            "created_at": created_at,
            "accessed_at": accessed_at,
        }
        for key, value, expires_at, created_at, accessed_at in cache.items(domain)
    ]
    keys.sort(key=lambda entry: entry["bytes"], reverse=True)
    return {"domain": domain, "total": len(keys), "keys": keys[:limit]}


@router.post("/clear")
async def clear_cache(payload: dict[str, Any]) -> dict[str, Any]:
    """清理全部缓存、指定域或字符串标签匹配的单个键。"""
    scope = payload.get("scope")
    if scope == "all":
        cache.clear()
        return {"scope": "all", "removed": None}
    if scope == "domain":
        domain = str(payload.get("domain") or "")
        if not domain:
            raise HTTPException(status_code=400, detail="缺少缓存域")
        removed = cache.clear_prefix((domain,))
        return {"scope": scope, "domain": domain, "removed": removed}
    if scope == "key":
        label = str(payload.get("key") or "")
        if not label:
            raise HTTPException(status_code=400, detail="缺少缓存键")
        for key, _, _, _ in cache.keys():
            if key_label(key) == label:
                cache.pop(key)
                return {"scope": scope, "key": label, "removed": 1}
        return {"scope": scope, "key": label, "removed": 0}
    raise HTTPException(status_code=400, detail="无效的清理范围")
