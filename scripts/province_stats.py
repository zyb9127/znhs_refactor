#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分省统计脚本：① 分省调用量  ② 分省并发峰值（生产 Linux 直接跑，仅标准库）。

数据源（优先）：
  logs/provinces/<省>/response_YYYY-MM-DD.jsonl
    - ts          响应完成时间
    - elapsed_ms  整请求耗时（进入 /marketing/recommend 到返回）
    - code / province / intent / script_count / trace_id
兜底（response 无数据时用 --fallback-app 打开）：
  logs/app_YYYY-MM-DD.log 里的 `[recommend] ... elapsed=xxms`
可选（--also-llm）：
  logs/provinces/<省>/llm_YYYY-MM-DD.jsonl —— 单次大模型调用层的量/并发

用法：
  # ① 分省调用量（默认全部日期、全部省）
  python3 scripts/province_stats.py calls
  python3 scripts/province_stats.py calls --date 2026-08-12
  python3 scripts/province_stats.py calls --province beijing,guangdong --by-day
  python3 scripts/province_stats.py calls --by-intent          # 细分到 省/意图
  python3 scripts/province_stats.py calls --also-llm           # 附大模型调用量

  # ② 分省并发峰值（扫描线求峰值并发及其持续窗口）
  python3 scripts/province_stats.py peak
  python3 scripts/province_stats.py peak --date 2026-08-12
  python3 scripts/province_stats.py peak --by-hour             # 每小时峰值走势
  python3 scripts/province_stats.py peak --also-llm            # 附大模型并发峰值

  # 生产环境自定义日志根
  python3 scripts/province_stats.py peak --log-root /data/znhs/logs --date 2026-08-12
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# ── 时间 / 数值工具 ───────────────────────────────────────────────

_TS_FMTS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S")


def parse_ts(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = str(s).strip()
    for fmt in _TS_FMTS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def fmt_ms(ms: float) -> str:
    return f"{ms/1000:.2f}s" if ms >= 1000 else f"{ms:.0f}ms"


def hms(dt: Optional[datetime]) -> str:
    return dt.strftime("%H:%M:%S.%f")[:-3] if dt else "-"


# ── 事件模型 ─────────────────────────────────────────────────────

@dataclass
class Event:
    """一次请求（或一次 LLM 调用）的时间窗口：start = ts - elapsed_ms, end = ts。"""
    province: str
    intent: str
    start: datetime
    end: datetime
    elapsed_ms: float
    code: int = 200
    script_count: int = 0
    trace_id: str = ""

    @property
    def day(self) -> str:
        return self.end.strftime("%Y-%m-%d")

    @property
    def ok(self) -> bool:
        return self.code == 200


def peak_window(events: List[Event]) -> Tuple[int, Optional[datetime], Optional[datetime], Optional[Event]]:
    """扫描线求峰值并发：start +1 / end -1（同刻先 -1 再 +1，避免虚高）。

    返回 (峰值, 窗口起, 窗口止, 峰值样例事件)；窗口为并发首次达到峰值的持续区间。
    """
    if not events:
        return 0, None, None, None
    points: List[Tuple[datetime, int, Event]] = []
    for e in events:
        points.append((e.end, -1, e))
        points.append((e.start, +1, e))
    points.sort(key=lambda x: (x[0], x[1]))
    cur = peak = 0
    win_start: Optional[datetime] = None
    win_end: Optional[datetime] = None
    win_open: Optional[datetime] = None
    sample: Optional[Event] = None
    for t, delta, e in points:
        cur += delta
        if cur > peak:
            peak, win_start, win_end, win_open, sample = cur, t, None, t, e
        elif cur == peak and win_open is None:
            win_open = t
        elif cur < peak and win_open is not None and win_end is None:
            win_end = t
    return peak, win_start, win_end, sample


def peak_window_str(events: List[Event]) -> str:
    pk, s, e, _ = peak_window(events)
    if pk == 0:
        return ""
    if s and e and e > s:
        return f"{hms(s)}~{hms(e)} ({(e - s).total_seconds():.1f}s)"
    return f"@{hms(s)}"


def avg_ms(events: List[Event]) -> float:
    vals = [e.elapsed_ms for e in events if e.elapsed_ms >= 0]
    return sum(vals) / len(vals) if vals else 0.0


# ── 日志读取 ─────────────────────────────────────────────────────

def _iter_jsonl(paths: Iterable[str]) -> Iterable[Dict]:
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for lineno, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        yield json.loads(line)
                    except json.JSONDecodeError:
                        print(f"[warn] JSON 解析失败 {path}:{lineno}", file=sys.stderr)
        except OSError as e:
            print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)


