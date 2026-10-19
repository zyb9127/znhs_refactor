"""派生字段：在入参/接口出参之上再算一层，产出可直接填槽的派生值。

背景
----
各省报文里越来越多「按维度分组的数组」与「需要比较后才能决定话术走向」的诉求，
而原有取值链只支持点路径取标量（``_get_path`` 遇到 list 直接返回 None），
且模板匹配是静态字符串标签（product_id/stage/scene）+ 降级链，无法表达数值条件。
典型场景（天津营销活动）：

  1. ``userinfo_json`` 是账期数组（``timeType`` 0/1/2/3 = 当月/上月/上上月/上上上月），
     需要取出「当月」那一条才能谈超套；
  2. 超套上网费 + 超套通话费之和 < 1 元推场景一、否则推场景二 —— 两个场景推的是
     **不同产品**（业务编码不同），选错是业务错误，必须确定性计算，不能交给模型判断；
  3. 费用≠0 但超额量=0 属账单异常，要追加一句坐席提示。

设计取舍
--------
- **不做表达式 DSL / 不用 eval**：配置是结构化 JSON，前端才能表单化渲染、运营才看得懂，
  也不引入注入风险与「配置即代码」的调试难题。三个算子已覆盖上述全部诉求。
- **不改模板选择引擎**：派生值以普通透传字段身份注入【上下文数据】，
  既可在话术里用 ``{派生名}`` / ``{派生名[子键]}`` 引用，也可被
  ``biz_config.template_match.*_from`` 当作取值字段消费 —— 复用现有 12 档匹配，
  不必给 template_selector/template_index/conflict_detector 加第二套匹配语义。
- **array_find 返回整个元素（dict）而非单个字段**：dict 型透传值会被 build_prompt
  注册为子字段根，于是 ``{usage_current}``（可读展开）与
  ``{usage_current[over_flow_fee]}``（精确填槽）两种引用同时可用；
  这也正好绕开「顶层 list 不会注册为子字段根」的既有限制，无需改 prompt_builder。
- **取不到值一律不成立、不伪造 0**：``sum`` 全部路径都取不到时返回 None 而非 0，
  比较算子遇到空值直接判否。否则「没数据」会被当成「真的是 0」，
  在天津规则里会把用户错误路由到场景一。

算子
----
``array_find``  数组按条件选元素 → dict（where 支持比较算子/裸值精确相等；match=all|any；
                可选 sort_by+order 先排序再取第一个，如按 timeType 升序取最近账期）
    ``{"type": "array_find", "from": "userinfo_json", "where": {"timeType": "0"}}``
    ``{"type": "array_find", "from": "userinfo_json", "match": "any",
       "where": {"over_flow_fee": {"gt": 0}, "over_voice_fee": {"gt": 0}},
       "sort_by": "timeType", "order": "asc"}``
``array_render`` 数组逐元素套 item_template 渲染再拼接 → 一段多行文本（变长块）。
                过滤/排序口径与 array_find 相同（where/match/sort_by/order），命中几条就渲几行，
                用于「有多少个月超套就展示多少个月」这类行数不定的分月明细。
    ``{"type": "array_render", "from": "userinfo_json", "match": "any",
       "where": {"over_flow": {"gt": 0}, "over_voice": {"gt": 0},
                 "over_flow_fee": {"ne": 0}, "over_voice_fee": {"ne": 0}},
       "sort_by": "timeType", "order": "asc",
       "label_field": "timeType", "label_map": {"0": "本月", "1": "上月"},
       "hint_field": "账单提示",
       "hint_rules": [{"when": {"over_flow_fee": {"ne": 0}, "over_flow": {"eq": 0}},
                       "value": "【请结合账单解释】"}, {"value": ""}],
       "item_template": "{label}流量超出{over_flow}，超套费用{over_flow_fee}元，{账单提示}",
       "join": "\n"}``
``sum``         多路径求和 → 数值
    ``{"type": "sum", "from": ["usage_current.over_flow_fee", "usage_current.over_voice_fee"]}``
``bucket``      按阈值 / 多字段条件分档 → 字符串
    ``{"type": "bucket", "from": "over_fee_total",
       "rules": [{"lt": 1, "value": "场景一"}, {"value": "场景二"}]}``

求值按声明顺序进行，后面的字段可引用前面已算出的派生名（JSON 对象保序）。
派生结果为 None 或空串时**不产出**该字段（兜底档写 ``"value": ""`` 即表示「不追加」）。

成品块后填（``"verbatim": true``，任意算子可配，典型用于 array_render）
--------------------------------------------------------------------
派生值本身已是定稿文案（如逐月明细），交给 LLM「原样填入」仍有被改写/合并/漏行的风险。
配 ``verbatim: true`` 后：上下文里该槽位只给占位符本身，模型只负责在话术中保留 ``{派生名}``，
生成后由 ScriptStep._apply_slot_facts 确定性替换为成品文本（见 prompt_builder / script_step）。
"""
import operator
import re
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

