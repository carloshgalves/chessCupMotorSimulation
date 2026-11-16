# Real85 #169 — genuinely paired, frozen historical identity controls

## Question

After actual CPU gradient fitting on every real game in the within-FIT
training arm (#177), does the corresponding model of Fischer, Tal,
Karpov or Kasparov predict a real historical TUNE move better than
the three other player-specific models or an untrained generic model
**on the exact same board position and legal alternative**?

We do NOT assume players have strongly distinct styles. If multiple
historical personalities generalize similarly, the output must say so,
not invent separability.

## Design

1. Reconstruct the exact original global game-trajectory split and
   ~97/3 sub-split within the real85 ~85% TRAIN arm. TUNE contains no
   outer TEST or final held-year games.
2. Train the four CPU sparse low-rank residuals using all their genuine
   FIT moves as in #177, without adding artificial sampled games.
3. Freeze all four models; use identical FENs, historical targets and
   deterministic legal alternatives for each player to score the
   correct model, three swapped-name controls and a completely
   untrained baseline.
4. Group loss by original game, not by individual decisions, to avoid
   pseudo-replication. Bootstrap paired differences by game with
   fixed seed and 500 replicates; include observed samples and 95%
   percentile intervals. Negative or near-zero improvement is a
   legitimate result.
5. Predict each TUNE game's anonymous identity using the lowest
   mean pairwise loss of the four frozen models, counting ties rather
   than assigning them arbitrarily. The game is only one sample;
   game-by-game correctness is exploratory.
6. Confirm that no model weights or even unseen embedding tokens are
   initialized or mutated while scoring TUNE.

## Reproduction

```bash
python -m chesssim.real85_neural_stream build --fetch \
  --players fischer tal karpov kasparov \
  --out /tmp/real-four-input

python -m chesssim.real85_online_residual \
  --inputs /tmp/real-four-input \
  --players fischer tal karpov kasparov \
  --out /tmp/real-four-trained

python -m chesssim.real85_blind_controls \
  --inputs /tmp/real-four-input \
  --weights /tmp/real-four-trained \
  --players fischer tal karpov kasparov \
  --out /tmp/four-tune-identity.json
```

## Interpretation and bounds

The score is a **pairwise move preference versus one legal sampled
alternative**. It is neither exact top-1 move prediction over all
legal options nor a position-independent characterization of style.
The generic control is a random-initialized residual, NOT Stockfish
or strong Maia, and the wrong-name comparison alone is not evidence
of match strength. Models may share legitimately similar chess
patterns. The known public PGNs may overlap model pretraining;
that risk is not adjudicated by this benchmark.

Both outer TEST and final by-year holdout stay excluded. TUNE has
already been used to assess the model, so selecting hyperparameters
from these exploratory results requires a fresh validation protocol.
No official chess personality or real UCI tournament is altered.
Future work must compare to **real Rodent/Stockfish games**, calibrate
actual strength, repertoire, tactical losses and endgame technique,
and require human approval before game deployment.
