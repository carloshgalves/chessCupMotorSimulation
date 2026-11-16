# Real85 — one experimental engine using actual historical openings and all-phase neural learning

## What is combined

The original game-disjoint real85 85% TRAIN repertoire from #168 supplies
focal historical openings up to **ply 40** (the first twenty complete
moves). Rodent IV remains the unmodified authentic UCI engine,
controlling every move outside the eligible historical book. A
separately fitted CPU rank-six neural residual from #177 uses over 80%
of each historical player's real games, across opening, middlegame
and endgame decisions. It may influence only moves **from ply 41**,
and only when an independent Stockfish MultiPV shallow assessment
accepts a bounded, non-mate alternate (default <=25 centipawns
from that search's best line). This is a single experimental UCI
game, not three disconnected reports.

The opponent Rodent Simple remains completely unconditioned in
openings. There is no historical opponent book. Colors are reversed.
SHADOW and GUIDED arms play legal, full, clockless UCI games, rather
than ranking generated move samples.

This integration requires a new **narrow exception** to the historical
book versus external-policy mutex in matches._play_one. It applies
ONLY to the concrete, original-stockfish-guarded frozen neural
adapter, with a verified focal book, no book for Simple, max book
ply <=40 and policy min ply strictly beyond the book's cutoff.
Legacy trained policy adapters remain prohibited from using books,
and all official clocked games still reject any external policy.

Every real UCI move is recorded in a PGN with its original profile
and model hashes; the JSON artifact has a complete per-ply safety
trace, all factual book moves, Stockfish depth, reported cp gap,
and whether a neural move actually replaced Rodent's choice.
Incomplete games remain incomplete rather than invented draws.
A depth-10 Stockfish postgame sample is descriptive and distinct
from the depth-8 move selection, not a certified independent
human-Elo or deep tactical-safety validation.

## Reproduce without paid APIs

```bash
python -m chesssim.real85_playable fit --fetch \
  --players fischer tal karpov kasparov \
  --max-opening-plies 40 \
  --out /tmp/real85-book

python -m chesssim.real85_neural_stream build --fetch \
  --players fischer tal karpov kasparov \
  --out /tmp/real85-input

python -m chesssim.real85_online_residual \
  --inputs /tmp/real85-input \
  --players fischer tal karpov kasparov \
  --out /tmp/real85-weights

python -m chesssim.real85_neural_guarded_uci \
  --rodent-root /path/to/original-rodent-iv \
  --stockfish /usr/games/stockfish \
  --weights /tmp/real85-weights \
  --profiles /tmp/real85-book \
  --players fischer tal karpov kasparov \
  --rodent-depth 4 --stockfish-depth 8 \
  --min-ply 41 --cp-budget 25 \
  --out /tmp/real85-combined-games
```

## Limits and next controls

The historical opening lookup only applies to exact legal chess
positions that actually occurred in training PGNs, not semantic
generalization of human opening plans. Few book moves may activate
against unrestricted Simple; that is an observed limitation, never
filled with invented data. The neural style residual is still a
separate small CPU model, **not Maia3/DSC150 full-network LoRA**.
Stockfish depth8 guards only incremental move substitutions within
its shallow horizon and cannot assure tactical optimality or the
real player's strength. Actual Elo calibration, deeper tactic and
endgame evaluations, independent blind identity and hundreds of
paired time-limited games remain requirements before a profile may
be called historically accurate. It is correct for some historical
players to share styles or exhibit only small real differences.

The original Rodent IV personalities, official tournament protocol
and historical-rated human claims remain untouched. Any promotion
must be human-approved after a new review.
