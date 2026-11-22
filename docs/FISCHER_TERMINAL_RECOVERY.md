# Fischer: evidence-preserving recovery of the original 450-ply cap

## What failed, based on the completed real run

The first full 3,616-case UCI continuation benchmark is GitHub Actions
[run 44415124944](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/44415124944).
All 32 shard artifacts were present. The original aggregator did **not**
crash, lose games or discover a chess rule failure; it deliberately returned
`INCOMPLETE_DO_NOT_SCORE` and exit code 2 because **22 cases reached exactly
450 continuation plies** without a legal terminal result. The remaining
3,594 were complete. All 22 came from FIT; some are paired original/book
continuations with identical histories. These 22 must **not** be silently
scored as draws or wins.

All original shard artifacts, position histories, engine/profile hashes,
the original `fischer_full_games.py` bytes and its 32 manifests remain
untouched. This recovery reads the original source via GitHub Actions
`actions/download-artifact` and extends **only** those original incomplete
cases, keeping the original prefix and every one of its 450 moves. The
playing algorithm and depth/node caps are unchanged: original Fischer
d16 versus Simple d12/d16, five million nodes requested per move and
genuine Rodent UCI. Historical book choices happen only during the first
40 board plies and therefore cannot be newly applied during recovery.
No Stockfish score is substituted for a move or final result.

Recovery has a separately versioned maximum of 1,800 **continuation**
plies (additional 1,350 beyond the original cap). It is a safety/resource
cap **not** a draw rule. A continued game is scored only if python-chess
recognizes its genuine termination, including checkmate, stalemate,
repetition or legal move-count draw conditions. A game still unfinished at
the extended cap is `PARTIAL`, and final scoring is refused. No original
completed result is recomputed or altered.

Each recovered case points to the original record's byte SHA-256, source
run, exact root/case, original game manifest and versioned recovery code.
It replays the original + recovered moves for legal continuity and
chess-rule terminal outcome. It checkpoints every ten additional moves,
with atomic replacement; completed results and preceding checkpoint
cannot be silently overwritten. The source manifest, source scientific
code hashes and engine/profile hashes are checked before UCI execution.
Engine state is restarted for the post-cap continuation; it is the same
search protocol, but exact hypothetical continuation of the old runner's
internal engine state cannot be directly established.

## Current evidence, not final WDL

The original completed-case scoring (3,594 cases, 22 censored) is
descriptive. **No** final 3,616-game score, Elo, competence vector,
historical equivalence or Arena promotion is claimed before every
censored case has a terminal result.

For the paired book-minus-original effect, the interval from assigning
all censored outcomes their best and worst possible legal scores is
strictly a *censoring bound*, not a confidence interval. The recovery
scan reports these bounds separately by FIT/TUNE and opponent depth,
in addition to actual WDL of completed games.

## Run protocol

1. PR CI: test recovery invariants and download all 32 original artifacts;
   independently replay/reconcile every original and identify the same
   22 capped cases, with evidence and censoring bounds.
2. After merge, explicitly trigger
   `fischer-cap-recovery.yml` with `workflow_dispatch`.
   The source run is pinned to **44415124944**, original main SHA
   `227f9e86235f30493e39f700a0c17d8fea68a2a0`.
3. Only the nine affected source shards receive real UCI continuations;
   up to nine parallel jobs and intermediate checkpoint artifacts.
   No complete source game is recalculated.
4. The final aggregate downloads originals and repairs, verifies each
   source SHA, all moves and true outcomes, and only then publishes WDL,
   paired original/book contrasts and game/root-cluster intervals.
   TUNE has only 23 historical source games, is previously inspected,
   and does not imply independent testing.

Recovery artifacts with `IN_PROGRESS`/`PARTIAL` status must not be
promoted or counted as draws. If a hosted runner exceeds its time limit, the `always()` artifact
upload preserves committed recovery checkpoints. After that dispatch
has finished, run a new dispatch with the optional
`resume_recovery_run_id` input to reuse its immutable case prefix
and completed tail checkpoints. The new workflow checks the prior run
identity and the recovery code/manifest hashes; no case is silently
replayed from the start. For example:

    gh workflow run fischer-cap-recovery.yml --repo carloshgalves/chessCupMotorSimulation --ref main -f resume_recovery_run_id=<prior_recovery_run_id>

Further resource/cap changes require an explicit new recovery version,
not reclassification of a censored game.

This is **not** a substitute for a fresh blind evaluation, direct
historical Elo calibration or the six competence dimensions.
