# Tal Balanced d16 — retrospective style and 10+2 qualification

## Objective and evidence

The original Tal depth16 scored 29.69% in FIT and 15.18% in retrospective
TUNE against Simple depth16 during the full 608-game run #45167232903.
The bounded five-candidate FIT experiment #45197012079 selected Tal
Balanced depth16, and its separate FIT confirmation showed +22.92
percentage points, 95% root-cluster bootstrap CI +4.17 to +39.58 pp.

This is a relative in-project improvement, NOT the historical Tal's
FIDE Elo and NOT evidence of perfect human choice reproduction.

## Source, population, and candidate

- Pin the Tal PGN SHA-256
  586aac4613e170474f12893679ab992d3999ca5109df7df77c634a76846d2687
  (2,431 accepted games). Only the 66 historical TUNE games
  supply retrospective style positions and clock roots. The 341 TEST
  games are unopened for choice scoring.
- IMPORTANT: TUNE is NOT blind. All its opening roots appeared in
  the earlier Tal d16 baseline. The result supports a bounded
  product check but not statistically independent human-strength proof.
- Replay all 66 TUNE game trajectories and choose exactly one
  nonforced Tal position per phase (opening, middlegame, endgame)
  using a SHA-salted rank fixed before motor evaluation.
- Choose twelve distinct 12-ply TUNE roots using a separate
  SHA rank independent of any chess-engine outcome. Both original
  Tal d16 and Balanced d16 play both colors against Simple d16:
  12 roots x 2 variants x 2 colors = 48 actual 10+2 chess games.
- Generate Balanced only as a sandbox personality-file overlay, with
  the precise eight weight edits selected by the earlier FIT DOE.
  Rodent binary, original Tal, Simple and Fischer d16 stay SHA-pinned
  and untouched. The official Arena, XP and roster do not change.

## Product style and repeatability gates

Evaluate human Tal, original Tal d16, Balanced d16 and Simple d16
on identical pre-move board positions. Disclose frequencies of
forcing moves (capture or check), checks, pawn moves, and non-pawn
moves closer to the opposing king. These are descriptive proxies,
not validated psychological, sacrifice or tactical-strength labels.

Require at least 100 positions from 50 original TUNE games;
Balanced must differ from Simple on >=5% of positions, agree
exactly with historic Tal on >=30%, and not underperform original
Tal's exact historical agreement by over 8 percentage points.
At least 2/4 frequency proxies must be no farther from historical
Tal than Simple's rate, allowing 2.5 percentage points tolerance;
Balanced's checking and forcing frequency cannot fall more than
10 percentage points below original Tal's.

Repeat exact Balanced choices on a fixed 1-in-7 SHA selected
subset. Require >=12 repeats, >=95% exact repeatability, and
>=90% realized depth16 in style probes.

These are deliberately modest GAME PRODUCT gates, not a claim
that Tal's mind or chess style has been recreated.

## Clocked-strength gate

All 48 real 10+2 games must terminate legally; no cap draw or
invented results. Each move's wall-clock time and remaining clock
must be replayed independently. The focal engine slot called
'fischer' by the audited generic 10+2 chess executor is
explicitly mapped to the tested Tal variant in each envelope.
It NEVER means Fischer actually participated in the Tal test.

Balanced must achieve >=40% against Simple d16, improve >=10 pp
over original Tal on identical roots/colors, with 95% paired
root-bootstrap CI lower bound strictly above zero, and zero
time forfeits by both focal Tal profiles. Report all W/D/L,
clock forfeits and the paired confidence interval.

Incomplete or corrupt data is INCOMPLETE_DO_NOT_SCORE.
Otherwise the preregistered result is one of:
ELIGIBLE_FOR_TAL_BALANCED_ASSET_RELEASE_REVIEW or
DO_NOT_FREEZE_TAL_BALANCED. Green CI is not automatic approval.

After real evidence is reviewed, a separate explicit maintainer
sign-off is required to freeze an immutable Tal Balanced profile.
The Fischer d16 frozen game asset remains unchanged. The common
six-skills 0–110 and XP research remains issue #203.

## Execution

The PR automatically runs the plan and twelve 10+2+style
shards, then uploads all actual decisions and the aggregated
release-gate JSON. Human approval remains a separate step.
An intentional manual rerun is available through the GitHub
Actions workflow tal-balanced-validation.yml.

No engine, original personality, official competition settings,
or official release record is changed by this PR.
