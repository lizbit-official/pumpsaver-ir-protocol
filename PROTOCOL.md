# SymCom / Littelfuse PumpSaver Plus — IR Broadcast Protocol

**Spec version:** 0.2 (2026-07-09)
**Applies to:** the PumpSaver Plus family; reverse engineered on a **233P-1.5** (the 1/3-1.5 hp
variant of the 233-P). Other Informer-compatible SymCom models — 233-P, 231-P Insider, 234-P,
235P, 236-P, 111P — very likely share the framing, possibly with different register maps. The same hardware ships **rebranded**: Pentek / Pentair
"Submersible Pump Protector" SPP-233P, SPP-235P, SPP-111-3RLP and SD-F30x (their reader is the
*Pentek SPP-Informer*; Berkeley, Myers and Sta-Rite sell the same SPP SKUs), and Goulds /
CentriPro (Xylem) "PumpSaver by SymCom" part numbers (233, 2333RL, 2353RL50/75/100, 231Insider…).
Beware suffix conventions: Pentek `-100` = 10 HP, CentriPro `RL100` = 100:5 CT.
**Status:** wire format **verified** (three independently-derived decoders; ~8.4 M words across
~57 h of captures decode with only two glitched bursts rejected, counter arithmetic proven
against external ground truth);
register semantics partially verified — see confidence column in the register map.

This protocol appears to have no public documentation anywhere — no patent, FCC filing, or prior
reverse engineering was found. It was recovered entirely from captures of one 233P-1.5 in service.

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

**Units guard:** one early receiver pipeline stored every duration ten times too large (for
example, a physical ~12 ms separator appears near 120,000 in that format). Normalize those
captures by dividing the stored values by 10. The reference decoder defines `timing_scale` as
the number of microseconds per stored unit: its default `timing_scale="auto"` tries `1` (normal
µs) and `0.1` (the observed tenfold legacy representation) and selects one only from
successfully validated protocol frames. `timing_scale=1` or `0.1` (CLI: `--timing-scale`) makes
the choice explicit. Non-native auto-detection requires at least two valid data words or an exact
sync word, so one noise-like burst cannot silently rescale a capture. Do not infer units from the
single largest gap alone.

**Implementation color (from a family teardown):** the transmitter is a Microchip **PIC16F684**
(8-bit, 2 K instructions, 256 B EEPROM, no hardware UART) behind an LM258/LM339 analog front
end. The IR stream is necessarily bit-banged off the PIC's internal RC oscillator — which
explains the ±1 % per-unit bit-period spread (201.5–202.9 µs), the absence of checksums, and the
20-fault history depth (ring + calibration constants ≈ a full 256 B EEPROM).

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
- The reference decoder's normal validation also requires alternating pulse signs, register
  0x01–0x75, and the exact `0x90FFAAAA` sync word. Its explicit relaxed/research mode preserves
  header-valid unknown register addresses for captures from potentially different models; this
  should not be treated as the same integrity guarantee.

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

Semantics were established on a 233P-1.5 protecting a ~240 V single-phase well pump.
Confidence: **verified** = cross-checked against external ground truth (independently logged pump
on/off events) or arithmetic structure; **candidate** = consistent hypothesis, unconfirmed;
**unknown** = stable observed value, meaning unassigned. Machine-readable copy:
[`pumpsaver_ir/registers.json`](pumpsaver_ir/registers.json).

### Live telemetry & counters

| Reg | Name | Scale | Confidence | Notes |
|---|---|---|---|---|
| 0x0F | Pump-start counter | ×1 | **verified** | The transmitted 16-bit word increased by 1 within ~2 s of every independently logged pump start (8/8). User-clearable per manual; counter width beyond this word is unresolved because no clear or rollover was captured |
| 0x10 | Active power (W) | ×1 | **verified** | **True watts** — no correction needed: ~26 W idle (device self-draw), ~820 W running, inrush peaks ~1,700. P = V·I·PF holds at idle *and* running once 0x12's leg-sum is accounted for |
| 0x11 | Line voltage | ×10 | **verified** | Tracks sags from any load on the supply leg |
| 0x12 | Current (A) | ×100 idle, ×200 running | **verified** | **Leg-sum channel**: under pump load both 240 V hot legs pass the sensing path, so the value reads ~2× true motor current (~870 → 4.35 A true); the device's own idle draw is single-count (~14 → 0.14 A). Established by the power identity holding exactly at both operating points; the fault log stores single-count amps |
| 0x13 | Power factor | ×1000 | candidate | ~0.78, barely moves idle↔run; consistent with P/(V·I) at both operating points; heavily averaged (unchanged even at inrush samples) |
| 0x17 | Run-time (minutes) | ×1 | **verified** | The transmitted 16-bit word increased by 1 per 60 s of running; its tick carry-chains match binary increment exactly. User-clearable; counter width beyond this word is unresolved because no clear or rollover was captured |

