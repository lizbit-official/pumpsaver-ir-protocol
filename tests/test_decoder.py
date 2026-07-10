"""Decoder tests. Run with pytest, or directly: python tests/test_decoder.py"""

import json
import os
import random
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pumpsaver_ir import (  # noqa: E402
    BIT_US,
    DecodeError,
    Word,
    burst_to_word,
    decode_capture,
    detect_polarity,
    detect_timing_scale,
    detect_timing_scale_records,
    iter_ndjson,
    registers_from_words,
)
from pumpsaver_ir.__main__ import _group_csv_rows, _words  # noqa: E402
from tools.corpus_report import analyze_capture  # noqa: E402

SAMPLE = os.path.join(os.path.dirname(__file__), "..", "examples", "sample_capture.ndjson")


@contextmanager
def raises(error_type, message: str):
    """Small stdlib-only equivalent of pytest.raises for direct execution."""
    try:
        yield
    except error_type as exc:
        assert message in str(exc), f"{message!r} not found in {str(exc)!r}"
    else:
        raise AssertionError(f"{error_type.__name__} was not raised")


def encode_word(reg: int,
                value: int,
                jitter_us: float = 0.0,
                *,
                bit_us: float = BIT_US,
                edge_skew_us: float | None = None) -> list[int]:
    """Synthetic transmitter: (reg, value) -> signed timings, idle-positive."""
    if edge_skew_us is None:
        edge_skew_us = bit_us / 2
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
        d = n * bit_us + (edge_skew_us if b == "1" else -edge_skew_us)
        d += random.uniform(-jitter_us, jitter_us)
        out.append(-round(d) if b == "1" else round(d))
    return out


def test_roundtrip_exhaustive_registers():
    for reg in range(0x01, 0x76):
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


def test_header_only_burst_is_rejected_in_strict_mode():
    # A bare '1001' burst pads to 0x90000000: valid header, reg 0, value 0.
    # Register 0 never occurs on the known wire protocol, so this is noise.
    with raises(DecodeError, "register 0x00"):
        burst_to_word([-310, 300, -310], idle_positive=True)

    # Researchers can explicitly retain header-valid unknown words.
    w = burst_to_word([-310, 300, -310], idle_positive=True, strict=False)
    assert (w.reg, w.value, w.nbits) == (0, 0, 4)


def test_out_of_range_registers_rejected_but_relaxed_mode_preserves_them():
    for reg in (0x00, 0x76, 0xFE):
        pulses = encode_word(reg, 0x1234)
        with raises(DecodeError, "invalid data register"):
            burst_to_word(pulses)
        assert burst_to_word(pulses, strict=False).reg == reg


def test_corrupt_sync_rejected_but_exact_sync_accepted():
    assert burst_to_word(encode_word(0xFF, 0xAAAA)).is_sync
    with raises(DecodeError, "corrupt sync"):
        burst_to_word(encode_word(0xFF, 0xAAAB))
    raw = burst_to_word(encode_word(0xFF, 0xAAAB), strict=False)
    assert (raw.reg, raw.value, raw.is_sync) == (0xFF, 0xAAAB, False)


def test_non_alternating_signs_and_idle_start_rejected():
    pulses = encode_word(0x10, 820)
    broken = pulses[:2] + [abs(pulses[2])] + pulses[3:]
    with raises(DecodeError, "non-alternating"):
        burst_to_word(broken)
    with raises(DecodeError, "starts at idle"):
        burst_to_word(pulses, idle_positive=False)


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


def test_auto_detects_legacy_tenfold_timings_from_small_capture():
    # Two words plus one true 12 ms separator are enough; no large fixture is
    # needed. Multiplication mimics the observed early capture pipeline, whose
    # stored duration values are ten times physical microseconds.
    micros = encode_word(0x10, 820) + [12000] + encode_word(0x11, 2410)
    legacy = [t * 10 for t in micros]

    assert detect_timing_scale(legacy) == 0.1
    auto = list(decode_capture(legacy))
    explicit = list(decode_capture(legacy, timing_scale=0.1))
    assert [(w.reg, w.value) for w in auto if isinstance(w, Word)] == [
        (0x10, 820),
        (0x11, 2410),
    ]
    assert explicit == auto


