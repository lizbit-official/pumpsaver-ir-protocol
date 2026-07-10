"""Reference decoder for the SymCom / Littelfuse PumpSaver Plus IR broadcast."""

from .decoder import (
    BIT_US,
    HEADER,
    SEPARATOR_US,
    SYNC_REG,
    SYNC_VALUE,
    DecodeError,
    TimingScale,
    Word,
    burst_to_word,
    decode_capture,
    detect_polarity,
    detect_timing_scale,
    detect_timing_scale_records,
    iter_ndjson,
    registers_from_words,
    split_bursts,
)

__version__ = "0.2.0"

__all__ = [
    "BIT_US",
    "HEADER",
    "SEPARATOR_US",
    "SYNC_REG",
    "SYNC_VALUE",
    "DecodeError",
    "TimingScale",
    "Word",
    "burst_to_word",
    "decode_capture",
    "detect_polarity",
    "detect_timing_scale",
    "detect_timing_scale_records",
    "iter_ndjson",
    "registers_from_words",
    "split_bursts",
]