Both counter meanings and their low-word arithmetic are verified, but calling
them irreversible “lifetime” totals would overstate the evidence. Each appears
on the wire as one 16-bit value, and the corpus contains neither a manual clear
nor a `0xFFFF → 0` transition. Registers 0x16 and 0x18 remained zero and are
plausible places for additional run-time bits, but assigning either one as an
upper word without a carry observation would be speculation. Consumers should
preserve historical totals and treat a decrease as an unresolved clear-or-wrap
event until a transition capture settles the encoding.

### Configuration / stored values (0x01–0x18 remainder)

| Reg | Observed | Confidence | Hypothesis |
|---|---|---|---|
| 0x01 | 953 | **candidate (strong)** | **Calibration power, W** — running load = 86 % of it, and every fault-log wattage sits inside the documented 70–90 % dry-well band. Refuted as a trip threshold (would trip perpetually) and as any amps record (2–3× inrush never moved it) |
| 0x02 | 2384 | candidate | Calibration voltage, 238.4 V (within 2 % of modal live V; refuted as a max-V record) |
| 0x03 | 1223 | **candidate (strong)** | **Overcurrent trip** = exactly 125 % × implied cal current (leg-sum scale ⇒ ~4.9 A true cal current). Refuted as a min/max-amps record |
| 0x05 | 2112 | candidate | Low-voltage trip **or** min-volts-since-cal, 211.2 V — 24 days of captures never discriminated (live V stayed within 233.1–248.8 V) |
| 0x07 | 45060 | unknown | = 0xB004; = 751 h × 60 exactly, or an ID/bit-field — unresolved |
| 0x08 | 1223 | candidate | = 0x03; duplication unexplained |
| 0x09 | 50 | **candidate (strong)** | 5.0 s overcurrent trip delay (×10). Rival readings refuted by the test unit: its restart-delay knob sits near 100–160 min while this reads 50, and the model has no CT |
| 0x0A | 2488 | candidate | High-voltage trip **or** max-volts-since-cal, 248.8 V (live V once grazed exactly 2488 for a single sample and retreated — suggestive, not probative) |
| 0x0B | 1027 | **candidate (strong)** | **Firmware version 4.03** — matches the SymCom family's documented packed-version convention exactly (high byte = major, low = minor; their Solutions software renders reg 1 of a 777 the same way) |
| 0x0C | 10277 | unknown | = 0x2825. Model-ID reading weakened: the family convention is a *literal decimal* model code (777-P2 stores 778, 77C stores 77) and no register holds 233. The family also used month+serial / year registers — 0x0C/0x07 may be a serial/date pair |
| 0x0E | 2315 | candidate | Min-volts-since-cal or nominal voltage, 231.5 V (refuted as a max-V record) |
| 0x14 | 41 | candidate | 4.1 s ≈ the manual's fixed 4 s dry-well trip delay (×10) |
| 0x04, 0x06, 0x0D, 0x15, 0x16, 0x18 | 0 | unknown | Restart-delay setting/remaining and CT size are expected to live among these (all are 0 / n-a except during a lockout or on CT-equipped models). 0x16/0x18 are also unproven upper-word candidates for 0x17; neither changed in the corpus |

### Fault-history ring (0x19–0x75) — decoded

The Informer's documented "last 20 faults" feature. Layout (all **newest first**), established by
full-resolution analysis of the corpus plus exclusion arithmetic:

| Range | Layout | Contents on the test unit |
|---|---|---|
| 0x19–0x1D | 20 × 4-bit fault codes, packed MSB-first | `[4, 1 ×19]` — one code-4 fault, then nineteen code-1s |
| 0x1E–0x56 | 19 × **(W, V×10, A×100)** snapshots on a rigid 3-register grid (record *k* starts at 0x1E+3k) | W 755–838, V 239.4–243.2, A 5.41–6.60 (single-count amps; implied PF 0.52–0.57 — the underload signature) |
| 0x57–0x74 | 20 × 3-byte **run-clock timestamps**: 24-bit BE, minutes, same unit as 0x17 | `[32572, 6703 ×14, 6702 ×5]` |
| 0x75 | Trailer word `0x7E3C` | Unresolved (= the code-4 timestamp − 256 min exactly; never updated at a normal pump start) |

