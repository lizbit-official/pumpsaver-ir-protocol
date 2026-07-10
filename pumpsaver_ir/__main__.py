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
from typing import Iterable, Iterator

from .decoder import (
    BIT_US,
    SEPARATOR_US,
    DecodeError,
    Word,
    decode_capture,
    detect_timing_scale_records,
    iter_ndjson,
)


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


def _words(path: str, **decode_options):
    """Yield ``(record_index, ts, Word)`` and collect decode statistics.

    Automatic timing-scale evidence is scored over the whole file in a
    streaming pre-pass. The decode pass then uses that fixed scale, so a
    malformed record is counted as an error instead of restarting detection
    or aborting the remaining file.
    """
    stats = {"words": 0, "errors": 0, "sync": 0}
    options = dict(decode_options)
    if options.get("timing_scale", "auto") == "auto":
        options["timing_scale"] = detect_timing_scale_records(
            (data for _, data in iter_ndjson(path)),
            bit_us=options.get("bit_us", BIT_US),
            edge_skew_us=options.get("edge_skew_us"),
            separator_us=options.get("separator_us", SEPARATOR_US),
        )

    for record_index, (ts, data) in enumerate(iter_ndjson(path)):
        try:
            items = decode_capture(data, **options)
            for item in items:
                if isinstance(item, DecodeError):
                    stats["errors"] += 1
                    continue
                stats["words"] += 1
                if item.is_sync:
                    stats["sync"] += 1
                    continue
                yield record_index, ts, item
        except DecodeError:
            # Record-level failures (for example empty data or unusable
            # polarity) do not have a burst object to yield. Count one error
            # and continue now that the file-wide scale is established.
            stats["errors"] += 1
    _words.stats = stats  # type: ignore[attr-defined]


def cmd_table(path: str, **decode_options) -> None:
    sem = _load_semantics()
    latest: dict[int, int] = {}
    counts: dict[int, int] = {}
    for _, _, w in _words(path, **decode_options):
        latest[w.reg] = w.value
        counts[w.reg] = counts.get(w.reg, 0) + 1
    for reg in sorted(latest):
        v = latest[reg]
        print(f"reg 0x{reg:02X} ({reg:3d})  = {v:6d}  0x{v:04X}  n={counts[reg]:<6d} {_fmt(reg, v, sem)}")
    s = _words.stats  # type: ignore[attr-defined]
    print(f"\n{len(latest)} registers; {s['words']} words ({s['sync']} sync), {s['errors']} undecodable bursts",
          file=sys.stderr)


def cmd_events(path: str, **decode_options) -> None:
    sem = _load_semantics()
    last: dict[int, int] = {}
    for _, ts, w in _words(path, **decode_options):
        if last.get(w.reg) != w.value:
            old = last.get(w.reg)
            change = f"{old} -> {w.value}" if old is not None else f"= {w.value}"
            print(f"{ts}  reg 0x{w.reg:02X} {change:>16}  {_fmt(w.reg, w.value, sem)}")
            last[w.reg] = w.value


def _group_csv_rows(words: Iterable[tuple[int, float | None, Word]],
                    regs: list[int]) -> Iterator[tuple[float | None, tuple[int, ...]]]:
    """Group word updates by their source-record identity.

    Timestamps are output labels, not record identifiers: distinct NDJSON
    records can legitimately have the same timestamp or no timestamp. Values
    carry forward, but a row is emitted only for a source record that updated
    at least one requested register. The final record is flushed at EOF.
    """
    current: dict[int, int] = {}
    group_record = 0
    group_ts: float | None = None
    has_group = False
    group_changed = False

    for record_index, ts, word in words:
        if not has_group:
            group_record = record_index
            group_ts = ts
            has_group = True
        elif record_index != group_record:
            if group_changed and all(reg in current for reg in regs):
                yield group_ts, tuple(current[reg] for reg in regs)
            group_record = record_index
            group_ts = ts
            group_changed = False

        if word.reg in regs:
            current[word.reg] = word.value
            group_changed = True

    if has_group and group_changed and all(reg in current for reg in regs):
        yield group_ts, tuple(current[reg] for reg in regs)


def cmd_csv(path: str, regs: list[int], **decode_options) -> None:
    print("ts," + ",".join(f"reg_0x{r:02X}" for r in regs))
    for ts, values in _group_csv_rows(_words(path, **decode_options), regs):
        print(f"{ts}," + ",".join(str(value) for value in values))


def cmd_stats(path: str, **decode_options) -> None:
    for _ in _words(path, **decode_options):
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
    p.add_argument(
        "--timing-scale",
        default="auto",
        metavar="auto|N",
        help=(
            "microseconds per raw unit (default: auto; use 0.1 for observed "
            "legacy values stored 10x too large)"
        ),
    )
    p.add_argument("--bit-us", type=float, default=BIT_US,
                   help="transmitter bit period in microseconds (default: 202)")
    p.add_argument("--edge-skew-us", type=float,
                   help="receiver edge skew in microseconds (default: half the bit period)")
    p.add_argument("--separator-us", type=float, default=SEPARATOR_US,
                   help="minimum inter-word gap in microseconds (default: 8000)")
    p.add_argument(
        "--relaxed",
        action="store_true",
        help="accept header-valid registers outside 0x01-0x75 (research/other models)",
    )
    args = p.parse_args()
    try:
        timing_scale: str | float
        if args.timing_scale == "auto":
            timing_scale = "auto"
        else:
            timing_scale = float(args.timing_scale)
        decode_options = {
            "timing_scale": timing_scale,
            "bit_us": args.bit_us,
            "edge_skew_us": args.edge_skew_us,
            "separator_us": args.separator_us,
            "strict": not args.relaxed,
        }
        if args.command == "table":
            cmd_table(args.capture, **decode_options)
        elif args.command == "events":
            cmd_events(args.capture, **decode_options)
        elif args.command == "csv":
            regs = [int(r, 16) for r in args.regs.split(",")]
            if not regs:
                raise ValueError("csv register list cannot be empty")
            cmd_csv(args.capture, regs, **decode_options)
        elif args.command == "stats":
            cmd_stats(args.capture, **decode_options)
    except (DecodeError, OSError, ValueError, json.JSONDecodeError) as exc:
        p.error(str(exc))


if __name__ == "__main__":
    main()
