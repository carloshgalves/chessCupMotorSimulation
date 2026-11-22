# Fischer: full historical decision audit and six common competencies (#191)

## Boundaries, no invented results

The earlier completed 3,616 UCI continuations (`44415124944` +
terminal recovery `44970751726`) showed **no significant competitive
advantage** from the historical FIT opening book. A high exact-match rate
on FIT is not the same as human strength; playing a chess engine at depth
16 is not an Elo, and depth 18 might be stronger than Fischer rather than
more faithful. Therefore the next candidate is the **unchanged Rodent
Fischer** at searched depths 16 and 18. We do not silently overwrite the
historical profile, activate Arena, update its Elo or enable career XP.

The accepted pinned Fischer source contains **827 historical games**:
683 FIT, 23 TUNE and 121 excluded outer TEST. All 827 games are
replayed and **every** focal move is counted. The only decisions excluded
from per-position engine quality are *forced moves with exactly one legal
option*: no tactical/strategic competence is inferred from forced moves.
No game or move is arbitrarily subsampled. Preserve each full move
history, source hashes and source identity.

Previous project work already inspected the 121 historical TEST games.
Consequently these are genuinely **outside the training/selection arm**
but **not an untouched blind external validation set**. Neither TUNE
nor TEST may select search parameters after the FIT decision, and any
new prospective independent set must have an approved separate source,
reconciled non-overlap, frozen policy, verified usage rights and preregistered
gates.

## Pre-move six-competency contexts

Labels are deterministic, overlapping diagnostic *proxies*, not a
scientifically validated six-dimensional psychological model:

| Proposed attribute | Frozen provisional decision-context proxy |
|---|---|
| Opening | Before ply 40 unless the material-based endgame detector applies |
| Tactics | Reference top move has >=80cp advantage over its runner-up or is the sole forced positive mate |
| Dynamics | Middlegame with material piece-type asymmetry >=2 |
| Defense | Side is in check, or reference evaluation -300..-80cp |
| Positional / strategy | Non-check balanced middlegame (abs(ref)<=100cp) with two close choices (0..35cp) |
| Endgames | Material-based endgame detector, independent of clock |

Not every position belongs to every dimension. The number of tagged
positions, games, FEN hashes, unscored mates, skill overlap and missing
dimensions must be disclosed. In particular a large MultiPV gap is
**not proof of a tactical puzzle**, and evaluation balance does not
directly measure long-range positional understanding.

### Common reference and difficulty

- Same pinned PGN, same **reference Stockfish at depth 20**, Threads=1,
  Hash=64 MiB, verified reported depth at every analysis.
- Two preferred root variations (MultiPV2) classify the context
  *without looking at Fischer's or Rodent's selected move*.
- Every distinct root move actually chosen (historical, Rodent depth
  16/18, reference) is rescored with the identical **forced-root**
  Stockfish depth 20 method; it records signed CP differences, +25cp
  acceptable moves, >100cp losses and **separate mate-sensitive cases**.
  Signed negative CP is kept as search disagreement, not silently
  replaced with zero or counted as skill.
- An explicit common pre-move conditioning stratum uses number of legal
  options (few/medium/many), advantage state (balanced/advantaged/
  disadvantaged/mate) and top-versus-runner-up CP margin
  (close/moderate/clear/mate). These are objective **context
  descriptors**, NOT an a priori empirically proven monotonic difficulty
  ordering. Statistical comparisons use the same root and cluster
  confidence intervals by entire historical game, not IID plies.
- Depths 16/18 each request at most five million UCI search nodes per
  move. Report *achieved* depth, overshoots, missing telemetry and
  short searches. Do not label a search that stopped at depth 16 as
  a successful depth 18 search.

The experiment produces actual conditional outcomes for all six
attributes, grouped by game period, phase, source arm, position
context and missingness. Raw game moves/FENs are not redistributed in
the result; SHA identities allow reproduction with a licensed source.

## Experimental strength calibration

