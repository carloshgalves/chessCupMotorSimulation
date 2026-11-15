# Trainable DSC residual, four historical players — research V2

The original DSC-150M pretrained backbone is FROZEN at code SHA
229ef4291c38c088a9fd3a410e4c0e926a20cea1 and weights SHA256
66b21d7882bb3fb9737bc798e709671325838e5469ed6bfcf114b3abbcb48e24.

After V7 style weights (#153, #155) failed blind identity, this new
experiment learns one 256-coordinate conditional logistic head per
Fischer, Tal, Karpov and Kasparov. The head is genuinely trained by
12 deterministic epochs of bounded SGD and cross-entropy on human
labeled FIT games while the TRUE full original DSC legal move
probabilities act as log-prob offsets. A generic head is fitted to
the pooled FIT games and individual heads are regularized to it.

This design is NOT full training of 150M parameters, not LoRA, and not
a mere static manually entered personality profile. Low-dimensional
semantic features are hashed deterministically from the pre-move
board and candidate move only. No future human label or result is
given to the model at inference.

New globally deterministic split from **TRAIN-year** games only:
SHA256(dsc-residual-v2|mainline) modulo 10. FIT buckets 0–6,
TUNE bucket 7 and AUDIT buckets 8–9, with all cross-arm canonical FEN
transpositions embargoed. CPU budget samples **512 FIT + 48 TUNE +
48 AUDIT decisions per player**, maximum 2 decisions per FIT game,
1 per TUNE/AUDIT game. This intentionally does NOT claim to fit on
the entire historical corpus yet. Separate games, reconstructed full
PGN stack and immutable source SHA are required. If too few remain,
the experiment fails instead of borrowing a held-out arm.

Alpha 0/0.25/0.5/1/2 is chosen only on TUNE; AUDIT reports original
DSC, generic trainable residual, named individual and swapped
individual on the SAME examples; game-bootstrap IC95, overall and
per-player matching; four-way blind identity (same names hidden
during inference, majority and 25% chance baseline). AUDIT is used
once for this version, not for parameter tuning or retraining.

No checkpoint is approved by this development-only code. Any
candidate that meets positive audit gates still requires a frozen
single-use holdout, Stockfish tactical assessment, rights and source
provenance audit, and independent approval. Never silently change
original Rodent IV personalities. Original DSC upstream training may
include these PGNs and the complete historical holdout remains sealed.
