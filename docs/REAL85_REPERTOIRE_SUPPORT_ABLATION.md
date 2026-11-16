# Real85 — empirical opening-support ablation, not a promotion (#170)

The experimental Rodent IV + historical repertoire can choose a move
observed in **at least N real training games** in an identical four-field
FEN position, up to ply 40 (20 complete moves). The base model and 21
authentic Rodent personalities remain unchanged.

## Testable hypothesis

The existing `opening_min_count=2` condition drastically reduces book
activations against an **unconstrained** Rodent IV Simple opponent. A
strictly opt-in `opening_min_count=1` allows rare, genuine historically
observed moves and may improve actual repertoire coverage, but could
also create more tactical mistakes. **No improvement is presumed.**

The source-locked PGN Mentor snapshot uses 85% game-disjoint TRAIN games
for each of Fischer, Tal, Karpov and Kasparov. The leftover 15% TEST arm
was already inspected by prior exploratory studies; it must not be
retrospectively presented as a fresh independent confirmation.
No Maia/DSC neural weights are trained by this feature.

## Reproducible contrast

First build real85 profiles using `python -m chesssim.real85_playable fit
--fetch --max-opening-plies 40 --out /tmp/real85-profiles`. Then run
the **same original Rodent IV executable**, all four authentic profiles
and the unrestricted UCI Simple adversary, at the same fixed search
depth and color reversal:

```bash
python -m chesssim.real85_playable play \
  --rodent-root /path/to/rodent --profiles /tmp/real85-profiles \
  --opening-min-count 1 --depth 4 --out /tmp/book-min-1

python -m chesssim.real85_playable play \
  --rodent-root /path/to/rodent --profiles /tmp/real85-profiles \
  --opening-min-count 2 --depth 4 --out /tmp/book-min-2
```

Each `results.json` and PGN carries the explicit minimum support
setting. In actual free-opponent games, only the **focal** historical
player uses the learned opening policy. The optional
`--seed-opponent-historical-book` creates a *different conditioned
experiment*; it is not a fair independent control against Simple.

For pairwise historical profiles, the same opt-in flag is available
from `python -m chesssim.real85_roundrobin --opening-min-count 1`.

The linked CI constructs Rodent IV from the pinned original source,
re-fits authentic 85%-TRAIN PGNs, plays 16 actual UCI games across the
two arms, and independently analyzes tactical quality via Stockfish.
Only completed chess outcomes are scored; games that reach the ply
budget remain INCOMPLETE. It publishes counts of actual book plies,
score versus Simple, and sampled Stockfish mean centipawn loss with
denominators. Book coverage is not equivalent to strength and does
not establish historical Elo.

## Experimental interpretation

Avoid choosing a production threshold from 2 games per player.
Subsequent work must select a policy using only a **within-FIT TUNE**
partition, randomize opening seeds while preserving matched colors and
paired opponents, use more games and middle/endgame Stockfish analysis
and require nondegradation of tactual quality. Preserve shared styles
when supported by the corpus and do not invent identity separability.
