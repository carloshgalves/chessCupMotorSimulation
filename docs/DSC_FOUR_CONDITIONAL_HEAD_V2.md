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


## Adversarial review of first genuine run

First genuine four-player run: GitHub Actions
`43671011004`, artifact `four-real-dsc-conditional-residual-heads`
ID **14144800789**, ZIP SHA256
`daaf4e6e73e95b13e9dc624ccae47ec62dd0e9c2f3842d06ad5b18f35a2f5a81`.
The model artifact hash was
`9919649fcd318359aa52f51073e7061d3f37cafa9d8860fb63a6e4935d3177df`.
The report hash was
`cd2acd8ba6ac08064a4e6a0aa876b67116f63d5f2e524107f3903d2db19ec8ba`.

2,048 genuine FIT decisions (512 per player), 192 TUNE and
192 AUDIT decisions in 192 independent audit games.
506 canonical position keys embargoed globally across FIT/TUNE/AUDIT.
Top1 on AUDIT: original DSC 114/192, trained generic head 113/192,
four personal heads 111/192. Personal minus generic macro difference
−1.0417 pp, game bootstrap 95% [−3.125,+1.0417] pp,
paired p=0.6102. Blind 4-way identity 40/192 (20.83%),
balanced game bootstrap 95% [15.43%,26.61%]. 0/4 gates passed.
TUNE alphas Fischer 0; Karpov 0; Kasparov .25; Tal 2.
The Tal personal head fell from generic 29/48 to personal 27/48.
**Evidence is negative, not hidden or edited away.**

Two findings from final code review were addressed after the first
run: (i) top-1 alone could hide harmful changes to the correct
move probability; add proper mean log-loss and top-3 for each
arm/personality; (ii) unadjusted per-player pairwise comparisons
could overstate candidate acceptance; require an additional
post-audit conservative two-sided Bonferroni p<=.0125 for
both individual-vs-generic and correct-vs-swapped comparisons
over four historical players; this gate is a SOFTWARE SAFETY RULE, not a new preregistered hypothesis, and cannot justify confirmatory claims. Also correct a misleading comment
calling the unsigned hash encoding "signed" and remove unused
test imports flagged by Python quality CI. Re-run the genuine
original-model inference and complete independent re-review
at the updated HEAD before moving this PR from DRAFT.

The first run's statistical findings are NOT changed by these
software corrections. No model may be promoted based on this
train-year developmental audit.