- **Code 1 = dry-well, proven quantitatively:** the nineteen code-1s all fall at run-minutes
  6702–6703 — an auto-retry storm whose per-retry run time brackets to [3.2, 4.6] s, matching the
  manual's fixed 4 s dry-well delay and excluding the 5 s overcurrent delay; dry-well is also the
  fault class with automatic restart-delay retry.
- **Code 4 = rapid-cycle (hypothesis):** no public code table exists for this family (verified
  negative — the name mapping lives only in Informer firmware, and the documented enums of the
  777/601 siblings do not transfer). But the hardware distinguishes exactly four trip classes,
  and two independent SymCom document orderings list them *dry-well, overcurrent, voltage,
  rapid-cycle* — suggesting `0=none, 1=dry-well, 2=overcurrent, 3=voltage, 4=rapid-cycle`.
  The code-4 event (run-clock 32,572 min = 22d 14h 52m, normal snapshot voltage) fits a
  rapid-cycle lockout's frequency profile.
- The timestamp encoding was proven by exclusion: packed d/h/m and BCD contain invalid digits,
  hour-units and little-endian readings are physically impossible, and the storm's LSB step
  (`0x1A2F`→`0x1A2E`) is exactly adjacent minutes. 24-bit minute counters are a SymCom house
  convention (documented for the 777-P2's start counters; the Informer-MS display caps at
  ≈2²⁴ minutes), and the 20-fault ring depth recurs on the MotorSaver 455.
- The ring is **write-once**: byte-identical across 24 days of captures and 11 pump starts. Only
  six registers ever change without a fault: 0x0F, 0x10, 0x11, 0x12, 0x13, 0x17. (An earlier
  draft's "0x3F transient" was a sampling artifact — 0x3F is fault record #11's wattage.)

### Informer screen mapping

The Informer handheld's operating instructions document, screen by screen, everything the
device transmits — making them the ground-truth catalog for this register map. Status of each
documented screen:

**Legend:** ✅ verified 🟡 candidate ❓ unmapped

| # | Informer screen *(manual example)* | Register → meaning | Status |
|---|---|---|---|
| 1 | Model *(`SymCom, Inc. / Model: 233-P`)* | 0x0C = 10277 is the model-ID candidate | ❓ |
| 2 | Live summary *(`Line: 2.30 kW / 230 VAC 12.0 A`)* | 0x10 → W, 0x11 → V×10, 0x12 → A×100 | ✅ |
| 3 | Low-power trip *(`Line Pwr: 3.00 / Trip Pt: (2.40)`)* | live side = 0x10 ✅; trip-point register unmapped — 0x01 turned out to be **calibration power**, the 100 % reference the 70–90 % knob applies to | ✅ / ❓ |
| 4 | Overload trip *(`Line Amps: 12.0 / Trip Pt: (15.0)`)* | live side = 0x12; trip point: 0x03 = 0x08 = 1223 → 12.23 A? (spec: 125 % of cal current ⇒ cal ≈ 9.8 A) | 🟡 |
| 5 | Calibration voltage *(`Line Volts: 230 / Cal. Volts: (230)`)* | 0x02 = 2384 or 0x0E = 2315 (two voltage-shaped constants compete) | 🟡 |
| 6 | Restart delay *(`Rst Dly Set: 30m / Rst Dly: 12m 18s`)* | Both unmapped — no lockout ever occurred in the corpus. Field video shows the remaining-time updating with ~1 s resolution during lockout, so it likely lives among the always-0 live registers (0x04/0x06/0x0D/0x15/0x16/0x18) | ❓ |
| 7 | CT size + pump starts *(`CT Size: n/a / PumpStarts: 213`)* | starts: **0x0F** ✅; CT size always "n/a" on non-CT models like the 233P-1.5 — plausibly one of the zero registers | ✅ / ❓ |
| 8 | Total run time *(`27d 16h 33m`)* | **0x17** → minutes (display formats d/h/m) | ✅ |
| 9 | Fault history ×20 *(name / `kW V A` at fault / `Time: 32d 4h 57m`)* | **Structure decoded** — codes at 0x19–0x1D, (W, V, A) snapshots on the 0x1E+3k grid, 24-bit run-clock-minute timestamps at 0x57–0x74. Code names partially known: 1 = dry-well (proven), 4 = unidentified | ✅ |
| 10 | Max/min since calibration *(`Max. Amps: 17.0 / Min. Amps: 9.0`, `Max. Volts: 240 / Min. Volts: 215`)* | volts: 0x0A / 0x05 remain candidates (vs trip settings — 24 days never discriminated); the amps candidates were all refuted (0x01 = cal power; 0x0B unmoved by 2–3× inrush) — max/min-amps registers unlocated | 🟡 |