def _match_province(prov: str, wanted: Optional[List[str]]) -> bool:
    return not wanted or prov in wanted


def load_events(
    log_root: Path,
    kind: str,                       # "response" | "llm"
    date: Optional[str],
    provinces: Optional[List[str]],
) -> List[Event]:
    """从分省 JSONL 还原事件窗口。测试页双写的 province=test 记录一律跳过。"""
    prov_glob = "*"  # 目录层用通配，province 过滤在记录级做（记录里的 province 才是真实归属）
    pattern = str(log_root / "provinces" / prov_glob / f"{kind}_{date or '*'}.jsonl")
    files = sorted(glob.glob(pattern))
    events: List[Event] = []
    seen = set()
    for rec in _iter_jsonl(files):
        prov = str(rec.get("province") or "unknown")
        if prov == "test" or not _match_province(prov, provinces):
            continue
        tid = str(rec.get("trace_id") or "")
        stage = str(rec.get("stage") or "")
        dedup = (tid, kind, rec.get("ts"), stage) if tid else (
            kind, rec.get("ts"), rec.get("phone"), rec.get("elapsed_ms"), prov)
        if dedup in seen:
            continue
        seen.add(dedup)
        end = parse_ts(rec.get("ts") or "")
        try:
            elapsed = float(rec.get("elapsed_ms") or 0)
        except (TypeError, ValueError):
            elapsed = 0.0
        if end is None or elapsed < 0:
            continue
        try:
            code = int(rec.get("code") or (200 if rec.get("success", True) else 500))
        except (TypeError, ValueError):
            code = 0
        try:
            sc = int(rec.get("script_count") or (1 if kind == "llm" else 0))
        except (TypeError, ValueError):
            sc = 0
        events.append(Event(
            province=prov,
            intent=str(rec.get("intent") or ""),
            start=end - timedelta(milliseconds=elapsed),
            end=end,
            elapsed_ms=elapsed,
            code=code,
            script_count=sc,
            trace_id=tid,
        ))
    return events


_APP_ELAPSED_RE = re.compile(
    r"(?P<ts>\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r".*?\[recommend\].*?elapsed=(?P<ms>\d+(?:\.\d+)?)ms",
)


def load_events_from_app_log(log_root: Path, date: Optional[str]) -> List[Event]:
    """兜底：app_*.log 里的 [recommend] elapsed=xxms（无省份维度，归为 unknown）。"""
    files = sorted(glob.glob(str(log_root / (f"app_{date}.log" if date else "app_*.log"))))
    events: List[Event] = []
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    m = _APP_ELAPSED_RE.search(line)
                    if not m:
                        continue
                    end = parse_ts(m.group("ts"))
                    if end is None:
                        continue
                    elapsed = float(m.group("ms"))
                    events.append(Event(
                        province="unknown", intent="",
                        start=end - timedelta(milliseconds=elapsed), end=end,
                        elapsed_ms=elapsed,
                    ))
        except OSError as e:
            print(f"[warn] 无法读取 {path}: {e}", file=sys.stderr)
    return events


# ── 公共：分组 ───────────────────────────────────────────────────

def group_by(events: List[Event], key) -> Dict[str, List[Event]]:
    buckets: Dict[str, List[Event]] = defaultdict(list)
    for e in events:
        buckets[key(e)].append(e)
    return buckets


def _print_header(cmd: str, log_root: Path, date: Optional[str], provinces: Optional[List[str]]) -> None:
    print("=" * 64)
    print(cmd)
    print("=" * 64)
    print(f"日志根目录 : {log_root}")
    print(f"日期过滤   : {date or '全部'}")
    print(f"省份过滤   : {','.join(provinces) if provinces else '全部'}")
    print()


