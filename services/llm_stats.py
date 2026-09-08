"""
分省模型调用量统计（一期：Redis 计数器）

背景
----
运营需要按「省份 × 日期 × 意图」看 LLM 调用量（总次数、成功/失败、token 用量、
平均耗时）。多实例部署下各实例只写本地内存计数，由后台 daemon 线程每 30s 批量
``HINCRBY`` 到现有 Redis（复用 redis_config_bus 的 Cluster 连接，不新建连接池），
天然完成跨实例汇总。

性能红线
--------
- ``record()`` 在请求主链路上只做一次「Lock + dict 自增」的纯内存操作（微秒级、零 I/O），
  绝不触碰 Redis；
- 所有 Redis 读写都在 daemon flush 线程 / 查询接口里，任何 Redis 异常只
  ``logger.warning``，绝不向上抛，不影响话术生成主链路。

Redis Key 设计（沿用 ``znhs:agent:`` 前缀，日期用 hash tag 保证同日 key 同 slot）
--------------------------------------------------------------------------------
- 计数 HASH：``znhs:agent:stats:llm:{yyyymmdd}:{province}``
  field：``calls`` / ``success`` / ``fail`` / ``prompt_tokens`` / ``completion_tokens`` /
  ``elapsed_ms_sum``，及按意图细分 ``calls@{intent}`` / ``fail@{intent}`` /
  ``prompt_tokens@{intent}`` / ``completion_tokens@{intent}``
- 省份索引 SET：``znhs:agent:stats:llm:{yyyymmdd}:_provinces``（flush 时 SADD，查询免 SCAN）
- TTL 均 40 天（对齐分省日志 30 天保留，留缓冲）
- ``{yyyymmdd}`` 是 Redis hash tag，同日 key 落同 slot，cluster 下无需跨 slot 事务

计数语义
--------
每次 ``llm_service.generate()`` 最终成功记 1 次 success、最终失败记 1 次 fail
（重试中间态不计数，与 ``province_logger`` 的分省日志口径一致）。剔除 ``province=test``
（测试页双写），空省份归入 ``unknown``。

开关：环境变量 ``ZNHS_LLM_STATS=0`` 或 Redis client 为空（dev 模式）时整体禁用，
``record()`` 直接 no-op，查询接口返回 ``enabled: false``。
"""
from __future__ import annotations

import os
import threading
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from loguru import logger

# Redis key 前缀与 TTL
KEY_PREFIX = "znhs:agent:stats:llm:"
TTL_SECONDS = 40 * 86400          # 40 天
FLUSH_INTERVAL = 30               # flush 间隔（秒），对齐计划 30s
# 内存缓冲上限（date,province 桶数）：防 Redis 长期故障时内存无限膨胀，超限丢弃并告警
_BUFFER_MAX_KEYS = 100_000
# 意图细分 field 的已知前缀（query 时按此前缀表把 field 拆回意图维度）
_INTENT_FIELD_PREFIXES = ("calls", "fail", "prompt_tokens", "completion_tokens")
# 整体 field（非意图细分）
_TOTAL_FIELDS = ("calls", "success", "fail", "prompt_tokens", "completion_tokens", "elapsed_ms_sum")


def _hash_key(day: str, province: str) -> str:
    """计数 HASH key：``znhs:agent:stats:llm:{yyyymmdd}:{province}``（日期为 hash tag）。"""
    return f"{KEY_PREFIX}{{{day}}}:{province}"


def _provinces_key(day: str) -> str:
    """省份索引 SET key：``znhs:agent:stats:llm:{yyyymmdd}:_provinces``。"""
    return f"{KEY_PREFIX}{{{day}}}:_provinces"


def _norm_province(province: Any) -> str:
    """省份归一：空 → ``unknown``；``test``（测试页双写）返回空串表示应剔除。"""
    prov = str(province or "").strip()
    if not prov:
        return "unknown"
    if prov == "test":
        return ""
    return prov