def test_single_word_and_custom_physical_timing_are_supported():
    pulses = encode_word(0x17, 57671, bit_us=250.0, edge_skew_us=80.0)
    word = burst_to_word(pulses, bit_us=250.0, edge_skew_us=80.0)
    assert (word.reg, word.value) == (0x17, 57671)

    # The required active first pulse safely reveals the polarity even when a
    # source record contains one word and no inter-word separator.
    assert detect_polarity(pulses) is True
    assert detect_polarity([-pulse for pulse in pulses]) is False
    explicit = list(decode_capture(
        pulses,
        timing_scale=1,
        bit_us=250.0,
        edge_skew_us=80.0,
    ))
    automatic = list(decode_capture(
        pulses,
        bit_us=250.0,
        edge_skew_us=80.0,
    ))
    assert explicit == automatic == [word]


def test_auto_scale_rejects_noise_and_does_not_use_relaxed_words_as_evidence():
    noise = [-310, 300, -310, 12000, -310, 300, -310]
    with raises(DecodeError, "cannot auto-detect timing scale"):
        list(decode_capture(noise))

    # An unknown register can be decoded for research, but only after the
    # caller states the units. Auto-detection deliberately uses strict frames.
    unknown_legacy = [t * 10 for t in (
        encode_word(0x76, 123) + [12000] + encode_word(0x76, 124)
    )]
    with raises(DecodeError, "cannot auto-detect timing scale"):
        list(decode_capture(unknown_legacy, strict=False))
    raw = list(decode_capture(unknown_legacy, timing_scale=0.1, strict=False))
    assert [(w.reg, w.value) for w in raw if isinstance(w, Word)] == [
        (0x76, 123),
        (0x76, 124),
    ]

    # Even a single coincidental frame is insufficient evidence for a
    # non-native rescale. Known one-word legacy input remains available via
    # the explicit multiplier.
    one_legacy_word = [t * 10 for t in encode_word(0x10, 820)]
    with raises(DecodeError, "low-confidence"):
        detect_timing_scale(one_legacy_word)
    with raises(DecodeError, "low-confidence"):
        detect_timing_scale_records([one_legacy_word])
    decoded = burst_to_word(one_legacy_word, timing_scale=0.1)
    assert (decoded.reg, decoded.value) == (0x10, 820)


def test_csv_rows_use_the_values_timestamp_and_flush_eof():
    words = [
        (0, 100.0, Word(0x10, 10, 32)),
        (0, 100.0, Word(0x11, 20, 32)),
        (1, 100.0, Word(0x10, 11, 32)),
        (2, None, Word(0x11, 21, 32)),
        (3, None, Word(0x10, 12, 32)),
    ]
    assert list(_group_csv_rows(words, [0x10, 0x11])) == [
        (100.0, (10, 20)),
        (100.0, (11, 20)),
        (None, (11, 21)),
        (None, (12, 21)),
    ]


def test_file_scale_combines_one_word_records_and_retains_malformed_record():
    legacy_records = [
        [timing * 10 for timing in encode_word(0x10, 820)],
        [timing * 10 for timing in encode_word(0x11, 2410)],
    ]
    assert detect_timing_scale_records(legacy_records) == 0.1

    with tempfile.TemporaryDirectory() as temp_dir:
        capture = Path(temp_dir) / "legacy.ndjson"
        records = [
            {"ts": None, "data": legacy_records[0]},
            {"ts": None, "data": legacy_records[1]},
            {"ts": None, "data": [0]},
        ]
        capture.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )

        decoded = list(_words(str(capture), timing_scale="auto"))
        assert [(record, ts, word.reg, word.value) for record, ts, word in decoded] == [
            (0, None, 0x10, 820),
            (1, None, 0x11, 2410),
        ]
        assert _words.stats == {"words": 2, "errors": 1, "sync": 0}

        report = analyze_capture(capture)
        assert report["timing_scale"] == 0.1
        assert report["capture_records"] == 3
        assert report["decoded_words"] == 2
        assert report["invalid_bursts"] == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all tests passed")
