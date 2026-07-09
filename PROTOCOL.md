# SymCom / Littelfuse PumpSaver Plus — IR Broadcast Protocol

**Spec version:** 0.1 (2026-07-09)
**Applies to:** PumpSaver Plus 233-P (reverse engineered on this model; other Informer-compatible
SymCom models — 231-P Insider, 233-1.5-P, 234-P, 235P, 236-P, 111P — very likely share the framing,
possibly with different register maps).
**Status:** wire format **verified** (three independently-derived decoders; ~8.4 M words across
~57 h of captures decode with only two glitched bursts rejected, counter arithmetic proven
against external ground truth);
register semantics partially verified — see confidence column in the register map.

This protocol appears to have no public documentation anywhere — no patent, FCC filing, or prior
reverse engineering was found. It was recovered entirely from captures of one 233-P in service.

---

## 1. Overview

PumpSaver Plus devices continuously broadcast their internal state over a **baseband infrared**
link (no 38/56 kHz carrier) intended for SymCom's "Informer" handheld reader. The broadcast is
unidirectional and unsolicited: whenever the device is powered, it cyclically transmits all of its
registers — live telemetry, configuration, counters, and the last-20-faults history — as
self-contained addressed words. A receiver can begin listening at any time and reconstruct the
full state within ~6 seconds.

```
transmission (20 words, ~0.36 s)                        every ~0.5 s (~2x/s)
┌──────┬──────┬──────┬──────┬──────┬──────┬──   ──┬──────┐
│ SYNC │ data │ data │ data │ data │ SYNC │  ...  │ data │   then ~146 ms idle
└──────┴──────┴──────┴──────┴──────┴──────┴──   ──┴──────┘
```

## 2. Physical layer

| Parameter | Value |
|---|---|
| Medium | Baseband IR, two levels (LED on/off). **Not** carrier-modulated — demodulating receivers (TSOP-style) will not work |
| Bit rate | 5,000 baud nominal (bit period 200 µs; 201.5–202.9 µs fitted on the test unit) |
| Line code | NRZ, MSB-first. Runs of identical bits appear as one pulse whose width = run length × bit period |
| Idle level | Logical 0. The line rests at idle between words (11–16 ms) and between transmissions (~146 ms) |
| Range | 1–10 ft per Informer spec |

**Edge skew (important):** every transition through a real receiver is delayed ~½ bit (~100 µs)
in one direction, so *idle-level pulses measure ~100 µs short* and *active-level pulses ~100 µs
long*. Measured widths therefore quantize to **odd multiples of ~100 µs**. Recover run lengths
with integer arithmetic (robust to clock drift and wobble):

```
n_bits(idle pulse)   = round((width_us + 101) / 202)
n_bits(active pulse) = round((width_us - 101) / 202)
```

**Polarity detection:** capture hardware may report either level as "positive". The inter-word
separators (>8 ms) always sit at the idle level, so whichever sign the separators carry is the
idle/logical-0 level.

## 3. Word format

Every pulse burst between two >8 ms idle gaps is **one 32-bit word**, occupying a fixed ~18.2 ms
slot:

```
 bit 31        24 23        16 15                     0
 ┌───────────────┬─────────────┬───────────────────────┐
 │ 1 0 0 1 0 0 0 0│ register   │ value (big-endian)    │
 │     0x90       │   0x01-0x75 │                       │
 └───────────────┴─────────────┴───────────────────────┘
```

- **Trailing zero bits are not transmitted** — they merge into the idle gap. Words are
  variable-length on the wire (observed 13–32 bits). Decoders MUST right-pad the received bits
  with `0` to 32 bits.
