# Tal Balanced — fresh FIT 10+2 strength replication

## Why another experiment?

The prior 240-game FIT DOE selected Balanced d16 with +22.92pp on
twelve confirmation roots. The next retrospective TUNE gate
[#45216462418](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/45216462418)
found style and repeatability acceptable, but real **10+2** competitive
strength insufficient to freeze: original Tal d16 scored 27.08%
(4W/5D/15L), Balanced 35.42% (5W/7D/12L) against Simple d16.
Paired Balanced gain +8.33pp had IC95 **[-10.42,+27.08]pp**.

Green workflow proves complete valid evidence, **not** a passing release gate.
Do not weaken thresholds or use TUNE again as a tuning set. Candidate remains
Tal Balanced d16, unchanged; no further parameter adjustment in this stage.

## Predeclared replication experiment

- Historical PGN SHA256
  `586aac4613e170474f12893679ab992d3999ca5109df7df77c634a76846d2687`.
- Scan all **2,024 original FIT trajectories** and their first 12 legal
  half-moves. Deduplicate exact opening prefixes. Exclude the 96 FIT
  openings used in the first 608-game graded benchmark, **all 24 FIT
  openings used in the Balanced profile-selection DOE**, and exact
  prefixes shared with retrospective TUNE. **No TUNE or TEST
  decisions will be scored for model selection.**
- Choose **48 remaining distinct FIT roots** by a SHA-256 salt frozen
  before any move evaluation. Roots selected only from source metadata,
  without filtering based on engine wins, losses, phase or move match.
- For each root play six complete **10+2** matches:
  original Tal d16 vs Simple d16 in both colors;
  Balanced d16 vs Simple d16 in both colors;
  Balanced d16 vs original Tal d16 in both colors.
  **48 x 3 x 2 = 288** actual legal games, distributed over 24
  runner shards.
- Each candidate and control gets the same pinned Rodent IV engine,
  fixed search target depth16 and 5M node cap. Tal Balanced is the
  **exact SHA256-pinned candidate**
  `c0f291047023a6f936fd337f11530e6fe886ca0f4f157a5d2f838086857c0897`
  from the prior DOE. Original Tal, Simple and frozen Fischer have
  independent pinned SHA checks; variants use **only temporary CI
  personality overlays**.
- Every actual chess move and its elapsed wall time are replayed; clock
  forfeits obey chess rules. Engineering caps, timeout, missing games
  or illegal results **cannot be scored as draws**.
- All six games of a root occupy the same runner, and the 95%
  bootstrap CI resamples **whole two-color roots**. Publish W/D/L,
  percentages and grouped intervals for the three confrontations.
  In particular, report the paired Balanced-minus-original gain
  when both face Simple on exactly the same root/color.
- The direct Tal Balanced vs Tal original match provides an
  **additional orthogonal** head-to-head check, but should not be
  misrepresented as a rating-calibration experiment.

## Locked acceptance criteria, independent of outcome

All 288 matches must terminate legally, with zero focal-engine
time-forfeit losses. Balanced must score **>=40% against Simple d16**
and improve over original Tal by **>=10 percentage points** with
lower bound of the 95% paired-root bootstrap CI strictly above zero.
In the head-to-head, Balanced must score at least **50%** against
original Tal. The latter is a corroborative competitive condition,
not a FIDE strength estimate.

If the criteria hold, the output is
`ELIGIBLE_FOR_TAL_BALANCED_FURTHER_RELEASE_REVIEW`. If they fail,
the result is `NO_CONFIRMED_TAL_BALANCED_CLOCK_STRENGTH_DO_NOT_FREEZE`.
Missing or nonterminal games produce `INCOMPLETE_DO_NOT_SCORE`.
**A green GitHub Actions run does not mean release approval.**

The earlier 138-position retrospective style gate passed, but was
not blind. This replication tests only strength on fresh FIT roots
(which remain in the development population). It **does not touch
the 341 historical TEST games**, claim human Mikhail Tal Elo, or
silently upgrade the official game roster. A separate maintainer
signoff is required before freezing any new candidate.

## Run

On PR, the workflow first checks unit tests, lints and verifies the
full historical root inventory; then 24 bounded CI jobs play the
288 matches, with real results stored in per-shard JSON artifacts.
The `aggregate` job validates each case and publishes
`tal-fresh-fit-clock-results.json`.

Rerunning this experiment or changing its acceptance thresholds in
response to unfavorable results would create a post-selection test,
so any future candidate needs a new preregistered protocol and
new opening-root population.

The fixed Fischer d16 product asset remains untouched throughout.
