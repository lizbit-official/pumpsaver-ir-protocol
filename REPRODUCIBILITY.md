# Reproducing the protocol results

This repository separates results that can be reproduced from public data
from conclusions that depended on the original private capture corpus. That
boundary matters: a static broadcast is enough to validate the decoder, but
not enough to prove what every register means.

## Public regression corpus

`examples/sample_capture.ndjson` is the redistributable fixture. Its hash and
selected expected metrics live in `reproducibility/public-corpus.json`.
From the repository root, run:

```sh
python -m pip install -e .
python -m pytest
python tools/corpus_report.py --check-manifest reproducibility/public-corpus.json
python -m pumpsaver_ir stats examples/sample_capture.ndjson
```

The fixture independently supports these claims:

- the `0x90 | register | value16` word framing and `0x90FFAAAA` sync word;
- zero decode errors for this fixture, 594 words, and 117 data registers;
- the register values observed at that instant;
- parity between the Python decoder and the ESPHome component's host decoder
  test when both consume the same fixture.

It also contains one static fault-history image. That image can exercise a
proposed layout, but it cannot demonstrate that the ring shifts correctly or
establish the physical meaning of a fault code.

## Reporting another corpus

`tools/corpus_report.py` accepts any number of ESPHome NDJSON capture paths and
emits stable JSON with the file hash, decode counts, timestamps, register
changes, and latest raw values:

```sh
python tools/corpus_report.py capture-a.ndjson capture-b.ndjson > report.json
```

The manifest format is `pumpsaver-corpus-manifest-v1`:

```json
{
  "format": "pumpsaver-corpus-manifest-v1",
  "captures": [
    {
      "path": "path-relative-to-the-repository-root.ndjson",
      "sha256": "lowercase SHA-256 hex",
      "expect": {
        "decoded_words": 594,
        "latest_registers": {"0x0F": 11179}
      }
    }
  ]
}
```

`expect` is a recursive subset match: include only the measurements that are
intended as durable regression assertions. Capture hashes are always checked.

## Extended 2026-07 analysis

The original analysis workspace retained small decoder scripts, derived CSVs,
and a narrative report under `analysis-2026-07/`. A checksum inventory is in
`reproducibility/extended-analysis.sha256` so an owner of that workspace can
identify the exact files reviewed here. Those archival scripts explored more
than one convention and are evidence of the investigation, not maintained
alternatives to the decoder in this package.

The raw captures and Home Assistant ground-truth exports are private and are
not copied into this repository. Consequently, the public fixture cannot by
itself reproduce or independently verify:

- the claimed 54-capture, 24-day chronology and 13.1-million-word aggregate;
- correlations between pump starts/runtime and external Home Assistant logs;
- the assertion that configuration registers never changed across that span;
- the claim that one early receiver pipeline stored durations tenfold too
  large (the `timing_scale=0.1` compatibility path is tested synthetically);
- fault-ring movement, retry timing, or the mapping of code 1 to dry well;
- the claim that register `0x12` sums both hot-leg currents while running;
- any rollover behavior of the 16-bit counters.

To promote one of these claims to a public regression, contribute a minimized,
redacted capture slice plus a manifest entry and document the independent
ground truth used to label it. Do not publish network credentials, device IDs,
location metadata, or unrelated Home Assistant history.
