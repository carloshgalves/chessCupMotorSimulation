# Tal Balanced d16 — product release decision record

## Disposition

**CANDIDATE QUALIFIED FOR EXPLICIT HUMAN PRODUCT REVIEW, NOT YET FROZEN.**
The original Mikhail Tal Rodent IV personality did not meet the
preregistered strength requirement against the Simple d16 control.
The independently generated, fully identified **Tal Balanced d16** variant
significantly outperformed the original in matched games with real 10+2 clocks.
It also passed the retrospective style/repeatability tests.

This is an engineering **chess-game personality** assessment. It is
**not** evidence that Tal Balanced corresponds to Mikhail Tal's historic
FIDE Elo, could beat the real player, or always plays his moves.

### Scientific record (all actual, immutable Action artifacts)

| Evidence | GitHub run | Outcome |
| --- | --- | --- |
| Fixed-depth 608-game baseline | [#45167232903](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45167232903) | Original Tal d16 **29.69% FIT / 15.18% TUNE** vs Simple d16 |
| Candidate selection + different FIT confirmation roots | [#45197012079](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45197012079), artifact #14837141120 | Balanced d16 selected; 24-game FIT confirmation **+22.92pp**, 95% bootstrap [**+4.17, +39.58pp**] |
| Retrospective TUNE style + 10+2 | [#45216462418](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45216462418), artifact #14840262340 | **Style PASS, reproducibility PASS; TUNE real-clock strength FAIL** |
| Fresh FIT 288-game 10+2 replication | [#45229239851](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45229239851), artifact #14852677791 | 288/288 legal games, 0 censored, **all 5 preregistered strength gates PASS** |

Three primary scientific report SHA-256 values are embedded in
`docs/evidence/tal-balanced-d16-release-review.json`. The evidence includes
the **negative TUNE result**; it must never be retrospectively concealed or
reclassified as passing.

### Actual 10+2 strength: 48 newly selected FIT roots

| Pairing, 96 full games each | W–D–L for focal | Score | Root-cluster bootstrap 95% CI |
| --- | --- | --- | --- |
| Tal **original** d16 vs Simple d16 | 16–9–71 | **21.35%** | 14.06–29.17% |
| Tal **Balanced** d16 vs Simple d16 | 34–24–38 | **47.92%** | 40.10–56.25% |
| Tal **Balanced** d16 vs original Tal d16 | 67–12–17 | **76.04%** | 66.67–84.38% |

Paired improvement against Simple on identical roots, colors and controls:
**+26.56 percentage points**, with root-bootstrap 95% CI
**[+15.63, +36.98pp]**. Both direct and indirect comparisons favor the new
variant. There were **zero time forfeits**, and all 288 chess games ended
legally. This passes the *locked* criteria: at least 40% vs Simple d16,
at least 10pp paired improvement with 95% lower CI above zero, at least
50% in direct head-to-head, and all games complete without focal clock flags.

### Style retention: retrospective, not blind

Against **138** TUNE historical Tal positions from **66** recorded games:

| Observed descriptor | Result |
| --- | --- |
| Balanced exact move match to historical Tal | **57.97%** |
| Original Rodent Tal exact move match | **51.45%** |
| Simple exact move match | **52.90%** |
| Balanced chooses a different move from Simple | **34.78%** |
| Repeated Balanced search produces same move | **20/20** |
| Balanced reaches d16 on style probes | **100%** |

The earlier gate approved the precommitted preferences and search
repeatability, but the Tal historical style is represented only by
observable proxies (checks, forcing, pawn and kingward moves), **not** by
verified human psychology, sacrifice quality or broad stylistic fidelity.

### The negative result that must not disappear

The earlier TUNE real-clock gate scored **35.42%** Balanced vs Simple d16
versus **27.08%** for original Tal d16, a gain of **+8.33pp** with CI
**[-10.42, +27.08pp]**. That failed the precommitted 40% score,
10pp improvement and positive lower-confidence-bound requirements.
Its status was `DO_NOT_FREEZE_TAL_BALANCED`, and it remains a
genuine limitation despite the later successful 288-game FIT replication.

The later FIT replication was fixed before playing, excluded 120 earlier
FIT roots and exact FIT/TUNE shared prefixes, and did not revisit TUNE
during model selection. **It is still within the broader FIT development
population, not an untouched outer TEST holdout**. A further entirely
blind external claim should not be made.

### Exact product identity

This evidence qualifies only one candidate, with **no automatic** changes
to the shipped game:

| Property | Fixed value |
| --- | --- |
| Original | Rodent IV Tal `tal.txt` SHA256 `f6b58d7cc9e7d2fcdbbc24a20ea72035d05ca5ed5e3401bda240b1b298ce149d` |
| Candidate | TalBalanced `talbalanced.txt` SHA256 `c0f291047023a6f936fd337f11530e6fe886ca0f4f157a5d2f838086857c0897` |
| Motor | Original Rodent IV SHA256 `c2ccc7a99b442dcaa2aa2dc06e020f0182fe7a198c6f080f45f555c126cdcd95` |
| Fixed budget | d16, 5M-node cap, book override off |
| Frozen Fischer control | Profile SHA256 `ee2ab4e175f0dbee88f2f12595393d14001915670e086198139d15ad86eae577` |

TalBalanced differs from the exact original by eight existing Rodent
personality parameters: OwnAttack=260, OppAttack=135, OwnMobility=105,
OppMobility=90, KingTropism=60, PiecePressure=150, PawnShield=150,
PawnStorm=145. The candidate is regenerated only from the pinned original
with `render_tal_variant`; no trained-model inference or opponent-specific
optimizations are required for ordinary play.

### Release review checklist

- [x] Actual GitHub aggregate of the full 288-game panel verified.
- [x] Actual original/Balanced/Simple WDL and paired root-bootstrap CIs reconciled.
- [x] Original candidate FIT selection and separate FIT confirmation recorded.
- [x] Historical TUNE style and repeatability gates recorded.
- [x] Earlier failed TUNE clock result preserved prominently.
- [x] Candidate identity and original/Fischer SHA hashes bound to immutable evidence.
- [x] No historical FIDE Elo, exact-human-play or blankly independent TEST claim.
- [ ] **Explicit maintainer decision to accept Tal Balanced as a stable game personality.**
- [ ] On approval only: preserve this exact SHA candidate as a versioned game
      asset and add it to the qualified-asset manifest without removing
      original Tal or changing Fischer, Arena, XP or the rating system.

**Release gate policy:** Never silently mark the green Actions workflows
as a product approval. Until sign-off, this review is a read-only proposal;
`src/chesssim/data/qualified_game_assets.json` must continue to list
**only Fischer** as frozen.

The remaining external strength/Elo and shared skill attribute work,
if pursued, belongs in dedicated scientific issues rather than being
invented in a product freeze announcement.