class LlmStats:
    """分省 LLM 调用量统计（模块级单例，见文件末尾 ``llm_stats``）。

    状态全部挂在实例上（不做 __new__ 单例），测试可直接 ``LlmStats()`` 造干净实例。
    """

    def __init__(self) -> None:
        self._client: Any = None
        self._enabled: bool = False
        self._lock = threading.Lock()
        # (yyyymmdd, province) → {field: 累计值}
        self._buffer: Dict[tuple, Dict[str, int]] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._flush_interval: int = FLUSH_INTERVAL
        self._dropped_warned: bool = False

    # ── 初始化 ──────────────────────────────────────────────────

    def init(self, redis_client: Any) -> None:
        """接收 redis_config_bus 的 client（复用同一 Cluster 连接，不新建连接池）。

        ``ZNHS_LLM_STATS=0`` 或 client 为空（dev 模式未初始化 Redis 总线）时整体禁用。
        """
        if str(os.environ.get("ZNHS_LLM_STATS", "1")).strip() == "0":
            logger.info("[LlmStats] ZNHS_LLM_STATS=0，统计已禁用")
            self._client = None
            self._enabled = False
            return
        self._client = redis_client
        self._enabled = redis_client is not None
        if self._enabled:
            logger.info("[LlmStats] ✅ 已启用（复用 Redis 配置总线连接）")
        else:
            logger.info("[LlmStats] Redis client 为空（dev 模式），统计禁用")

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    # ── 计数（请求主链路：纯内存，零 I/O，绝不抛异常）────────────────

    def record(
        self,
        province: Any,
        intent: Any,
        model: Any,
        success: bool,
        elapsed_ms: float,
        prompt_tokens: Any = 0,
        completion_tokens: Any = 0,
    ) -> None:
        """记录一次 LLM 最终调用结果。纯内存 dict 自增，无任何 I/O。

        ``model`` 一期只入参不落 Redis（为二期维度预留），不在 key/field 中体现。
        """
        try:
            if not self.enabled:
                return
            prov = _norm_province(province)
            if not prov:
                return  # province=test（测试页双写）不计入统计
            day = time.strftime("%Y%m%d")
            it = str(intent or "").strip()
            pt = int(prompt_tokens or 0)
            ct = int(completion_tokens or 0)
            with self._lock:
                key = (day, prov)
                buf = self._buffer.get(key)
                if buf is None:
                    if len(self._buffer) >= _BUFFER_MAX_KEYS:
                        if not self._dropped_warned:
                            self._dropped_warned = True
                            logger.warning(
                                f"[LlmStats] ⚠️ 内存缓冲已超 {_BUFFER_MAX_KEYS} 桶"
                                "（疑似 Redis 长期故障），新增计数将被丢弃"
                            )
                        return
                    buf = {}
                    self._buffer[key] = buf
                buf["calls"] = buf.get("calls", 0) + 1
                if success:
                    buf["success"] = buf.get("success", 0) + 1
                else:
                    buf["fail"] = buf.get("fail", 0) + 1
                buf["prompt_tokens"] = buf.get("prompt_tokens", 0) + pt
                buf["completion_tokens"] = buf.get("completion_tokens", 0) + ct
                buf["elapsed_ms_sum"] = buf.get("elapsed_ms_sum", 0) + int(elapsed_ms or 0)
                if it:
                    buf[f"calls@{it}"] = buf.get(f"calls@{it}", 0) + 1
                    if not success:
                        buf[f"fail@{it}"] = buf.get(f"fail@{it}", 0) + 1
                    buf[f"prompt_tokens@{it}"] = buf.get(f"prompt_tokens@{it}", 0) + pt
                    buf[f"completion_tokens@{it}"] = buf.get(f"completion_tokens@{it}", 0) + ct
        except Exception as e:  # 双保险：主链路绝不因统计受影响
            logger.warning(f"[LlmStats] record 异常（已忽略）: {e}")

    # ── flush 线程（daemon，Event.wait 循环，照抄 config_poller 模式）──────

    def start(self, interval_seconds: Optional[int] = None) -> None:
        """启动后台 flush 线程（daemon）。未启用时直接跳过。"""
        if not self.enabled:
            logger.info("[LlmStats] 未启用，跳过 flush 线程")
            return
        if self._thread and self._thread.is_alive():
            return
        self._flush_interval = int(interval_seconds or FLUSH_INTERVAL)
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, name="llm-stats-flush", daemon=True,
        )
        self._thread.start()
        logger.info(f"[LlmStats] flush 线程已启动（间隔 {self._flush_interval}s）")

    def stop(self) -> None:
        self._stop_event.set()

    def _loop(self) -> None:
        # wait() 超时返回 False 才继续 flush；stop() 触发 set() 后立即退出循环
        while not self._stop_event.wait(self._flush_interval):
            self.flush_once()

    def flush_once(self) -> bool:
        """把内存缓冲批量写入 Redis。拆成独立方法便于单测（不依赖真实线程/sleep）。

        先 swap 出当前缓冲（record 继续写新缓冲），再逐 key ``HINCRBY``——
        桶数极小（省份 × 天数），不用 pipeline，规避 cluster 跨 slot 问题；
        然后 ``SADD`` 省份索引、``EXPIRE`` 40 天。整体 try/except：失败把数据
        合并回缓冲（下轮重试），只告警不抛出。
        """
        if not self.enabled:
            return False
        with self._lock:
            if not self._buffer:
                return True
            pending, self._buffer = self._buffer, {}
        try:
            for (day, prov), fields in pending.items():
                key = _hash_key(day, prov)
                for field, delta in fields.items():
                    if delta:
                        self._client.hincrby(key, field, int(delta))
                self._client.sadd(_provinces_key(day), prov)
                self._client.expire(key, TTL_SECONDS)
                self._client.expire(_provinces_key(day), TTL_SECONDS)
            self._dropped_warned = False
            return True
        except Exception as e:
            logger.warning(f"[LlmStats] flush 失败（数据已回缓冲，下轮重试）: {e}")
            with self._lock:
                for k, fields in pending.items():
                    cur = self._buffer.get(k)
                    if cur is None:
                        if len(self._buffer) >= _BUFFER_MAX_KEYS:
                            continue  # 缓冲已满，丢弃最老这批（上面已告警过）
                        self._buffer[k] = dict(fields)
                    else:
                        for f, d in fields.items():
                            cur[f] = cur.get(f, 0) + d
            return False

    # ── 查询 ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_day(s: Any) -> Optional[date]:
        """``YYYY-MM-DD`` / ``YYYYMMDD`` → date；非法返回 None。"""
        text = str(s or "").strip().replace("-", "")
        try:
            return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
        except (ValueError, IndexError):
            return None

    def _iter_days(self, start: date, end: date) -> List[str]:
        days: List[str] = []
        cur = start
        while cur <= end:
            days.append(cur.strftime("%Y%m%d"))
            cur += timedelta(days=1)
        return days

    def list_provinces(self, start: Any, end: Any) -> List[str]:
        """日期范围内出现过的省份列表（供前端筛选下拉）。异常返回 []。"""
        if not self.enabled:
            return []
        d0, d1 = self._parse_day(start), self._parse_day(end)
        if not d0 or not d1:
            return []
        try:
            out: set = set()
            for day in self._iter_days(d0, d1):
                for prov in self._client.smembers(_provinces_key(day)) or ():
                    out.add(str(prov))
            return sorted(out)
        except Exception as e:
            logger.warning(f"[LlmStats] list_provinces 查询失败: {e}")
            return []

    def query_daily(
        self, start: Any, end: Any, province: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """按日期范围查询统计行（每行 = 省份 × 日期）。

        返回 ``[{date, province, calls, success, fail, prompt_tokens,
        completion_tokens, avg_elapsed_ms, intents: {intent: {...}}}]``，
        按 (date, province) 排序。任何 Redis 异常只告警并返回 []。
        """
        if not self.enabled:
            return []
        d0, d1 = self._parse_day(start), self._parse_day(end)
        if not d0 or not d1 or d0 > d1:
            return []
        prov_filter = str(province or "").strip()
        rows: List[Dict[str, Any]] = []
        try:
            for day in self._iter_days(d0, d1):
                if prov_filter:
                    provinces = [prov_filter]
                else:
                    provinces = sorted(
                        str(p) for p in (self._client.smembers(_provinces_key(day)) or ())
                    )
                for prov in provinces:
                    raw = self._client.hgetall(_hash_key(day, prov)) or {}
                    if not raw:
                        continue
                    rows.append(self._row_from_hash(day, prov, raw))
        except Exception as e:
            logger.warning(f"[LlmStats] query_daily 查询失败: {e}")
            return []
        return rows

    @staticmethod
    def _row_from_hash(day: str, prov: str, raw: Dict[Any, Any]) -> Dict[str, Any]:
        """单日单省 HASH → 查询行（整体字段 + 意图细分）。"""
        totals: Dict[str, int] = {f: 0 for f in _TOTAL_FIELDS}
        intents: Dict[str, Dict[str, int]] = {}
        for field, val in raw.items():
            f = str(field)
            try:
                n = int(val)
            except (TypeError, ValueError):
                continue
            if "@" in f:
                prefix, _, intent = f.partition("@")
                if prefix in _INTENT_FIELD_PREFIXES and intent:
                    slot = intents.setdefault(intent, {
                        "calls": 0, "fail": 0,
                        "prompt_tokens": 0, "completion_tokens": 0,
                    })
                    slot[prefix] = slot.get(prefix, 0) + n
            elif f in totals:
                totals[f] += n
        calls = totals["calls"]
        return {
            "date": f"{day[:4]}-{day[4:6]}-{day[6:8]}",
            "province": prov,
            "calls": calls,
            "success": totals["success"],
            "fail": totals["fail"],
            "prompt_tokens": totals["prompt_tokens"],
            "completion_tokens": totals["completion_tokens"],
            "avg_elapsed_ms": round(totals["elapsed_ms_sum"] / calls, 1) if calls else 0,
            "intents": intents,
        }


# 全局单例
llm_stats = LlmStats()
