# Tal — controlled strength repair, without erasing tactical personality

## Diagnosis from the real 608-game baseline

The full terminal-only benchmark, run
[#45167232903](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45167232903),
scored original **Tal d16**:

| Arm | Simple d12 | Simple d16 |
|---|---|---|
| FIT (192 per opponent) | **59.38%** (97W/34D/61L) | **29.69%** (38W/38D/116L) |
| TUNE (112 per opponent) | **54.02%** (47W/27D/38L) | **15.18%** (11W/12D/89L) |

The original Tal profile is not a ~2000 FIDE Elo engine by measurement:
**there are no human FIDE Elo anchors here**. It does, however, have a
measured serious in-project competitive weakness against Simple d16.

A source-code/profile inspection of **pinned** Rodent IV
`e8d84c8c8c189a1cf4eb27c578fc573af3b916d2` established:

- Tal assigns **OwnAttack=450** versus **OppAttack=100**.
- It sets KingTropism=80, PiecePressure=190, PawnStorm=181,
  PawnShield=189 and a high middle-game QueenValueMg=1160.
- Simple has OwnAttack=100, OppAttack=100, KingTropism=0
  and a conventional QueenValueMg=975.
- The `basic.ini` file uses **HIDE_OPTIONS**. Therefore it is incorrect
  to assume all hidden evaluation weights can be overridden through
  `python-chess SimpleEngine.configure()`. The real Rodent source
  parses these options when it reads a personality file.

These differences **suggest**, but do not prove, a high-risk evaluation
imbalance, a defensive deficiency, or material overvaluation. A
stronger search at d18 is a separate hypothesis; simply raising
depth does not guarantee strength under a 5-million-node cap.

## Preregistered hypotheses and variants

Generate candidate `.txt` personality files **only in a temporary
CI sandbox** from the original SHA256-pinned `tal.txt`. The original
Tal file must remain SHA256
`f6b58d7cc9e7d2fcdbbc24a20ea72035d05ca5ed5e3401bda240b1b298ce149d`.
Candidate files are **not** committed to the official roster. Each
runner copies, hash-checks and launches the original Rodent IV binary
with a **private test-only personality registry**. The official Rodent,
Simple and Fischer bytes have to match the frozen #202 manifest.

| Variant | Depth | Change hypothesis |
|---|---:|---|
| original16 | 16 | Untouched Tal, paired control |
| defense16 | 16 | Improve opponents' threats, mobility awareness, king shield and storm balance without suppressing Tal's own attack |
| balanced16 | 16 | Reduce the original overwhelming attack bias and pressure weights but preserve attacking preferences |
| material16 | 16 | Normalize inflated middle-/endgame material piece valuations while keeping aggressive attack evaluation |
| original18 | 18 | Test extra search only, unchanged original personality |

All candidate weights are **declared in code before executing a game**;
no genetic optimizer, repeated peek at TEST or adaptive mutation occurs.
Every original profile remains unchanged. No local source is substituted
for the pinned original.

## Independence of selection and confirmation

- The historical source is 2,431 accepted Tal games. Only FIT
  (**2,024**) supplies experimental opening roots.
- The previous 608-game experiment used 96 selected FIT roots. This
  experiment **excludes those exact 96 historical prefixes** and any
  FIT prefix shared with TUNE. The TEST arm (341 games) is never scored
  for this search. Do not tune on TUNE's poor original score.
- A separate fixed SHA salt chooses **24 fresh roots**:
  first 12 for *pilot selection*, next 12 for *independent
  confirmation within FIT*. Both are unknown to the variant tuning
  algorithm before the protocol runs.
- Five variants each face **Simple d16** on each of the two colors
  at each root: **24 × 5 × 2 = 240 complete games**, split into 12
  runner shards. Depth is fixed per variant and nodes capped at 5M;
  actual depth attained must be reported for interpretation.
- All games start after the same genuine 12 historical half-moves
  and use the same root and reversed color. Every move/result is
  independently replayed; no engineering-cap draw is allowed. Any
  incomplete/illegal/missing game prevents scoring the entire panel.
- Game-score 95% confidence intervals use **resampling of entire
  two-color opening-root blocks**. They do not treat individual plies
  or the two color matches as independent observations.

## Predeclared selection rule

Choose the best candidate among defense16, balanced16, material16
and original18 based **only on pilot** advantage against original16
on the same root-color matchups. A candidate is *provisionally selected*
only when its pilot improvement is at least **+10 percentage points**.

Then, with that identity already locked, disclose the **separate
confirmation** results. A label
`PROMISING_STRENGTH_REPAIR_STYLE_GATE_PENDING` requires both:

1. Confirmed independent-FIT improvement of >= **+10 pp**, and
2. 95% root-bootstrap CI for that paired difference **strictly above zero**.

Any failure is `NO_VALIDATED_STRENGTH_GAIN_DO_NOT_PROMOTE`, not
an opportunity to loosen the gate after seeing results.

**Passing this experiment does NOT automatically promote or freeze
Tal.** A separate phase must establish that tactical preferences
survive (same-position human and original Tal controls), compare with
an independent TUNE/TIME-CONTROL benchmark and perform the 10+2
legal-game and repeatability gate before any official Tal release
change. This explicitly avoids making Tal a renamed Simple engine.

## GitHub Actions execution

The workflow `.github/workflows/tal-strength-doe.yml` intentionally
runs all 240 real games on the pull request itself **once its source
and unit-test plan passes**. This is an explicitly bounded experiment,
not a recurring daily job or an automatically approved profile
mutation; its 12 matrix jobs can consume GitHub Actions minutes.
Superseded PR runs are canceled to control quota.

Artifacts: `tal-repair-plan`, `shard-0` ... `shard-11` and
`tal-strength-repair-report`. The aggregator publishes all five
descriptive scores for both stages and the paired interval for the
winner; it cannot hide failed or missing cases.

No new engine or Tal asset is declared playable until **actual,
reviewed match evidence** supports it. Fischer d16 remains frozen.
