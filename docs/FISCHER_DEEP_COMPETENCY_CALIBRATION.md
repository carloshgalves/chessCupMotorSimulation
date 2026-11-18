# Individual search and competency calibration — Fischer (#191)

The maintainer authorized depth 16 or higher if needed, and clarified that
faithfulness means reproducing a player's actual abilities/preferences, not
maximizing separation from other players. Two humans can be genuinely similar.

## Scientific target and runtime budget

- Depth is an adjustable engine parameter, not the rating, skill level or an
  invariant across players. Individual search budgets may vary by phase and
  context. Do not infer depth 14 for Lasker or 17 for Fischer without evidence.
- Prior #193: original Fischer d8 still lost 11.21 cp more than the historical
  human on peak FIT (game bootstrap 6.07..16.62); Simple d8 had comparable
  quality and higher exact agreement. Distinctiveness is not the sole target.
- This exploratory pilot fixes 24 hash-selected games from the existing 126
  peak FIT games and all 23 TUNE games, one nonforced position per phase/game
  using the existing #193 position salt. Evaluate original Fischer and
  Simple at depth caps 8/12/16, same 5 million node cap per search, no books.
  Report actual UCI depth, nodes, time and missing/short searches; never claim
  a requested depth was attained. Node limits depend on engine compliance.
  This subset is diagnostic, not a new independent validation sample.
- Common Stockfish reference d18, MultiPV2 for context proxies, forced-root
  d18 for all candidate moves and the human choice. One thread, hash 64 MiB,
  record engine hashes. Equal scoring procedure, separate mate metrics,
  signed paired gaps, game-cluster uncertainty; not human Elo. If no plateau,
  increase nodes/depth and reference depth or change engine; measure latency
  on the target laptop and do not sacrifice playable response time silently.

## Identify and measure abilities

Six dimensions retain the common schema. Strategy is interpreted here as
positional/strategic play; dynamic play remains a separate dimension.
Classification uses the board before the move, never whether a human
played the reference move. Multiple tags can overlap. Initial exploratory
contexts below are **proxies**, not validated skill labels:

| Dimension | Current diagnostic | Future stronger measurement |
|---|---|---|
| Opening | phase up to ply cutoff | coverage, move probabilities, out-of-book decisions and repertoire drift |
| Tactics | MultiPV gap >=80 cp or unique positive-mate candidate | independently annotated combinations, missed opportunities and calculation difficulty |
| Dynamics | middlegame piece-type imbalance >=2 | initiative, compensation, sacrifices and how advantage evolves |
| Defense | in check or best evaluation -300..-80 cp | prevent threats, survival of defensible positions and counterplay |
| Positional/strategy | balanced, non-check middlegame with small MultiPV gap | pawn structures, prophylaxis, piece coordination and longer-horizon plans |
| Endgames | material-based phase proxy | tablebase-eligible exact decisions, conversion, drawing and endgame families |

Missing or small contexts are not filled with a guessed value. A large
MultiPV gap is not a proof of tactics, nor does balanced mean strategic.
The initial proxy reports coverage and reference gaps, not true scores.

## Make 90 and 87 meaningful

Separate *preference* (distribution over good choices conditional on the
position) from *competence* (losses, probability of acceptable choice,
difficulty-adjusted success). Both are estimated from real games. Do not
force every player into a unique stylistic class. Style features can be
shared; evaluate longitudinal and conditional historical distributions.
An exact match alone does not establish style.

Preregister common difficulty anchors from legal, rated, known-time-control
games across multiple levels/eras and annotated shared tasks; fit and
validate a multidimensional item-response/choice model. A possible
form is P(success | item i, ability k) = logistic(a_i*(theta_k-b_i));
for overlapping skills use a vector of item loadings. Measured success
must account for multiple near-equivalent moves, loss of expected points,
mates, game clusters, and uncertain item classification. Use reference
cohorts only to establish scale, not to train another historical persona
before Fischer. Split calibration and validation by game and avoid
trajectory/FEN leakage; prior real85 TEST has been inspected historically
and cannot serve as an untouched final validation.

The common integer 0..110 display transform must have frozen, justified
anchors; do not equate 90 to 90% top1, 87 to 87 cp, or a depth to a score.
Do not independently min/max normalize each player. Estimate uncertainty,
minimum support and coverage; leave values null where evidence insufficient.
Regan & Haworth, Intrinsic Chess Ratings (2011):
https://ojs.aaai.org/index.php/AAAI/article/view/7951 (models choice quality
with reference populations of known Elo; not a ready six-dimensional scale).
Stockfish WDL is an engine self-play model, not a human probability:
https://official-stockfish.github.io/docs/stockfish-wiki/Useful-data.html

## Make a competency operate in the motor

For each independently calibrated dimension measure a response surface
of parameters (search budget, evaluation terms, learned policy, book and
tablebases if appropriately licensed). Optimize discrepancy to the
historical target per context and preserve acceptable tactical safety,
then evaluate full games at a common time control. Test perturbations
one dimension at a time with same-root counterfactuals: estimated
response matrix R[k,j] = change in metric k / change in parameter j.
If parameters have mixed effects, jointly optimize and report tradeoffs;
do not assume a depth increment changes only tactics. Compare +1 and
larger +5/+7 career increments on common tasks; require small marginal
rather than arbitrary discontinuous jumps and cumulative directional
response with bounded collateral change. Store player-specific proven
policy, budget, limits, version and confidence. Career XP buys only
calibrated attributes; official historical reference is immutable and
competitive rating starts 2500. No gameplay effect or values activated
in this slice. No new player started.
