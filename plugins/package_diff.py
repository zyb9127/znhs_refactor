"""
PackageDiff — 套餐差异计算 + 差异表格展示

提供：
1. PackageDiff 类：计算当前套餐与目标套餐的费用/流量/语音差异
2. summary_str()：差异单行文字摘要（用于 LLM Prompt 注入）
3. to_table()：差异表格结构（用于前端展示）

字段适配策略（兼容多省份命名）：
- 月费: initFee / monthly_fee / fee / price
- 流量: offerFlow / data_quota / flow / data（单位：GB，若>200则视为MB自动转换）
- 语音: offerVoice / voice_quota / voice / minutes
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# ══════════════════════════════════════════════════════════════
# 字段提取辅助（兼容多命名）
# ══════════════════════════════════════════════════════════════

def _get_fee(pkg: Dict[str, Any]) -> Optional[float]:
    """提取月费（元），支持分/角/元多单位"""
    for key in ("initFee", "monthly_fee", "fee", "price", "curOfferFee"):
        v = pkg.get(key)
        if v is not None:
            try:
                f = float(v)
                # 若值 > 1000，推断为"分"单位，自动转换
                if f > 1000:
                    f = f / 100
                return f
            except (TypeError, ValueError):
                pass
    return None


def _get_flow(pkg: Dict[str, Any]) -> Optional[float]:
    """提取流量（GB），若原始值 > 200 则视为 MB 自动转换"""
    for key in ("offerFlow", "data_quota", "flow", "data", "flowGb"):
        v = pkg.get(key)
        if v is not None:
            try:
                f = float(str(v).replace("G", "").replace("GB", "").replace("M", "").replace("MB", ""))
                if "M" in str(v).upper() or (f > 200 and "G" not in str(v).upper()):
                    f = f / 1024  # MB → GB
                return round(f, 2)
            except (TypeError, ValueError):
                pass
    return None


def _get_voice(pkg: Dict[str, Any]) -> Optional[float]:
    """提取语音分钟数"""
    for key in ("offerVoice", "voice_quota", "voice", "minutes", "minute"):
        v = pkg.get(key)
        if v is not None:
            try:
                return float(str(v).replace("分钟", "").replace("min", ""))
            except (TypeError, ValueError):
                pass
    return None


def _safe_diff(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(b - a, 2)


def _diff_label(diff: Optional[float], unit: str = "") -> str:
    """将差值格式化为带方向的标签"""
    if diff is None:
        return "—"
    if diff > 0:
        return f"+{diff}{unit}"
    if diff < 0:
        return f"{diff}{unit}"
    return "持平"


# ══════════════════════════════════════════════════════════════
# PackageDiff 核心类
# ══════════════════════════════════════════════════════════════

@dataclass
class PackageDiff:
    """套餐差异计算器

    用法：
        diff = PackageDiff(current_package, recommended_package)
        print(diff.summary_str())
        table = diff.to_table()
    """
    current: Dict[str, Any]   # 当前套餐
    target:  Dict[str, Any]   # 目标（推荐）套餐

    # ── 延迟计算属性 ─────────────────────────────────────────────

    @property
    def cur_fee(self) -> Optional[float]:
        return _get_fee(self.current)

    @property
    def tgt_fee(self) -> Optional[float]:
        return _get_fee(self.target)

    @property
    def fee_diff_yuan(self) -> float:
        """月费差异（元），升档为正"""
        d = _safe_diff(self.cur_fee, self.tgt_fee)
        return d if d is not None else 0.0

    @property
    def cur_data(self) -> Optional[float]:
        return _get_flow(self.current)

    @property
    def tgt_data(self) -> Optional[float]:
        return _get_flow(self.target)

    @property
    def data_diff_gb(self) -> float:
        """流量差异（GB），增加为正"""
        d = _safe_diff(self.cur_data, self.tgt_data)
        return d if d is not None else 0.0

    @property
    def cur_voice(self) -> Optional[float]:
        return _get_voice(self.current)

    @property
    def tgt_voice(self) -> Optional[float]:
        return _get_voice(self.target)

    @property
    def voice_diff_min(self) -> float:
        """语音差异（分钟），增加为正"""
        d = _safe_diff(self.cur_voice, self.tgt_voice)
        return d if d is not None else 0.0

    # ── 格式化输出 ────────────────────────────────────────────────

    @property
    def fee_str(self) -> str:
        return _diff_label(
            _safe_diff(self.cur_fee, self.tgt_fee), "元"
        )

    @property
    def data_str(self) -> str:
        return _diff_label(
            _safe_diff(self.cur_data, self.tgt_data), "GB"
        )

    @property
    def voice_str(self) -> str:
        return _diff_label(
            _safe_diff(self.cur_voice, self.tgt_voice), "分钟"
        )

    def summary_str(self) -> str:
        """单行差异摘要，用于嵌入 LLM Prompt

        示例：月费+1元，流量+20GB，语音-30分钟

        仅输出双侧数据齐全、真正可计算的维度；任一侧缺失（如生产接口
        未返回当前套餐）时跳过该维度，避免把「月费—」这类无效标记喂给
        LLM 后被照抄成“月费一”。全部不可算时返回空串，由 Prompt 组装层
        按“空事实不展示”规则跳过整行。
        """
        parts = []
        if _safe_diff(self.cur_fee, self.tgt_fee) is not None:
            parts.append(f"月费{self.fee_str}")
        if _safe_diff(self.cur_data, self.tgt_data) is not None:
            parts.append(f"流量{self.data_str}")
        if _safe_diff(self.cur_voice, self.tgt_voice) is not None:
            parts.append(f"语音{self.voice_str}")
        return "，".join(parts)

    def table_str(self) -> str:
        """纯文本差异对比表格（用于 LLM Prompt {table} 占位符）

        省份无关：字段通过 _get_fee/_get_flow/_get_voice 多别名提取。
        示例：
          项目          当前套餐       推荐套餐       差异
          价格(元/月)   99             129            +30元
          流量          30GB           50GB           +20GB
          语音          200分钟        170分钟        -30分钟
        """
        rows = [
            ("项目",       "当前套餐",                           "推荐套餐",                          "差异"),
            ("价格(元/月)", str(self.cur_fee) if self.cur_fee is not None else "—",
                            str(self.tgt_fee) if self.tgt_fee is not None else "—", self.fee_str),
            ("流量",       f"{self.cur_data}GB"  if self.cur_data  is not None else "—",
                            f"{self.tgt_data}GB"  if self.tgt_data  is not None else "—", self.data_str),
            ("语音(分钟)",  str(self.cur_voice) if self.cur_voice is not None else "—",
                            str(self.tgt_voice) if self.tgt_voice is not None else "—", self.voice_str),
        ]
        # 动态追加宽带行
        cur_bw = (self.current.get("bandWidth") or self.current.get("bandwidth")
                  or self.current.get("broadband_speed") or "")
        tgt_bw = (self.target.get("bandWidth") or self.target.get("bandwidth")
                  or self.target.get("broadband_speed") or "")
        if cur_bw or tgt_bw:
            diff_bw = "" if cur_bw == tgt_bw else ("新增" if not cur_bw else "变更")
            rows.append(("宽带", cur_bw or "无", tgt_bw or "无", diff_bw))

        col_w = [max(len(str(r[i])) for r in rows) for i in range(4)]
        lines = []
        for i, row in enumerate(rows):
            line = "  ".join(str(row[j]).ljust(col_w[j]) for j in range(4))
            lines.append(line)
            if i == 0:
                lines.append("-" * sum(col_w) + "--" * 3)
        return "\n".join(lines)



    def to_table(self) -> Dict[str, Any]:
        """差异对比表格结构（供前端展示）

        结构：
        {
            "headers": ["项目", "当前产品", "目标产品", "差异"],
            "rows": [
                {"label": "产品名", "current": ..., "target": ..., "diff": ""},
                {"label": "价格(元)", ...},
                {"label": "流量(GB)", ...},
                {"label": "分钟数", ...},
                ...
            ]
        }
        """
        cur_name = (
            self.current.get("offerName") or self.current.get("package_name") or "当前套餐"
        )
        tgt_name = (
            self.target.get("offerName") or self.target.get("package_name") or "推荐套餐"
        )

        rows: List[Dict[str, Any]] = [
            {
                "label":   "产品名",
                "current": cur_name,
                "target":  tgt_name,
                "diff":    "",
            },
            {
                "label":   "价格(元/月)",
                "current": self.cur_fee if self.cur_fee is not None else "—",
                "target":  self.tgt_fee if self.tgt_fee is not None else "—",
                "diff":    self.fee_str,
            },
            {
                "label":   "流量",
                "current": f"{self.cur_data}GB"  if self.cur_data  is not None else "—",
                "target":  f"{self.tgt_data}GB"  if self.tgt_data  is not None else "—",
                "diff":    self.data_str,
            },
            {
                "label":   "语音(分钟)",
                "current": self.cur_voice if self.cur_voice is not None else "—",
                "target":  self.tgt_voice if self.tgt_voice is not None else "—",
                "diff":    self.voice_str,
            },
        ]

        # 宽带（兼容多省份字段名）
        cur_bw = (self.current.get("bandWidth") or self.current.get("bandwidth")
                  or self.current.get("broadband_speed") or "")
        tgt_bw = (self.target.get("bandWidth") or self.target.get("bandwidth")
                  or self.target.get("broadband_speed") or "")
        if cur_bw or tgt_bw:
            rows.append({
                "label":   "宽带",
                "current": cur_bw or "无",
                "target":  tgt_bw or "无",
                "diff":    "" if cur_bw == tgt_bw else ("新增" if not cur_bw and tgt_bw else "变更"),
            })

        # 副卡（兼容多省份字段名）
        cur_supp = (self.current.get("suppCard") or self.current.get("supp_card")
                    or self.current.get("subCard") or "")
        tgt_supp = (self.target.get("suppCard") or self.target.get("supp_card")
                    or self.target.get("subCard") or "")
        if cur_supp or tgt_supp:
            rows.append({
                "label":   "副卡",
                "current": f"{cur_supp}张" if cur_supp else "无",
                "target":  f"{tgt_supp}张" if tgt_supp else "无",
                "diff":    _diff_label(_safe_diff(
                    float(cur_supp) if str(cur_supp).isdigit() else None,
                    float(tgt_supp) if str(tgt_supp).isdigit() else None,
                ), "张"),
            })

        # 其他权益
        tgt_rights = self.target.get("otherRight") or []
        if isinstance(tgt_rights, list):
            tgt_rights = [r for r in tgt_rights if r and str(r).strip()]
        if tgt_rights:
            rows.append({
                "label":   "其他权益",
                "current": "—",
                "target":  "、".join(str(r) for r in tgt_rights),
                "diff":    "新增",
            })

        return {
            "headers": ["项目", "当前产品", "目标产品", "差异"],
            "rows":    rows,
        }
