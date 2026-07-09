"""CLI: decode PumpSaver IR captures.

Usage:
    python -m pumpsaver_ir table  capture.ndjson   # latest value per register
    python -m pumpsaver_ir events capture.ndjson   # register changes over time
    python -m pumpsaver_ir csv    capture.ndjson [regs]  # time series (default: live telemetry)
    python -m pumpsaver_ir stats  capture.ndjson   # decode quality statistics
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import resources

from .decoder import DecodeError, Word, decode_capture, iter_ndjson


def _load_semantics() -> dict[int, dict]:
    with resources.files("pumpsaver_ir").joinpath("registers.json").open() as f:
        spec = json.load(f)
    return {int(r["reg"], 16): r for r in spec["registers"]}


def _fmt(reg: int, value: int, sem: dict[int, dict]) -> str:
    r = sem.get(reg)
    if r and r.get("scale", 1) != 1:
        return f"{value / r['scale']:g} {r.get('unit', '')} ({r['name']})".rstrip()
    if r:
        return f"{value} {r.get('unit', '')} ({r['name']})".rstrip()
    return str(value)


def _words(path: str):
    """Yield (ts, Word) over a capture file, collecting error counts."""
    stats = {"words": 0, "errors": 0, "sync": 0}
    for ts, data in iter_ndjson(path):
        for item in decode_capture(data):
            if isinstance(item, DecodeError):
                stats["errors"] += 1
                continue
            stats["words"] += 1
            if item.is_sync:
                stats["sync"] += 1
                continue
            yield ts, item
    _words.stats = stats  # type: ignore[attr-defined]


def cmd_table(path: str) -> None:
    sem = _load_semantics()
    latest: dict[int, int] = {}
    counts: dict[int, int] = {}
    for _, w in _words(path):
        latest[w.reg] = w.value
        counts[w.reg] = counts.get(w.reg, 0) + 1
    for reg in sorted(latest):
        v = latest[reg]
        print(f"reg 0x{reg:02X} ({reg:3d})  = {v:6d}  0x{v:04X}  n={counts[reg]:<6d} {_fmt(reg, v, sem)}")
    s = _words.stats  # type: ignore[attr-defined]
    print(f"\n{len(latest)} registers; {s['words']} words ({s['sync']} sync), {s['errors']} undecodable bursts",
          file=sys.stderr)


def cmd_events(path: str) -> None:
    sem = _load_semantics()
    last: dict[int, int] = {}
    for ts, w in _words(path):
        if last.get(w.reg) != w.value:
            old = last.get(w.reg)
            change = f"{old} -> {w.value}" if old is not None else f"= {w.value}"
            print(f"{ts}  reg 0x{w.reg:02X} {change:>16}  {_fmt(w.reg, w.value, sem)}")
            last[w.reg] = w.value


def cmd_csv(path: str, regs: list[int]) -> None:
    print("ts," + ",".join(f"reg_0x{r:02X}" for r in regs))
    current: dict[int, int] = {}
    pending = False
    last_ts = None
    for ts, w in _words(path):
        if w.reg in regs:
            current[w.reg] = w.value
            pending = True
        if ts != last_ts and pending and len(current) == len(regs):
            print(f"{ts}," + ",".join(str(current[r]) for r in regs))
            pending = False
        last_ts = ts


def cmd_stats(path: str) -> None:
    for _ in _words(path):
        pass
    s = _words.stats  # type: ignore[attr-defined]
    total = s["words"] + s["errors"]
    rate = 100.0 * s["words"] / total if total else 0.0
    print(f"bursts: {total}  decoded: {s['words']} ({rate:.2f}%)  "
          f"sync: {s['sync']}  errors: {s['errors']}")


def main() -> None:
    p = argparse.ArgumentParser(prog="pumpsaver_ir", description=__doc__)
    p.add_argument("command", choices=["table", "events", "csv", "stats"])
    p.add_argument("capture", help="NDJSON capture file")
    p.add_argument("regs", nargs="?", default="0x10,0x11,0x12,0x13",
                   help="csv mode: comma-separated register list (default live telemetry)")
    args = p.parse_args()
    if args.command == "table":
        cmd_table(args.capture)
    elif args.command == "events":
        cmd_events(args.capture)
    elif args.command == "csv":
        cmd_csv(args.capture, [int(r, 16) for r in args.regs.split(",")])
    elif args.command == "stats":
        cmd_stats(args.capture)


if __name__ == "__main__":
    main()