# 比较算子：仅这几个，够表达阈值分档与零值判定，不扩成通用表达式
_COMPARATORS: Dict[str, Callable[[float, float], bool]] = {
    "lt": operator.lt,
    "lte": operator.le,
    "gt": operator.gt,
    "gte": operator.ge,
}
_EQ_OPS = ("eq", "ne")
_ALL_OPS = tuple(_COMPARATORS) + _EQ_OPS

# 从 "3元" / "1.5GB" / "12,000" 这类带单位的串里取数值
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _s(v: Any) -> str:
    return "" if v is None else str(v).strip()


def _is_blank(v: Any) -> bool:
    """None / 空串 / 纯空白视为「没数据」（0 与 "0" 不算）。"""
    return v is None or (isinstance(v, str) and not v.strip())


def _parse_number(v: Any) -> Optional[float]:
    """宽松数值解析：'0'→0.0，'3元'→3.0，''/None/非数→None。bool 不当数字。"""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    s = v.strip()
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        pass
    m = _NUM_RE.search(s.replace(",", ""))
    return float(m.group()) if m else None


def _norm_num(f: float) -> Any:
    """整数值去掉小数点（3.0 → 3），避免注入上下文时出现 '3.0 元' 这类别扭表述。"""
    return int(f) if abs(f - round(f)) < 1e-9 else round(f, 4)


def _child(obj: Any, key: str) -> Any:
    """取子级：dict 按键、list 按数字下标（越界/非数字→None）。"""
    if isinstance(obj, dict):
        return obj.get(key)
    if isinstance(obj, list):
        try:
            return obj[int(key)]
        except (ValueError, IndexError):
            return None
    return None


def _resolve_path(path: Any, derived: Dict[str, Any], raw: Any) -> Any:
    """点路径取值：根名先查已算出的派生字段，再查原始报文。"""
    parts = [p for p in _s(path).split(".") if p != ""]
    if not parts:
        return None
    root = parts[0]
    if root in derived:
        cur: Any = derived[root]
    elif isinstance(raw, dict) and root in raw:
        cur = raw[root]
    else:
        return None
    for key in parts[1:]:
        cur = _child(cur, key)
        if cur is None:
            return None
    return cur


def _cmp_one(value: Any, op: str, operand: Any) -> bool:
    """单条比较。取不到值一律判否 —— 不把「没数据」当成「真的是 0」。"""
    if _is_blank(value):
        return False
    a, b = _parse_number(value), _parse_number(operand)
    if op in _EQ_OPS:
        # 两边都是数才按数比（"0" == 0），否则退化为字符串比（支持按分类值判定）
        same = (a == b) if (a is not None and b is not None) else (_s(value) == _s(operand))
        return same if op == "eq" else not same
    fn = _COMPARATORS.get(op)
    if fn is None or a is None or b is None:
        return False
    return fn(a, b)


def _cmp_all(value: Any, cond: Any) -> bool:
    """cond 可以是 {op: 操作数} 字典，也可以是裸值（等价于 {"eq": 值}）。"""
    if not isinstance(cond, dict):
        return _cmp_one(value, "eq", cond)
    ops = {k: v for k, v in cond.items() if k in _ALL_OPS}
    if not ops:
        return False
    return all(_cmp_one(value, op, operand) for op, operand in ops.items())


# ── 三个算子 ────────────────────────────────────────────────────────
Resolver = Callable[[Any], Any]


