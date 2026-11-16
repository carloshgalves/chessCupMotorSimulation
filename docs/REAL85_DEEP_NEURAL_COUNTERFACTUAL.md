# Real85 / #170 — independent deeper same-position audit of actual neural overrides

## Motivation

PR #180 combined 85%-real-PGN historical opening books with a CPU rank-six
neural residual and original Rodent IV UCI play in **16 genuine full games**.
Across Fischer, Tal, Karpov and Kasparov, the CI recorded 28 actually-used
historical book plies and 67 true neural substitutions from ply 41 onward.
The move-selection safety gate used Stockfish depth 8, with a nominal 25
centipawn bound relative to the best move *within its shallow analyzed set*.
This **does not prove** that a neural override is harmless at greater depth,
nor that either candidate plays at the real human's historical strength.

## Exact counterfactual question

At every **actual** guided neural substitution, freeze the position
before the played move (replay the generated PGN, not the historical TEST
corpus). Compare **the original Rodent move** with **the neural move that
was really played**, analyzing each as the only allowed Stockfish root
move, on the identical chess FEN at depth 12:

- Original/root-candidate loss is not inferred from the subsequent board,
  which would introduce an extra ply of search.
- A positive (neural minus Rodent) centipawn difference favors neural;
  negative favors Rodent. Count degradations deeper than 25, 100, 250cp.
- Any mate score makes the centipawn comparison **mate-affected and excluded**,
  rather than converting a forced mate into an arbitrary cp value.
- Every PGN/result pair is verified: 16 terminal chess results, all 16
  report traces attached to played legal moves, FEN hashes and actor identity,
  genuine focal opening moves and free Rodent Simple controls, original
  Rodent/Stockfish executable SHA headers, and each replacement's recorded
  shallow guard metadata. Bad, duplicated, missing or manipulated evidence
  fails closed.
- Record *every* override, not 12 sampled endgames or a cherry-picked subset.
  The experimental per-game distribution is preserved, but there are only
  two guided games per historical player; no credible human-strength
  confidence interval is claimed.

## Reproduction

Generate the game evidence with the **same** workflow as #180: fit real85
books; rebuild real85 FIT/TUNE input; train residuals on all eligible FIT
moves; build pinned original Rodent IV; play 16 combined UCI games using
the actual Stockfish move-time depth-8 gate.

Once the resulting `results.json` and `games.pgn` exist, run:

```bash
python -m chesssim.real85_neural_counterfactual \
  --results /tmp/combined-uci/results.json \
  --pgn /tmp/combined-uci/games.pgn \
  --stockfish /usr/games/stockfish \
  --depth 12 \
  --out /tmp/real85-deeper-counterfactual.json
```

The CI rebuilds both original engine and the frozen individual neural models
from source-locked real PGNs, repeats the full UCI game evidence and then
reports exact counts and independent *root-constrained* deeper scores.
Stockfish is used only for post-game evaluation; the model's weights and
original Rodent profiles are not changed. Neither previously inspected
outer TEST nor held-year final holdout contributes a decision to the audit.

## Limits and next scientific action

This is an exploratory **incremental move-safety stress test**, not a
paired causal win-rate comparison: GUIDED and SHADOW diverge in chess
positions once any override occurs. Each deeper evaluation remains
search-depth sensitive and may change at another depth, opening or time
control. Game-level dependencies and sample size preclude human Elo
calibration. Negative evidence should be retained as a concrete tactical
finding; the next protected gate may refuse overrides that worsen at
deeper depth, then be evaluated on fresh independent games. Do not choose
parameters on an already-inspected historical TEST set.

Separately, the real85 book still activates rarely against an unconstrained
opponent (28 total plies across 16 games in #180), so the next repertoire
slice needs within-TRAIN evaluation of generalization rather than fabricating
off-book human choices. Endgame technique must be compared on new
position sets independently of these generated matches before promotion.
Accept shared style among players when the genuine corpus supports it.
