"""
Skill 结构化元信息（skill_meta）服务

背景：
  ES 中原本只存 api_nodes / biz_config 两类配置内容，skill 的结构化信息
  （名称/状态/创建人/时间等）只在本地 _meta.json，导致 SkillManager 列表
  在纯 ES 环境下拿不到完整 skill 信息。

本服务引入第三种 config_type = "skill_meta"，复用 es_config_store 的
版本化存储（configs/meta 两个 index，不新建 index）：
  - build_skill_meta   : 由本地 _meta.json + ES/生效配置推导出结构化信息
  - sync_skill_meta_to_es : 存量迁移——扫描 ES 中已有 api_nodes/biz_config
    的 (province, intent)，为缺失 skill_meta 的自动生成并写入 ES
  - get_skill_meta     : Redis → ES → 本地 _meta.json 三级读取
  - upsert_skill_meta  : 发布配置后刷新 skill_meta（updated_at 等）

文档 _id 沿用既有规则：{province}:{intent}:skill_meta:{version}
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

CONFIG_TYPE_META = "skill_meta"


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _skills_root() -> Path:
    env_path = os.environ.get("SKILLS_RUNTIME_PATH", "").strip()
    if env_path:
        return Path(env_path)
    from utils.skill_runtime import SkillRuntimeLoader  # 延迟 import
    return SkillRuntimeLoader.SKILLS_ROOT


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning(f"[SkillMeta] 读取 {path} 失败: {e}")
    return None


# ── 构建 ─────────────────────────────────────────────────────

def build_skill_meta(
    province: str,
    intent: str,
    api_nodes: Optional[Dict[str, Any]] = None,
    biz_config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """构建 skill 结构化信息。

    优先级：本地 _meta.json 字段 > ES 已有 skill_meta > 由配置内容推导 > 默认值。
    api_nodes / biz_config 传入 ES/生效配置用于统计（不传则读本地文件）。

    ES 兜底层解决「仅存于 ES 的技能包」（容器内无本地目录）刷新 skill_meta 时，
    名称/状态/创建时间等身份字段被默认值覆盖的问题——例如下线后一次配置发布
    把 status 打回 published。
    """
    base = _skills_root() / province / intent
    local_meta = _read_json(base / "_meta.json") or {}
    manifest = _read_json(_skills_root() / province / "manifest.json") or {}

    # ES 中已有的 skill_meta 作为身份字段兜底（本地 _meta.json 缺失/缺字段时）
    es_meta: Dict[str, Any] = {}
    try:
        es_meta = get_skill_meta(province, intent) or {}
    except Exception:
        es_meta = {}
    if es_meta:
        # 本地字段优先，ES 已有值兜底
        merged = dict(es_meta)
        merged.update({k: v for k, v in local_meta.items() if v not in (None, "")})
        local_meta = merged
        # ES 的 skill_name 键与本地 name 键对齐
        if not local_meta.get("name") and es_meta.get("skill_name"):
            local_meta["name"] = es_meta["skill_name"]

    if api_nodes is None:
        api_nodes = _read_json(base / "config" / "api_nodes.json") or {}
    if biz_config is None:
        biz_config = _read_json(base / "config" / "biz_config.json") or {}

    province_name = manifest.get("province_name", province)

    api_node_count = sum(
        1 for c in api_nodes.values()
        if isinstance(c, dict) and c.get("enabled", True)
    )
    mock_mode = any(
        c.get("mock_mode") for c in api_nodes.values() if isinstance(c, dict)
    )
    template_count = len(biz_config.get("script_templates_v2") or [])
    top_n = (biz_config.get("strategy") or {}).get("top_n", 0)

    return {
        "skill_id":       local_meta.get("skill_id") or f"{province}-{intent}",
        "skill_name":     local_meta.get("name") or f"{province_name} · {intent}",
        "province":       province,
        "province_name":  province_name,
        "intent":         intent,
        "version":        local_meta.get("version", "1.0.0"),
        "status":         local_meta.get("status", "published"),
        "description":    local_meta.get("description", ""),
        "author":         local_meta.get("author", ""),
        "created_by":     local_meta.get("created_by", ""),
        "created_at":     local_meta.get("created_at", _now()),
        "updated_at":     _now(),
        "has_api_nodes":  bool(api_nodes),
        "has_biz_config": bool(biz_config),
        "api_node_count": api_node_count,
        "mock_mode":      mock_mode,
        "template_count": template_count,
        "top_n":          top_n,
        "entry_script":   local_meta.get("entry_script", ""),
        "required_plugins": local_meta.get("required_plugins", []),
    }


# ── 读取（Redis → ES → 本地）──────────────────────────────────

def get_skill_meta(province: str, intent: str) -> Optional[Dict[str, Any]]:
    """读取 skill_meta：Redis 缓存 → ES published → None（调用方可 fallback 本地）。"""
    try:
        from services.redis_config_bus import redis_config_bus
        if redis_config_bus.enabled:
            cached = redis_config_bus.get_config(province, intent, CONFIG_TYPE_META)
            if cached:
                return cached
    except Exception:
        pass
    try:
        from services.es_config_store import es_config_store
        if es_config_store.enabled:
            data = es_config_store.get_published(province, intent, CONFIG_TYPE_META)
            if data:
                # 回写 Redis 缓存
                try:
                    from services.redis_config_bus import redis_config_bus
                    if redis_config_bus.enabled:
                        redis_config_bus.set_config(province, intent, CONFIG_TYPE_META, data)
                except Exception:
                    pass
                return data
    except Exception as e:
        logger.warning(f"[SkillMeta] 读取 ES skill_meta 失败 {province}/{intent}: {e}")
    return None


# ── 写入 / 刷新 ───────────────────────────────────────────────

def upsert_skill_meta(
    province: str,
    intent: str,
    operator: str = "system",
    api_nodes: Optional[Dict[str, Any]] = None,
    biz_config: Optional[Dict[str, Any]] = None,
    overrides: Optional[Dict[str, Any]] = None,
) -> bool:
    """构建并写入 skill_meta 到 ES + Redis（best-effort，失败不抛异常）。

    overrides：写入前强制覆盖的字段（如状态流转 {"status": "offline"}），
    优先级最高，供「下线/重新发布」等状态操作把变更持久化到 ES 真源。
    """
    try:
        from services.es_config_store import es_config_store
        if not es_config_store.enabled:
            return False
        meta = build_skill_meta(province, intent, api_nodes=api_nodes, biz_config=biz_config)
        if overrides:
            meta.update(overrides)
        ok, msg, _v = es_config_store.save_and_publish(
            province, intent, CONFIG_TYPE_META, meta,
            operator=operator, comment="skill_meta upsert", notify=False,
        )
        if not ok:
            logger.warning(f"[SkillMeta] 写入 ES 失败 {province}/{intent}: {msg}")
            return False
        try:
            from services.redis_config_bus import redis_config_bus
            if redis_config_bus.enabled:
                redis_config_bus.set_config(province, intent, CONFIG_TYPE_META, meta)
        except Exception:
            pass
        logger.info(f"[SkillMeta] 已写入 skill_meta: {province}/{intent}")
        return True
    except Exception as e:
        logger.warning(f"[SkillMeta] upsert 失败 {province}/{intent}: {e}")
        return False


# ── 存量迁移 ─────────────────────────────────────────────────

def sync_skill_meta_to_es(force: bool = False) -> Dict[str, Any]:
    """存量迁移：为 ES 中已有 api_nodes/biz_config 但缺 skill_meta 的技能包
    自动生成结构化信息并写入 ES。

    - 以 ES 中的现有配置为基准（用户要求：基于 ES 存量建立关联）
    - 同时兜底覆盖本地存在但 ES 缺配置的技能包（仅当 ES 中已有任一配置时才写，
      避免给纯本地技能包造出"ES 已发布"的假象）
    - force=True 时对已存在的 skill_meta 也重建（用于字段结构升级）

    返回迁移摘要，供管理端点直接展示。
    """
    summary: Dict[str, Any] = {"created": [], "skipped": [], "failed": [], "es_enabled": False}
    try:
        from utils.skill_runtime import IS_DEV
        from services.es_config_store import es_config_store
        if IS_DEV:
            summary["message"] = "development 模式跳过（本地文件为唯一配置源）"
            return summary
        if not es_config_store.enabled:
            summary["message"] = "ES 未启用，无法迁移"
            return summary
        summary["es_enabled"] = True

        all_es = es_config_store.load_all_published()  # {"prov:intent": {config_type: data}}
        logger.info(f"[SkillMeta] 存量迁移开始：ES 中共有 {len(all_es)} 个技能包的配置")

        for key, cfgs in all_es.items():
            if ":" not in key:
                continue
            province, intent = key.split(":", 1)
            has_cfg = ("api_nodes" in cfgs) or ("biz_config" in cfgs)
            if not has_cfg:
                summary["skipped"].append({"key": key, "reason": "无 api_nodes/biz_config"})
                continue
            if CONFIG_TYPE_META in cfgs and not force:
                summary["skipped"].append({"key": key, "reason": "skill_meta 已存在"})
                continue
            ok = upsert_skill_meta(
                province, intent, operator="auto-migration",
                api_nodes=cfgs.get("api_nodes"), biz_config=cfgs.get("biz_config"),
            )
            (summary["created"] if ok else summary["failed"]).append({"key": key})

        logger.info(
            f"[SkillMeta] 存量迁移完成：新建 {len(summary['created'])}，"
            f"跳过 {len(summary['skipped'])}，失败 {len(summary['failed'])}"
        )
        summary["message"] = (
            f"新建 {len(summary['created'])}，跳过 {len(summary['skipped'])}，"
            f"失败 {len(summary['failed'])}"
        )
        return summary
    except Exception as e:
        logger.warning(f"[SkillMeta] 存量迁移异常: {e}")
        summary["message"] = f"迁移异常: {e}"
        return summary
