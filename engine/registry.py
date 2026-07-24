"""
组件化执行引擎 —— 组件注册表

- COMPONENT_CLASSES：组件类注册表（name -> 组件类），由 @register_component 填充；
- 组件实例缓存：key = "{name}:{province}:{intent}"，双检锁懒创建，
  镜像 core/pipeline.py MarketingPipeline 的 Bundle 缓存策略；
- invalidate_components：配置热更新时按省份/意图清除实例缓存。
"""
from __future__ import annotations

from threading import Lock
from typing import Any, Dict, List, Optional, Type

from loguru import logger

from engine.component import StepComponent

# ══════════════════════════════════════════════════════════════
# 组件类注册表
# ══════════════════════════════════════════════════════════════

# name -> 组件类
COMPONENT_CLASSES: Dict[str, Type[StepComponent]] = {}

# 组件实例缓存：key = "{name}:{province}:{intent}"
_INSTANCES: Dict[str, StepComponent] = {}
_LOCK = Lock()


def register_component(cls: Type[StepComponent]) -> Type[StepComponent]:
    """类装饰器：按 cls.name 注册组件类，重名覆盖并 warning。

    用法：
        @register_component
        class DataFetchComponent(StepComponent):
            name = "data_fetch"
            ...
    """
    name = getattr(cls, "name", "") or ""
    if not name:
        raise ValueError(f"组件类 {cls.__name__} 未设置 name 类属性，无法注册")
    if name in COMPONENT_CLASSES and COMPONENT_CLASSES[name] is not cls:
        logger.warning(
            f"组件重名覆盖注册: name={name} "
            f"旧={COMPONENT_CLASSES[name].__name__} 新={cls.__name__}"
        )
    COMPONENT_CLASSES[name] = cls
    return cls


def get_component_class(name: str) -> Optional[Type[StepComponent]]:
    """按注册名取组件类，未注册返回 None"""
    return COMPONENT_CLASSES.get(name)


def list_components() -> List[Dict[str, Any]]:
    """列出全部已注册组件（供 /api/config-agent 暴露给前端）

    Returns:
        [{"name": 注册名, "doc": 类 docstring 摘要, "config_schema": params 的 JSON Schema}]
    """
    result: List[Dict[str, Any]] = []
    for name in sorted(COMPONENT_CLASSES):
        cls = COMPONENT_CLASSES[name]
        doc = (cls.__doc__ or "").strip()
        result.append(
            {
                "name": name,
                "doc": doc,
                "config_schema": dict(getattr(cls, "config_schema", {}) or {}),
            }
        )
    return result


# ══════════════════════════════════════════════════════════════
# 组件实例缓存（镜像 MarketingPipeline.StepBundle 缓存策略）
# ══════════════════════════════════════════════════════════════

def get_component(name: str, province: str, intent: str = "default") -> StepComponent:
    """获取（或双检锁懒创建）组件实例。

    key = f"{name}:{province}:{intent}"，同 key 实例跨请求复用。

    Raises:
        KeyError: 组件名未注册
    """
    key = f"{name}:{province}:{intent}"
    inst = _INSTANCES.get(key)
    if inst is None:
        with _LOCK:
            if key not in _INSTANCES:
                cls = COMPONENT_CLASSES.get(name)
                if cls is None:
                    raise KeyError(f"未注册的组件: {name}")
                _INSTANCES[key] = cls(province, intent)
                logger.info(f"组件实例已创建: {key} ({cls.__name__})")
            inst = _INSTANCES[key]
    return inst


def invalidate_components(
    province: Optional[str] = None,
    intent: Optional[str] = None,
) -> None:
    """使组件实例缓存失效（配置热更新时调用，语义同 MarketingPipeline.invalidate_bundle）

    - invalidate_components(province, intent) → 只清除该省份+意图的全部组件实例
    - invalidate_components(province)         → 清除该省份所有 intent 的组件实例
    - invalidate_components()                 → 清除全部组件实例
    """
    with _LOCK:
        if not province:
            count = len(_INSTANCES)
            _INSTANCES.clear()
            logger.info(f"全部组件实例缓存已失效: 共{count}个")
            return
        stale: List[str] = []
        for key in _INSTANCES:
            # key 格式 "{name}:{province}:{intent}"，组件名不含冒号，从右侧拆分
            parts = key.rsplit(":", 2)
            if len(parts) != 3:
                continue
            _, key_province, key_intent = parts
            if key_province != province:
                continue
            if intent is not None and key_intent != intent:
                continue
            stale.append(key)
        for key in stale:
            _INSTANCES.pop(key, None)
        logger.info(
            f"组件实例缓存已失效: province={province} intent={intent or '(全部)'} "
            f"共{len(stale)}个"
        )
