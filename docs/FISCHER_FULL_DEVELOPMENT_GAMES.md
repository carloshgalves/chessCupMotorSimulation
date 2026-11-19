# Fischer exhaustive UCI continuations (#191)

## Candidate frozen for this development experiment

Merged #196: 706 historical games, 1,655 scored positions. Original d16
reproduced 56.45% FIT / 56% TUNE; d16 guarded book 62.55% / 56%.
No book-induced >25cp or mate regression in the independent d18 audit;
the residual caused 15 FIT and one TUNE >25cp losses, one FIT >100cp
loss, and did not consistently improve agreement. Therefore the next
candidate is d16 original + guarded FIT-only repertoire, **without**
the residual. This is not promotion or equivalence evidence.

## Full panel, no arbitrary game limit

- Reconstruct the 12-ply prefixes of every accepted FIT/TUNE source game.
  Count all source games and explicit exclusions (nonstandard board, fewer
  than 12 plies, already terminal). Group identical move prefixes within
  each arm; all source games remain accounted for. Distinct histories can
  transpose; disclose both prefix and FEN counts and overlap across arms.
- For each distinct prefix per arm replay the entire history, then execute
  all eight continuations: original vs guarded book, both colors of
  Fischer, against Simple at depth cap 12 and 16. Fischer cap 16,
  five million requested nodes per search. Books disabled in Rodent,
  FIT empirical book min count 2, fixed seed 2033, d14 guard 25cp as in
  #196. No neural intervention. No source continuation past ply 12 is fed to the
  engine or forced as a sequence; the empirical book may contain FIT moves,
  including those of the originating FIT game. Each actual move is chosen by the UCI
  engine or the guarded opening rule. The complete source opening is
  kept in the board history for repetition and draw adjudication.
- Terminal legal outcomes only: claimable draws, checkmate, stalemate,
  insufficient material and automatic draws. A 450-ply continuation cap
  is an incomplete run, never a draw or win. Report score only when
  **every** planned case is present and complete. Raw scores and paired
  book-minus-original results by arm/opponent depth, root-cluster
  bootstrap; no cross-arm pooling or human Elo estimate.
- Engine, profile and protocol code hashes, chess library version,
  manifest and checkpoint bindings, replay
  legality and opening trace are checked. These establish reproducibility
  and consistency, not a cryptographic proof of original UCI execution.
  Node cap is requested, not assumed enforced; report overshoot/missing
  telemetry. No calibrated attributes, Arena activation, official profile
  modification, or reserved TEST access.

## Execution and cost

PR CI runs unit tests and the **complete real-source plan**, not a small
game sample masquerading as a benchmark. Real continuation execution
is explicitly dispatched after review: 32 deterministic root shards,
up to sixteen concurrent jobs (subject to GitHub runner availability),
330 minutes maximum per job and a 300-minute play-step limit to reserve
artifact upload time. This could
consume many runner-hours. Do not automatically launch it on each push
or claim its checks passed before real execution. A timeout or incomplete
record denies scoring. Retain per-case continuation checkpoints for
local resume; hosted retries restore the same-run shard artifact when available, or a
completed prior dispatch explicitly selected via `resume_run_id`. If no
artifact exists for a shard, that shard starts fresh. No artifact is
promised for a cancelled job; wait for uploads when preserving results.
The original executor rejects mismatched protocol/source/engine hashes. Any retained complete case can be reused
with exactly identical hashes, plan and shard configuration.

Local commands (paths must refer to the actual installed binaries):

    python -m chesssim.fischer_full_games plan --fetch
    python -m chesssim.fischer_full_games play --fetch --rodent-root "$RODENT_IV_HOME" --stockfish /usr/games/stockfish --count 32 --index 0 --out runs/full/shard-0
    python -m chesssim.fischer_full_games aggregate --fetch --count 32 --shards runs/full --out runs/full-summary.json

Create the parent output directory first, then run every index 0..31
and aggregate. On GitHub Actions the workflow is
`fischer-full-development-games.yml` (manual dispatch).
Do not reduce the panel because the run is expensive. If it exceeds
resources, resume or revise the execution infrastructure with the
same full source coverage and protocol, documenting the failure.

### Recover a previous hosted execution

Only after the predecessor run has completed (successful, failed or
cancelled):

    gh workflow run fischer-full-development-games.yml --repo carloshgalves/chessCupMotorSimulation --ref main -f resume_run_id=44415124944

The source dispatch must belong to this repository and workflow on main;
active runs are refused to avoid a changing checkpoint snapshot. Each
shard lists its own nonexpired artifact and restores only atomically
committed JSON files; stale temporary files are discarded in the staging
copy. Exact manifest verification and legal replay by the original
executor are mandatory. Hosted artifacts are retained for 14 days in the
new workflow; earlier runs may have shorter retention. Shards without
artifacts are recomputed (never dropped). Retrying the same run prefers
its own prior shard artifact. Aggregation still requires every case.

This is infrastructure only: no changes to protocol source files,
search budgets, candidate definitions or historical coverage. Parallelism
can reduce wall time but does not reduce total CPU work. Do not mix
warm-hash or cached search results with this frozen fresh-search protocol
without a separate determinism/reproducibility qualification.