After all FIT results have arrived, the algorithm selects a depth of
16 or 18 **per phase** using *only* those FIT decisions, with minimum
100 paired finite moves, 30 different games, 95% reported depth
attainment, <=5-million node limit and explicit safety restrictions.
Depth 18 is eligible only when it reduces absolute human-relative
mean CP discrepancy by >=0.5 CP **without increasing** >100 CP tail
losses, lost positive mates or newly negative mate positions.
Otherwise d16 remains. The selection is *not* optimized on TEST.

The resulting budget is explicitly **UNVALIDATED_EXPERIMENTAL_BUDGET**.
The pipeline reports the frozen policy's TUNE and retrospective TEST
performance separately; insufficient subgroups remain unsupported.
This policy can be queried in a development-only helper, but it does
not change the engine's official personality or gameplay by default.

A future *separate* complete UCI opponent experiment can test this
phase policy with real games and matched time controls. It must not
confuse synthetic strength (versus Simple at depth 16) with intrinsic
historical Fischer playing strength or an Elo estimate.

## Shared 0..110 scale — empirical anchors required

An attribute such as "tactics 90" must be tied to the **same calibration
scale for everyone**, not Fischer's own best and worst scores.

The `common_competency_scale.py` model supports a provisional empirical
common rating-conditional logistic scale with game/player-disjoint
rated CC0 *Lichess standard rapid* reference observations. The data must
be scored using the **same Stockfish depth20, context thresholds and
move-acceptance definitions**. Ratings are explicitly
`LICHESS_GLICKO2_RAPID`, **not** FIDE Elo; this matters for
the historical/modern cohort and missing time-control mismatch.
The procedure uses every authorized rated example, requires
rating span and statistical support per skill, estimates
reference-context difficulty, validates predictions on held-out players,
and applies shared, fixed reference-population fifth-to-95th percentiles
to map onto 0..110 (never individual min/max). Out-of-support estimates
stay null.

Research-only projected values must state their calibration
population, evidence coverage, saturation, and lack of established
cross-era/time-control transfer. The original historical Fischer has no
complete time-control metadata, so even well-predicted modern rapid
anchors **do not automatically qualify his historical attributes**.
The game/Career/XP flags remain false. If no verified common anchors are
provided, the output explicitly leaves all six official values null.
Creating six arbitrary percentages would undermine the user's
requirement that +1 attribute has a small but observable causal
change in play.

Sources:
- Regan & Haworth, *Intrinsic Chess Ratings* (2011),
  https://ojs.aaai.org/index.php/AAAI/article/view/7951
  (model the quality of human move choices across known skill bands;
  this code does not claim to be their full IPR estimator).
- Lichess open database: https://database.lichess.org/
  (PGN game exports CC0; source license scope does not apply to every
  unrelated web page or commercial database).

## Execution — full coverage, not pilot samples

On PR, `fischer-all-competencies.yml` performs syntax, unit tests
and a REAL complete source inventory. The expensive evaluation is
explicitly dispatched when the commit and scientific gates are accepted.

    gh workflow run fischer-all-competencies.yml \
      --repo carloshgalves/chessCupMotorSimulation --ref main

There are 64 deterministic game shards and at most 16 concurrent
runners; every game gets one atomic per-game checkpoint.
Incomplete work is uploaded by `always()`; the aggregator refuses
to publish a complete report until every expected game and its exact
decision/FEN hash sequence has been rechecked against the pinned
PGN. Rerun a previously completed dispatch without reducing the
coverage by selecting the explicit predecessor:

    gh workflow run fischer-all-competencies.yml \
      --repo carloshgalves/chessCupMotorSimulation --ref main \
      -f resume_run_id=12345678

Replayed checkpoints are only trusted when source, original engine,
protocol Python code, python-chess version and shard manifest hashes
are identical. The manual run can consume substantial public Actions
compute: depth20 forced roots and two Rodent searches for *every*
nonforced historical decision are intentionally costly. A job timeout
does not justify reducing the game count.

The anchored calibration is another **separate** step and cannot be
claimed complete from successful source-audit CI alone. Neither the
six-dimensional numbers nor a Fischer-equivalent engine are
homologated by this PR.
