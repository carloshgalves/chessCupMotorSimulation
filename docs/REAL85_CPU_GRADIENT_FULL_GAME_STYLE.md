# Real85 #169 — true neural gradient fitting on all historical FIT moves

The source-locked stream from #176 provides **560,560 genuine human focal
decisions from 14,318 FIT games** across the eight playable historical
pilots. This research stage trains real parameters on *every eligible*
focal move of each game, not on a first-128-games sample or openings only.

## Architecture and scientific claims

A CPU-only **sparse rank-six bilinear neural style residual** learns
context/move preference by pairwise logistic SGD. Context features
encode game phase, material advantage, move number and castling rights.
Candidate-action features encode moving piece, oriented source and
destination, move displacement, capture, promotion and castling.
For each real decision a different **legal alternative** is chosen
deterministically from the same FEN, by global game digest, ply and seed.
The pairwise logistic gradient updates model embeddings and biases.
Forced single-legal-move decisions are counted but have no valid
negative and thus do not update weights.

This is NOT Maia3-79M or DSC150M fine-tuning, not a stand-alone strong
chess engine, and does NOT prove historical rating, fidelity, or even
that the learned style is better than Rodent IV. The frozen authentic
Rodent binary and personality profiles remain untouched. A future
opt-in safety-gated adapter must use a strong engine to determine
tactically acceptable moves and this neural residual only for style.

## Leakage and reproducibility

Uses the **original global 85% game TRAIN / 15% TEST** split from #176.
A deterministic sub-split keeps ~3% of original TRAIN as **TUNE**
with no gradient updates. Therefore effective neural FIT per player
is **82.06–83.55% of all real historical games**. Source checks enforce
exact PGN trajectory SHA, legal chess moves, FIT/TUNE arm, every focal
decision and saved shard SHA. Original TEST is excluded. The prior
year-held final holdout remains closed; external base-model pretraining
overlap has not been excluded.

TUNE inference may compute scores but cannot add model parameters or
change learned tensors. Pre/post SHA of the complete model enforces this.
Pairwise TUNE loss and preference rate are *exploratory*, not human
identification, strength rating or a basis to assert superiority.

## Usage (no paid API/GPU)

First prepare the eight authentic historical cohorts:

```bash
python -m chesssim.real85_neural_stream build --fetch \
  --players fischer tal karpov kasparov alekhine petrosian anand rubinstein \
  --out /tmp/real85-input
python -m chesssim.real85_neural_stream verify --dir /tmp/real85-input
```

Train all available FIT games and decisions for each player:

```bash
python -m chesssim.real85_online_residual \
  --inputs /tmp/real85-input \
  --players fischer tal karpov kasparov alekhine petrosian anand rubinstein \
  --checkpoint-every 300 --out /tmp/real85-trained
```

Optional **partial test** and explicit resume:

```bash
python -m chesssim.real85_online_residual --inputs /tmp/real85-input \
  --players fischer --max-fit-games 10 --checkpoint-every 5 \
  --out /tmp/fischer-partial
python -m chesssim.real85_online_residual --inputs /tmp/real85-input \
  --players fischer --resume --out /tmp/fischer-partial
```

The first invocation exits nonzero with PARTIAL status, and the second
resumes exactly at the next game, never repeating previously checkpointed
gradient updates. Regression tests compare uninterrupted vs resumed
weights bitwise. SHA-tagged, atomic checkpoint files contain the actual
trained small-model weights and optimizer state. The workflow publishes
**only model weights, hashes, aggregate counts, and TUNE metrics**,
never raw historical game trajectories.

## Remaining work to close #169/#170

Independent real-game UCI inference has NOT been implemented in this
slice. Required next steps: paired ablation and wrong-name controls on
intra-TRAIN TUNE; strong engine's tactical threshold; fully playable UCI
adaptation without changing original Rodent or tournament constraints;
calibration of strength, historical opening distributions and finals;
human-approved gate to promote any profile. Similarities between players
are allowed when data justify them. No invented rating or artificial
style separation.