def _matching_items(spec: Dict[str, Any], resolve: Resolver) -> List[Dict[str, Any]]:
    """数组按 where/match 过滤 + 可选 sort_by/order 排序，返回命中元素列表（保序）。

    array_find 取其第一个、array_render 遍历全部，共用同一套过滤排序口径。
    where 每项：裸值=精确相等（兼容 "0" 与 0），或 {op: 操作数}（eq/ne/gt/gte/lt/lte）。
    match：``all``（默认，全部满足）/ ``any``（任一满足）。
    sort_by + order：按该键数值排序（order 缺省 ``asc``），缺失/非数值排到最后；不配保持原序。
    取不到值不伪造：where 遇空值判否（_cmp_one 内保证），不会把「没数据」当成命中。
    """
    src = resolve(spec.get("from"))
    if not isinstance(src, list):
        return []
    where = spec.get("where")
    where = where if isinstance(where, dict) else {}
    reducer = any if str(spec.get("match") or "all").strip().lower() == "any" else all

    items = [it for it in src if isinstance(it, dict)]
    sort_by = spec.get("sort_by")
    if sort_by:
        reverse = str(spec.get("order") or "asc").strip().lower() == "desc"
        # 缺失/非数值排最后（升序时给 +inf、降序时给 -inf），sorted 稳定保留同值原序
        _miss = float("-inf") if reverse else float("inf")

        def _sort_key(it: Dict[str, Any]) -> float:
            n = _parse_number(it.get(sort_by))
            return _miss if n is None else n

        items = sorted(items, key=_sort_key, reverse=reverse)

    if not where:
        return items             # 无条件 → 排序后（或原序）全部元素
    return [it for it in items
            if reducer(_cmp_all(it.get(k), cond) for k, cond in where.items())]


def _op_array_find(spec: Dict[str, Any], resolve: Resolver) -> Any:
    """数组按条件选出**第一个**匹配元素（整个 dict）。过滤/排序口径见 _matching_items。"""
    items = _matching_items(spec, resolve)
    return items[0] if items else None


def _fill_item(tpl: str, scope: Dict[str, Any]) -> str:
    """按元素字段填 {token}；缺失/空值填空串，不残留占位符（\\w 含中文，支持 {账单提示}）。"""
    return re.sub(r"\{(\w+)\}", lambda m: _s(scope.get(m.group(1))), tpl)


def _eval_rules(rules: Any, scope: Dict[str, Any]) -> str:
    """bucket-when 口径按单个元素就地求值，返回第一个命中档的 value（用于逐月提示）。"""
    for rule in (rules or []):
        if not isinstance(rule, dict):
            continue
        when = rule.get("when")
        if isinstance(when, dict) and when:
            if all(_cmp_all(scope.get(k), cond) for k, cond in when.items()):
                return _s(rule.get("value"))
            continue
        return _s(rule.get("value"))          # 兜底档
    return ""


def _op_array_render(spec: Dict[str, Any], resolve: Resolver) -> Any:
    """数组逐元素套 item_template 渲染再拼接 → 一段多行文本（变长块）。

    过滤/排序复用 array_find 口径（where/match/sort_by/order）；命中几条就渲几行。
    每个元素：
      · label_field + label_map：把某字段值映射成可读标签供 {label} 引用（如 timeType→本月/上月），
        未命中映射时回退原值；
      · hint_field + hint_rules：按该元素就地判 bucket-when，结果供 {hint_field} 引用
        （逐月提示，仅在当月成立时追加）；
      · item_template 里 {字段名} 用元素自身字段填充，缺失/空值填空串。
    join 连接各行（默认换行）。无命中元素 / 无 item_template / 结果全空 → None（不产出）。
    """
    items = _matching_items(spec, resolve)
    tpl = _s(spec.get("item_template"))
    if not items or not tpl:
        return None
    label_field = _s(spec.get("label_field"))
    label_map = spec.get("label_map") if isinstance(spec.get("label_map"), dict) else {}
    hint_field = _s(spec.get("hint_field"))
    hint_rules = spec.get("hint_rules")
    join = spec.get("join")
    sep = "\n" if join is None else str(join)

    lines: List[str] = []
    for it in items:
        scope = dict(it)
        if label_field:
            raw_label = _s(it.get(label_field))
            scope["label"] = _s(label_map.get(raw_label, raw_label))
        if hint_field:
            scope[hint_field] = _eval_rules(hint_rules, it)
        line = _fill_item(tpl, scope).strip()
        if line:
            lines.append(line)
    return sep.join(lines) or None


