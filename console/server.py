"""FastAPI 挂载和路由注册。"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from nonebot import get_app, logger

from ..config import get_manager

from .routes import bots, config, membership, permissions

_mounted = False


def _mount_path() -> str:
    """读取控制台挂载路径。"""
    cfg = get_manager().get_system()
    console_cfg = cfg.get("console") if isinstance(cfg.get("console"), dict) else {}
    path = str(console_cfg.get("mount_path") or "/fxbot").strip()
    return path if path.startswith("/") else f"/{path}"


def mount_console() -> None:
    """挂载控制台后端和已构建静态资源。"""
    global _mounted
    if _mounted:
        return

    cfg = get_manager().get_system()
    console_cfg = cfg.get("console") if isinstance(cfg.get("console"), dict) else {}
    if not bool(console_cfg.get("enabled", True)):
        return

    app = get_app()
    prefix = _mount_path()
    router = APIRouter(prefix=prefix)
    router.include_router(membership.router)
    router.include_router(permissions.router)
    router.include_router(config.router)
    router.include_router(bots.router)

    @router.get("/health")
    async def health() -> dict[str, bool]:
        """控制台健康检查。"""
        return {"ok": True}

    dist_dir = Path(__file__).parent / "web" / "dist"
    index_file = dist_dir / "index.html"
    if index_file.exists():

        @router.get("")
        async def index() -> FileResponse:
            """返回控制台首页。"""
            return FileResponse(index_file, media_type="text/html")

        app.mount(f"{prefix}/assets", StaticFiles(directory=str(dist_dir)), name="fxbot_console_assets")
    else:
        logger.warning("[FxBot] 控制台前端 dist 不存在，仅挂载 API，不执行前端构建")

    app.include_router(router)
    _mounted = True
    logger.info(f"[FxBot] 控制台已挂载: {prefix}")
