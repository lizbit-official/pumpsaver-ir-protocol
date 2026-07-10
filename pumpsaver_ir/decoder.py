"""Reference decoder for the SymCom / Littelfuse PumpSaver Plus IR broadcast.

The device continuously broadcasts its internal registers over baseband IR
(no 38 kHz carrier) as 5,000-baud NRZ. Runs of identical bits appear as
single pulses whose *width* encodes the run length; a fixed half-bit skew
between the two signal levels makes every measured width an odd multiple
of ~100 µs.

Each pulse burst (separated by >8 ms of idle) is one 32-bit word:

    [ 0x90 ] [ register : 8 ] [ value : 16, big-endian ]

transmitted MSB-first with trailing idle-level (logical 0) bits omitted,
so words are variable-length on the wire and must be right-padded with
zeros to 32 bits. A sync word 0x90FFAAAA precedes every 4 data words.

See PROTOCOL.md for the full specification and evidence.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Iterable, Iterator, Literal, Sequence

BIT_US = 202.0        # nominal 200 µs (5,000 baud); fitted 201.5-202.9 on test unit
HALF_BIT_US = BIT_US / 2
SEPARATOR_US = 8000   # inter-word idle threshold (real separators are 11-16 ms)
WORD_BITS = 32
HEADER = 0x90
SYNC_REG = 0xFF
SYNC_VALUE = 0xAAAA
MAX_RUN = 28          # a run never spans more than the 28 bits after the '1001' prefix
# Multiplier from a stored duration to microseconds. Early captures from one
# receiver pipeline stored values 10x too large, hence the 0.1 candidate.
AUTO_TIMING_SCALES = (1.0, 0.1)

TimingScale = float | Literal["auto"]


class DecodeError(ValueError):
    """A pulse burst that does not parse as a protocol word."""


@dataclass(frozen=True)
class Word:
    """One decoded 32-bit word."""
    reg: int          # register index 0x01-0x75, or 0xFF for sync
    value: int        # 16-bit register value
    nbits: int        # bits present on the wire before zero-padding

    @property
    def is_sync(self) -> bool:
        return self.reg == SYNC_REG and self.value == SYNC_VALUE


def _timing_parameters(timing_scale: float,
                       bit_us: float,
                       edge_skew_us: float | None,
                       separator_us: float = SEPARATOR_US) -> tuple[float, float]:
    """Validate timing parameters and return ``(scale, edge_skew_us)``."""
    if (not isinstance(timing_scale, (int, float))
            or not math.isfinite(timing_scale)
            or timing_scale <= 0):
        raise DecodeError(f"timing_scale must be a positive finite number, got {timing_scale!r}")
    if (not isinstance(bit_us, (int, float))
            or not math.isfinite(bit_us)
            or bit_us <= 0):
        raise DecodeError(f"bit_us must be positive and finite, got {bit_us!r}")
    if edge_skew_us is None:
        edge_skew_us = bit_us / 2
    if (not isinstance(edge_skew_us, (int, float))
            or not math.isfinite(edge_skew_us)
            or edge_skew_us < 0
            or edge_skew_us >= bit_us):
        raise DecodeError(
            f"edge_skew_us must be finite and in [0, bit_us), got {edge_skew_us!r}"
        )
    if (not isinstance(separator_us, (int, float))
            or not math.isfinite(separator_us)
            or separator_us <= bit_us):
        raise DecodeError(
            f"separator_us must be finite and greater than bit_us, got {separator_us!r}"
        )
    return float(timing_scale), float(edge_skew_us)


def _run_length(duration_us: float,
                idle_level: bool,
                bit_us: float = BIT_US,
                edge_skew_us: float | None = None) -> int:
    """Number of bits in one pulse, correcting the half-bit edge skew.

    Idle-level pulses (the level the line rests at between words) measure
    ~half a bit short; active-level pulses measure ~half a bit long
    (each active edge is delayed ~100 µs through the receiver).
    """
    _, edge_skew_us = _timing_parameters(1.0, bit_us, edge_skew_us)
    if idle_level:
        n = round((duration_us + edge_skew_us) / bit_us)
    else:
        n = round((duration_us - edge_skew_us) / bit_us)
    if n < 1 or n > MAX_RUN:
        raise DecodeError(f"implausible run: {duration_us:.0f}us -> {n} bits")
    return n


def burst_to_word(pulses: Sequence[int | float],
                  idle_positive: bool = True,
                  *,
                  timing_scale: float = 1.0,
                  bit_us: float = BIT_US,
                  edge_skew_us: float | None = None,
                  strict: bool = True) -> Word:
    """Decode one pulse burst (timings between two >8 ms separators).

    ``pulses`` are signed durations (alternating sign, one entry per level
    change). ``timing_scale`` is the number of microseconds per stored unit:
    use ``1`` for microseconds or ``0.1`` for the observed legacy captures
    whose duration values are tenfold too large.
    ``idle_positive`` says which sign carries the inter-word idle level (True
    for captures made with the recommended receiver setup; see
    :func:`detect_polarity`). ``bit_us`` and ``edge_skew_us`` may be adjusted
    for another transmitter/receiver pair. Normal strict decoding accepts
    only data registers 0x01-0x75 and the exact sync word. Set ``strict=False``
    only for exploratory/raw decoding of another model's register space; the
    header, pulse ordering, and timing checks still apply.
    """
    timing_scale, edge_skew_us = _timing_parameters(
        timing_scale, bit_us, edge_skew_us
    )
    if not pulses:
        raise DecodeError("empty pulse burst")

    bits: list[str] = []
    total = 0
    previous_positive: bool | None = None
    for index, t in enumerate(pulses):
        if not isinstance(t, (int, float)) or not math.isfinite(t):
            raise DecodeError(f"non-finite pulse at index {index}: {t!r}")
        if t == 0:
            raise DecodeError(f"zero-length pulse at index {index}")
        positive = t > 0
        if previous_positive == positive:
            sign = "+" if positive else "-"
            raise DecodeError(
                f"non-alternating pulse signs at index {index}: consecutive {sign} pulses"
            )
        previous_positive = positive
        is_idle = positive == idle_positive
        if index == 0 and is_idle:
            raise DecodeError("burst starts at idle level (expected active header pulse)")
        duration_us = abs(t) * timing_scale
        n = _run_length(duration_us, is_idle, bit_us, edge_skew_us)
        total += n
        if total > WORD_BITS:
            raise DecodeError(f"burst exceeds {WORD_BITS} bits")
        # idle level = logical 0, active level = logical 1
        bits.append(("0" if is_idle else "1") * n)
    word = int("".join(bits).ljust(WORD_BITS, "0"), 2)
    if word >> 24 != HEADER:
        raise DecodeError(f"bad header 0x{word >> 24:02x} (expected 0x{HEADER:02x})")
    reg = (word >> 16) & 0xFF
    value = word & 0xFFFF
    if strict and reg == SYNC_REG:
        if value != SYNC_VALUE:
            raise DecodeError(
                f"corrupt sync word: register 0x{SYNC_REG:02x} has value 0x{value:04x} "
                f"(expected 0x{SYNC_VALUE:04x})"
            )
    elif strict and not 0x01 <= reg <= 0x75:
        raise DecodeError(f"invalid data register 0x{reg:02x} (expected 0x01-0x75)")
    return Word(reg=reg, value=value, nbits=total)


def split_bursts(data: Sequence[int | float],
                 *,
                 timing_scale: float = 1.0,
                 separator_us: float = SEPARATOR_US) -> Iterator[list[int | float]]:
    """Split one capture (e.g. an ESPHome remote_receiver raw dump) into
    pulse bursts at >``separator_us`` idle gaps.

    Returned durations stay in their original units; pass the same
    ``timing_scale`` to :func:`burst_to_word`.
    """
    timing_scale, _ = _timing_parameters(timing_scale, BIT_US, None, separator_us)
    burst: list[int | float] = []
    for t in data:
        if not isinstance(t, (int, float)) or not math.isfinite(t):
            raise DecodeError(f"non-finite timing value: {t!r}")
        if abs(t) * timing_scale > separator_us:
            if burst:
                yield burst
            burst = []
        else:
            burst.append(t)
    if burst:
        yield burst


def detect_polarity(data: Sequence[int | float],
                    *,
                    timing_scale: float = 1.0,
                    separator_us: float = SEPARATOR_US) -> bool:
    """Return idle_positive for this capture.

    The inter-word separators sit at the idle level, so their sign tells us
    the polarity regardless of how the receiver/firmware was configured. A
    separator-free record can still be resolved safely: every protocol word
    starts with the active/logical-1 pulse, so the idle sign is the opposite
    of the first pulse's sign.
    """
    timing_scale, _ = _timing_parameters(timing_scale, BIT_US, None, separator_us)
    pos = 0
    neg = 0
    for index, t in enumerate(data):
        if not isinstance(t, (int, float)) or not math.isfinite(t):
            raise DecodeError(f"non-finite timing value at index {index}: {t!r}")
        if abs(t) * timing_scale > separator_us:
            pos += t > 0
            neg += t < 0
    if pos == neg:
        if pos:
            raise DecodeError("cannot detect polarity: separator signs are ambiguous")
        if not data:
            raise DecodeError("cannot detect polarity: empty capture")
        first = data[0]
        if first == 0:
            raise DecodeError("cannot detect polarity: first pulse has zero length")
        return first < 0
    return pos > neg


def _decode_at_scale(data: Sequence[int | float],
                     idle_positive: bool | None,
                     timing_scale: float,
                     bit_us: float,
                     edge_skew_us: float | None,
                     separator_us: float,
                     strict: bool = True) -> list[Word | DecodeError]:
    """Decode at one known scale. Used by explicit and automatic modes."""
    if idle_positive is None:
        idle_positive = detect_polarity(
            data, timing_scale=timing_scale, separator_us=separator_us
        )
    items: list[Word | DecodeError] = []
    for burst in split_bursts(
        data, timing_scale=timing_scale, separator_us=separator_us
    ):
        try:
            items.append(burst_to_word(
                burst,
                idle_positive,
                timing_scale=timing_scale,
                bit_us=bit_us,
                edge_skew_us=edge_skew_us,
                strict=strict,
            ))
        except DecodeError as exc:
            items.append(exc)
    return items


def _choose_timing_scale(
        scores: dict[float, tuple[int, int, int]],
        failures: dict[float, str]) -> float:
    """Choose a scale from ``valid, errors, syncs`` aggregate scores."""
    candidates = [
        (valid, errors, syncs, scale)
        for scale, (valid, errors, syncs) in scores.items()
        if valid
    ]
    if not candidates:
        why = "; ".join(
            f"{scale:g}x: {failures.get(scale, 'no valid protocol words')}"
            for scale in AUTO_TIMING_SCALES
        )
        hint = (
            "pass timing_scale=1 for microseconds or timing_scale=0.1 for tenfold legacy timings"
        )
        raise DecodeError(f"cannot auto-detect timing scale ({why}); {hint}")

    # Prefer more strict frames, fewer malformed bursts, then exact syncs.
    # A complete tie goes to scale 1 because it is the documented/native unit
    # and avoids silently rescaling noise.
    valid, _, syncs, scale = max(
        candidates,
        key=lambda candidate: (
            candidate[0], -candidate[1], candidate[2], candidate[3] == 1.0
        ),
    )
    if scale != 1.0 and valid < 2 and syncs == 0:
        raise DecodeError(
            "timing scale 0.1 matched only one data word; refusing low-confidence "
            "automatic rescaling (pass timing_scale=0.1 explicitly if the units are known)"
        )
    return scale


def detect_timing_scale_records(
        records: Iterable[Sequence[int | float]],
        *,
        bit_us: float = BIT_US,
        edge_skew_us: float | None = None,
        separator_us: float = SEPARATOR_US) -> float:
    """Detect one timing scale from all records in a capture file.

    Evidence is accumulated across record boundaries. This matters for legacy
    NDJSON files that stored one separator-free word per line: two such valid
    lines satisfy the non-native confidence guard even though neither line
    does alone. Malformed records count against a candidate but do not prevent
    later records from supplying valid evidence.
    """
    _timing_parameters(1.0, bit_us, edge_skew_us, separator_us)
    mutable_scores = {
        scale: {"valid": 0, "errors": 0, "syncs": 0}
        for scale in AUTO_TIMING_SCALES
    }
    failures: dict[float, str] = {}

    for data in records:
        for scale in AUTO_TIMING_SCALES:
            score = mutable_scores[scale]
            try:
                items = _decode_at_scale(
                    data, None, scale, bit_us, edge_skew_us, separator_us
                )
            except DecodeError as exc:
                score["errors"] += 1
                failures.setdefault(scale, str(exc))
                continue

            valid = sum(isinstance(item, Word) for item in items)
            score["valid"] += valid
            score["errors"] += len(items) - valid
            score["syncs"] += sum(
                isinstance(item, Word) and item.is_sync for item in items
            )
            if not valid and scale not in failures:
                failures[scale] = str(items[0]) if items else "no pulse bursts"

    scores = {
        scale: (score["valid"], score["errors"], score["syncs"])
        for scale, score in mutable_scores.items()
    }
    return _choose_timing_scale(scores, failures)


def detect_timing_scale(data: Sequence[int | float],
                        idle_positive: bool | None = None,
                        *,
                        bit_us: float = BIT_US,
                        edge_skew_us: float | None = None,
                        separator_us: float = SEPARATOR_US) -> float:
    """Detect normal µs (``1``) versus tenfold legacy timings (``0.1``).

    Both candidates are decoded using the protocol's framing rules. The scale
    producing the most valid words (then the fewest malformed bursts) wins.
    ``timing_scale`` always means microseconds per raw unit. Thus the legacy
    representation needs ``0.1`` because its stored durations are 10x the
    physical microseconds. Separator-free records derive polarity from the
    required active first pulse.

    Raise :class:`DecodeError` when neither candidate yields a valid protocol
    word. Non-native detection still requires two data words or an exact sync
    word; use :func:`detect_timing_scale_records` to accumulate that evidence
    across NDJSON records.
    """
    _timing_parameters(1.0, bit_us, edge_skew_us, separator_us)
    scores: dict[float, tuple[int, int, int]] = {}
    failures: dict[float, str] = {}
    for scale in AUTO_TIMING_SCALES:
        try:
            items = _decode_at_scale(
                data, idle_positive, scale, bit_us, edge_skew_us, separator_us
            )
        except DecodeError as exc:
            scores[scale] = (0, 1, 0)
            failures[scale] = str(exc)
            continue
        valid = sum(isinstance(item, Word) for item in items)
        errors = len(items) - valid
        syncs = sum(isinstance(item, Word) and item.is_sync for item in items)
        scores[scale] = (valid, errors, syncs)
        if not valid:
            failures[scale] = str(items[0]) if items else "no pulse bursts"
    return _choose_timing_scale(scores, failures)


def decode_capture(data: Sequence[int | float],
                   idle_positive: bool | None = None,
                   *,
                   timing_scale: TimingScale = "auto",
                   bit_us: float = BIT_US,
                   edge_skew_us: float | None = None,
                   separator_us: float = SEPARATOR_US,
                   strict: bool = True) -> Iterator[Word | DecodeError]:
    """Decode one capture's timing array into Words.

    Yields a Word per burst, or the DecodeError for bursts that fail
    (callers wanting only clean words can filter on isinstance).

    ``timing_scale="auto"`` (the default) detects raw microseconds versus
    legacy timings stored tenfold too large. Set it explicitly to ``1`` or
    ``0.1`` for deterministic handling. ``bit_us``, ``edge_skew_us``, and
    ``separator_us`` make the physical timing model configurable without
    changing the wire format.
    ``strict=False`` exposes header-valid words outside the known 233-P map
    for research; when combining relaxed mode with auto timing, scale
    detection remains strict so arbitrary noise cannot select a scale. Pass
    an explicit timing scale when exploring a wholly different register map.

    Raises DecodeError before yielding anything if scale/polarity cannot be
    detected (for example, an empty/noise capture).
    """
    if timing_scale == "auto":
        scale = detect_timing_scale(
            data,
            idle_positive,
            bit_us=bit_us,
            edge_skew_us=edge_skew_us,
            separator_us=separator_us,
        )
    elif isinstance(timing_scale, (int, float)):
        scale, _ = _timing_parameters(
            timing_scale, bit_us, edge_skew_us, separator_us
        )
    else:
        raise DecodeError(
            f"timing_scale must be 'auto' or a positive number, got {timing_scale!r}"
        )
    yield from _decode_at_scale(
        data, idle_positive, scale, bit_us, edge_skew_us, separator_us, strict
    )


def iter_ndjson(path: str) -> Iterator[tuple[float | None, list[int | float]]]:
    """Yield (timestamp_ms, timings) from an NDJSON capture file.

    Accepts lines of the form {"ts": ..., "data": [...]} (as produced by
    the original MQTT capture tooling) or bare JSON arrays of timings.
    """
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)
            if isinstance(obj, list):
                yield None, obj
            else:
                yield obj.get("ts"), obj["data"]


def registers_from_words(words: Iterable[Word]) -> dict[int, int]:
    """Fold a word stream into the latest value per register (syncs skipped)."""
    regs: dict[int, int] = {}
    for w in words:
        if not w.is_sync:
            regs[w.reg] = w.value
    return regs
