"""
技能包配置统一发布器 —— 系统中唯一允许写配置的模块。

发布 = 本地文件（真源快照）+ ES 版本化存储 + Redis 缓存 + 变更广播 + 内存热更新。
所有写入口（management 路由、AutoConfigAgent 路由、config_agent proposal apply）一律经此。

模式差异（utils.skill_runtime.IS_DEV）：
  development：只写本地文件 + 本实例内存热更新（跳过 ES/Redis）
  production ：先 ES save_and_publish（ES 失败 => 立即整体失败，不写本地文件、不写 Redis，
               诚实失败，因为生产运行态读取以 ES 为准，本地文件不生效）
               成功后再写本地文件快照（失败只记 warnings）
               + Redis 缓存/广播（仅缓存层，失败只记 warnings）+ 内存热更新

实现约定：
  - 对 skill_runtime / es_config_store / redis_config_bus 一律方法内延迟 import，化解循环依赖
  - 本地文件写入为原子写：先写 .tmp 再 os.replace
  - 目录名可能含非 ASCII 字符，统一 pathlib + encoding="utf-8" + ensure_ascii=False
  - province/intent/config_type 入口做单一路径段校验，防路径穿越
  - 变更广播由 publisher 统一发出（ES 内部 notify 关闭），broadcast=False 可整体跳过
    （批量导入场景由调用方最后统一广播一次，避免广播风暴）
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

# publisher 只负责这两类配置；_meta.json 等杂项仍由各自调用方直接落盘
ALLOWED_CONFIG_TYPES = ("biz_config", "api_nodes")


@dataclass
class PublishResult:
    """一次配置发布的结果明细"""
    success: bool
    message: str
    version: Optional[int] = None        # ES 新版本号（ES 未写入为 None）
    file_written: bool = False
    es_written: bool = False
    redis_written: bool = False
    warnings: List[str] = field(default_factory=list)


# ── 内部工具 ──────────────────────────────────────────────────

def _is_safe_segment(name: Any) -> bool:
    """校验单一安全路径段：非空字符串、不含 / \\ 与 ..、不以 . 开头（防路径穿越）。"""
    if not isinstance(name, str) or not name.strip():
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    if name.startswith("."):
        return False
    return True


def _skills_root() -> Path:
    """skills-runtime 根目录解析（与 AutoConfigAgent/server.py 保持一致）：
    优先环境变量 SKILLS_RUNTIME_PATH，否则用 SkillRuntimeLoader.SKILLS_ROOT。"""
    env_path = os.environ.get("SKILLS_RUNTIME_PATH", "").strip()
    if env_path:
        return Path(env_path)
    from utils.skill_runtime import SkillRuntimeLoader  # 延迟 import
    return SkillRuntimeLoader.SKILLS_ROOT


def _config_file_path(province: str, intent: str, config_type: str) -> Path:
    """本地配置文件路径：skills-runtime/{province}/{intent}/config/{config_type}.json"""
    return _skills_root() / province / intent / "config" / f"{config_type}.json"


def _write_local_file(province: str, intent: str, config_type: str,
                      data: Dict[str, Any]) -> Tuple[bool, str]:
    """原子写本地配置文件：先写同目录 .tmp 再 os.replace，避免半写状态。"""
    path = _config_file_path(province, intent, config_type)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        logger.info(f"[SkillPublisher] 已写本地配置文件: {path}")
        return True, ""
    except Exception as e:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        return False, f"写本地文件失败({path}): {e}"


def _reload_registry(province: str, intent: str, result: PublishResult) -> None:
    """本实例内存热更新，失败只记 warnings（不影响发布结论）。"""
    try:
        from utils.skill_runtime import skill_registry  # 延迟 import
        skill_registry.reload(province, intent)
    except Exception as e:
        result.warnings.append(f"本实例热更新失败: {e}")
        logger.warning(f"[SkillPublisher] {province}/{intent} 热更新失败: {e}")


# ── 公开接口 ──────────────────────────────────────────────────

def publish_config(
    province: str,
    intent: str,
    config_type: str,
    data: Dict[str, Any],
    operator: str = "system",
    comment: str = "",
    reload: bool = True,
    broadcast: bool = True,
) -> PublishResult:
    """发布单个配置（biz_config / api_nodes），唯一写路径。

    production 模式顺序：
      1) es_config_store.save_and_publish（失败 => 立即 success=False，不写本地文件、不写 Redis）
      2) 写本地文件快照（原子写，失败记 warnings，不翻转 success）
      3) redis_config_bus.set_config（失败记 warnings）+ 变更广播（broadcast=True 时）
      4) reload=True 时 skill_registry.reload(province, intent) 更新本实例内存
    dev 模式只做：写本地文件（失败即发布失败）+ 4)。

    broadcast=False：跳过全部变更广播（ES 内部 notify 与 publisher 自身 publish_change），
    供批量导入场景使用（调用方最后统一广播一次，避免广播风暴）。
    """
    from utils.skill_runtime import IS_DEV  # 延迟 import，化解循环依赖

    # 入口路径段校验（防路径穿越）
    if not (_is_safe_segment(province) and _is_safe_segment(intent)
            and _is_safe_segment(config_type)):
        return PublishResult(False, "非法的省份/意图标识")
    if config_type not in ALLOWED_CONFIG_TYPES:
        return PublishResult(False, f"不支持的配置类型: {config_type}")
    if not isinstance(data, dict):
        return PublishResult(False, "配置数据必须是 JSON 对象")

    # ── api_nodes 重命名字段名规范化守护（唯一写路径，覆盖全部保存入口）──
    # field_rename / _unit_conversions.new_field 若含畸形括号（双括号、全/半角混用），
    # 运行时数据键将与话术模板子字段占位符无法同名对齐 → 槽位取不到值。
    result = PublishResult(True, "")

    if config_type == "api_nodes":
        from utils.field_naming import normalize_api_nodes_renames  # 延迟 import

        _renames_fixed = normalize_api_nodes_renames(data)
        if _renames_fixed:
            logger.warning(
                f"[publish_config] {province}/{intent} api_nodes 重命名目标字段名已规范化: "
                f"{_renames_fixed}"
            )
        # 保存时配置巡检（纯内存 lint，不阻断保存）：E201「from 槽位不存在」等问题
        # 意味着运行时对应映射域将静默为空（话术缺历史用量/标签的根因形态），
        # 在写入时就暴露给保存者与日志，而不是等生产话术出错才发现。
        try:
            from management.config_agent.linter import lint_api_nodes  # 延迟 import

            _lint = lint_api_nodes(data, province, intent)
            for _issue in _lint.get("errors", []) + _lint.get("warnings", []):
                _msg = (f"[配置巡检 {_issue.get('code')}] {_issue.get('path')}: "
                        f"{_issue.get('message')}")
                result.warnings.append(_msg)
            if _lint.get("errors"):
                logger.warning(
                    f"[publish_config] {province}/{intent} api_nodes 保存时巡检发现 "
                    f"{len(_lint['errors'])} 个错误级问题（已保存，但运行时对应映射域将为空）: "
                    f"{[i.get('message') for i in _lint['errors']]}"
                )
        except Exception as _lint_exc:  # noqa: BLE001 - 巡检失败不影响保存
            logger.debug(f"[publish_config] 保存时 lint 跳过: {_lint_exc}")

    if IS_DEV:
        # dev 模式：本地文件是唯一存储，写失败即发布失败
        ok_file, err = _write_local_file(province, intent, config_type, data)
        result.file_written = ok_file
        if not ok_file:
            result.success = False
            result.message = err
            logger.error(f"[SkillPublisher] {err}")
            return result
    else:
        # 1) 先 ES 版本化发布（生产真源）：失败 => 诚实失败，本地文件与 Redis 一律不写，
        #    保证失败后系统状态完全不变，用户重试不会产生半发布残留
        try:
            from services.es_config_store import es_config_store  # 延迟 import
            ok_es, msg, new_v = es_config_store.save_and_publish(
                province, intent, config_type, data,
                operator=operator, comment=comment,
                notify=False,  # 广播统一由 publisher 在 Redis 缓存更新后发出，避免重复广播
            )
        except Exception as e:
            ok_es, msg, new_v = False, f"ES 调用异常: {e}", 0
        if not ok_es:
            result.success = False
            result.message = f"发布失败（生产读取以 ES 为准，ES 写入未成功）: {msg}"
            logger.error(
                f"[SkillPublisher] {province}/{intent}/{config_type} ES 发布失败: {msg}"
            )
            return result
        result.es_written = True
        result.version = new_v

        # 2) ES 成功后写本地文件快照（生产读取以 ES 为准，文件失败仅降级为警告）
        ok_file, err = _write_local_file(province, intent, config_type, data)
        result.file_written = ok_file
        if not ok_file:
            result.warnings.append(err)
            logger.warning(f"[SkillPublisher] {err}")

        # 3) Redis 缓存 + 变更广播（失败记 warnings，ES 已成功则其他实例可从 ES 读到）
        try:
            from services.redis_config_bus import redis_config_bus  # 延迟 import
            if redis_config_bus.enabled:
                result.redis_written = bool(
                    redis_config_bus.set_config(province, intent, config_type, data)
                )
                if broadcast:
                    redis_config_bus.publish_change(province, intent)
            else:
                result.warnings.append("Redis 未启用，跳过缓存与广播")
        except Exception as e:
            result.warnings.append(f"Redis 缓存/广播失败: {e}")
            logger.warning(
                f"[SkillPublisher] {province}/{intent}/{config_type} Redis 缓存/广播失败: {e}"
            )

        # 3.5) 同步刷新 skill 结构化信息（skill_meta → ES/Redis，best-effort，
        #      保证 SkillManager 列表展示的名称/时间等与最新发布一致）
        try:
            from services.skill_meta_service import upsert_skill_meta  # 延迟 import
            if not upsert_skill_meta(province, intent, operator=operator):
                result.warnings.append("skill_meta 刷新未成功（不影响本次配置发布）")
        except Exception as e:
            result.warnings.append(f"skill_meta 刷新失败: {e}")

    # 4) 本实例内存热更新
    if reload:
        _reload_registry(province, intent, result)

    if result.es_written:
        result.message = f"发布成功，版本 v{result.version}"
    else:
        result.message = "发布成功（development 模式，仅本地文件）"
    return result


def publish_package(
    province: str,
    intent: str,
    api_nodes: Optional[Dict[str, Any]] = None,
    biz_config: Optional[Dict[str, Any]] = None,
    operator: str = "system",
    comment: str = "",
    reload: bool = True,
    broadcast: bool = True,
) -> Dict[str, PublishResult]:
    """批量发布技能包配置：仅发布非 None 的部分，最后统一 reload 一次。"""
    results: Dict[str, PublishResult] = {}
    if api_nodes is not None:
        results["api_nodes"] = publish_config(
            province, intent, "api_nodes", api_nodes,
            operator=operator, comment=comment, reload=False, broadcast=broadcast,
        )
    if biz_config is not None:
        results["biz_config"] = publish_config(
            province, intent, "biz_config", biz_config,
            operator=operator, comment=comment, reload=False, broadcast=broadcast,
        )
    if reload and any(r.success for r in results.values()):
        # 只 reload 一次，warnings 记到每个成功项上
        probe = PublishResult(True, "")
        _reload_registry(province, intent, probe)
        if probe.warnings:
            for r in results.values():
                if r.success:
                    r.warnings.extend(probe.warnings)
    return results


def read_local_config(
    province: str, intent: str, config_type: str
) -> Optional[Dict[str, Any]]:
    """读取本地技能包配置文件（skills-runtime/{province}/{intent}/config/{type}.json）。

    不存在 / 非法路径段 / 非允许类型 / 解析失败 一律返回 None（调用方据此判断）。
    """
    if not (_is_safe_segment(province) and _is_safe_segment(intent)):
        return None
    if config_type not in ALLOWED_CONFIG_TYPES:
        return None
    path = _config_file_path(province, intent, config_type)
    try:
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning(f"[SkillPublisher] 读取本地配置失败 {path}: {e}")
        return None


def republish_local(
    province: str,
    intent: str,
    config_types: Tuple[str, ...] = ("api_nodes",),
    operator: str = "system",
    comment: str = "",
) -> Dict[str, PublishResult]:
    """把本地技能包配置文件整包重新发布到 ES（生产事故恢复专用）。

    典型场景：生产 ES 里某省 skill 的 api_nodes 被误改/漏映射（如北京 raw_tags
    中间槽位丢失，导致 usage/tags 静默为空），而本地部署包里的
    skills-runtime/{province}/{intent}/config/api_nodes.json 仍是正确标准配置——
    直接读它整包覆盖发布到 ES，一键幂等恢复，无需人工写 ES。

    仅发布本地存在且可解析的 config_type；返回 {config_type: PublishResult}，
    最后统一 reload 一次本实例内存。
    """
    results: Dict[str, PublishResult] = {}
    for ct in config_types:
        data = read_local_config(province, intent, ct)
        if data is None:
            results[ct] = PublishResult(False, f"本地配置文件不存在或无法解析: {ct}")
            logger.warning(
                f"[SkillPublisher] republish_local 跳过 {province}/{intent}/{ct}: "
                f"本地文件不存在或无法解析"
            )
            continue
        results[ct] = publish_config(
            province, intent, ct, data,
            operator=operator,
            comment=comment or f"从本地标准配置重新发布({ct})",
            reload=False, broadcast=True,
        )
    if any(r.success for r in results.values()):
        probe = PublishResult(True, "")
        _reload_registry(province, intent, probe)
        if probe.warnings:
            for r in results.values():
                if r.success:
                    r.warnings.extend(probe.warnings)
    return results


def rollback_config(
    province: str,
    intent: str,
    config_type: str,
    target_version: int,
    operator: str = "system",
) -> PublishResult:
    """回滚到 ES 历史版本，并把回滚后的数据同步到本地文件 / Redis / 本实例内存。

    顺序：es_config_store.rollback → get_published 取数据 → 写本地文件
          → Redis set + 广播 → skill_registry.reload。
    """
    # 入口路径段校验（防路径穿越，与 publish_config 同一规则）
    if not (_is_safe_segment(province) and _is_safe_segment(intent)
            and _is_safe_segment(config_type)):
        return PublishResult(False, "非法的省份/意图标识")

    try:
        from services.es_config_store import es_config_store  # 延迟 import
        ok, msg = es_config_store.rollback(
            province, intent, config_type, int(target_version), operator=operator
        )
    except Exception as e:
        ok, msg = False, f"ES 回滚异常: {e}"
    if not ok:
        return PublishResult(False, msg, version=target_version)

    result = PublishResult(True, msg, version=int(target_version), es_written=True)

    # 取回滚后的已发布数据，同步本地文件
    data: Optional[Dict[str, Any]] = None
    try:
        from services.es_config_store import es_config_store
        data = es_config_store.get_published(province, intent, config_type)
    except Exception as e:
        result.warnings.append(f"读取回滚后配置失败: {e}")

    if isinstance(data, dict) and data:
        ok_file, err = _write_local_file(province, intent, config_type, data)
        result.file_written = ok_file
        if not ok_file:
            result.warnings.append(err)
    else:
        result.warnings.append("回滚成功但未读取到已发布配置，本地文件与 Redis 缓存未同步")

    # Redis 缓存刷新 + 变更广播（失败只记 warnings）
    try:
        from services.redis_config_bus import redis_config_bus  # 延迟 import
        if redis_config_bus.enabled:
            if isinstance(data, dict) and data:
                redis_config_bus.delete_config(province, intent, config_type)
                result.redis_written = bool(
                    redis_config_bus.set_config(province, intent, config_type, data)
                )
            redis_config_bus.publish_change(province, intent)
    except Exception as e:
        result.warnings.append(f"Redis 缓存/广播失败: {e}")
        logger.warning(
            f"[SkillPublisher] 回滚后 Redis 同步失败 {province}/{intent}/{config_type}: {e}"
        )

    # 本实例内存热更新
    _reload_registry(province, intent, result)
    return result