- A burst always starts with an active pulse (the header's leading `1`) — physically:
  active×1, idle×2, active×1 (`1001`), then the register/value bits.
- **Sync word:** `0x90FFAAAA` (register 0xFF, value 0xAAAA = `1010…` bit-training) is inserted
  before every 4 data words. It carries no data; decoders may treat it as any other word and
  discard it.
- No checksum exists. Integrity comes from: the constant `0x90` header, the fixed slot timing,
  strictly ascending register order within a scan, and continuous re-broadcast (~every 1.5 s for
  live registers). A decoder wanting validation should compare consecutive readings.

**Worked example** (captured during a pump run):

```
pulses (µs): -310 +300 -310 +1520 -920 +300 -310 +95 -310 +95 -715 +95 -310 +95 -510 +95 -310
runs:          1a   2i    1a    8i   4a   2i    1a  1i   1a   1i   3a  1i   1a   1i   2a  1i   1a
bits:          1    00    1  00000000 1111 00    1   0    1    0   111  0    1    0    11  0    1
             = 1001 0000 00001111 0010101110101101  (already 32 bits — odd value, no trailing 0s)
             = 0x900F2BAD  →  register 0x0F = 11181   (pump-start counter)
```

## 4. Broadcast schedule

117 registers, 0x01–0x75, in two blocks:

| Block | Registers | Refresh |
|---|---|---|
| Live | 0x01–0x18 (24 words) | every scan, ~1.45 s |
| Ring | 0x19–0x75 (93 words) | round-robined after the live block in 4 chunks (24+24+24+21); full refresh ~5.8 s |

Each scan (= one live block + one ring chunk, spanning 3 transmissions) sends its registers in
**strictly ascending order**. One full cycle = 189 data words (4× live block + the 4 ring
chunks) + 48 sync words = 237 words ≈ 5.8 s (a sync precedes every 4 data words *on average* —
189 is not a multiple of 4, so one group per cycle runs short). Transmissions (20 words ≈ 0.36 s
plus ~0.15 s idle, 4 sync-led groups) are arbitrary chunks of this continuous
cycle — the cycle wraps mid-transmission, so treat the input as a stream keyed by register index,
not as packets.

## 5. Register map

Semantics were established on a 233-P protecting a ~240 V single-phase well pump.
Confidence: **verified** = cross-checked against external ground truth (independently logged pump
on/off events) or arithmetic structure; **candidate** = consistent hypothesis, unconfirmed;
**unknown** = stable observed value, meaning unassigned. Machine-readable copy:
[`pumpsaver_ir/registers.json`](pumpsaver_ir/registers.json).

### Live telemetry & counters

| Reg | Name | Scale | Confidence | Notes |
|---|---|---|---|---|
| 0x0F | Pump-start counter | ×1 | **verified** | +1 within ~2 s of every independently-logged pump start (8/8). User-clearable per manual |
| 0x10 | Active power (W) | ×1 | **verified** | ~26 W idle (device self-draw), ~820 W reported running; inrush peaks ~1,700. P = V·I·PF exact at idle; reads ×½ vs V·I·PF while running (§7) |
| 0x11 | Line voltage | ×10 | **verified** | Tracks sags from any load on the supply leg |
| 0x12 | Current (A) | ×100 | **verified** | ~0.14 A idle, ~8.7 A reported running (may double-count both hot legs of the 240 V circuit; §7) |
| 0x13 | Power factor | ×1000 | candidate | ~0.78, barely moves idle↔run |
| 0x17 | Run-time (minutes) | ×1 | **verified** | +1 per 60 s of running; its tick carry-chains match binary increment exactly (the arithmetic proof of this spec). User-clearable |

### Configuration / stored values (0x01–0x18 remainder)

| Reg | Observed | Confidence | Hypothesis |
|---|---|---|---|
| 0x01 | 953 | unknown | — |
| 0x02 | 2384 | candidate | Calibration/nominal voltage, 238.4 V |
| 0x03 | 1223 | candidate | Overload trip 12.23 A (manual: 125 % of cal current) |
| 0x05 | 2112 | candidate | Low-voltage trip or min-volts-since-cal, 211.2 V |
| 0x07 | 45060 | candidate | = 751 h × 60 exactly; runtime snapshot at last service/reset? |
| 0x08 | 1223 | unknown | = 0x03 |
| 0x09 | 50 | unknown | Delay setting? |
| 0x0A | 2488 | candidate | High-voltage trip or max-volts-since-cal, 248.8 V |
| 0x0B | 1027 | unknown | — |
| 0x0C | 10277 | unknown | Model-ID candidate |
| 0x0E | 2315 | candidate | Stored voltage, 231.5 V |
| 0x14 | 41 | unknown | — |
| 0x04, 0x06, 0x0D, 0x15, 0x16, 0x18 | 0 | unknown | — |

### Fault-history ring (0x19–0x75)

Matches the Informer's documented "last 20 faults" feature; static across all captures — no new
faults occurred (sole exception: one single-scan transient `824→0→824` on 0x3F at a pump start):

| Range | Contents (observed) | Interpretation (candidate) |
|---|---|---|
| 0x19–0x1D | `0x4111, 0x1111 ×4` | 20 packed 4-bit fault-code slots (nineteen code-1, one code-4) |
| 0x1E | `774` | Unmapped; PF-like magnitude, sits between the fault codes and the triplets |
| 0x1F–0x56 | ~19 triplets: (2394–2432, 541–660, 755–838) | Per-fault snapshot: (V×10, A×100?, PF×1000?) — below-normal current fits dry-well/underload trips |
| 0x57–0x75 | bytes `00 7F 3C 00 (1A 2F 00)×14 (1A 2E 00)×~5 7E 3C` | Per-fault run-clock timestamps? Unmapped |

## 6. Reference decoding algorithm

```
for each capture:
    idle_sign = sign of pulses wider than 8 ms
    for each burst (pulses between >8 ms gaps):
        bits = ""
        for each pulse:
            n = round((|width| ± 101) / 202)      # + for idle-level, − for active-level
            bits += ("0" if idle-level else "1") × n
        word = int(bits padded with "0" to 32)
        require word >> 24 == 0x90
        emit (reg = (word >> 16) & 0xFF, value = word & 0xFFFF)
```

The reference implementation is [`pumpsaver_ir/decoder.py`](pumpsaver_ir/decoder.py) (~100 lines,
stdlib only). An ESP32/ESPHome implementation lives in the companion repo [esphome-pumpsaver](https://github.com/lizbit-official/esphome-pumpsaver).

## 7. Open questions

1. **Factor-2 in running power.** At idle, `P = V·I·PF` holds to within a few percent; while the
   pump runs, reported power is almost exactly half of `V·I·PF`. Either the current channel
   double-counts under pump load (both hot legs of the 240 V circuit through the sensing path →
   true running current ~4.35 A), or 0x13 is displacement-PF and true running PF ≈ 0.39.
   A single clamp-meter reading during a run would settle it.
2. Config-block semantics (0x01–0x0E): distinguish trip settings vs stored min/max vs calibration
   by twiddling the device's knobs / performing a recalibration and diffing registers.
3. Fault-ring layout: needs a capture spanning a real fault event (one ring-slot shift).
4. Model-ID register: which constant encodes "233-P"? Relevant for multi-model support.
5. Whether other SymCom models (231-P, 234-P, 235P, 236-P, larger 777/SubMonitor family) share
   this framing. The Informer's backward compatibility with pre-Plus models implies typed records
   were designed for extensibility.

Contributions with captures from other models or annotated by an actual Informer readout are
very welcome.

## 8. Evidence summary

- Wire format independently derived three ways (token-grammar induction; blind bit-period sweep,
  best fit 3.4 % normalized RMS vs 44 % for wrong configs; counter carry-chain arithmetic) — all
  converged on the same framing and values.
- All but 2 of ~8.4 M words across five captures / ~57 h (Nov 25 – Dec 5, 2025) parse with header
  `0x90` — the two failures are single-bit receiver glitches, rejected cleanly; cross-capture
  register continuity is exact (one capture ends 12 s before the next begins; counters agree).
- Counter semantics verified against independent Home-Assistant logging of the pump circuit
  (10 pump runs in a 42 h capture: every start/stop reflected in 0x0F/0x10/0x12/0x17 within
  seconds).
- Alternatives ruled out experimentally: UART framings (≤30 % parse, sync-only), digit/BCD symbol
  alphabets, and all common checksum schemes (chance-level).

## 9. Legal

This specification was produced by clean-room reverse engineering of over-the-air broadcasts from
a device owned by the author, for interoperability. "SymCom", "PumpSaver", "Informer" and
"Littelfuse" are trademarks of their respective owners. This project is not affiliated with or
endorsed by Littelfuse, Inc.
