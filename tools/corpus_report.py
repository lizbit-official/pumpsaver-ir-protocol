#!/usr/bin/env python3
"""Produce deterministic decode metrics for one or more NDJSON captures.

The report deliberately stays at the protocol layer: it records framing and
register observations without assigning unverified meanings to registers.
Use --check-manifest to verify a checked-in corpus manifest and its hashes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from pumpsaver_ir import (
    DecodeError,
    Word,
    decode_capture,
    detect_timing_scale_records,
    iter_ndjson,
)


FORMAT = "pumpsaver-corpus-report-v1"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as capture:
        for chunk in iter(lambda: capture.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def analyze_capture(path: Path, display_path: str | None = None) -> dict[str, Any]:
    capture_records = 0
    decoded_words = 0
    sync_words = 0
    invalid_bursts = 0
    register_changes = 0
    first_timestamp = None
    last_timestamp = None
    latest: dict[int, int] = {}

    # Score both known timing representations concurrently in a streaming
    # pre-pass, then keep the chosen scale fixed for every source record.
    timing_scale = detect_timing_scale_records(
        (timings for _, timings in iter_ndjson(str(path)))
    )

    for timestamp, timings in iter_ndjson(str(path)):
        capture_records += 1
        if capture_records == 1:
            first_timestamp = timestamp
        last_timestamp = timestamp
        try:
            for item in decode_capture(timings, timing_scale=timing_scale):
                if isinstance(item, DecodeError):
                    invalid_bursts += 1
                    continue
                if not isinstance(item, Word):
                    raise TypeError(f"unexpected decoder result: {type(item)!r}")
                decoded_words += 1
                if item.is_sync:
                    sync_words += 1
                    continue
                if item.reg in latest and latest[item.reg] != item.value:
                    register_changes += 1
                latest[item.reg] = item.value
        except DecodeError:
            # Empty or otherwise unsplittable records have no burst result to
            # count. Once the file scale is known, retain them as one invalid
            # record and continue the reproducibility report.
            invalid_bursts += 1

    total_bursts = decoded_words + invalid_bursts
    return {
        "path": display_path if display_path is not None else str(path),
        "sha256": sha256_file(path),
        "capture_records": capture_records,
        "timing_scale": timing_scale,
        "first_timestamp": first_timestamp,
        "last_timestamp": last_timestamp,
        "total_bursts": total_bursts,
        "decoded_words": decoded_words,
        "data_words": decoded_words - sync_words,
        "sync_words": sync_words,
        "invalid_bursts": invalid_bursts,
        "decode_rate_percent": round(100.0 * decoded_words / total_bursts, 6)
        if total_bursts
        else 0.0,
        "distinct_registers": len(latest),
        "register_changes": register_changes,
        "latest_registers": {
            f"0x{register:02X}": value for register, value in sorted(latest.items())
        },
    }


def compare_subset(actual: Any, expected: Any, location: str) -> list[str]:
    """Return differences while allowing a manifest to specify a subset."""
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return [f"{location}: expected an object, got {type(actual).__name__}"]
        errors: list[str] = []
        for key, value in expected.items():
            child = f"{location}.{key}"
            if key not in actual:
                errors.append(f"{child}: missing")
            else:
                errors.extend(compare_subset(actual[key], value, child))
        return errors
    if actual != expected:
        return [f"{location}: expected {expected!r}, got {actual!r}"]
    return []


def check_manifest(path: Path) -> tuple[dict[str, Any], list[str]]:
    with path.open(encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    if manifest.get("format") != "pumpsaver-corpus-manifest-v1":
        raise ValueError("unsupported or missing corpus manifest format")

    reports = []
    errors = []
    for index, entry in enumerate(manifest.get("captures", [])):
        relative_path = entry["path"]
        capture_path = REPOSITORY_ROOT / relative_path
        report = analyze_capture(capture_path, relative_path)
        reports.append(report)
        if report["sha256"] != entry["sha256"]:
            errors.append(
                f"captures[{index}].sha256: expected {entry['sha256']}, "
                f"got {report['sha256']}"
            )
        errors.extend(
            compare_subset(report, entry.get("expect", {}), f"captures[{index}].expect")
        )

    return {
        "format": FORMAT,
        "manifest": str(path),
        "verified": not errors,
        "captures": reports,
    }, errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("captures", nargs="*", type=Path, help="NDJSON capture paths")
    parser.add_argument(
        "--check-manifest",
        type=Path,
        metavar="FILE",
        help="verify hashes and expected metrics from a v1 corpus manifest",
    )
    args = parser.parse_args()
    if args.check_manifest and args.captures:
        parser.error("capture paths and --check-manifest are mutually exclusive")
    if not args.check_manifest and not args.captures:
        parser.error("provide at least one capture path or --check-manifest")
    return args


def main() -> int:
    args = parse_args()
    if args.check_manifest:
        report, errors = check_manifest(args.check_manifest)
    else:
        report = {
            "format": FORMAT,
            "captures": [analyze_capture(path) for path in args.captures],
        }
        errors = []
    print(json.dumps(report, indent=2, sort_keys=True))
    for error in errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
