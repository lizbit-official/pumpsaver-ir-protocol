"""Decoder tests. Run with pytest, or directly: python tests/test_decoder.py"""

import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pumpsaver_ir import (  # noqa: E402
    BIT_US,
    DecodeError,
    Word,
    burst_to_word,
    decode_capture,
    detect_polarity,
    iter_ndjson,
    registers_from_words,
)

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "sample_capture.ndjson")
HALF = BIT_US / 2


def encode_word(reg: int, value: int, jitter_us: float = 0.0) -> list[int]:
    """Synthetic transmitter: (reg, value) -> signed timings, idle-positive."""
    word = (0x90 << 24) | (reg << 16) | value
    bits = f"{word:032b}".rstrip("0") or "0"
    runs: list[tuple[str, int]] = []
    for b in bits:
        if runs and runs[-1][0] == b:
            runs[-1] = (b, runs[-1][1] + 1)
        else:
            runs.append((b, 1))
    out = []
    for b, n in runs:
        # active ('1') pulses measure half a bit long, idle ('0') half short
        d = n * BIT_US + (HALF if b == "1" else -HALF) + random.uniform(-jitter_us, jitter_us)
        out.append(-round(d) if b == "1" else round(d))
    return out


def test_roundtrip_exhaustive_registers():
    for reg in list(range(0x01, 0x76)) + [0xFF]:
        for value in (0, 1, 0xAAAA, 0x5555, 0xFFFF, 11179, 57671, 2439):
            w = burst_to_word(encode_word(reg, value), idle_positive=True)
            assert (w.reg, w.value) == (reg, value), f"reg={reg:#x} value={value:#x} -> {w}"


def test_roundtrip_random_with_jitter():
    random.seed(42)
    for _ in range(5000):
        reg, value = random.randrange(0x01, 0x76), random.randrange(0x10000)
        w = burst_to_word(encode_word(reg, value, jitter_us=45.0), idle_positive=True)
        assert (w.reg, w.value) == (reg, value)


def test_sync_word():
    w = burst_to_word(encode_word(0xFF, 0xAAAA), idle_positive=True)
    assert w.is_sync


def test_bad_header_rejected():
    # '101' pads to 0xA0000000 — wrong header, must raise
    try:
        burst_to_word([-310, 95, -310], idle_positive=True)
    except DecodeError as e:
        assert "header" in str(e)
    else:
        raise AssertionError("bad header was not rejected")


def test_header_only_burst_decodes_as_reg0():
    # A bare '1001' burst pads to 0x90000000: valid header, reg 0, value 0.
    # Register 0 never occurs on the wire, so only noise can produce this;
    # callers wanting to be strict can filter reg == 0.
    w = burst_to_word([-310, 300, -310], idle_positive=True)
    assert (w.reg, w.value, w.nbits) == (0, 0, 4)


def test_sample_capture_decodes_perfectly():
    words, errors, syncs = 0, 0, 0
    all_words: list[Word] = []
    for _, data in iter_ndjson(SAMPLE):
        assert detect_polarity(data) is True
        for item in decode_capture(data):
            if isinstance(item, DecodeError):
                errors += 1
                continue
            words += 1
            syncs += item.is_sync
            all_words.append(item)
    assert errors == 0, f"{errors} undecodable bursts"
    assert words == 594 and syncs == 120

    regs = registers_from_words(all_words)
    assert len(regs) == 117
    # Known values from this capture (2025-11-25, pump idle)
    assert regs[0x0F] == 11179        # pump-start counter
    assert regs[0x17] == 57671        # cumulative run-minutes
    assert regs[0x07] == 45060        # 751 h * 60
    assert 2400 <= regs[0x11] <= 2450  # line voltage x10, ~243 V idle
    assert regs[0x12] < 30             # idle current x100
    assert regs[0x10] < 40             # idle power, W


def test_polarity_flip_detected():
    for _, data in iter_ndjson(SAMPLE):
        flipped = [-t for t in data]
        assert detect_polarity(flipped) is False
        words = [w for w in decode_capture(flipped) if isinstance(w, Word)]
        assert len(words) > 0
        break


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
