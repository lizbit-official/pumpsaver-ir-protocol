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
from dataclasses import dataclass
from typing import Iterable, Iterator, Sequence

BIT_US = 202.0        # nominal 200 µs (5,000 baud); fitted 201.5-202.9 on test unit
HALF_BIT_US = BIT_US / 2
SEPARATOR_US = 8000   # inter-word idle threshold (real separators are 11-16 ms)
WORD_BITS = 32
HEADER = 0x90
SYNC_REG = 0xFF
SYNC_VALUE = 0xAAAA
MAX_RUN = 28          # a run never spans more than the 28 bits after the '1001' prefix


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


def _run_length(duration_us: float, idle_level: bool) -> int:
    """Number of bits in one pulse, correcting the half-bit edge skew.

    Idle-level pulses (the level the line rests at between words) measure
    ~half a bit short; active-level pulses measure ~half a bit long
    (each active edge is delayed ~100 µs through the receiver).
    """
    if idle_level:
        n = round((duration_us + HALF_BIT_US) / BIT_US)
    else:
        n = round((duration_us - HALF_BIT_US) / BIT_US)
    if n < 1 or n > MAX_RUN:
        raise DecodeError(f"implausible run: {duration_us:.0f}us -> {n} bits")
    return n


def burst_to_word(pulses: Sequence[int], idle_positive: bool = True) -> Word:
    """Decode one pulse burst (timings between two >8 ms separators).

    ``pulses`` are signed durations in µs (alternating sign, one entry per
    level change). ``idle_positive`` says which sign carries the inter-word
    idle level (True for captures made with the recommended receiver setup;
    see detect_polarity()).
    """
    bits: list[str] = []
    total = 0
    for t in pulses:
        if t == 0:
            raise DecodeError("zero-length pulse")
        is_idle = (t > 0) == idle_positive
        n = _run_length(abs(t), is_idle)
        total += n
        if total > WORD_BITS:
            raise DecodeError(f"burst exceeds {WORD_BITS} bits")
        # idle level = logical 0, active level = logical 1
        bits.append(("0" if is_idle else "1") * n)
    word = int("".join(bits).ljust(WORD_BITS, "0"), 2)
    if word >> 24 != HEADER:
        raise DecodeError(f"bad header 0x{word >> 24:02x} (expected 0x{HEADER:02x})")
    return Word(reg=(word >> 16) & 0xFF, value=word & 0xFFFF, nbits=total)


def split_bursts(data: Sequence[int]) -> Iterator[list[int]]:
    """Split one capture (e.g. an ESPHome remote_receiver raw dump) into
    pulse bursts at >SEPARATOR_US idle gaps."""
    burst: list[int] = []
    for t in data:
        if abs(t) > SEPARATOR_US:
            if burst:
                yield burst
            burst = []
        else:
            burst.append(t)
    if burst:
        yield burst


def detect_polarity(data: Sequence[int]) -> bool:
    """Return idle_positive for this capture.

    The inter-word separators sit at the idle level, so their sign tells us
    the polarity regardless of how the receiver/firmware was configured.
    """
    pos = sum(1 for t in data if abs(t) > SEPARATOR_US and t > 0)
    neg = sum(1 for t in data if abs(t) > SEPARATOR_US and t < 0)
    if pos == neg:
        raise DecodeError("cannot detect polarity: no unambiguous separators in capture")
    return pos > neg


def decode_capture(data: Sequence[int],
                   idle_positive: bool | None = None) -> Iterator[Word | DecodeError]:
    """Decode one capture's timing array into Words.

    Yields a Word per burst, or the DecodeError for bursts that fail
    (callers wanting only clean words can filter on isinstance).

    Raises DecodeError before yielding anything if polarity is not given
    and cannot be detected (no separators, e.g. an empty/noise capture).
    """
    if idle_positive is None:
        idle_positive = detect_polarity(data)
    for burst in split_bursts(data):
        try:
            yield burst_to_word(burst, idle_positive)
        except DecodeError as e:
            yield e


def iter_ndjson(path: str) -> Iterator[tuple[float | None, list[int]]]:
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
