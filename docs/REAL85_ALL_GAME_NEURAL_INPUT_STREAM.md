# Issue #169 — lossless 85%-real-game neural training INPUTS (not neural weights)

## Why a new stage?

The Maia3-79M and DSC150 previous adapters were trained on genuinely
historical but artificially small examples (128 FIT games x 3 decisions per
historical player in the neural contrastive pilot). This does **not** satisfy
the objective of using most of the documented historical career in training.
The earlier real85 PGN model (#168, #175) ingests ~85% of all distinct games,
but applies learned opening choices only to the first 40 plies, with original
Rodent IV search in the middlegame and endgame.

This stage removes the **input-sampling limit** for a future full neural
trainer: every original legal UCI move and initial FEN from every selected
real-game trajectory is preserved in bounded, per-game JSONL files, grouped
by historical player, with the full trajectory needed to reconstruct the
Maia chess history. The input serializer needs only one PGN game at a time
during emission and does not load a giant tensor dataset into RAM. It is
an input-preparation step, **not Maia/DSC inference and not weight training**.

## Strict partition protocol

Use the **exact global SHA256 game-trajectory identity** and the
`real85_playable.game_arm` split: approximately 85% of eligible real games
are original TRAIN, about 15% are excluded TEST. Within original TRAIN,
a second deterministic, global per-trajectory hash assigns approximately
97% to neural-gradient FIT and 3% to hyperparameter TUNE.

Thus the expected net fractions of all historical games are approximately
82.45% actual FIT, 2.55% TUNE and 15% excluded TEST. A player with
insufficient real games/TUNE decisions is explicitly
`INSUFFICIENT_INTRA_TRAINING_COVERAGE`, never a validated neural model.
The 80%+ weight-update-ready game fraction must be **measured per player**
rather than asserted from rounded averages. Games shared between players
are assigned the same arm. Repeated FEN states can occur across different
game trajectories; the TUNE partition is game-disjoint, NOT position-disjoint,
and checkpoint pretraining overlap is not independently certified.

There is **no cap on games or focal moves** in FIT. Every focal turn in
a stored game, whether opening, middlegame or ending, is available to
the downstream trainer. Opponent moves are also retained to replay
the complete historical board history. No generated positions, invented
players, fabricated games or estimated Elo are injected.

## Reproducible commands

```bash
python -m chesssim.real85_neural_stream build --fetch \
  --players fischer tal karpov kasparov \
            alekhine petrosian anand rubinstein \
  --out /tmp/chesssim-neural-input

python -m chesssim.real85_neural_stream verify \
  --dir /tmp/chesssim-neural-input
```

The first command creates `manifest.json` and each profile's
`{slug}-fit.jsonl` and `{slug}-tune.jsonl` in a new directory. A row is one
source game, not one sampled decision, with
`initial_fen`, `uci_moves`, `game_sha256`, the original player color,
SHA of the source PGN, and exact focal-decision counts. The downstream model
replays moves incrementally and chooses all focal targets; it never assumes
a FEN alone reconstructs Maia's sequence history.

The second command independently replays **every stored legal move**, checks
the original game hash, global partition, per-player and cross-player
game-arm consistency, source hash binding, shard SHA256, and exact
decision totals. Both commands fail closed on suspicious data.

### Security and provenance

- Original PGNs are fetched from the repository's pinned public snapshot
  (or locally operator-supplied files) and verified by original SHA256.
- **Do not commit, expose or upload the raw trajectory shards** in public
  CI artifacts. The workflow publishes only aggregate counts and file hashes
  in `manifest.json`. For local use, keep the JSONL directory private.
- Serializing the evidence does not apply gradients, choose a LoRA rank,
  touch the frozen pretrained model, change Rodent IV or prove a historical
  identity.
- The old final by-year holdout remains untouched; the game-disjoint TEST
  from real85 has already been inspected in earlier diagnostic studies
  and must not be retroactively called a fresh independent final benchmark.

## Remaining work to close #169

Implement an actual CPU-capable, gradient-based Maia/DSC training loop
that iterates the FIT shards without max-128-game or max-3-move truncation,
with deterministic shuffles and resumable checkpointing, frozen original
backbone, strong legal-mask/castling/promotion tests, and chunked latency
cache. Select hyperparameters using only within-FIT TUNE. Compare neural
adapters with authentic Rodent IV and its historical opening books on
**complete games**, including tactical errors and real endgames, not only
move top-1. Do not promote models or call the neural training complete
until these actions are actually run and audited.
