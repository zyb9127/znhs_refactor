"""
营销话术推荐智能体 — 主入口

职责：
  1. 读取运行环境（development / gray / production）
  2. 创建 FastAPI 应用并注册中间件
  3. 挂载静态资源（必须在路由注册之前，否则 catch-all 路由会拦截静态文件请求）
  4. 挂载各路由模块（实时服务、运营管理、接口映射、SPA 页面）
  5. 启动 uvicorn（直接运行时）

路由模块分布：
  routers/realtime.py                   — POST /znhs/marketing/recommend（无鉴权，固定前缀）
  routers/management.py                 — /api/skills、/api/templates、/api/interfaces、/health、/env
  management/interface_mapper/router.py — /api/parse_docx_preview、/api/interfaces/auto_map 等
  routers/spa.py                        — SPA 页面路由（Vue 前端，含 catch-all，必须最后挂载）
静态资源 & SPA 路由挂载策略（不依赖 SERVICE_PREFIX，按目录是否存在决定）：
  frontend/dist/      存在 → 灰度前端，固定挂载到 /znhs-gray/assets、/znhs-gray/static
  frontend/dist-prod/ 存在 → 生产前端，固定挂载到 /znhs/assets、/znhs/static

  两套前端可同时存在、独立服务，互不干扰。

SERVICE_PREFIX 规则（影响 API 路由 & 鉴权，与前端构建 base 路径对齐）：
  development / gray → /znhs-gray
  production         → /znhs
  ROOT_PATH 环境变量可覆盖（容器/Ingress 场景）

鉴权规则：
  - gray / production：强制开启
  - production_noauth：强制关闭（保持生产行为，仅受控测试用）
  - development：默认关闭；LINGYUN_AUTH_DISABLED=true 可强制关闭任何环境

本地开发：
  python main.py                                        # development，无鉴权，访问 /znhs-gray/
  $env:LINGYUN_ENV="gray"; python main.py               # 灰度，强制鉴权
  $env:LINGYUN_ENV="production"; python main.py         # 生产，强制鉴权，访问 /znhs/
  $env:LINGYUN_ENV="production_noauth"; python main.py  # 生产行为但关鉴权，访问 /znhs-gray/
  $env:LINGYUN_AUTH_DISABLED="true"; python main.py     # 强制关闭鉴权（任何环境）
"""
from __future__ import annotations

import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger

from utils.logger import setup_logger
from utils.env_config import (
    get_environment,
    get_service_prefix,
    get_auth_client_env,
    is_auth_enabled,
    is_gray_api_alias_enabled,
    load_env_specific_config,
)
from utils.skill_runtime import skill_registry

# ══════════════════════════════════════════════════════════════
# 模块级：纯配置读取（无副作用，uvicorn reload 安全）
# ══════════════════════════════════════════════════════════════

setup_logger()

ENV = get_environment()
load_env_specific_config(ENV)

SERVICE_PREFIX = get_service_prefix(ENV)
_AUTH_ENABLED = is_auth_enabled()
_AUTH_CLIENT_ENV = get_auth_client_env(ENV)

# 前端构建目录（两套独立，按目录是否存在决定是否挂载）
_BASE_DIR = Path(__file__).resolve().parent / "frontend"
_DIST_GRAY_DIR = _BASE_DIR / "dist-gray"      # 灰度前端 → /znhs-gray/
_ASSETS_GRAY_DIR = _DIST_GRAY_DIR / "assets"
_DIST_PROD_DIR = _BASE_DIR / "dist-prod"      # 生产前端 → /znhs/
_ASSETS_PROD_DIR = _DIST_PROD_DIR / "assets"