def _op_sum(spec: Dict[str, Any], resolve: Resolver) -> Any:
    """多路径求和。全部路径都取不到 → None（不返回 0，见模块头「不伪造 0」）。"""
    raw_from = spec.get("from")
    paths: List[Any] = raw_from if isinstance(raw_from, list) else [raw_from]
    total, hit = 0.0, False
    for p in paths:
        n = _parse_number(resolve(p))
        if n is not None:
            total += n
            hit = True
    return _norm_num(total) if hit else None


def _op_bucket(spec: Dict[str, Any], resolve: Resolver) -> Any:
    """按顺序取第一个命中的档位。

    每条 rule 二选一写法：
      - 单字段阈值：``{"lt": 1, "value": "场景一"}``，比较对象是 spec.from；
      - 多字段条件：``{"when": {"路径": {"ne": 0}, "路径2": {"eq": 0}}, "value": "…"}``，需全部成立。
    不带任何条件的 rule 即兜底档（放最后）。

    声明了 from 但取不到值时**整个分档不产出**，不落兜底档：分档没有判断依据，
    落兜底就等于凭空选了一档 —— 天津场景里这会变成「没数据也推产品」。
    纯 when 写法（不声明 from）不受此限，兜底档正常生效（配 ``"value": ""`` 表示「不追加」）。
    """
    default_from = spec.get("from")
    if not _is_blank(default_from) and _is_blank(resolve(default_from)):
        return None
    for rule in (spec.get("rules") or []):
        if not isinstance(rule, dict):
            continue
        when = rule.get("when")
        if isinstance(when, dict) and when:
            if all(_cmp_all(resolve(path), cond) for path, cond in when.items()):
                return rule.get("value")
            continue
        ops = {k: v for k, v in rule.items() if k in _ALL_OPS}
        if not ops:
            return rule.get("value")          # 兜底档
        if _cmp_all(resolve(default_from), ops):
            return rule.get("value")
    return None


_OPS: Dict[str, Callable[[Dict[str, Any], Resolver], Any]] = {
    "array_find": _op_array_find,
    "array_render": _op_array_render,
    "sum": _op_sum,
    "bucket": _op_bucket,
}


def compute_derived_fields(raw: Any, cfg: Any) -> Dict[str, Any]:
    """按声明顺序求值 ``api_nodes.<节点>.derived_fields``，返回 {派生名: 值}。

    - 后面的字段可引用前面已算出的派生名；
    - 值为 None / 空串的字段不产出（兜底档写 ``"value": ""`` 即「不追加」）；
    - 单条算子异常不影响其余字段，只告警 —— 派生字段是增强项，不该让话术整体失败。
    """
    if not isinstance(cfg, dict) or not cfg:
        return {}
    derived: Dict[str, Any] = {}
    for name, spec in cfg.items():
        if not isinstance(name, str) or not name or name.startswith("_"):
            continue
        if not isinstance(spec, dict):
            logger.warning(f"[derived_fields] {name!r} 配置不是对象，已跳过")
            continue
        op = _s(spec.get("type")).lower()
        handler = _OPS.get(op)
        if handler is None:
            logger.warning(
                f"[derived_fields] {name!r} 的 type={op!r} 不支持，已跳过"
                f"（可选：{' / '.join(_OPS)}）"
            )
            continue
        try:
            val = handler(spec, lambda p: _resolve_path(p, derived, raw))
        except Exception as exc:                                  # noqa: BLE001
            logger.warning(f"[derived_fields] {name!r} 求值失败已跳过：{exc}")
            continue
        if _is_blank(val):
            logger.info(f"[derived_fields] {name!r} 求值为空，不注入（缺数据或未命中任何档位）")
            continue
        derived[name] = val
    if derived:
        logger.info(f"[derived_fields] 已派生 {len(derived)} 个字段：{list(derived)}")
    return derived


# 随 passthrough 一起流转（可被 Redis 缓存）的「需后填槽位名」列表键；
# 下划线开头 = 内部键，不会被当成事实行注入【上下文数据】。
VERBATIM_SLOTS_KEY = "_verbatim_slots"


def verbatim_derived_names(cfg: Any) -> List[str]:
    """配置里声明了 ``verbatim: true`` 的派生名（保持声明顺序）。"""
    if not isinstance(cfg, dict):
        return []
    return [
        name for name, spec in cfg.items()
        if isinstance(name, str) and name and not name.startswith("_")
        and isinstance(spec, dict) and spec.get("verbatim") is True
    ]
