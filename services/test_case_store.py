"""
Skill 测试用例存储（与 skill 配置关联）

设计：
- 生产/灰度：复用 es_config_store 的版本化存储，新增 config_type="test_cases"
  （沿用既有 configs/meta 两个索引，不新建索引、不污染 biz_config 版本），
  跨用户/实例共享，真正与 skill 配置关联。
- 开发态（IS_DEV）或 ES 不可用：兜底写本地文件 config/test_cases.json。

数据结构：{"cases": [ {"name": str, "payload": {...完整请求体...}, "updated_at": str}, ... ]}
payload 即将 POST 给 /marketing/recommend 的完整请求体。
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from loguru import logger

CONFIG_TYPE_TEST = "test_cases"


def _skills_root() -> Path:
    env_path = os.environ.get("SKILLS_RUNTIME_PATH", "").strip()
    if env_path:
        return Path(env_path)
    from utils.skill_runtime import SkillRuntimeLoader  # 延迟 import
    return SkillRuntimeLoader.SKILLS_ROOT


def _local_path(province: str, intent: str) -> Path:
    return _skills_root() / province / intent / "config" / "test_cases.json"


def _read_local(province: str, intent: str) -> List[Dict[str, Any]]:
    p = _local_path(province, intent)
    try:
        if p.exists():
            data = json.loads(p.read_text(encoding="utf-8"))
            cases = data.get("cases") if isinstance(data, dict) else data
            if isinstance(cases, list):
                return cases
    except Exception as e:
        logger.warning(f"[TestCase] 读取本地用例失败 {province}/{intent}: {e}")
    return []


def get_test_cases(province: str, intent: str) -> List[Dict[str, Any]]:
    """读取测试用例列表：生产 ES → 本地文件兜底。"""
    try:
        from utils.skill_runtime import IS_DEV
        from services.es_config_store import es_config_store
        if not IS_DEV and es_config_store.enabled:
            data = es_config_store.get_published(province, intent, CONFIG_TYPE_TEST)
            if isinstance(data, dict) and isinstance(data.get("cases"), list):
                return data["cases"]
            # ES 尚无 → 回退本地（兼容首次/迁移）
    except Exception as e:
        logger.warning(f"[TestCase] 读取 ES 用例失败，回退本地 {province}/{intent}: {e}")
    return _read_local(province, intent)


def save_test_cases(
    province: str, intent: str,
    cases: List[Dict[str, Any]],
    operator: str = "system",
) -> bool:
    """保存完整用例列表：生产写 ES（version 化，notify=False 不触发热重载），
    开发态/ES 不可用兜底写本地文件。任一路径成功即返回 True。"""
    # 补时间戳
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    norm: List[Dict[str, Any]] = []
    for c in (cases or []):
        if not isinstance(c, dict):
            continue
        norm.append({
            "name": str(c.get("name") or "未命名用例"),
            "payload": c.get("payload") if isinstance(c.get("payload"), dict) else {},
            "updated_at": str(c.get("updated_at") or now),
        })
    payload = {"cases": norm}

    try:
        from utils.skill_runtime import IS_DEV
        from services.es_config_store import es_config_store
        if not IS_DEV and es_config_store.enabled:
            ok, msg, _v = es_config_store.save_and_publish(
                province, intent, CONFIG_TYPE_TEST, payload,
                operator=operator, comment="test_cases update", notify=False,
            )
            if ok:
                return True
            logger.warning(f"[TestCase] ES 写入用例失败，尝试本地 {province}/{intent}: {msg}")
    except Exception as e:
        logger.warning(f"[TestCase] ES 写入用例异常，尝试本地 {province}/{intent}: {e}")

    # 兜底：本地文件
    p = _local_path(province, intent)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.error(f"[TestCase] 本地写入用例失败 {province}/{intent}: {e}")
        return False
