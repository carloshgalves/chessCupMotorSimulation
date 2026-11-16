# Real85 staged historical expansion — wave 2, actual UCI matches (#167, #143)

## Cohort and evidence boundary

The first scientifically inspected four Rodent IV personalities were
Fischer, Tal, Karpov and Kasparov. The **second** cohort comprises
Alekhine, Petrosian, Anand and Rubinstein: four additional, distinct
historical players with authentic aliases in the **unchanged upstream
Rodent IV** personality registry.

The new workflow rebuilds the pinned original Rodent source, downloads
the verified pinned PGN Mentor mirror, and fits the empirical
position-conditional opening-choice counts from approximately 85% of
**each player's distinct, real game trajectories**. The remaining ~15%
is game-disjoint diagnostic TEST. Every historical TRAIN move contributes
to per-phase descriptive statistics, whereas the *actionable learned
policy* is restricted to plies 1–40 (20 full moves), with original Rodent
handling middle games and endings. This is **not Maia/DSC neural
finetuning on 85% of games**. Full neural work is issue #169.

All four newly fitted historical books and the source/repertoire hashes
are available in CI artifacts. The workflow executes **20 additional
real, complete games**: eight color-reversed against Rodent IV Simple,
which chooses its own moves freely, and twelve pairwise color-reversed
between the four historical personalities. `INCOMPLETE` is never
turned into a draw. Stockfish examines actual moves independently.

## How to reproduce

```bash
python -m chesssim.real85_playable fit --fetch \
  --players alekhine petrosian anand rubinstein \
  --max-opening-plies 40 --out /tmp/real85-wave-two

python -m chesssim.real85_playable play \
  --rodent-root /path/to/rodent --profiles /tmp/real85-wave-two \
  --players alekhine petrosian anand rubinstein \
  --depth 2 --max-plies 450 --out /tmp/wave-two-simple

python -m chesssim.real85_roundrobin \
  --rodent-root /path/to/rodent --profiles /tmp/real85-wave-two \
  --players alekhine petrosian anand rubinstein \
  --depth 2 --max-plies 450 --out /tmp/wave-two-roundrobin
```

The full CI runs from a pinned upstream Rodent commit and publishes
game results, repertoire file hashes, tested game counts and actual
Stockfish loss samples. It does **not** infer historical Elo from these
small-search games, certify personality independence, or change
official Rodent profiles.

## Coverage of the 64-player goal

The older inventory #143/#145 found 63 source PGNs present, one source
missing (Vera Menchik, 404 on the pinned snapshot), and 21 official
Rodent historical personality registrations among the 64. After this
cohort succeeds, **8 of the 64** will have documented real85-trained
books and validated full-game UCI execution; no claim is made for the
remaining 56. Subsequent waves should prioritize authentic registered
profiles with adequate source games, report insufficient/missing source
openly, and only later implement tested non-Rodent or neural adaptations
for otherwise unsupported players. Similar playing styles may remain
genuinely similar. No fabricated human history or unsupported Elo.
