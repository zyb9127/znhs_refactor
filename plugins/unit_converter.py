"""
单位转换工具 + 注册表 (UnitConverterRegistry)

converters 格式示例：
  {
    "initFee":       "fen_to_yuan",   # dict 直接字段
    "*.initFee":     "fen_to_yuan",   # list 中每个元素的字段（* 通配）
    "data.近6月流量": "mb_to_gb"      # 嵌套路径（. 分隔）
  }
"""
from typing import Any, Callable, Dict
import copy


# ══════════════════════════════════════════════════════════════
# 基础转换函数
# ══════════════════════════════════════════════════════════════

def mb_to_gb(value: Any) -> float:
    """流量 MB → GB（数值，保留2位小数）"""
    try:
        return round(float(value) / 1024, 2) if value else 0.0
    except (ValueError, TypeError):
        return 0.0


def fen_to_yuan(value: Any) -> float:
    """价格 分 → 元（数值，保留2位小数）"""
    try:
        return round(float(value) / 100, 2) if value else 0.0
    except (ValueError, TypeError):
        return 0.0


def mb_to_gb_str(value: Any) -> str:
    """流量 MB → GB（带单位字符串，如 "70.04GB"）"""
    return f"{mb_to_gb(value)}GB"


def fen_to_yuan_str(value: Any) -> str:
    """价格 分 → 元（带单位字符串，如 "12.80元"）"""
    return f"{fen_to_yuan(value)}元"


def passthrough(value: Any) -> Any:
    """透传，不做任何转换"""
    return value


# ══════════════════════════════════════════════════════════════
# UnitConverterRegistry — 转换函数注册表
# ══════════════════════════════════════════════════════════════

class UnitConverterRegistry:
    """
    单位转换注册表。
    
    支持通过字符串名称调用已注册的转换函数，
    并按路径规则对 dict/list 结构做递归字段级转换。
    """

    _converters: Dict[str, Callable] = {
        "mb_to_gb":        mb_to_gb,
        "mb_to_gb_str":    mb_to_gb_str,
        "fen_to_yuan":     fen_to_yuan,
        "fen_to_yuan_str": fen_to_yuan_str,
        "passthrough":     passthrough,
    }


    

    @classmethod
    def register(cls, name: str, func: Callable) -> None:
        """注册自定义转换函数（可在项目启动时扩展）"""
        cls._converters[name] = func

    @classmethod
    def apply(cls, name: str, value: Any) -> Any:
        """按名称执行单次转换；未找到时原值透传"""
        func = cls._converters.get((name or "").strip())
        if func is None:
            return value
        try:
            return func(value)
        except Exception:
            return value

    @classmethod
    def apply_converters(cls, data: Any, converters: Dict[str, str]) -> Any:
        """
        对 data 按 converters 规则递归做字段级转换，返回深拷贝后的新对象。

        支持路径模式：
          "key"         → dict 直接字段
          "*.key"       → list 中每个元素的字段（* 通配）
          "a.b.key"     → 嵌套路径（以 . 分隔）
          "*.a.key"     → list 中每个元素的嵌套字段

        示例：
          data      = {"initFee": 12800, "items": [{"fee": 9900}]}
          converters = {"initFee": "fen_to_yuan", "*.fee": "fen_to_yuan"}
          →  {"initFee": 128.0, "items": [{"fee": 99.0}]}
        """
        if not converters or data is None:
            return data
        result = copy.deepcopy(data)
        for path, converter_name in converters.items():
            cls._apply_path_converter(result, path, converter_name)
        return result

    @classmethod
    def _apply_path_converter(cls, data: Any, path: str, converter_name: str) -> None:
        """原地修改 data，对 path 指向的字段应用 converter_name 对应的转换函数"""
        if not path:
            return
        parts = path.split(".", 1)
        key  = parts[0]
        rest = parts[1] if len(parts) > 1 else None

        if key == "*":
            # 通配：对 list 中每个元素递归处理
            if isinstance(data, list):
                for item in data:
                    cls._apply_path_converter(item, rest or "", converter_name)
        elif rest:
            # 嵌套路径：继续往下找
            if isinstance(data, dict) and key in data:
                cls._apply_path_converter(data[key], rest, converter_name)
        else:
            # 叶子字段：执行转换
            if isinstance(data, dict) and key in data:
                data[key] = cls.apply(converter_name, data[key])


