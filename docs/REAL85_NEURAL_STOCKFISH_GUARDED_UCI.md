# Real85 #170 — actual games using trained history plus original Rodent IV and Stockfish

The CPU rank-six neural residual from #177 is trained on >=80% of the
real historical games of the eight pilot players. This research
experiment lets the first four models **influence actual full UCI
chess games** for Fischer, Tal, Karpov and Kasparov.

This is opt-in, unrated, experimental. It does not edit original
Rodent IV Personality files, change official tournaments, alter Maia/
DSC models, or claim any historical Elo or verified human fidelity.

## Three-stage architecture

1. **Real Rodent IV**: on each move the player's original Rodent
   Personality chooses its authentic bestmove.
2. **Independent Stockfish**: if this is the focal historical player
   and ply >=21, Stockfish evaluates up to four PV root moves at
   depth 8 (configurable 8–20). It identifies a near-equal candidate
   set within a strict max centipawn gap (default 25).
3. **Learned neural style**: the frozen low-rank residual scores those
   already-analyzed moves and may replace Rodent's original choice,
   *only if* the original choice is itself inside the independently
   analyzed safe set, a different near-equal candidate exists and
   there is no mate score in the analyzed PV. Otherwise Rodent keeps
   its move. Any Stockfish error aborts the match instead of silently
   claiming that a safety gate ran.

This only limits **the incremental risk of model overrides at a shallow
Stockfish depth**. It does NOT make Rodent itself tactically safe when
its move is absent from Stockfish's safe set, and depth-8 analysis can
miss deeper tactics. A stronger actual-stockfish depth, practical
controls and paired tournaments are still necessary. The policy
cannot interfere with official clocks or with opening-book code paths.

The original empirical real85 opening repertoire is intentionally
**disabled** in this first full-game safety experiment because the
existing development-only policy API prohibits simultaneous books.
Historical openings remain available and separately proven with the
original unmodified Rodent in #168/#175. Integrating learned openings
alongside tactical-safe neural middlegames requires its own security
review and new experimental contract.

## Actual-game A/B protocol

For each of Fischer, Tal, Karpov and Kasparov run
two complete games against Rodent IV Simple with reversed colors in
both conditions:

- SHADOW: calculate neural preference and Stockfish gate but play
  original Rodent bestmove 100% of the time.
- GUIDED: use the neural recommendation only inside the exact
  Stockfish safe-set contract.

Both branches use authentic original Rodent UCI from the fixed
upstream commit and a real Stockfish engine with a pinned binary
SHA in the report. Every event records FEN hash, selected actor,
Rodent bestmove, proposed neural alternative, safety status,
centipawn allowance, and whether a genuine override was played.
The result JSON preserves the **complete per-ply policy trace** for every
played game. CI independently checks each effective override against
its recorded Stockfish candidate count, allowed centipawn difference,
model recommendation, player identity and unchanged Simple control.
Only actual completed chess results are scored. Reaching a ply
budget yields INCOMPLETE, never an artificial draw. PGNs are
preserved, and postgame Stockfish depth10 reanalyzes genuine moves.

## Commands

```bash
python -m chesssim.real85_neural_stream build --fetch \
  --players fischer tal karpov kasparov \
  --out /tmp/four-input

python -m chesssim.real85_online_residual \
  --inputs /tmp/four-input \
  --players fischer tal karpov kasparov \
  --out /tmp/four-trained

python -m chesssim.real85_neural_guarded_uci \
  --rodent-root /path/to/pinned-original-rodent \
  --stockfish /usr/games/stockfish \
  --weights /tmp/four-trained \
  --players fischer tal karpov kasparov \
  --rodent-depth 4 --stockfish-depth 8 \
  --cp-budget 25 --min-ply 21 \
  --out /tmp/four-guarded-games
```

The 16-game CI is an **engineering smoke and early exploratory
ablation**, not historical strength calibration. A higher-quality
study must use hundreds of games and a time-controlled non-PGN
position distribution, compare with human move distributions,
opening repertoire, endgame accuracy, depth-dependent tactical
loss, and disjoint older holdouts after protocol freeze. Allow real
players to be similar rather than manufacturing distinct labels.
All official promotions remain explicitly disabled until human
review.
