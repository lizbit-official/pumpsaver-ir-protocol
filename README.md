# pumpsaver-ir-protocol

Protocol specification and reference decoder for the IR data broadcast of **SymCom / Littelfuse
PumpSaver Plus** pump-protection relays (reverse engineered on a 233-P). The same devices ship
rebranded as **Pentek / Pentair SPP-series** (SPP-233P, SPP-235P, SD-F30x…) and
**Goulds / CentriPro "PumpSaver by SymCom"** units.

These relays constantly broadcast their internal state over a baseband IR link meant for
SymCom's discontinued "Informer" handheld:

- live **voltage, current, power, power factor**
- lifetime **pump-start** and **run-time** counters
- trip-point configuration and the last-20-faults history

This repo documents that protocol — apparently for the first time anywhere — and provides a
tested reference decoder.

**Want this in Home Assistant?** See the companion repo [esphome-pumpsaver](https://github.com/lizbit-official/esphome-pumpsaver) for a ready-made
ESPHome component (an ESP32 + a $0.30 IR phototransistor pointed at the device).

## The protocol in one paragraph

5,000-baud NRZ over baseband IR (no carrier), where each pulse's *width* encodes a run of
identical bits. Every burst is one 32-bit word — `0x90 | register:8 | value:16` — with trailing
zero bits absorbed into the gap. 117 registers broadcast cyclically: live telemetry every ~1.5 s,
everything else every ~5.8 s. No checksums; state is fully reconstructible from ~6 s of signal.
Full details, register map, and evidence: [PROTOCOL.md](PROTOCOL.md).

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

Other commands: `events` (register changes over time), `csv` (time series), `stats` (decode
quality). No dependencies beyond Python 3.10+.

### Library use

```python
from pumpsaver_ir import decode_capture, registers_from_words, Word

words = [w for w in decode_capture(timings) if isinstance(w, Word)]
regs = registers_from_words(words)
print(f"{regs[0x11] / 10} V, {regs[0x10]} W, {regs[0x0F]} lifetime starts")
```

`timings` is a list of signed pulse durations in µs (positive/negative = the two IR levels),
e.g. an ESPHome `remote_receiver` raw dump. Polarity is auto-detected.

## Capturing your own data

Any receiver that can timestamp baseband IR edges at ~10 µs resolution works. **A 38 kHz
demodulating receiver (TSOP-style) will NOT work** — the signal has no carrier. The proven setup
(bare IR phototransistor + ESP32 `remote_receiver`) is documented in
[esphome-pumpsaver](https://github.com/lizbit-official/esphome-pumpsaver),
including an NDJSON capture pipeline compatible with this decoder.

## Repo layout

```
PROTOCOL.md                  the specification (start here)
pumpsaver_ir/decoder.py      reference decoder (~100 lines, stdlib only)
pumpsaver_ir/registers.json  machine-readable register map with confidence levels
examples/sample_capture.ndjson  30 real transmissions from a 233-P (~15 s, 2.5 broadcast cycles)
tests/test_decoder.py        round-trip + real-capture tests
```

## Status & contributing

Wire format: **solved and verified** (see PROTOCOL.md §8). Register semantics: live telemetry and
counters verified; configuration and fault-history blocks are mapped but not yet labeled.
The most useful contributions:

- A capture annotated with an actual **Informer** readout (instant semantic map)
- Captures from **other models** (231-P, 234-P, 235P, 236-P… — including Pentek SPP-series and
  Goulds/CentriPro rebrands) to test framing generality
- A capture spanning a **fault event** (decodes the fault-ring layout)
- A clamp-meter reading during a pump run (settles PROTOCOL.md §7 Q1)

## License

MIT. Not affiliated with or endorsed by Littelfuse, Inc. — see PROTOCOL.md §9.