# ── 子命令①：分省调用量 ──────────────────────────────────────────

def cmd_calls(events: List[Event], llm_events: Optional[List[Event]], by_day: bool, by_intent: bool) -> None:
    if not events:
        print("（无调用记录）")
        return

    def _row(label: str, evs: List[Event]) -> str:
        ok = sum(1 for e in evs if e.ok)
        scripts = sum(e.script_count for e in evs)
        return (f"  {label:<26} {len(evs):>8} {ok:>8} {len(evs) - ok:>8} "
                f"{scripts:>8} {fmt_ms(avg_ms(evs)):>12}")

    print("── 分省调用量 ──")
    print(f"  {'省份':<24} {'请求数':>8} {'成功':>8} {'失败':>8} {'话术数':>8} {'平均耗时':>12}")
    by_prov = group_by(events, lambda e: e.province or "unknown")
    for prov in sorted(by_prov, key=lambda p: len(by_prov[p]), reverse=True):
        print(_row(prov, by_prov[prov]))
    print("  " + "-" * 70)
    print(_row("合计", events))

    if by_day:
        print("\n── 分省 × 日期 ──")
        print(f"  {'省份/日期':<24} {'请求数':>8} {'成功':>8} {'失败':>8} {'话术数':>8} {'平均耗时':>12}")
        for prov in sorted(by_prov):
            for day in sorted(group_by(by_prov[prov], lambda e: e.day)):
                sub = [e for e in by_prov[prov] if e.day == day]
                print(_row(f"{prov} / {day}", sub))

    if by_intent:
        print("\n── 分省 × 意图 ──")
        print(f"  {'省份/意图':<24} {'请求数':>8} {'成功':>8} {'失败':>8} {'话术数':>8} {'平均耗时':>12}")
        by_key = group_by(events, lambda e: f"{e.province}/{e.intent}" if e.intent else (e.province or "unknown"))
        for key in sorted(by_key, key=lambda k: len(by_key[k]), reverse=True):
            print(_row(key, by_key[key]))

    if llm_events is not None:
        print("\n── 分省大模型调用量（llm_*.jsonl）──")
        if not llm_events:
            print("  （无 LLM 调用记录）")
        else:
            print(f"  {'省份':<24} {'调用次数':>8} {'平均耗时':>12}")
            by_llm = group_by(llm_events, lambda e: e.province or "unknown")
            for prov in sorted(by_llm, key=lambda p: len(by_llm[p]), reverse=True):
                print(f"  {prov:<24} {len(by_llm[prov]):>8} {fmt_ms(avg_ms(by_llm[prov])):>12}")
            print(f"  {'合计':<24} {len(llm_events):>8} {fmt_ms(avg_ms(llm_events)):>12}")


# ── 子命令②：分省并发峰值 ────────────────────────────────────────

def _print_peak_table(title: str, events: List[Event]) -> None:
    print(f"── {title} ──")
    if not events:
        print("  （无数据）")
        return
    pk, _, _, sample = peak_window(events)
    print(f"  总峰值并发 : {pk}   窗口 : {peak_window_str(events) or '-'}")
    if sample:
        print(f"  峰值样例   : province={sample.province} intent={sample.intent} trace={sample.trace_id}")
    print()
    print(f"  {'省份':<20} {'请求数':>8} {'峰值并发':>8} {'平均耗时':>12}  峰值窗口")
    by_prov = group_by(events, lambda e: e.province or "unknown")
    rows = []
    for prov, evs in by_prov.items():
        ppk, _, _, _ = peak_window(evs)
        rows.append((ppk, prov, evs))
    for ppk, prov, evs in sorted(rows, reverse=True, key=lambda x: (x[0], len(x[2]))):
        print(f"  {prov:<20} {len(evs):>8} {ppk:>8} {fmt_ms(avg_ms(evs)):>12}  {peak_window_str(evs)}")


