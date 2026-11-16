# Real85 #170 — opt-in deeper confirmation BEFORE a neural move is played

## Concrete defect and purpose

PR #181 measured every actual post-ply40 neural substitution of original
Rodent IV in the real85 16-game book+neural experiment. At independent
depth12 on the same original board, **2 of 63 non-mate comparisons
lost more than 100 centipawns** relative to the move the original
Rodent would have played. Four additional comparisons involved mate.
This was not a historical human TEST benchmark, but a real tactical
weakness in the shallow depth8 / 25cp permission gate.

This slice makes a depth12 **root-forced counterfactual confirmation**
available as a strictly **opt-in** protection of any actual neural
override. Original Rodent IV binaries/personality files, Maia/DSC base
model weights, official chess clocks and all previously used opening
PGN fractions remain untouched. The original shallow-only policy
remains available as a control and does not silently change behavior.

## Before-play algorithm

For a focal player and actual position at ply >=41:

1. Ask the original Rodent IV personality for its real UCI bestmove.
2. Run the existing independent Stockfish depth8 MultiPV and retain
   only near-best, non-mate moves within 25cp. If Rodent's candidate
   itself does not pass, keep Rodent unchanged.
3. Score those safe candidates with the already-gradient-trained CPU
   player-specific rank-six style residual. If it chooses Rodent,
   do not perform any extra search.
4. If a different neural move is selected, force Stockfish at
   depth12 to evaluate the actual Rodent move and proposed neural
   move, each separately from the **same original FEN** at depth12,
   with its own cleared UCI game hash.
5. If a candidate reveals mate, retain original Rodent. If any
   required root PV/score/depth is missing or Stockfish crashes, fail
   the experimental game closed rather than claiming a check occurred.
6. Only allow the neural choice when its verified depth12 score
   from the side to move is **no more than 25cp below the original
   Rodent move**. Record the original cp, proposed cp, signed
   difference, depth, candidate identity and outcome in the full
   per-ply trace.
7. The Simple adversary remains book-free and unmodified. Historical
   books still use genuine ~85% real-PGN FIT opening data up to ply40
   only. No official rated game can use this experimental policy.

SHADOW executes both candidate safety checks but never plays the
proposed neural move. GUIDED plays only if both stages passed. This
additional search may reduce playing speed on CPU and limit
individual stylistic interventions; that is an explicit tradeoff,
not a promise of stronger play.

## Execution

```bash
python -m chesssim.real85_neural_guarded_uci \
  --rodent-root /path/to/rodent-original \
  --stockfish /usr/games/stockfish \
  --weights /tmp/real85-trained-residual \
  --profiles /tmp/real85-opening-books \
  --players fischer tal karpov kasparov \
  --rodent-depth 4 --stockfish-depth 8 \
  --confirmation-depth 12 --cp-budget 25 \
  --min-ply 41 --out /tmp/real85-deep-confirmed
```

The CI reconstructs the genuine FIT repertoire and neural models,
builds the unchanged original Rodent IV, and plays 16 complete UCI
games in two conditions (SHADOW and GUIDED), with both colors.
For every accepted override, it checks depth12 root scores, no mates
and signed neural-vs-Rodent delta >= -25cp. Independently,
the #181 audit re-evaluates the NEW actual matches at **depth14**,
reporting gains and losses even if the deeper guard still misses
some tactic. No generated game is misrepresented as a historical
human observation. No human Elo, identity fidelity, or tactical
optimality is claimed from 16 games. More robust large-scale games
with new seeds/opponents are required to support production choice.

## Interpretation

The new 25cp threshold is the **preexisting** safety budget, not
a hyperparameter tuned to the previously inspected human TEST.
Depth12 can still miss depth14+ tactics. If independent depth14
finds regressions, report them honestly; no regression acceptance
criterion should be retrofitted to this experiment after seeing
results. Playable does not mean historic strength; shared styles
and small individual deviations remain valid research outcomes.
