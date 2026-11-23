# Fischer d16 — game asset frozen after maintainer sign-off

## Disposition

**All four predeclared final product gates passed.** The maintainer explicitly
approved release on 2026-11-23. The existing, **unmodified** Rodent IV
**Fischer d16** personality is now **FROZEN as a product game asset**, bound
to the evidence below and the fixed-depth configuration. This status does
not by itself change gameplay integration or the official Arena. This is a *product acceptance
decision*, not a claim that engine Fischer has the real player's Elo or would
choose exactly the same moves.

The repository source of truth for the verification is:

- [Final GitHub Actions run #45097738965](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45097738965)
- Immutable Actions artifact `fischer-final-freeze-evidence`, ID **14784309307**,
  with `fischer-final-report.json` and `fischer-freeze-proposal.json`
- Complete report SHA-256:
  `913952622395d3910dc4084e772a18fa07e95db6833aa54d5812846d918d213c`
- Prior complete 3,616-game baseline run:
  [#44970751726](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/44970751726)
- All 8 `play` jobs, `plan` and `aggregate` **SUCCESS**.
- Source panel: fixed SHA-salted 12 distinct historical twelve-ply roots drawn
  from 121 previously inspected TEST games; **not blinded**.

### Fixed evidence and criteria

| Gate | Precommitted criterion | Verified outcome |
| --- | --- | --- |
| Previous fixed-depth strength | FIT: >=75% vs Simple d12; 45–65% vs d16; TUNE: >=75% vs d12; 40–70% vs d16 | FIT d12 **87.35%**, FIT d16 **53.21%**, TUNE d12 **85.87%**, TUNE d16 **56.52%** — PASS |
| Clock 10+2 | All 24 legally complete, Fischer >=30% score, zero Fischer flags | **12W–8D–4L**, **16/24 (66.67%)**, **0 flags**, 24/24 complete — PASS |
| Style | >=100 positions, >=100 independent original games; >=2/4 frequency proxies within tolerance vs Simple, >=5% different moves, >=40% historical move match and not >3pp below Simple | **284 positions from 121 original games**; 4/4 tolerance-qualified, **32.04% different moves**, **53.17% Fischer** vs **54.23% Simple** historical move agreement — PASS |
| Reproducibility | >=12 repeat probes, >=95% exact repeat, >=90% depth16 reached | **43/43 (100%)** repeats; **100%** depth attainment in style probes — PASS |

Clock-score 95% root-cluster bootstrap interval: **52.08%–81.25%**.
**Do not infer a human Elo or force-equivalence claim from this small
pair-color clock sample.**

Historical-style interpretation must remain precise:

- The profiles disagree on **91 of 284** positions (32.04%). They are distinguishable
  without asserting that every difference is a uniquely Fischer-like preference.
- Simple matches the historical move in **54.23%**, slightly more often than Fischer's
  **53.17%**. Thus **do not claim Fischer exceeds Simple in exact human move
  agreement**.
- In the four frequency proxies, Fischer is genuinely closer to the historical
  aggregate in **pawn moves** and **non-pawn moves toward the opposing king**;
  its **checking** and **forcing** rates pass only because of the predeclared
  2.5-percentage-point tolerance.
- These metrics describe behavior but do not prove the psychological or complete
  historical playing style of Bobby Fischer.

### Stable identity

Freeze only the audited **unmodified** profile at depth16, unless the
release owner explicitly starts a new, distinct qualification. Do **not**
enable the experimental FIT-driven d18 endgame budget or guarded opening
book under this qualification: neither was the candidate in this final gate.

| Artifact | SHA-256 |
| --- | --- |
| Rodent IV binary | `c2ccc7a99b442dcaa2aa2dc06e020f0182fe7a198c6f080f45f555c126cdcd95` |
| Original Fischer profile | `ee2ab4e175f0dbee88f2f12595393d14001915670e086198139d15ad86eae577` |
| Simple control profile | `19f83fb05f96039a34e0e9a36bf66fc841693f70d73ee2a243df42e90caf1613` |

The frozen profile means **no more open-ended re-training or arbitrary
strength tweaks for Fischer**. Changes to the binary, profile, behavior
configuration or competition protocol require explicit versioning and new
qualification. Other player models can now proceed on their own tracks.

### Release review checklist

- [x] Full report downloaded and SHA-256 independently matched the generated proposal.
- [x] All four predeclared gates verified in actual final `aggregate` output.
- [x] Legal completion, 10+2, style and repeatability metrics reconciled.
- [x] Scientific limitations and Simple's greater exact move-match disclosed.
- [x] Baseline and unmodified motor/profile SHA hashes pinned.
- [x] **Explicit maintainer approval to label Fischer d16 as frozen game asset** (2026-11-23).
- [x] Post-approval: document stable release status in a dedicated asset manifest,
      without changing Rodent binaries, original personality, career ratings or
      Arena defaults; open Tal as the next qualification track.

The status in `docs/evidence/fischer-d16-freeze-proposal.json` is
`FROZEN_GAME_ASSET`, reflecting **explicit maintainer approval**, not inferred
from green CI. The evidence report and hashes must stay immutable. Any future
strength, style, binary, search-depth or opening-book changes are a **new,
separately qualified candidate**. Existing Arena and roster remain unchanged.
