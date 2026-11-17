# #170 — 64 authentic UCI games: matched historical-style SHADOW/GUIDED seed crossover

## Scientific motivation

PR #182 established an opt-in depth12 counterfactual confirmation before
the rank-6 neural residual can replace original Rodent IV's choice. It
completed 16 real UCI games and allowed 40 overrides; depth14 reanalysis
of those 40 found zero deteriorations over 100cp **within that small
experiment**. That does not demonstrate historical Elo, strength uplift,
generalization or statistically reliable individuality. The original
85%-TRAIN opening repertoire activated for just 28 half-moves.

This follow-up expands **actual chess game evaluation**, not the
historical training sample or the model's parameter count. The engine
is still the authentic Rodent IV personality, genuine real85
historical FEN/choice frequencies until ply40, and a frozen experimental
CPU rank6 residual trained on every eligible focal decision in >80% of
original games, with Stockfish depth8/25cp and independent depth12
root-forced cp/mate confirmation for every proposed override.

## Locked, paired 64-game design

The four historical pilots are **Fischer, Tal, Karpov, Kasparov**.
Four **preselected**, distinct numeric seeds: 2033, 2041, 2053, 2063.
At each seed, each player plays two authentic games against an
unmodified, book-free Rodent IV Simple baseline, with reversed colors,
under BOTH conditions:

- SHADOW: the learned residual and both Stockfish safety checks
  may observe but cannot change Rodent's actual move.
- GUIDED: the residual may replace Rodent only when the unchanged
  shallow and newly developed deeper guards accept its genuine
  candidate. Books still use actual training-position counts, never
  synthetic or tailored opponent openings.

4 players × 4 seeds × 2 colors × 2 modes = **64 complete
UCI games** (32 guided, 32 controls), from the original starting
position. The UCI engine depth stays at four; the shallow safety gate
at eight, and the candidate confirmation at twelve. The distinct
opening seed affects only the historically trained stochastic book
tie-breaking, not the corpus, personality, source, or engine.
Seed 2033 was already inspected in the previous pilot and
must not be represented as a fresh independent evaluation; the other
seeds do not alter TRAIN/TUNE/TEST.

All game outcomes must be real terminal chess results, never an
artificial draw from a ply limit. The exact PGN and all per-ply
safety events are replayed and checked against player, opening
seed, chosen color, original engine SHA, Stockfish SHA, opening trace,
and model intervention. Original SHADOW never replaces Rodent and
Simple never uses a historical book.

## Metrics and limitations

For each (player, seed, focal color) pair, calculate the real
focal-player score 0, ½, 1 for SHADOW and GUIDED, and their
difference. Group both colors under the same seed for a
predeclared descriptive crossover block. Keep the *real* game
identity digest and first 12 UCI moves to report observed distinct
trajectories and opening diversity, not assume seeds produced unique
games. Report total actual neural substitutions, separately rejected
deep candidates, and historical book plies. Do not claim that
the two modes visited the same chess position after a divergence.

With only **four seed clusters**, no strength confidence interval,
p-value, human Elo or independent historical fidelity is defensible.
The human TEST/old year-held holdout is not an optimization target
and is not used to select a better seed, depth or cp budget.
Negative guided-vs-shadow results are findings, not a reason to
change thresholds post-hoc.

For **every** actually guided neural substitute, a second *postgame*
Stockfish comparison at **depth14** checks the original Rodent and
played neural moves as forced searches on the same original root FEN.
All mate-affected cases and deteriorations, including >100cp, are
published without using success thresholds to hide negative results.
If a seed produces no neural substitutions, that is reported as such;
no fake interventions or forced seed changes are allowed.

## Reproduction — CPU and free GitHub Actions

Rebuild real85 opening books with the fixed source snapshot,
rebuild full-game real85 neural FIT/TUNE streams and actually
train the original four rank6 CPU residuals; compile the pinned
original Rodent IV and install a real independent Stockfish.
Then:

```bash
python -m chesssim.real85_seeded_crossover \
  --rodent-root /path/to/pinned-original-rodent \
  --stockfish /usr/games/stockfish \
  --profiles /tmp/real85-books \
  --weights /tmp/real85-weights \
  --seeds 2033 2041 2053 2063 \
  --rodent-depth 4 \
  --out /tmp/real85-crossover
```

The new report is `/tmp/real85-crossover/crossover-report.json`.
Each seed has its own signed-by-SHA evidence files under
`seed-<number>/results.json` and `games.pgn`. Game hashes and
seed/color-arm pairing are checked before scoring. The CI appends
per-seed `depth14-counterfactual.json` where guided moves exist.
Original human FIT/TUNE trajectories are never published as CI
artifacts. This is evidence for **which next scientific weakness
to correct**, not a deployable historical human simulation claim.

## Next actions

Use the observed opening-diversity and strength statistics to
plan a larger opponent-strength-calibrated evaluation, and
diagnose actual middlegame/endgame losses on new non-TEST generated
games. Preserve individual style similarities when they reflect
historical chess reality, not as a classifier metric to game.
Expand to the other 60 players only when real PGNs and genuine
Rodent profiles are available, without fabricating sources or Elo.