# ══════════════════════════════════════════════════════════════
# lifespan：有副作用的初始化（只在 worker 真正启动时执行一次）
# ══════════════════════════════════════════════════════════════

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期钩子：startup → yield → shutdown。"""
    from utils.config_loader import config_loader

    logger.info(f"🚀 启动环境: {ENV.upper()}  前缀: {SERVICE_PREFIX}  鉴权: {_AUTH_ENABLED}")

    # ── 1. 初始化 ES 配置存储 ──────────────────────────────────
    es_cfg = config_loader.get("elasticsearch")
    if es_cfg:
        try:
            from services.es_config_store import es_config_store
            es_config_store.init(es_cfg)
        except Exception as e:
            logger.warning(f"⚠️ ES 配置存储初始化失败（不影响启动）: {e}")

    # ── 2. 初始化 Redis 配置总线 ───────────────────────────────
    redis_bus_cfg = config_loader.get("redis_bus")
    if redis_bus_cfg:
        try:
            from services.redis_config_bus import redis_config_bus
            redis_config_bus.init(redis_bus_cfg)
        except Exception as e:
            logger.warning(f"⚠️ Redis 配置总线初始化失败（不影响启动）: {e}")

    # ── 3. 加载技能包（从 ES/Redis 或本地文件）────────────────
    skill_registry.initialize()
    logger.info(f"📦 已加载技能包: {len(skill_registry.list_all())} 个")

    # ── 3.5 skill 结构化信息存量迁移：为 ES 中已有 api_nodes/biz_config
    #     但缺 skill_meta 的技能包自动生成结构化信息并写入 ES，
    #     使 SkillManager 列表可直接从 ES 展示 skill 信息 ─────────
    try:
        from services.skill_meta_service import sync_skill_meta_to_es
        meta_summary = sync_skill_meta_to_es()
        if meta_summary.get("es_enabled"):
            logger.info(f"🧩 skill_meta 存量迁移: {meta_summary.get('message', '')}")
            if meta_summary.get("created"):
                # 迁移新建了 skill_meta，重载 registry 让本次启动即可读到
                skill_registry.initialize()
    except Exception as e:
        logger.warning(f"⚠️ skill_meta 存量迁移失败（不影响启动）: {e}")

    # ── 4. 启动多实例配置同步（sync_mode=pubsub|poll|hybrid 与轮询间隔
    #     poll_interval_seconds 均在 config/config.json 的 redis_bus 段配置）
    skill_registry.start_config_sync(redis_bus_cfg)

    # ── 5. 预热 LLM 服务 ───────────────────────────────────────
    from services.llm_service import llm_service
    await llm_service.warmup()

    # ── 6. Kafka（灵运操作日志，可选）────────────────────────
    try:
        from services.kafka_service import init_kafka
        from services.lingyun_auth import LingyunAuthClient
        init_kafka(LingyunAuthClient(env=_AUTH_CLIENT_ENV))
    except Exception as e:
        logger.warning(f"⚠️ Kafka 初始化失败（不影响主服务）: {e}")

    yield

    # ── Shutdown ───────────────────────────────────────────────
    await llm_service.close()
    try:
        from services.kafka_service import shutdown_kafka
        shutdown_kafka()







        
    except Exception:
        pass
    logger.info("🛑 服务关闭")


# ══════════════════════════════════════════════════════════════
# 应用创建
# ══════════════════════════════════════════════════════════════

app = FastAPI(
    title="营销话术推荐智能体",
    version="2.0.0",
    description="运营端 + 实时服务一体化",
    docs_url=f"{SERVICE_PREFIX}/docs" if SERVICE_PREFIX else "/docs",
    openapi_url=f"{SERVICE_PREFIX}/openapi.json" if SERVICE_PREFIX else "/openapi.json",
    redoc_url=None,
    redirect_slashes=False,
    lifespan=lifespan,
)

# ── CORS ──────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 灵运鉴权中间件（gray / production 强制开启）──────────────────
if _AUTH_ENABLED:
    from services.lingyun_auth import LingyunAuthMiddleware, LingyunAuthClient

    # 同一进程同时服务灰度（/znhs-gray/）和生产（/znhs/），
    # 按请求路径前缀选择对应的鉴权客户端：
    #   灰度：http://bigmodel.zhiduo.cmos:8080/gray
    #   生产：http://bigmodel.zhiduo.cmos:8080
    _prefix_clients = {
        "/znhs-gray/": LingyunAuthClient(env="gray"),
        "/znhs/": LingyunAuthClient(env="prod"),
    }
    logger.info("🔐 多环境鉴权：/znhs-gray/ → gray，/znhs/ → prod")

    app.add_middleware(
        LingyunAuthMiddleware,
        client_env=_AUTH_CLIENT_ENV,   # 兜底客户端（无前缀匹配时使用）
        skip_prefixes=[
            # 运营管理接口的 marketing 路径（两套前缀都跳过）
            "/znhs-gray/marketing/",
            "/znhs/marketing/",
            "/znhs-gray/health",
            "/znhs/health",
            "/health",
            # 对外实时服务：固定挂载在 /znhs/marketing/，任何环境均无需鉴权
            "/znhs/marketing/test/",
            # 静态资源：前端 JS/CSS 文件不需要鉴权
            "/znhs-gray/assets/",
            "/znhs-gray/static/",
            "/znhs/assets/",
            "/znhs/static/",
            # 运营前端 SPA 入口页面（HTML）不需要鉴权，鉴权由前端 JS 完成
            "/znhs-gray/",
            "/znhs-gray/favicon",
        ],
        prefix_clients=_prefix_clients,
    )
    logger.info(f"🔐 灵运鉴权已启用 (env={_AUTH_CLIENT_ENV})")

# ── 灰度 API 别名中间件（/gray/... → SERVICE_PREFIX/...）──────────
_GRAY_API_ALIAS = "/gray"
if is_gray_api_alias_enabled(SERVICE_PREFIX, _GRAY_API_ALIAS):
    from services.lingyun_auth import GrayApiAliasMiddleware
    app.add_middleware(
        GrayApiAliasMiddleware,
        gray_prefix=_GRAY_API_ALIAS,
        target_prefix=SERVICE_PREFIX,
    )
    logger.info(f"↕️  灰度 API 别名: {_GRAY_API_ALIAS} → {SERVICE_PREFIX}")

# ══════════════════════════════════════════════════════════════
# 静态资源挂载（必须在路由注册之前！）
#
# 策略：按目录是否存在独立挂载，不依赖 SERVICE_PREFIX。
#   frontend/dist/      → 固定挂载到 /znhs-gray/assets、/znhs-gray/static
#   frontend/dist-prod/ → 固定挂载到 /znhs/assets、/znhs/static
# 两套可同时存在并独立服务。
# ══════════════════════════════════════════════════════════════

# ── 灰度前端静态资源（/znhs-gray/）────────────────────────────────
if _ASSETS_GRAY_DIR.is_dir():
    app.mount(
        "/znhs-gray/assets",
        StaticFiles(directory=str(_ASSETS_GRAY_DIR)),
        name="gray_assets",
    )
    logger.info(f"📦 灰度前端 assets 已挂载: /znhs-gray/assets → {_ASSETS_GRAY_DIR}")

if _DIST_GRAY_DIR.is_dir():
    app.mount(
        "/znhs-gray/static",
        StaticFiles(directory=str(_DIST_GRAY_DIR)),
        name="gray_static",
    )
    logger.info(f"📦 灰度前端 static 已挂载: /znhs-gray/static → {_DIST_GRAY_DIR}")

# ── 生产前端静态资源（/znhs/）─────────────────────────────────────
if _ASSETS_PROD_DIR.is_dir():
    app.mount(
        "/znhs/assets",
        StaticFiles(directory=str(_ASSETS_PROD_DIR)),
        name="prod_assets",
    )
    logger.info(f"📦 生产前端 assets 已挂载: /znhs/assets → {_ASSETS_PROD_DIR}")

if _DIST_PROD_DIR.is_dir():
    app.mount(
        "/znhs/static",
        StaticFiles(directory=str(_DIST_PROD_DIR)),
        name="prod_static",
    )
    logger.info(f"📦 生产前端 static 已挂载: /znhs/static → {_DIST_PROD_DIR}")

# ══════════════════════════════════════════════════════════════
# 路由挂载（静态资源之后）
# ══════════════════════════════════════════════════════════════

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent / "AutoConfigAgent"))

from routers.realtime import router as realtime_router
from routers.management import router as management_router
from management.interface_mapper.router import router as interface_mapper_router
from routers.spa import gray_router as spa_gray_router, prod_router as spa_prod_router
from AutoConfigAgent.server import router as auto_config_router
from management.config_agent.router import router as config_agent_router

# 实时服务固定使用 /znhs 前缀，与环境无关
app.include_router(realtime_router, prefix="/znhs")

# ── 管理 API：灰度和生产各挂一套，不依赖 SERVICE_PREFIX ──────────
# 灰度前端（/znhs-gray/）下的管理接口
app.include_router(management_router, prefix="/znhs-gray")
app.include_router(interface_mapper_router, prefix="/znhs-gray")
app.include_router(auto_config_router, prefix="/znhs-gray/api/auto-config")
app.include_router(config_agent_router, prefix="/znhs-gray")




# 生产前端（/znhs/）下的管理接口
app.include_router(management_router, prefix="/znhs")
app.include_router(interface_mapper_router, prefix="/znhs")
app.include_router(auto_config_router, prefix="/znhs/api/auto-config")
app.include_router(config_agent_router, prefix="/znhs")
logger.info("🔀 管理路由已挂载: /znhs-gray/api/... 和 /znhs/api/...")

# ── SPA 路由（必须最后注册，含 catch-all）─────────────────────────
# 无论 dist 目录是否存在都注册路由，spa.py 内部会处理未构建的情况（返回提示信息）
# 灰度前端 SPA 路由（固定挂载在 /znhs-gray/）
app.include_router(spa_gray_router, prefix="/znhs-gray")
logger.info("🌐 灰度前端 SPA 路由已注册: /znhs-gray/")

# 生产前端 SPA 路由（固定挂载在 /znhs/）
# 注意：必须在 realtime_router 之后，确保 /znhs/marketing/recommend 不被 catch-all 拦截
app.include_router(spa_prod_router, prefix="/znhs")
logger.info("🌐 生产前端 SPA 路由已注册: /znhs/")

# ══════════════════════════════════════════════════════════════
# 顶层重定向
# ══════════════════════════════════════════════════════════════

@app.get("/", include_in_schema=False)
async def _redirect_root():
    # 优先重定向到生产前端，其次灰度
    if _DIST_PROD_DIR.is_dir():
        return RedirectResponse(url="/znhs/")
    return RedirectResponse(url="/znhs-gray/")


@app.get("/health")
async def _health_root():
    return {"status": "ok", "skills": len(skill_registry.list_all()), "ts": int(time.time())}


@app.get("/znhs-gray", include_in_schema=False)
async def _redirect_gray_no_slash():
    return RedirectResponse(url="/znhs-gray/")


@app.get("/znhs", include_in_schema=False)
async def _redirect_prod_no_slash():
    return RedirectResponse(url="/znhs/")

# ══════════════════════════════════════════════════════════════
# 入口
# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        reload=(ENV == "development"),
        log_level="info",
    )
