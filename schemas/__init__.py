"""
schemas 包 —— JSON 契约单一真源（前后端共享）

提供：
- get_schema(name)：按名读取 schema JSON（模块级缓存，模块加载后零 IO）
- list_schemas()：列出可用 schema 名
- get_standard_domains()：7 大标准域 / 变量标签 / linked_var 候选等单一真源
- validate(instance, schema)：内置轻量 JSON Schema 子集校验器（不依赖 jsonschema 库）

校验器支持的子集：type（object/array/string/integer/number/boolean）、
required、properties、items、enum；additionalProperties 仅记录 info 不报错。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

_SCHEMA_DIR = os.path.dirname(os.path.abspath(__file__))

# 模块级缓存：{文件名(不含扩展): 已解析 JSON}
_cache: Dict[str, Dict[str, Any]] = {}
_cache_lock = threading.Lock()


def _resolve_filename(name: str) -> Optional[str]:
    """将 schema 名解析为 schemas 目录下的实际文件名，找不到返回 None。

    支持传入 "template" / "template.schema" / "template.schema.json" /
    "standard_domains" / "standard_domains.json" 等写法。
    """
    base = name.strip()
    if not base:
        return None
    # 去掉可能带的扩展名
    for suffix in (".schema.json", ".json", ".schema"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    for candidate in (f"{base}.schema.json", f"{base}.json"):
        if os.path.isfile(os.path.join(_SCHEMA_DIR, candidate)):
            return candidate
    return None


def get_schema(name: str) -> Dict[str, Any]:
    """读取指定名称的 schema/数据 JSON，模块级缓存。

    Args:
        name: schema 名，如 "template" / "biz_config" / "standard_domains"

    Returns:
        解析后的 dict

    Raises:
        KeyError: 名称不存在
    """
    filename = _resolve_filename(name)
    if filename is None:
        raise KeyError(f"schema 不存在: {name}")
    key = filename
    cached = _cache.get(key)
    if cached is not None:
        return cached
    with _cache_lock:
        cached = _cache.get(key)
        if cached is not None:
            return cached
        path = os.path.join(_SCHEMA_DIR, filename)
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        _cache[key] = data
        return data


def list_schemas() -> List[str]:
    """列出 schemas 目录下全部可用 schema 名（去掉扩展名）。"""
    names: List[str] = []
    for fn in sorted(os.listdir(_SCHEMA_DIR)):
        if fn.endswith(".schema.json"):
            names.append(fn[: -len(".schema.json")])
        elif fn.endswith(".json"):
            names.append(fn[: -len(".json")])
    return names


def get_standard_domains() -> Dict[str, Any]:
    """返回 standard_domains.json 内容（7 大标准域/变量标签/linked_var 候选等）。"""
    return get_schema("standard_domains")


# ── 内置轻量校验器 ─────────────────────────────────────────────

# JSON Schema type 名 → Python 类型判断函数
def _check_type(value: Any, type_name: str) -> bool:
    if type_name == "object":
        return isinstance(value, dict)
    if type_name == "array":
        return isinstance(value, list)
    if type_name == "string":
        return isinstance(value, str)
    if type_name == "integer":
        # bool 是 int 的子类，需排除
        return isinstance(value, int) and not isinstance(value, bool)
    if type_name == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if type_name == "boolean":
        return isinstance(value, bool)
    # 未知类型名不做校验（宽松）
    return True


def _validate_node(instance: Any, schema: Dict[str, Any], path: str, errors: List[str]) -> None:
    """递归校验单个节点，错误追加到 errors（格式：路径: 描述）。"""
    if not isinstance(schema, dict):
        return

    # type：支持字符串或字符串列表
    type_spec = schema.get("type")
    if type_spec:
        types = type_spec if isinstance(type_spec, list) else [type_spec]
        if not any(_check_type(instance, t) for t in types):
            errors.append(f"{path or '$'}: 类型应为 {'/'.join(types)}，实际为 {type(instance).__name__}")
            return  # 类型不符时不再深入校验子结构

    # enum
    enum_vals = schema.get("enum")
    if isinstance(enum_vals, list) and enum_vals and instance not in enum_vals:
        errors.append(f"{path or '$'}: 取值应属于 {enum_vals}，实际为 {instance!r}")

    # object：required / properties / additionalProperties（仅提示，不报错）
    if isinstance(instance, dict):
        for req_key in schema.get("required", []) or []:
            if req_key not in instance:
                errors.append(f"{path or '$'}: 缺少必填字段 {req_key}")
        props = schema.get("properties") or {}
        for key, sub_schema in props.items():
            if key in instance:
                sub_path = f"{path}.{key}" if path else key
                _validate_node(instance[key], sub_schema, sub_path, errors)
        # additionalProperties：按规格仅记录 info，不计入错误列表

    # array：items
    if isinstance(instance, list):
        items_schema = schema.get("items")
        if isinstance(items_schema, dict):
            for idx, item in enumerate(instance):
                sub_path = f"{path}[{idx}]" if path else f"[{idx}]"
                _validate_node(item, items_schema, sub_path, errors)


def validate(instance: Any, schema: Dict[str, Any]) -> List[str]:
    """内置轻量校验器：按 schema 校验 instance，返回错误描述列表（空列表=通过）。

    仅支持 JSON Schema 子集：type/required/properties/items/enum；
    additionalProperties 不报错；$ref/$defs 不解析（api_nodes 顶层动态 key 仅文档用途）。
    """
    errors: List[str] = []
    _validate_node(instance, schema, "", errors)
    return errors


__all__ = ["get_schema", "list_schemas", "get_standard_domains", "validate"]


if __name__ == "__main__":
    # 单元级自测：python -m schemas（在 ROOT 下执行）
    failures: List[str] = []

    def _assert(cond: bool, msg: str) -> None:
        if not cond:
            failures.append(msg)

    # 1. list_schemas 应包含全部 5 个 JSON
    names = list_schemas()
    for expect in ("standard_domains", "template", "biz_config", "api_nodes", "pipeline"):
        _assert(expect in names, f"list_schemas 缺少 {expect}: {names}")

    # 2. standard_domains 结构与关键内容
    sd = get_standard_domains()
    _assert(len(sd.get("domains", [])) == 7, "domains 应为 7 个")
    _assert({d["key"] for d in sd["domains"]} == {
        "current_package", "usage", "tags", "user_info",
        "recommended_packages", "user_profile", "domain_ext",
    }, "domains key 集合不符")
    _assert(sd["var_labels"].get("cur_brief") == "当前套餐信息", "var_labels.cur_brief 不符")
    _assert(len(sd["var_labels"]) == 13, "var_labels 应为 13 项")
    _assert(len(sd.get("linked_vars", [])) == 11, "linked_vars 应为 11 项")
    _assert(next(v for v in sd["linked_vars"] if v["key"] == "diff_str")["slot"] == "__computed__",
            "diff_str slot 应为 __computed__")
    _assert(next(v for v in sd["linked_vars"] if v["key"] == "table")["slot"] == "",
            "table 应无 slot")

    # 3. get_schema 缓存：两次返回同一对象
    _assert(get_schema("template") is get_schema("template.schema.json"), "get_schema 缓存失效")

    # 4. 未知名称抛 KeyError
    try:
        get_schema("no_such_schema")
        _assert(False, "未知 schema 应抛 KeyError")
    except KeyError:
        pass

    # 5. validate：template schema 正反例
    tpl_schema = get_schema("template")
    ok_tpl = {"template_name": "t1", "template_content": "你好{cur_brief}", "priority": 2,
              "status": "online", "linked_vars": ["cur_brief"]}
    _assert(validate(ok_tpl, tpl_schema) == [], "合法模板不应有错误")
    bad_tpl = {"template_name": 123, "status": "bad", "linked_vars": "x", "priority": 1.5}
    errs = validate(bad_tpl, tpl_schema)
    _assert(any("template_content" in e and "必填" in e for e in errs), "应报缺少 template_content")
    _assert(any(e.startswith("template_name") for e in errs), "应报 template_name 类型错误")
    _assert(any(e.startswith("status") for e in errs), "应报 status 枚举错误")
    _assert(any(e.startswith("linked_vars") for e in errs), "应报 linked_vars 类型错误")
    _assert(any(e.startswith("priority") for e in errs), "应报 priority 类型错误（1.5 非 integer）")

    # 6. validate：pipeline schema 正反例
    pl_schema = get_schema("pipeline")
    ok_pl = {"steps": [
        {"component": "data_fetch", "params": {}},
        {"component": "recommend", "when": {"ctx_field": "recommended_packages", "op": "nonempty"}},
    ]}
    _assert(validate(ok_pl, pl_schema) == [], "合法 pipeline 不应有错误")
    bad_pl = {"steps": [{"params": {}}, {"component": "x", "when": {"op": "gt"}}]}
    errs = validate(bad_pl, pl_schema)
    _assert(any("steps[0]" in e and "component" in e for e in errs), "应报 steps[0] 缺 component")
    _assert(any("steps[1].when.op" in e for e in errs), "应报 when.op 枚举错误")

    # 7. validate：biz_config 宽松性（杂字段不报错）
    bc_schema = get_schema("biz_config")
    _assert(validate({"strategy": {"default_strategy": "direct"}, "杂字段": 1}, bc_schema) == [],
            "biz_config 杂字段不应报错")
    _assert(validate({"strategy": {"llm_max_concurrency": "3"}}, bc_schema) != [],
            "llm_max_concurrency 字符串应报类型错误")

    # 8. validate：api_nodes 顶层动态 key 不报错（additionalProperties 不校验）
    an_schema = get_schema("api_nodes")
    _assert(validate({"node1": {"enabled": True}}, an_schema) == [], "api_nodes 动态节点不应报错")
    _assert(validate([], an_schema) != [], "api_nodes 传数组应报类型错误")

    # 9. bool 不应通过 integer/number 校验
    _assert(validate(True, {"type": "integer"}) != [], "bool 不应通过 integer")
    _assert(validate(True, {"type": "boolean"}) == [], "bool 应通过 boolean")

    if failures:
        for f in failures:
            print("FAIL:", f)
        raise SystemExit(1)
    print("schemas 自测通过：全部断言成功")
