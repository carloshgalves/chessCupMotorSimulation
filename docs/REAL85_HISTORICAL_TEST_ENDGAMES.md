# Real85 — historical TEST-only material-endgame diagnostics (#170)

## Research question

Do the four original Rodent IV personalities reproduce the **actual move
decisions** of Bobby Fischer, Mikhail Tal, Anatoly Karpov and Garry Kasparov
in historically recorded, legally replayable chess endgames? How much tactical
error (according to independent Stockfish depth 8) is observed against the
same real positions?

No player similarity or strength rating is presumed.

## Scope and reproducibility

The training corpus used to establish source and game partitions is the frozen,
SHA-verified PGN Mentor snapshot already employed in #168:
Fischer 827 unique games (706 FIT / 121 TEST), Tal 2431 (2090/341),
Karpov 3528 (3016/512), Kasparov 2128 (1824/304).
Those **85% FIT** games contribute to the empirical opening model and phase
statistics of #168, **not** to this endgame TEST evaluation and **not** to an
endgame-specific weight update. Full-game neural fitting remains #169.

Each trajectory's globally hashed TEST assignment is recomputed and verified
against the frozen training manifest and PGN bytes. No new split, alternate
holdout or per-player-specific game reassignment is introduced. The prior
year-based holdout is not opened.

For each TEST PGN, the extractor identifies actual played decisions of the
historic player in positions with <=16 total non-pawn material points
across BOTH players. It selects at most one endgame decision **per game** with
a deterministic SHA rule, then **every available** independent TEST endgame game per player (no artificial cap).
This avoids a long game overpowering a sample. Each audited record carries
the real trajectory SHA, FEN SHA, color, ply, year and historical UCI move.
Missing or insufficient endgames are reported, never invented.

The next step runs **original Rodent IV UCI** with its official untouched
Personality and internal books disabled in the same historical FENs. An
independent Stockfish binary evaluates the baseline FEN, the observed human
move, and the real Rodent move at the **same root FEN and search depth**;
for each historical/Rodent candidate it constrains the root search to that
legal move rather than spending an extra search ply on the child position. True legal moves are
validated before scoring. Engine/profile/baseline source hashes and depth are
retained.

Per-player metrics: independent real games sampled, human-move agreement,
mean/median Rodent nonmate centipawn loss, human nonmate centipawn loss,
count of >=250 cp Rodent errors and separate mate-affected counts.

**Limitations:** (a) Stockfish analyses before and after a move are
depth-sensitive and can differ due to horizon effects. (b) The test endgame population is larger but still not a random representative sample of the full career, and diagnostic precision is not a historical strength rating. (c) Human chess
decisions may intentionally prefer objectively inferior positions, so
centipawn loss is not style identity. (d) No parameter may be selected
using these TEST positions. Prepare future calibration using a separate
TUNE partition within the 85% FIT games, and then rerun a predeclared
confirmatory TEST only once.

## Usage

After producing `/tmp/real85-fit` with
`python -m chesssim.real85_playable fit --fetch --max-opening-plies 40 --out /tmp/real85-fit`:

```bash
python -m chesssim.real85_endgame_audit extract \
  --fetch --profiles /tmp/real85-fit --per-player 0 \
  --out /tmp/real85-historical-endgame-test.json

python -m chesssim.real85_endgame_audit audit \
  --positions /tmp/real85-historical-endgame-test.json \
  --rodent-root /path/to/rodent-iv --stockfish /usr/games/stockfish \
  --rodent-depth 4 --stockfish-depth 8 \
  --out /tmp/real85-rodent-endgame-audit.json
```

Passing engineering CI does not constitute historical homologation. Do not
blend this TEST-only report with within-FIT model selection, official games,
or old held-year benchmark evaluations.