Registers with **no** Informer screen at all — 0x13 (PF×1000), 0x07, 0x0B, 0x0C — may be
vestigial or internal: the pre-Plus 1998 protocol carried "5 signal parameters" and a
motor-efficiency figure for older Informer 1.xx units, and 0x09/0x14 read as the firmware's
fixed trip delays rather than anything displayed.

**What would settle the 🟡 rows** (cheapest first): a *recalibration* (the manual says it resets
min/max and fault snapshots — whatever changes is min/max, whatever doesn't is a setting); a
*knob turn* (restart delay / sensitivity → pins 0x09 and the low-power trip); a *fault event*
(shifts the ring and exercises restart-delay-remaining); or best of all an *Informer readout*
transcribed next to a capture — see the
[register-identification issue form](https://github.com/lizbit-official/pumpsaver-ir-protocol/issues/new?template=02-register-identification.yml).

## 6. Reference decoding algorithm

```
for each capture:
    idle_sign = sign of pulses wider than 8 ms,
                or opposite the active first pulse for a separator-free record
    for each burst (pulses between >8 ms gaps):
        require first pulse is active and pulse signs alternate
        bits = ""
        for each pulse:
            n = round((|width| ± 101) / 202)      # + for idle-level, − for active-level
            bits += ("0" if idle-level else "1") × n
        word = int(bits padded with "0" to 32)
        require word >> 24 == 0x90
        reg = (word >> 16) & 0xFF; value = word & 0xFFFF
        require reg in 0x01..0x75, or (reg, value) == (0xFF, 0xAAAA)
        emit (reg, value)
```

The dependency-free reference implementation is
[`pumpsaver_ir/decoder.py`](pumpsaver_ir/decoder.py). An ESP32/ESPHome
implementation lives in the companion repo
[esphome-pumpsaver](https://github.com/lizbit-official/esphome-pumpsaver).

## 7. Open questions

1. **Code-4 fault identity** — best hypothesis rapid-cycle (see §5); no public enumeration
   exists, so confirmation needs a captured fault of known type or an Informer readout of this
   unit's fault #1 name.
2. **Trip settings vs min/max records** for the voltage pair 0x0A/0x05: 24 days of captures never
   discriminated. A recalibration (the manual says it resets min/max) or an Informer readout
   settles it in one shot.
3. **Unlocated registers**: restart-delay setting & remaining, CT size, max/min amps since cal,
   model ID (0x0C?), the roles of 0x07/0x0B/0x0E, the 0x03/0x08 duplication, the 0x75 trailer.
4. **Counter width and rollover** — the 16-bit words at 0x0F and 0x17 have verified
   increment semantics, but no wrap or user-clear event was captured. A capture spanning
   `0x17: 0xFFFF → 0` (while watching 0x16 and 0x18) would distinguish a standalone
   16-bit counter from a wider encoding.
5. *(Resolved in v0.2: the former "factor-2 in running power" question — 0x10 is true watts; the
   current channel leg-sums both hots under load. See §5.)*
6. Whether other SymCom models (231-P, 234-P, 235P, 236-P, larger 777/SubMonitor family) share
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
- v0.2 follow-up: 13.1 M words across 54 captures spanning 24 days decode at ~100 % (5 isolated
  failures); every non-live register is bit-identical across the whole span; counters strictly
  monotone. The fault-ring record grid was derived independently by two analyses that agree
  exactly, and the leg-sum current model reproduces the power identity at both operating points.

## 9. Legal

This specification was produced by clean-room reverse engineering of over-the-air broadcasts from
a device owned by the author, for interoperability. "SymCom", "PumpSaver", "Informer" and
"Littelfuse" are trademarks of their respective owners. This project is not affiliated with or
endorsed by Littelfuse, Inc.
