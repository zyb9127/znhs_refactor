"""
环境配置工具

集中管理运行环境判断及其衍生配置：
  - 运行环境（development / gray / production / production_noauth）
  - 鉴权开关（gray/production 强制开启，development 默认关闭，production_noauth 强制关闭）
  - 服务 URL 前缀（SERVICE_PREFIX）
    - development / gray / production_noauth → /znhs-gray（与灰度前端 base 对齐）
    - production                             → /znhs（与生产前端 base 对齐）

环境来源优先级：
  1. 环境变量 LINGYUN_ENV（容器/CI 覆盖）
  2. config/config.json → app.environment
  3. 默认 development

鉴权控制：
  - LINGYUN_AUTH_DISABLED=true  → 强制关闭（本地联调用）
  - gray / production           → 强制开启
  - production_noauth           → 强制关闭（保持生产行为但关鉴权，仅限受控测试）
  - development                 → 默认关闭，可显式 LINGYUN_AUTH_ENABLED=true 开启

production_noauth 说明：
  除鉴权外，其余行为与 production 一致（真实外部接口、ES/Redis 配置存储、
  LLM host 路由、Kafka 启用），但 URL 前缀使用 /znhs-gray（复用灰度前端构建），
  用于在受控环境免登录验证功能。
  ⚠️ 存在安全风险，切勿长期用于正式生产。
"""
from __future__ import annotations

import os
from typing import Optional

from loguru import logger

from utils.config_loader import config_loader

# ── 合法环境值 ────────────────────────────────────────────────
_VALID_ENVS = ("development", "gray", "production", "production_noauth")
_TRUTHY = ("true", "1", "yes", "on")

# 各环境对应的 SERVICE_PREFIX（与前端构建 base 路径对齐）
# production_noauth 使用 /znhs-gray 前缀（复用灰度前端构建，免登录访问）
_ENV_PREFIX_MAP = {
    "development": "/znhs-gray",
    "gray": "/znhs-gray",
    "production": "/znhs",
    "production_noauth": "/znhs-gray",
}


def _truthy(val: Optional[str]) -> bool:
    return (val or "").strip().lower() in _TRUTHY


def _sanitize_env_value(val: str) -> str:
    """清洗环境值中的脏字符。

    容错：部署平台/手工编辑可能把分隔符或引号误带入值中，
    如 "; production_noauth"、"；production_noauth"（中文全角分号）。
    """
    return val.strip().strip(";；'\"，, ").strip().lower()


def get_environment() -> str:
    """解析当前运行环境（development / gray / production / production_noauth）。"""
    raw = _sanitize_env_value(os.getenv("LINGYUN_ENV") or "")
    if raw:
        env = raw
    else:
        cfg_val = config_loader.get("app.environment", "development")
        env = _sanitize_env_value(str(cfg_val)) if cfg_val else "development"
        env = env or "development"

    if env not in _VALID_ENVS:
        logger.warning(f"⚠️ 无效的运行环境 '{env}'（需为 {_VALID_ENVS}），回退到 'development'")
        env = "development"
    return env


def load_env_specific_config(env: str) -> None:
    """根据环境设置 LINGYUN_AUTH_ENABLED 环境变量。

    规则：
      - LINGYUN_AUTH_DISABLED=true  → 强制关闭（本地联调用）
      - gray / production           → 强制开启
      - production_noauth           → 强制关闭（保持生产行为但关鉴权，仅限受控测试）
      - development                 → 默认关闭，可显式 LINGYUN_AUTH_ENABLED=true 开启
    """
    if _truthy(os.getenv("LINGYUN_AUTH_DISABLED")):
        os.environ["LINGYUN_AUTH_ENABLED"] = "false"
        logger.info("🔓 LINGYUN_AUTH_DISABLED=true，鉴权关闭（覆盖生产/灰度默认）")
        return

    if env == "production_noauth":
        os.environ["LINGYUN_AUTH_ENABLED"] = "false"
        logger.warning(
            "⚠️ production_noauth 环境：保持生产行为（真实接口 / ES/Redis / LLM host 路由），"
            "前缀 /znhs-gray，但已关闭灵运鉴权。请仅在受控测试场景使用，切勿长期用于正式生产！"
        )
        return

    if env in ("production", "gray"):
        os.environ["LINGYUN_AUTH_ENABLED"] = "true"
        logger.info(
            f"🔐 {env} 环境已启用鉴权（LINGYUN_AUTH_ENABLED=true；"
            "本地跳过请设 LINGYUN_AUTH_DISABLED=true）"
        )
        return

    # development：默认关闭；可显式 LINGYUN_AUTH_ENABLED=true 开启
    explicit = (os.getenv("LINGYUN_AUTH_ENABLED") or "").strip()
    os.environ["LINGYUN_AUTH_ENABLED"] = "true" if _truthy(explicit) else "false"


def is_auth_enabled() -> bool:
    """当前进程是否已开启灵运鉴权。"""
    return os.getenv("LINGYUN_AUTH_ENABLED", "false").lower() == "true"


def get_auth_client_env(env: str) -> str:
    """LingyunAuthClient 仅识别 gray / prod。"""
    return "gray" if env == "gray" else "prod"


def get_service_prefix(env: Optional[str] = None) -> str:
    """统一 URL 前缀。

    优先级：
      1. ROOT_PATH 环境变量（容器/Ingress 覆盖）
      2. 按 ENV 自动选择：development/gray → /znhs-gray，production → /znhs

    Args:
        env: 当前运行环境，为 None 时自动调用 get_environment()
    """
    override = os.environ.get("ROOT_PATH", "").strip().rstrip("/")
    if override:
        return override

    if env is None:
        env = get_environment()
    return _ENV_PREFIX_MAP.get(env, "/znhs-gray")


def is_gray_api_alias_enabled(service_prefix: str, gray_api_alias_prefix: str) -> bool:
    """是否需要注册 GrayApiAliasMiddleware。"""
    if not gray_api_alias_prefix:
        return False
    svc = (service_prefix or "").strip().rstrip("/")
    return bool(svc) and gray_api_alias_prefix != svc


def get_gray_ui_prefix() -> str:
    """灰度前端 UI 前缀（与 SERVICE_PREFIX 一致，供 env 接口返回）。"""
    return get_service_prefix()


def is_gray_spa_ingress_restore() -> bool:
    """是否启用 GraySpaIngressRestoreMiddleware。

    用于 Ingress 剥除灰度前缀后 SPA 路由补偿，默认关闭。
    可通过环境变量 GRAY_SPA_INGRESS_RESTORE=true 开启。
    """
    return _truthy(os.getenv("GRAY_SPA_INGRESS_RESTORE"))
