# Real85: individual UCI search budgets (issue #170, exploratory slice A)

This is an **opt-in experimental motor calibration tool** for the original
Rodent IV profiles of Fischer, Tal, Karpov and Kasparov. It does not edit
Rodent source, binary, personality settings, rated tournaments, or the
official 10+0 protocol. It is not a learned player model and does not
establish historical Elo.

## Why this exists

The real85 pilot (#168) played 40 complete, actual UCI games, including a
two-color 4-player round robin at depths 2 and 4. Results and Stockfish
mean-centipawn-loss vary strongly by player and search depth. A common depth
cannot answer whether each profile can be calibrated independently.
Avoid selecting a "best profile" from only two games per pairing.

The reference historical corpus consists of 8,914 eligible unique real PGNs:
Fischer 827, Tal 2,431, Karpov 3,528, Kasparov 2,128.
The 85% FIT/15% TEST deterministic trajectory split is preserved. All
FIT moves contribute to descriptive style counts; empirical **opening**
choices are trained through ply 40. No claim is made that Maia/DSC neural
weights used 85% of the corpus. Neural large-corpus work is #169.

## CLI example (local Rodent IV, no paid service)

Create a JSON file for *all* four participants (keys must match exactly):

```json
{"fischer": 4, "tal": 6, "karpov": 4, "kasparov": 6}
```

```bash
python -m chesssim.real85_playable fit --fetch \
  --players fischer tal karpov kasparov --max-opening-plies 40 \
  --out /tmp/real85-trained

python -m chesssim.real85_roundrobin \
  --rodent-root /path/to/rodent --profiles /tmp/real85-trained \
  --players fischer tal karpov kasparov \
  --depth 4 --depths-file /path/to/player-depths.json \
  --out /tmp/real85-calibration-roundrobin

python -m chesssim.real85_playable play \
  --rodent-root /path/to/rodent --profiles /tmp/real85-trained \
  --players fischer tal karpov kasparov \
  --depth 4 --depths-file /path/to/player-depths.json \
  --out /tmp/real85-calibration-versus-simple
```

The round robin uses the specified UCI search depth for each player on
**its own turns**, with reversed colors. In the second experiment, Simple
always searches at `--depth 4`, the focal identity uses its own specified
depth, and **Simple's opening moves are free UCI**, not historical opponent
moves. The `--seed-opponent-historical-book` flag is a separate conditioned
experiment and cannot be called an independent strength baseline.

Each PGN carries `ExperimentalDepthWhite`, `ExperimentalDepthBlack`,
`DepthByPlayerOptIn`, and the true side-specific `SearchLimit`. The
round-robin aggregate carries a canonical SHA256 of the depth plan.
Incomplete games stay `*`/INCOMPLETE, never fabricated draws.

## Evidence and promotion

The CI fixture uses depth plan 4/6/4/6 **only as an engineering smoke
test**, not as a historically selected optimum. Its actual game PGNs and
JSON reports are retained as workflow artifacts.

Before claiming calibrated force or historical fidelity, separately
pre-register within-FIT/TUNE model selection, compare paired full games
with/without learned openings, grade untrained middlegame/endgame positions
using independent Stockfish depths, and keep the 15% trajectory TEST sealed
for evaluation. Do not infer historical Elo from Rodent-only depth-2/4/6
games. The old year-held-out final test (#147) remains sealed, and neural
pretraining overlap is not certified. Promotion to any official personality
still requires explicit human review.