def cmd_peak(events: List[Event], llm_events: Optional[List[Event]], by_hour: bool) -> None:
    if not events and not llm_events:
        print("（无并发数据）")
        return
    _print_peak_table("请求层并发峰值（/marketing/recommend）", events)

    if by_hour and events:
        print("\n── 按小时峰值走势（请求数 / 该小时峰值并发）──")
        print(f"  {'小时':<16} {'请求数':>8} {'峰值并发':>8}")
        for hour in sorted(group_by(events, lambda e: e.start.strftime("%Y-%m-%d %H:00"))):
            sub = [e for e in events if e.start.strftime("%Y-%m-%d %H:00") == hour]
            hpk, _, _, _ = peak_window(sub)
            print(f"  {hour:<16} {len(sub):>8} {hpk:>8}  {'#' * min(hpk, 40)}")

    if llm_events is not None:
        print()
        _print_peak_table("大模型调用层并发峰值（llm_*.jsonl）", llm_events)


# ── CLI ──────────────────────────────────────────────────────────

def default_log_root() -> Path:
    try:
        cand = Path(__file__).resolve().parent.parent / "logs"
        if cand.is_dir():
            return cand
    except NameError:
        pass
    return Path.cwd() / "logs"


def _parse_provinces(raw: Optional[str]) -> Optional[List[str]]:
    if not raw:
        return None
    items = [p.strip() for p in raw.replace("，", ",").split(",") if p.strip()]
    return items or None


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--log-root", default=str(default_log_root()),
                   help="日志根目录（含 provinces/ 与 app_*.log），默认自动探测")
    p.add_argument("--date", default=None, help="日期 YYYY-MM-DD；默认全部已有日期")
    p.add_argument("--province", default=None, help="只看某些省，逗号分隔，如 beijing,guangdong")
    p.add_argument("--also-llm", action="store_true", help="附带大模型调用层（读 llm_*.jsonl）")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="分省统计：① 调用量 calls ② 并发峰值 peak（读分省 JSONL / app.log）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd")

    p_calls = sub.add_parser("calls", help="分省调用量（请求数 / 成功 / 失败 / 话术数 / 平均耗时）")
    _add_common(p_calls)
    p_calls.add_argument("--by-day", action="store_true", help="再按日期细分")
    p_calls.add_argument("--by-intent", action="store_true", help="再按意图细分（省/意图）")
    p_calls.add_argument("--fallback-app", action="store_true", help="response 无数据时回退 app_*.log")

    p_peak = sub.add_parser("peak", help="分省并发峰值（扫描线求峰值及持续窗口）")
    _add_common(p_peak)
    p_peak.add_argument("--by-hour", action="store_true", help="输出每小时峰值走势")
    p_peak.add_argument("--fallback-app", action="store_true", help="response 无数据时回退 app_*.log")

    args = parser.parse_args(argv)
    if not args.cmd:
        parser.print_help()
        return 0

    log_root = Path(args.log_root).expanduser().resolve()
    if not log_root.is_dir():
        print(f"[error] 日志根目录不存在: {log_root}", file=sys.stderr)
        print("请用 --log-root 指定，例如: --log-root /data/znhs/logs", file=sys.stderr)
        return 2

    provinces = _parse_provinces(args.province)
    events = load_events(log_root, "response", args.date, provinces)
    if not events and getattr(args, "fallback_app", False):
        print("[info] 分省 response_*.jsonl 无数据，回退解析 app_*.log …\n")
        events = load_events_from_app_log(log_root, args.date)
    llm_events = load_events(log_root, "llm", args.date, provinces) if args.also_llm else None

    if args.cmd == "calls":
        _print_header("分省调用量", log_root, args.date, provinces)
        cmd_calls(events, llm_events, by_day=args.by_day, by_intent=args.by_intent)
    else:  # peak
        _print_header("分省并发峰值", log_root, args.date, provinces)
        cmd_peak(events, llm_events, by_hour=args.by_hour)

    if not events and not (llm_events or []):
        print("\n未读到任何记录。请确认：")
        print(f"  1) 路径是否正确：{log_root}/provinces/*/response_*.jsonl")
        print("  2) 环境变量 ZNHS_PROVINCE_LOG 是否被关掉（默认开）")
        print("  3) calls/peak 可加 --fallback-app 尝试解析 app_*.log")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
