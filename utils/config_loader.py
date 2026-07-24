"""
配置加载器（精简版）

只加载两个文件：
  - config/config.json      → 应用级配置（端口、日志、缓存等）
  - config/agents_config.json → 全局共性配置（当前仅含 llm_gateway）

业务个性化配置（话术模板、接口映射、Prompt）全部由
skills-runtime/{province}/{intent}/config/ 统一管理，
不再在此处加载。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

from loguru import logger

_CONFIG_DIR = Path(__file__).parent.parent / "config"


class ConfigLoader:
    """配置加载器（单例）"""

    _instance: Optional["ConfigLoader"] = None
    _initialized: bool = False
    _app_config:    Dict[str, Any] = {}
    _agents_config: Dict[str, Any] = {}

    def __new__(cls) -> "ConfigLoader":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if not self._initialized:
            self._load_app_config()
            self._load_agents_config()
            ConfigLoader._initialized = True

    def _load_app_config(self) -> None:
        path = _CONFIG_DIR / "config.json"
        try:
            with open(path, encoding="utf-8") as f:
                ConfigLoader._app_config = json.load(f)
            logger.info(f"✅ 成功加载配置文件: {path}")
        except Exception as e:
            logger.warning(f"⚠️ 加载 config.json 失败，使用默认值: {e}")
            ConfigLoader._app_config = {
                "app": {"host": "0.0.0.0", "port": 8000},
                "cache": {"enabled": True, "ttl": 3600},
            }

    def _load_agents_config(self) -> None:
        path = _CONFIG_DIR / "agents_config.json"
        try:
            with open(path, encoding="utf-8") as f:
                ConfigLoader._agents_config = json.load(f)
            logger.info(f"✅ 成功加载 LLM 网关配置: {path}")
        except Exception as e:
            logger.warning(f"⚠️ 加载 agents_config.json 失败，使用默认值: {e}")
            ConfigLoader._agents_config = {}

    def get(self, key: str, default: Any = None) -> Any:
        """读取 config.json 中的值，支持点号路径（如 'app.port'）"""
        if key == "llm":
            return self.get_llm_config()
        parts = key.split(".")
        val: Any = self._app_config
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
                if val is None:
                    return default
            else:
                return default
        return val

    def get_llm_config(self, province: str = "default") -> Dict[str, Any]:
        """获取 LLM 配置（话术生成等通用调用；优先 dashscope，其次 llm_gateway）。

        话术生成与接口智能解析共用 dashscope 节点（与原项目 znhs_xy_old 一致），
        因此这里同样走 get_dashscope_config 的按环境切换逻辑，保证两者在
        development / gray / production 各环境使用的模型完全一致，避免生产环境
        话术生成仍指向公网 DeepSeek 而报域名解析失败。
        """
        if "dashscope" in self._agents_config:
            return self.get_dashscope_config()
        return {
            k: v
            for k, v in self._agents_config.get("llm_gateway", {}).items()
            if not str(k).startswith("_")
        }

    def get_dashscope_config(self) -> Dict[str, Any]:
        """获取 dashscope 专用配置（接口映射 Agent / 接口智能解析使用）。

        按运行环境合并 env_overrides：
          - development                          → 基础配置（直连 DeepSeek 公网）
          - gray / production / production_noauth → 叠加内网代理配置（qwen-plus，IP 直连）

        目的：内网环境无法解析公网域名（api.deepseek.com），会报
        "Temporary failure in name resolution"，故在这些环境切到内网代理，
        与原项目 znhs_xy_old 的调用方式保持一致。
        """
        cfg = self._agents_config.get("dashscope", {})
        base = {
            k: v
            for k, v in cfg.items()
            if not str(k).startswith("_") and k != "env_overrides"
        }

        overrides = cfg.get("env_overrides") or {}
        if overrides:
            # 延迟导入，避免与 env_config 形成模块级循环依赖
            try:
                from utils.env_config import get_environment
                env = get_environment()
            except Exception:
                env = str(self.get("app.environment", "development")).strip().strip(";；'\"，, ").strip().lower()

            env_cfg = overrides.get(env) or {}
            for k, v in env_cfg.items():
                if not str(k).startswith("_"):
                    base[k] = v
        return base

    def get_province_full_config(self, province: str = "default") -> Dict[str, Any]:
        """省份配置已迁移至 skills-runtime，此处返回空字典以保持接口兼容。"""
        return {}

    def reload(self) -> None:
        """热重载配置文件"""
        ConfigLoader._initialized = False
        ConfigLoader._app_config = {}
        ConfigLoader._agents_config = {}
        self._load_app_config()
        self._load_agents_config()
        ConfigLoader._initialized = True
        logger.info("✅ 配置已热重载")


# 全局单例
config_loader = ConfigLoader()
