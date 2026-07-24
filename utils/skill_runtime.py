"""
Skill 运行时 — 纯配置驱动，所有省份共用同一主流程

包含：
  SkillScenarioPackage  — 技能包数据模型
  SkillRuntimeLoader    — 技能包文件加载器
  SkillRuntimeRegistry  — 单例注册表
  SkillExecutor         — 技能执行器（统一三步管道，省份差异由配置文件表达）

配置写入（save_* / rollback_config）统一委托 services.skill_publisher（唯一写路径）。

目录约定（skills-runtime 为纯配置目录，不含省份代码）：
  skills-runtime/
  └── {province}/
      └── {intent}/
          └── config/
              ├── api_nodes.json   # 接口配置 + 字段映射
              └── biz_config.json  # 策略（含 expose_raw_bean 等开关）+ 话术模板 + Prompt

新增省份只需创建目录 + 两个配置文件，无需编写任何代码。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

# ── 开发模式检测 ───────────────────────────────────────────────
# config/config.json 中 app.environment=development 时跳过 ES/Redis，
# 配置直接读写本地文件，方便单机本地调试。
def _detect_dev_mode() -> bool:
    try:
        _cfg_path = Path(__file__).parents[1] / "config" / "config.json"
        with open(_cfg_path, encoding="utf-8") as _f:
            _cfg = json.load(_f)
        env = _cfg.get("app", {}).get("environment", "production")
        # 与 utils.env_config 保持一致：清洗误带入的分隔符/引号（如全角 '；' 前缀）
        return env.strip().strip(";；'\"，, ").strip().lower() == "development"
    except Exception:
        return False

IS_DEV: bool = _detect_dev_mode()
if IS_DEV:
    logger.info("[SkillRuntime] 🛠 开发模式：配置读写使用本地文件，跳过 ES/Redis")


# ══════════════════════════════════════════════════════════════
# Schema
# ══════════════════════════════════════════════════════════════

@dataclass
class SkillScenarioPackage:
    """技能包：单个省份+意图的完整配置集合"""
    province:  str
    intent:    str
    version:   str = "1.0.0"
    enabled:   bool = True
    config:    Dict[str, Any] = field(default_factory=dict)
    """合并后的配置: {"api_nodes": {...}, "biz_config": {...}}"""
    meta:      Dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return f"{self.province}:{self.intent}"


# ══════════════════════════════════════════════════════════════
# Loader
# ══════════════════════════════════════════════════════════════

class SkillRuntimeLoader:
    """从文件系统加载技能包"""

    SKILLS_ROOT = Path(__file__).parents[1] / "skills-runtime"

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = root or self.SKILLS_ROOT

    def list_provinces(self) -> List[str]:
        return [
            d.name for d in self.root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        ]

    def list_intents(self, province: str) -> List[str]:
        pdir = self.root / province
        if not pdir.is_dir():
            return []
        return [
            d.name for d in pdir.iterdir()
            if d.is_dir() and not d.name.startswith(".") and d.name != "config"
        ]

    def load_package(
        self,
        province: str,
        intent: str,
        force_local: bool = False,
        es_bulk: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Optional[SkillScenarioPackage]:
        """加载单个技能包。

        加载优先级（production 模式）：
          1. es_bulk —— load_all 传入的 ES match_all 批量结果（路径二，
             不依赖 meta 指针索引，一次遍历拿到全部 published 配置）
          2. Redis 热缓存（已发布配置）
          3. ES published 配置（meta 指针 + 字段搜索兜底）
          4. 本地文件（fallback）

        force_local=True 时直接读本地文件（用于初始化迁移）。

        本地目录不存在时不再直接失败：production 模式下若 ES/Redis 中
        存在该技能包的已发布配置（含仅存于 ES 的技能包），仍可正常加载。
        """
        base = self.root / province / intent
        local_exists = base.exists()
        if not local_exists and (force_local or IS_DEV):
            logger.warning(f"[SkillLoader] 技能包目录不存在: {base}")
            return None

        # ── 读取配置（多层优先级）──────────────────────────────
        api_nodes:  Dict[str, Any] = {}
        biz_config: Dict[str, Any] = {}
        skill_meta: Optional[Dict[str, Any]] = None
        source = "local"

        key = f"{province}:{intent}"
        if not force_local and not IS_DEV and es_bulk is not None:
            # 路径二：直接用 match_all 批量结果，不再逐个查 meta 指针
            bulk_cfgs = es_bulk.get(key) or {}
            api_nodes  = bulk_cfgs.get("api_nodes")  or {}
            biz_config = bulk_cfgs.get("biz_config") or {}
            skill_meta = bulk_cfgs.get("skill_meta") or None
            if api_nodes or biz_config:
                source = "es"
                logger.info(f"[SkillLoader] 从 ES 批量遍历(match_all)加载: {key}")

        if not force_local and source == "local" and not (api_nodes or biz_config):
            api_nodes, biz_config, source = self._load_config_from_external(province, intent)

        # fallback 到本地文件
        if not api_nodes:
            api_nodes  = self._read_json(base / "config" / "api_nodes.json")  or {}
        if not biz_config:
            biz_config = self._read_json(base / "config" / "biz_config.json") or {}

        if not local_exists and not api_nodes and not biz_config:
            logger.warning(
                f"[SkillLoader] {province}/{intent} 本地目录不存在且外部存储无配置，跳过加载"
            )
            return None

        config = {"api_nodes": api_nodes, "biz_config": biz_config}

        pkg = SkillScenarioPackage(
            province = province,
            intent   = intent,
            config   = config,
            meta     = {
                "province_name": province,
                "intent_name":   intent,
                "config_source": source,
                "local_exists":  local_exists,
                "loaded_at":     datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            },
        )

        # ── 结构化 skill 信息（skill_meta）：优先用批量结果，缺失时再单查
        if not IS_DEV and not force_local:
            try:
                if skill_meta is None:
                    from services.skill_meta_service import get_skill_meta
                    skill_meta = get_skill_meta(province, intent)
                if skill_meta:
                    pkg.meta["skill_meta"] = skill_meta
                    if skill_meta.get("version"):
                        pkg.version = str(skill_meta["version"])
                    logger.info(f"[SkillLoader] 已加载 ES skill_meta: {province}/{intent}")
            except Exception as e:
                logger.warning(f"[SkillLoader] 加载 skill_meta 失败 {province}/{intent}: {e}")

        logger.info(f"[SkillLoader] ✅ 加载技能包: {pkg.key} v{pkg.version} [配置来源: {source}]")
        return pkg

    def load_all(self) -> Dict[str, SkillScenarioPackage]:
        """加载所有技能包：本地目录 ∪ ES 已发布配置（production 模式）。

        production 模式采用「路径二」：对 ES configs 索引做一次 match_all
        批量遍历（load_all_published），province/intent 直接从文档字段读出，
        不依赖 meta 指针索引；批量结果同时作为各技能包的配置数据源，
        保证 SkillManager 能查询到 ES 中全部接口（api_nodes）与
        话术模板（biz_config）。本地目录仅作补充/兜底。
        """
        keys: List[tuple] = []
        seen: set = set()
        for province in self.list_provinces():
            for intent in self.list_intents(province):
                keys.append((province, intent))
                seen.add(f"{province}:{intent}")

        # production 模式：一次 match_all 遍历 ES，拿到全部已发布配置
        es_bulk: Optional[Dict[str, Dict[str, Any]]] = None
        if not IS_DEV:
            try:
                from services.es_config_store import es_config_store
                if es_config_store.enabled:
                    es_bulk = es_config_store.load_all_published() or {}
                    logger.info(
                        f"[SkillLoader] ES match_all 批量遍历: 共 {len(es_bulk)} 个技能包，"
                        f"keys={sorted(es_bulk.keys())}"
                    )
                    for key in es_bulk.keys():
                        if key not in seen and ":" in key:
                            province, intent = key.split(":", 1)
                            keys.append((province, intent))
                            seen.add(key)
                            logger.info(f"[SkillLoader] 发现仅存于 ES 的技能包: {key}")
            except Exception as e:
                logger.warning(f"[SkillLoader] ES 批量遍历失败（回退单查+本地目录）: {e}")
                es_bulk = None

        packages: Dict[str, SkillScenarioPackage] = {}
        for province, intent in keys:
            pkg = self.load_package(province, intent, es_bulk=es_bulk)
            if pkg and pkg.enabled:
                packages[pkg.key] = pkg

        # 按配置来源汇总，方便一眼确认生产是否真正走了 ES/Redis
        by_source: Dict[str, int] = {}
        for pkg in packages.values():
            src = pkg.meta.get("config_source", "unknown")
            by_source[src] = by_source.get(src, 0) + 1
        summary = ", ".join(f"{k}={v}" for k, v in sorted(by_source.items())) or "无"
        logger.info(f"[SkillLoader] 共加载 {len(packages)} 个技能包（配置来源: {summary}）")
        if not IS_DEV and by_source.get("local", 0) == len(packages) and packages:
            logger.warning(
                "[SkillLoader] ⚠️ 生产模式下所有技能包均来自本地文件——"
                "说明 ES 中尚无任何已发布配置（或 ES/Redis 不可用）。"
                "请检查启动日志中 [ESConfigStore] 的 index 状态，"
                "并确认是否已通过 SkillManager 页面保存/发布过配置。"
            )
        return packages

    # ── 外部存储加载 ───────────────────────────────────────────────

    @staticmethod
    def _load_config_from_external(
        province: str, intent: str
    ) -> tuple:
        """
        从 Redis / ES 加载配置，返回 (api_nodes, biz_config, source)。
        source ∈ "redis" / "es" / "local"（local 表示外部无数据，走本地 fallback）。
        development 模式：直接返回 ({}, {}, "local")，走本地文件 fallback。
        production 模式：Redis 热缓存 → ES published
        任意层失败直接返回空 {} 交给调用方 fallback 本地文件。
        """
        # development 模式：跳过外部存储，直接用本地文件
        if IS_DEV:
            return {}, {}, "local"

        try:
            from services.redis_config_bus import redis_config_bus
            from services.es_config_store import es_config_store

            # production 模式：Redis → ES published
            api_nodes  = redis_config_bus.get_config(province, intent, "api_nodes")
            biz_config = redis_config_bus.get_config(province, intent, "biz_config")

            if api_nodes is not None or biz_config is not None:
                logger.info(f"[SkillLoader] 从 Redis 缓存加载: {province}/{intent}")
                return api_nodes or {}, biz_config or {}, "redis"

            # Redis 未命中，去 ES
            api_nodes  = es_config_store.get_published(province, intent, "api_nodes")  or {}
            biz_config = es_config_store.get_published(province, intent, "biz_config") or {}

            if api_nodes or biz_config:
                logger.info(f"[SkillLoader] 从 ES published 加载: {province}/{intent}")
                # 回写 Redis 缓存
                if api_nodes:
                    redis_config_bus.set_config(province, intent, "api_nodes", api_nodes)
                if biz_config:
                    redis_config_bus.set_config(province, intent, "biz_config", biz_config)
                return api_nodes, biz_config, "es"

            # 外部存储均无该技能包数据（ES 尚未发布过），fallback 本地
            logger.info(
                f"[SkillLoader] Redis/ES 均无 {province}/{intent} 的已发布配置，"
                "fallback 本地文件（首次通过管理页保存/发布后才会写入 ES）"
            )
            return {}, {}, "local"

        except Exception as e:
            logger.warning(f"[SkillLoader] 外部存储加载失败，将 fallback 本地文件: {e}")
            return {}, {}, "local"

    # ── 工具方法 ──────────────────────────────────────────────────

    @staticmethod
    def _read_json(path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"[SkillLoader] 读取 {path} 失败: {e}")
            return None


# ══════════════════════════════════════════════════════════════
# Registry（单例）
# ══════════════════════════════════════════════════════════════

class SkillRuntimeRegistry:
    """技能包注册表（应用启动时初始化）"""

    _instance: Optional[SkillRuntimeRegistry] = None
    _packages: Dict[str, SkillScenarioPackage] = {}
    _loader: Optional[SkillRuntimeLoader] = None

    def __new__(cls) -> SkillRuntimeRegistry:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def initialize(self, root: Optional[Path] = None) -> None:
        self._loader = SkillRuntimeLoader(root)
        self._packages = self._loader.load_all()

    def get(self, province: str, intent: str) -> Optional[SkillScenarioPackage]:
        return self._packages.get(f"{province}:{intent}")

    def get_executor(self, province: str, intent: str) -> Optional[SkillExecutor]:
        pkg = self.get(province, intent)
        if pkg is None:
            return None
        return SkillExecutor(pkg)

    def remove(self, province: str, intent: str) -> bool:
        """从内存注册表移除技能包（删除 Skill 时调用），并失效对应 bundle 缓存。"""
        key = f"{province}:{intent}"
        if key not in self._packages:
            return False
        self._packages.pop(key, None)
        from core.pipeline import MarketingPipeline
        MarketingPipeline.invalidate_bundle(province)
        logger.info(f"[Registry] 已从内存移除技能包: {key}")
        return True

    def _confirmed_deleted_externally(self, province: str, intent: str) -> bool:
        """确认技能包已从 ES 真源删除（区别于 ES 暂时不可用）。

        仅当 ES 可用且 api_nodes / biz_config 均无 published 版本时返回 True，
        避免外部存储抖动时误删内存中的健康技能包。
        """
        if IS_DEV:
            return False
        try:
            from services.es_config_store import es_config_store
            if not es_config_store.enabled:
                return False
            for ct in ("api_nodes", "biz_config"):
                info = es_config_store.get_current_version_info(province, intent, ct)
                if info is None:            # 查询异常 → 视为不可判定，保守不删
                    return False
                if info.get("published_version"):
                    return False            # 仍有已发布配置
            return True
        except Exception:
            return False

    def reload(self, province: Optional[str] = None, intent: Optional[str] = None) -> None:
        """热重载（配置更新后调用）"""
        if self._loader is None:
            self._loader = SkillRuntimeLoader()
        if province and intent:
            key = f"{province}:{intent}"
            pkg = self._loader.load_package(province, intent)
            if pkg:
                self._packages[key] = pkg
                # 使 Pipeline 中对应 bundle 失效
                from core.pipeline import MarketingPipeline
                MarketingPipeline.invalidate_bundle(province)
                logger.info(f"[Registry] 热重载技能包: {key}")
            elif key in self._packages and self._confirmed_deleted_externally(province, intent):
                # 其他实例删除了该技能包（收到广播后 reload 加载不到且 ES 确认无数据）
                self._packages.pop(key, None)
                from core.pipeline import MarketingPipeline
                MarketingPipeline.invalidate_bundle(province)
                logger.info(f"[Registry] 技能包已在外部存储删除，同步移除内存包: {key}")
        else:
            self._packages = self._loader.load_all()
            from core.pipeline import MarketingPipeline
            MarketingPipeline.invalidate_bundle()
            logger.info("[Registry] 全量热重载完成")

    def list_all(self) -> List[Dict[str, Any]]:
        return [
            {
                "key":      pkg.key,
                "province": pkg.province,
                "intent":   pkg.intent,
                "version":  pkg.version,
                "enabled":  pkg.enabled,
                "meta":     pkg.meta,
            }
            for pkg in self._packages.values()
        ]

    def save_biz_config(
        self, province: str, intent: str, biz_config: Dict[str, Any],
        skip_reload: bool = False,
        operator: str = "system",
    ) -> bool:
        """保存 biz_config（保存即发布）。

        实际写入统一委托 services.skill_publisher.publish_config（唯一写路径）：
        ES 版本化 + 本地文件快照 + Redis 缓存/广播 + 本实例热更新。
        production 模式 ES 写入失败时返回 False（诚实失败），且不更新本实例内存
        （状态完全不变，用户重试不会产生重复模板）。
        skip_reload=True 供批量导入使用：跳过热重载与变更广播，调用方最后统一处理。
        """
        try:
            from services.skill_publisher import publish_config  # 延迟 import 避免循环依赖
            res = publish_config(
                province, intent, "biz_config", biz_config,
                operator=operator,
                reload=not skip_reload,
                broadcast=not skip_reload,
            )
            if res.success:
                # 仅发布成功才更新本实例内存（诚实失败 = 状态不变）
                pkg = self.get(province, intent)
                if pkg:
                    pkg.config["biz_config"] = biz_config
            else:
                logger.error(
                    f"[Registry] 保存 biz_config 失败({province}/{intent}): {res.message}"
                )
            return res.success
        except Exception as e:
            logger.error(f"[Registry] 保存 biz_config 失败({province}/{intent}): {e}")
            return False

    def save_api_nodes(
        self, province: str, intent: str, api_nodes: Dict[str, Any],
        operator: str = "system",
    ) -> bool:
        """保存 api_nodes（保存即发布）。逻辑同 save_biz_config，委托 skill_publisher。
        发布失败时不更新本实例内存（诚实失败 = 状态不变）。"""
        try:
            from services.skill_publisher import publish_config  # 延迟 import 避免循环依赖
            res = publish_config(
                province, intent, "api_nodes", api_nodes,
                operator=operator, reload=True,
            )
            if res.success:
                pkg = self.get(province, intent)
                if pkg:
                    pkg.config["api_nodes"] = api_nodes
            else:
                logger.error(
                    f"[Registry] 保存 api_nodes 失败({province}/{intent}): {res.message}"
                )
            return res.success
        except Exception as e:
            logger.error(f"[Registry] 保存 api_nodes 失败({province}/{intent}): {e}")
            return False

    # ── 回滚（production 跨实例通知）─────────────────────────────────

    def rollback_config(
        self,
        province: str,
        intent: str,
        config_type: str,
        target_version: int,
        operator: str = "system",
    ) -> tuple:
        """回滚到历史版本，委托 services.skill_publisher.rollback_config。"""
        from services.skill_publisher import rollback_config as publisher_rollback  # 延迟 import
        res = publisher_rollback(
            province, intent, config_type, target_version, operator=operator
        )
        return res.success, res.message

    # ── 多实例配置同步（production 实例启动时调用）──────────────────

    def start_config_sync(self, sync_cfg: Optional[Dict[str, Any]] = None) -> None:
        """
        启动多实例配置同步，development 模式跳过（本地文件直接读写，无需同步）。

        sync_cfg 取自 config/config.json 的 redis_bus 段，两个可选字段：
          sync_mode              pubsub | poll | hybrid（默认 hybrid）
            pubsub — 仅 Redis Pub/Sub 实时通知（原有唯一行为）
            poll   — 仅定时轮询 ES 版本号（不依赖 Redis 是否健康）
            hybrid — 两者并存：Pub/Sub 负责低延迟，轮询负责自愈，兜住 Pub/Sub 的
                     漏报窗口（Redis 重启/网络抖动期间发出的广播可能丢失）
          poll_interval_seconds  轮询间隔秒数（默认 300，仅 poll/hybrid 模式生效）

        province/intent 为 "*" 时触发全量 reload（Pub/Sub 断线重连补偿用；轮询路径
        只会精确上报发生变化的 province/intent，不会触发 "*"）。
        """
        if IS_DEV:
            logger.info("[Registry] 开发模式，跳过多实例配置同步")
            return

        sync_cfg = sync_cfg or {}
        mode = str(sync_cfg.get("sync_mode", "hybrid")).lower()
        if mode not in ("pubsub", "poll", "hybrid"):
            logger.warning(f"[Registry] 未知 sync_mode={mode!r}，回退为 hybrid")
            mode = "hybrid"
        poll_interval = int(sync_cfg.get("poll_interval_seconds", 300))

        def _on_change(province: str, intent: str) -> None:
            if province == "*" or intent == "*":
                # 全量 reload：断线重连后补偿遗漏的变更
                self.reload()
            else:
                logger.info(f"[Registry] 配置变更触发 reload: {province}/{intent}")
                self.reload(province, intent)

        if mode in ("pubsub", "hybrid"):
            from services.redis_config_bus import redis_config_bus
            redis_config_bus.start_subscriber(_on_change)

        if mode in ("poll", "hybrid"):
            from services.config_poller import config_poller
            config_poller.start(_on_change, interval_seconds=poll_interval)

        logger.info(
            f"[Registry] 多实例配置同步模式: {mode}"
            + (f"（轮询间隔 {poll_interval}s）" if mode != "pubsub" else "")
        )

    def start_config_subscriber(self) -> None:
        """兼容旧入口：等价于 start_config_sync(None)，即默认 hybrid 模式。"""
        self.start_config_sync(None)

    # ── 一次性数据迁移（本地文件 → ES）──────────────────────────────

    def migrate_local_to_es(self, operator: str = "migration") -> Dict[str, Any]:
        """
        将本地文件的配置迁移到 ES（首次部署时调用一次）。
        只迁移 ES 中尚未有 published 版本的技能包，不覆盖已有数据。
        返回迁移结果统计。
        """
        from services.es_config_store import es_config_store

        if not es_config_store.enabled:
            return {"skipped": "ES 不可用", "migrated": 0}

        loader = self._loader or SkillRuntimeLoader()
        migrated, skipped = [], []

        for pkg in self._packages.values():
            p, i = pkg.province, pkg.intent

            for ct in ("biz_config", "api_nodes"):
                existing = es_config_store.get_published(p, i, ct)
                if existing:
                    skipped.append(f"{p}/{i}/{ct}（已有 published 版本）")
                    continue

                local_data = pkg.config.get(ct, {})
                if not local_data:
                    skipped.append(f"{p}/{i}/{ct}（本地为空）")
                    continue

                # 直接 save_and_publish（无需草稿环节）
                ok, msg, _ = es_config_store.save_and_publish(
                    p, i, ct, local_data, operator=operator, comment="初始迁移"
                )
                if ok:
                    migrated.append(f"{p}/{i}/{ct}")
                    logger.info(f"[Registry] 迁移 {p}/{i}/{ct} → ES 成功")
                else:
                    skipped.append(f"{p}/{i}/{ct}（发布失败: {msg}）")

        return {"migrated": len(migrated), "skipped": len(skipped), "details": migrated}


    # ── 模板 CRUD（操作 script_templates_v2 列表）────────────────

    def get_all_templates(
        self,
        province: Optional[str] = None,
        intent:   Optional[str] = None,
        name:     Optional[str] = None,
        scene:    Optional[str] = None,
        stage:    Optional[str] = None,
        status:   Optional[str] = None,
        page:     int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """跨技能包聚合查询话术模板列表，支持过滤 + 分页。"""
        all_tpls: List[Dict[str, Any]] = []
        for pkg in self._packages.values():
            if province and pkg.province != province:
                continue
            if intent and pkg.intent != intent:
                continue
            for tpl in pkg.config.get("biz_config", {}).get("script_templates_v2", []):
                item = dict(tpl)
                # 以「所属技能包」为准，忽略模板内可能残留的旧 province/intent
                # （历史上从 beijing 复制/迁移的模板会带 province:"beijing" 等脏字段，
                #   若沿用会导致按 id 定位到不存在的技能包，删除/编辑落空）
                item["province"] = pkg.province
                item["intent"]   = pkg.intent
                all_tpls.append(item)

        if name:
            all_tpls = [t for t in all_tpls if name.lower() in t.get("template_name", "").lower()]
        if scene:
            all_tpls = [t for t in all_tpls if t.get("scene") == scene]
        if stage:
            all_tpls = [t for t in all_tpls if t.get("stage") == stage]
        if status:
            all_tpls = [t for t in all_tpls if t.get("status") == status]

        all_tpls.sort(key=lambda t: t.get("created_at", ""), reverse=True)

        total = len(all_tpls)
        start = (page - 1) * page_size
        return {
            "total":     total,
            "page":      page,
            "page_size": page_size,
            "items":     all_tpls[start: start + page_size],
        }

    def get_template_by_id(self, template_id: str) -> Optional[Dict[str, Any]]:
        """按 template_id 跨技能包查找单条模板。"""
        for pkg in self._packages.values():
            for tpl in pkg.config.get("biz_config", {}).get("script_templates_v2", []):
                if tpl.get("template_id") == template_id:
                    result = dict(tpl)
                    # 以「所属技能包」为准，忽略模板内残留的旧 province/intent 脏字段，
                    # 否则单删/批删/编辑会按脏 province 定位到不存在的技能包而落空（删除 0 条）
                    result["province"] = pkg.province
                    result["intent"]   = pkg.intent
                    return result
        return None

    def upsert_template(
        self,
        province: str,
        intent:   str,
        template_data: Dict[str, Any],
        skip_reload: bool = False,
    ) -> Dict[str, Any]:
        """新建或更新话术模板，返回最终模板数据（含 template_id）。

        - template_data 含 template_id → 更新已有模板
        - template_data 不含 template_id → 自动生成 ID，新建
        - skip_reload=True 跳过热重载，供批量导入使用（导入完成后统一 reload 一次）
        """
        import uuid as _uuid
        import time as _time
        from datetime import datetime as _dt

        pkg = self.get(province, intent)
        if pkg is None:
            raise ValueError(f"技能包不存在: {province}:{intent}")

        biz_cfg   = dict(pkg.config.get("biz_config", {}))
        templates = list(biz_cfg.get("script_templates_v2", []))

        tid = template_data.get("template_id")
        if tid:
            # 更新
            for i, t in enumerate(templates):
                if t.get("template_id") == tid:
                    merged = {**t, **template_data}
                    templates[i] = merged
                    break
            else:
                templates.append(template_data)
        else:
            # 新建
            template_data = dict(template_data)
            template_data["template_id"] = (
                f"tpl_{province}_{int(_time.time()*1000)}_{_uuid.uuid4().hex[:6]}"
            )
            template_data.setdefault(
                "created_at", _dt.now().strftime("%Y-%m-%d %H:%M:%S")
            )
            templates.append(template_data)

        biz_cfg["script_templates_v2"] = templates
        ok = self.save_biz_config(province, intent, biz_cfg, skip_reload=skip_reload)
        if not ok:
            raise RuntimeError("保存 biz_config 失败")
        # 无论是否 skip_reload，都立即更新内存中的 biz_config，
        # 避免批量写入时后续行读到过时数据导致互相覆盖
        pkg.config["biz_config"] = biz_cfg
        return template_data

    def bulk_upsert_templates(
        self,
        province: str,
        intent:   str,
        templates_data: List[Dict[str, Any]],
        skip_reload: bool = True,
    ) -> List[Dict[str, Any]]:
        """批量新建/更新话术模板：将多条模板一次性合并进 biz_config，**只触发一次**
        ES 写入 + skill_meta 刷新（可选热重载/广播）。

        相比逐条 upsert_template（每条一次 publish_config，即一次全量 biz_config
        ES 版本化写入 + skill_meta 写入），批量导入 N 条时能把 2N 次外部存储写入
        压缩为 2 次，彻底消除「导入话术模板一直循环刷版本号」的问题，且避免
        biz_config 逐行累加导致的 O(N²) 写放大。

        - templates_data 中含 template_id 且已存在 → 覆盖合并；否则新建（自动生成 ID）
        - 返回写入后的模板列表（含 template_id）
        """
        import uuid as _uuid
        import time as _time
        from datetime import datetime as _dt

        pkg = self.get(province, intent)
        if pkg is None:
            raise ValueError(f"技能包不存在: {province}:{intent}")

        if not templates_data:
            return []

        biz_cfg   = dict(pkg.config.get("biz_config", {}))
        templates = list(biz_cfg.get("script_templates_v2", []))
        id_index  = {
            t.get("template_id"): i
            for i, t in enumerate(templates) if t.get("template_id")
        }

        saved: List[Dict[str, Any]] = []
        for raw in templates_data:
            data = dict(raw)
            tid = data.get("template_id")
            if tid and tid in id_index:
                idx = id_index[tid]
                templates[idx] = {**templates[idx], **data}
                saved.append(templates[idx])
                continue
            data["template_id"] = (
                tid or f"tpl_{province}_{int(_time.time()*1000)}_{_uuid.uuid4().hex[:6]}"
            )
            data.setdefault("created_at", _dt.now().strftime("%Y-%m-%d %H:%M:%S"))
            templates.append(data)
            id_index[data["template_id"]] = len(templates) - 1
            saved.append(data)

        biz_cfg["script_templates_v2"] = templates
        ok = self.save_biz_config(province, intent, biz_cfg, skip_reload=skip_reload)
        if not ok:
            raise RuntimeError("保存 biz_config 失败")
        # 发布成功后同步内存，供后续读取拿到最新数据
        pkg.config["biz_config"] = biz_cfg
        return saved

    def delete_template(
        self,
        province:    str,
        intent:      str,
        template_id: str,
    ) -> bool:
        """删除话术模板，返回 True 表示成功，False 表示模板不存在。"""
        pkg = self.get(province, intent)
        if pkg is None:
            return False

        biz_cfg   = dict(pkg.config.get("biz_config", {}))
        templates = biz_cfg.get("script_templates_v2", [])
        new_list  = [t for t in templates if t.get("template_id") != template_id]

        if len(new_list) == len(templates):
            return False   # 未找到

        biz_cfg["script_templates_v2"] = new_list
        return self.save_biz_config(province, intent, biz_cfg)

    def delete_templates(
        self,
        province:     str,
        intent:       str,
        template_ids: List[str],
    ) -> int:
        """批量删除话术模板：一次性移除多个 template_id，只触发**一次** ES 写入/热重载。

        返回实际删除的条数。若底层保存失败抛 RuntimeError（诚实失败，状态不变）。
        相比逐条 delete_template（每条一次 publish_config），大幅降低 ES 写入次数，
        既是批量删除能力，也显著减少生产环境「外部存储不可用」的失败面。
        """
        pkg = self.get(province, intent)
        if pkg is None:
            return 0

        ids = {t for t in (template_ids or []) if t}
        if not ids:
            return 0

        biz_cfg   = dict(pkg.config.get("biz_config", {}))
        templates = biz_cfg.get("script_templates_v2", [])
        new_list  = [t for t in templates if t.get("template_id") not in ids]
        removed   = len(templates) - len(new_list)

        if removed == 0:
            return 0   # 无匹配，无需写入

        biz_cfg["script_templates_v2"] = new_list
        ok = self.save_biz_config(province, intent, biz_cfg)
        if not ok:
            raise RuntimeError("保存 biz_config 失败")
        return removed

    def get_available_apis(self, province: str, intent: str) -> List[str]:
        """返回技能包 api_nodes.json 中已配置的接口名称列表（供模板关联接口下拉选择）。"""
        pkg = self.get(province, intent)
        if pkg is None:
            return []
        api_nodes = pkg.config.get("api_nodes", {})
        return list(api_nodes.keys()) if isinstance(api_nodes, dict) else []


# 全局单例
skill_registry = SkillRuntimeRegistry()


# ══════════════════════════════════════════════════════════════
# Executor
# ══════════════════════════════════════════════════════════════

class SkillExecutor:
    """技能执行器：根据技能包配置执行完整推荐流程"""

    def __init__(self, package: SkillScenarioPackage) -> None:
        self.package = package

    async def execute(self, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """执行技能包流程（统一走主流程三步管道，省份差异通过配置文件表达）"""
        from core.context import FlowContext
        from core.pipeline import MarketingPipeline
        import uuid, time

        trace_id = (
            request_data.get("callId")
            or request_data.get("trace_id")
            or f"{self.package.province}-{int(time.time()*1000)}-{uuid.uuid4().hex[:6]}"
        )

        ctx = FlowContext(
            phone=request_data.get("phone", ""),
            intent=request_data.get("intent", self.package.intent),
            province=self.package.province,
            top_n=int(request_data.get("topN", request_data.get("top_n", 3))),
            trace_id=trace_id,
            extra_data=request_data.get("extra_data") or {},
            extra_info=request_data.get("extra_info") or {},
            extra_context=request_data.get("extra_context") or {},
            batch_contexts=request_data.get("batch_contexts") or [],
        )
        result = await MarketingPipeline().execute(ctx, skill_config=self.package.config)

        # other_info：透传原始接口响应（bean）。
        #   - biz_config.strategy.expose_raw_bean 显式配置时以其为准（true 开 / false 关）；
        #   - 未配置（None）时按「接口查询模式」自动判定：只要技能包存在启用的 source_type=api
        #     节点且拿到了原始响应，就透传，与 znhs_xy_old 接口查询模式返回保持一致；
        #     纯直传（direct）技能无 api 节点则保持 other_info=None，行为不变。
        strategy = self.package.config.get("biz_config", {}).get("strategy", {})
        expose = strategy.get("expose_raw_bean")
        api_nodes = self.package.config.get("api_nodes", {}) or {}
        # 启用的接口查询（source_type=api）节点名，按配置顺序
        api_node_names = [
            name for name, cfg in api_nodes.items()
            if isinstance(cfg, dict)
            and not str(name).startswith("_")
            and cfg.get("enabled", True)
            and cfg.get("source_type", "api") == "api"
        ]
        if expose is None:
            expose = bool(api_node_names)
        if expose:
            # 选取透传源：优先 api 节点中第一个非空原始响应（跳过直传/失败节点的空响应），
            # 再兜底任意非空响应，避免多节点场景下第一个空节点把 other_info 挤成 {}。
            first_raw: Any = {}
            for name in api_node_names:
                r = ctx.raw_responses.get(name)
                if r:
                    first_raw = r
                    break
            if not first_raw:
                first_raw = next((r for r in ctx.raw_responses.values() if r), {})
            result["other_info"] = (
                first_raw.get("bean", first_raw)
                if isinstance(first_raw, dict)
                else first_raw
            )
            logger.info(
                f"[SkillRuntime] other_info 透传：expose={expose} "
                f"api_nodes={api_node_names} "
                f"raw_nodes={ {k: bool(v) for k, v in ctx.raw_responses.items()} } "
                f"has_other_info={result.get('other_info') not in (None, {}, [])}"
            )

        return result
