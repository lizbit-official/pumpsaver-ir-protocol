# pumpsaver-ir-protocol

Protocol specification and reference decoder for the IR data broadcast of
**SymCom / Littelfuse PumpSaver Plus** pump-protection relays, reverse
engineered on a 233-P. The same devices ship rebranded as **Pentek / Pentair
SPP-series** (SPP-233P, SPP-235P, SD-F30x; also sold under Berkeley, Myers and
Sta-Rite labels) and as **Goulds / CentriPro "PumpSaver by SymCom"** units.

These relays constantly broadcast their internal state over a baseband IR link
meant for SymCom's discontinued "Informer" handheld:

- live **voltage, current, power, power factor**
- device cumulative **pump-start** and **run-time** counters
- trip-point configuration and the last-20-faults history

This repo documents that protocol, apparently for the first time anywhere, and
provides a tested reference decoder.

**Want this in Home Assistant?** See the companion repo
[esphome-pumpsaver](https://github.com/lizbit-official/esphome-pumpsaver):
a ready-made ESPHome component (an ESP32 plus a $0.30 IR phototransistor
pointed at the device).

## The protocol at a glance

5,000-baud NRZ over baseband IR (no carrier), where each pulse's *width*
encodes a run of identical bits. Every burst is one 32-bit word:

```
0x90 | register:8 | value:16 (big-endian)
```

with trailing zero bits absorbed into the gap. 117 registers broadcast
cyclically: live telemetry every ~1.5 s, everything else every ~5.8 s. No
checksums; state is fully reconstructible from ~6 s of signal. The fault
history decodes as 20 four-bit codes, 19 (W, V, A) trip snapshots, and 20
run-clock timestamps.

Full details, evidence, and the register map with confidence levels:
[PROTOCOL.md](PROTOCOL.md). Machine-readable register map:
[`pumpsaver_ir/registers.json`](pumpsaver_ir/registers.json).

## Quick start

```console
$ git clone https://github.com/lizbit-official/pumpsaver-ir-protocol && cd pumpsaver-ir-protocol
$ python3 -m pumpsaver_ir table examples/sample_capture.ndjson
...
reg 0x0F ( 15)  =  11179  0x2BAB  n=10     11179 starts (pump_starts)
reg 0x10 ( 16)  =     26  0x001A  n=10     26 W (power)
reg 0x11 ( 17)  =   2439  0x0987  n=10     243.9 V (voltage)
reg 0x12 ( 18)  =     14  0x000E  n=10     0.14 A (current)
reg 0x13 ( 19)  =    781  0x030D  n=10     0.781  (power_factor)
...
reg 0x17 ( 23)  =  57671  0xE147  n=11     57671 min (run_minutes)
...
117 registers; 594 words (120 sync), 0 undecodable bursts
```

Other commands: `events` (register changes over time), `csv` (time series),
`stats` (decode quality). CSV rows are grouped under the timestamp of the
capture record that supplied their updates (with the final record flushed at
EOF); records remain distinct even when timestamps are equal or absent. No
dependencies beyond Python 3.10+.

Capture timings are normally microseconds. One early receiver pipeline stored
duration values ten times too large; the CLI checks both that legacy model and
normal µs against valid protocol frames across the complete input file by
default. The option is a
raw-to-microsecond multiplier, so units can also be stated explicitly:

```console
$ python3 -m pumpsaver_ir stats early-capture.ndjson --timing-scale 0.1
```

Advanced timing overrides are available as `--bit-us`, `--edge-skew-us`, and
`--separator-us`; run `python3 -m pumpsaver_ir --help` for details.
To avoid silently rescaling noise, automatic legacy detection requires at
least two valid data words (or an exact sync word); use an explicit multiplier
for a known one-word legacy sample.

### Library use

```python
from pumpsaver_ir import decode_capture, registers_from_words, Word

words = [w for w in decode_capture(timings, timing_scale="auto")
         if isinstance(w, Word)]
regs = registers_from_words(words)
print(f"{regs[0x11] / 10} V, {regs[0x10]} W, {regs[0x0F]} starts")
```

`timings` is a list of signed pulse durations (positive/negative are the two IR
levels), e.g. an ESPHome `remote_receiver` raw dump. Polarity and whether the
durations are normal µs or the observed tenfold legacy values are auto-detected
from separators, the required active first pulse in a separator-free record,
and valid frames. Set `timing_scale=1` or `timing_scale=0.1` to make units
explicit. A single legacy word still needs an explicit scale because it cannot
meet the intentional two-word non-native confidence guard.

Normal decoding is intentionally strict: it accepts data registers 0x01-0x75
and only the exact `0x90FFAAAA` sync word, and rejects broken pulse-sign
alternation. For exploratory captures from other models, pass `strict=False`
or use the CLI's `--relaxed` flag. Relaxed mode still verifies timing, pulse
order, and the `0x90` header. Automatic scale detection always uses strict
frames as evidence; if a capture contains only an unknown register range,
combine relaxed mode with an explicit timing scale:

```python
unknown_words = list(decode_capture(
    timings, timing_scale=0.1, strict=False
))
```

## Capturing your own data

Any receiver that can timestamp baseband IR edges at ~10 µs resolution works.
**A 38 kHz demodulating receiver (TSOP-style) will NOT work**; the signal has
no carrier. The proven setup (bare IR phototransistor + ESP32
`remote_receiver`) is documented in
[esphome-pumpsaver](https://github.com/lizbit-official/esphome-pumpsaver),
including an NDJSON capture pipeline compatible with this decoder.

## Repo layout

```
PROTOCOL.md                  the specification (start here)
pumpsaver_ir/decoder.py      reference decoder (stdlib only)
pumpsaver_ir/registers.json  machine-readable register map with confidence levels
examples/sample_capture.ndjson  30 real transmissions from a 233-P (~15 s, 2.5 broadcast cycles)
tests/test_decoder.py        round-trip + real-capture tests
```

## Status & contributing

Wire format: **solved and verified** (PROTOCOL.md §8). Register semantics:
live telemetry, counters, and the fault-ring *structure* are verified; several
configuration registers and the fault-code names are candidates awaiting
confirmation. The
[Informer screen mapping](PROTOCOL.md#informer-screen-mapping) shows exactly
which screens remain unconfirmed.

Counter caveat: 0x0F and 0x17 have verified increment semantics, but the
broadcast exposes 16-bit words and the corpus did not include a manual clear
or rollover. They should therefore be described as user-clearable device
counters, not irreversible lifetime totals. Registers 0x16 and 0x18 stayed
zero; treating either as an upper word would require a carry observation.

The most useful contributions:

- A capture annotated with an actual **Informer** readout (instant semantic
  map) via the
  [register-identification form](https://github.com/lizbit-official/pumpsaver-ir-protocol/issues/new?template=02-register-identification.yml)
- Captures from **other models** (231-P, 234-P, 235P, 236-P, and the Pentek /
  Goulds / Berkeley / Myers / Sta-Rite rebrands) via the
  [capture form](https://github.com/lizbit-official/pumpsaver-ir-protocol/issues/new?template=01-submit-capture.yml)
- A capture spanning a **fault or trip event** (confirms the fault-code
  enumeration and reveals the restart-delay registers)
- Observations paired with a settings change: knob turns, a recalibration
  (splits trip settings from stored min/max records)

Questions welcome in
[Discussions](https://github.com/lizbit-official/pumpsaver-ir-protocol/discussions).

## License

MIT. Not affiliated with or endorsed by Littelfuse, Inc. See PROTOCOL.md §9.
