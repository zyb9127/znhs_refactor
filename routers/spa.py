"""
SPA 页面路由

处理 Vue 前端的页面路由（SPA 模式）：
  - 灰度前端：读取 frontend/dist/（固定挂载在 /znhs-gray/ 下）
  - 生产前端：读取 frontend/dist-prod/（固定挂载在 /znhs/ 下）
  - SPA catch-all：Vue Router 刷新时回退 index.html
  - 目录不存在时返回 JSON 占位响应

两套路由完全独立，不依赖环境变量或 SERVICE_PREFIX。
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

# 前端构建目录
_BASE_DIR = Path(__file__).resolve().parents[1]
_DIST_GRAY_DIR = _BASE_DIR / "frontend" / "dist-gray"  # 灰度前端（/znhs-gray/）
_DIST_PROD_DIR = _BASE_DIR / "frontend" / "dist-prod"  # 生产前端（/znhs/）

# 灰度 SPA 路由（由 main.py 固定以 /znhs-gray 挂载）
gray_router = APIRouter(include_in_schema=False)

# 生产 SPA 路由（由 main.py 固定以 /znhs 挂载）
prod_router = APIRouter(include_in_schema=False)


# ── 工具函数 ──────────────────────────────────────────────────

def _file_response(path: str) -> FileResponse:
    resp = FileResponse(path)
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    return resp


def _spa_gray_response() -> FileResponse | JSONResponse:
    """返回灰度环境 Vue SPA 入口 HTML（frontend/dist/index.html）。"""
    dist_index = _DIST_GRAY_DIR / "index.html"
    if dist_index.exists():
        return _file_response(str(dist_index))

    logger.error(
        "frontend/dist/index.html 不存在，请在 frontend/ 目录执行：\n"
        "  npm run build:artifact:gray   # 输出到 dist/"
    )
    return JSONResponse({"message": "营销话术推荐智能体 v2.0.0", "error": "灰度前端未构建，请先执行 npm run build:artifact:gray"})


def _spa_prod_response() -> FileResponse | JSONResponse:
    """返回生产环境 Vue SPA 入口 HTML（frontend/dist-prod/index.html）。"""
    dist_index = _DIST_PROD_DIR / "index.html"
    if dist_index.exists():
        return _file_response(str(dist_index))

    logger.error(
        "frontend/dist-prod/index.html 不存在，请在 frontend/ 目录执行：\n"
        "  npm run build:artifact:prod   # 输出到 dist-prod/"
    )
    return JSONResponse({"message": "营销话术推荐智能体 v2.0.0", "error": "生产前端未构建，请先执行 npm run build:artifact:prod"})


def _spa_catchall_reserved(spa_path: str) -> bool:
    """避免把 API / 静态等路径误判成前端路由而返回 index.html。"""
    if not spa_path:
        return False
    low = spa_path.lower()
    if low.startswith(("api/", "assets/", "static/", "marketing/")):
        return True
    head = spa_path.split("/")[0]
    return head in ("api", "assets", "static", "internal", "marketing", "docs", "openapi.json", "redoc", "health")


# ── 灰度前端页面路由（固定挂载在 /znhs-gray/ 下）────────────────

@gray_router.get("/")
async def gray_index():
    return _spa_gray_response()


@gray_router.get("/mapping")
async def gray_mapping_config():
    return _spa_gray_response()


@gray_router.get("/template")
async def gray_template_config():
    return _spa_gray_response()


@gray_router.get("/interface-mapper")
async def gray_interface_mapper():
    return _spa_gray_response()


@gray_router.get("/agent_test")
async def gray_agent_test():
    return _spa_gray_response()


@gray_router.get("/{spa_path:path}")
async def gray_spa_catch_all(spa_path: str):
    """灰度前端 Vue 路由刷新回退。"""
    if _spa_catchall_reserved(spa_path):
        raise HTTPException(404, detail="Not Found")
    return _spa_gray_response()


# ── 生产前端页面路由（固定挂载在 /znhs/ 下）─────────────────────

@prod_router.get("/")
async def prod_index():
    return _spa_prod_response()


@prod_router.get("/mapping")
async def prod_mapping_config():
    return _spa_prod_response()


@prod_router.get("/template")
async def prod_template_config():
    return _spa_prod_response()


@prod_router.get("/interface-mapper")
async def prod_interface_mapper():
    return _spa_prod_response()


@prod_router.get("/agent_test")
async def prod_agent_test():
    return _spa_prod_response()


@prod_router.get("/{spa_path:path}")
async def prod_spa_catch_all(spa_path: str):
    """生产前端 Vue 路由刷新回退。"""
    if _spa_catchall_reserved(spa_path):
        raise HTTPException(404, detail="Not Found")
    return _spa_prod_response()
