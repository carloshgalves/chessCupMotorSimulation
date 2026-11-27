# Bilateral peak human strength, in centipawns

Issue #237 supersedes the closed Elo research #235/#236. No Elo conversion or exact-move fidelity gate.

## Definition
On the same original human board, compare loss relative to the same pinned Stockfish reference:
delta = mean(human loss − motor loss). Positive means motor stronger; negative means weaker. Use equal weight for opening, middlegame and endgame, excluding the first 16 plies to isolate search outside historical opening books. This is a paired quality proxy, not proof of identical match win probabilities or absolute strength.

## Qualification
- Target |delta| ≤ 3 cp; maximum |delta| ≤ 5 cp. Both directions rejected equally.
- Require 95% cluster-bootstrap interval wholly in ±5 cp; no pass from a point estimate alone. At least 40 distinct games, 20 stable jointly numeric decisions per phase, 85% coverage.
- Each phase within ±10 cp, differences in severe (>100 cp) and mate-sensitive error frequency within ±5 percentage points. Inconsistent Stockfish measurements and nonnumeric mates explicitly tracked.
- Fit on up to 30 deterministic decisions per phase, one per trajectory/phase. No global TEST game in FIT. Select by absolute gap, phase imbalance and quality gates, not move matching or score maximization.
- Compare exact existing baseline and alternatives, depth 8..24, 50k..5M node caps, existing evaluation variants and symmetric local adjustments around the baseline. Lock selection before opening reserved evaluation. Same original positions, adaptive pinned SF depth 24/28/32.
- Reserved panel up to 100 decisions per phase; global excluded TEST arm, no FIT trajectory/FEN overlap. Earlier experiments did inspect some global TEST positions, so this is reserved for the current selection, not a claim of completely fresh project-wide evidence. Independent review or fresh external holdout required before activation.
- For passing candidates: 10 repeat probes; four complete paired-color clock games vs Simple d16, no flag or incomplete game. Competitive sanity only, not common Elo or arbitrary strength floor.
- Preserve prior book digest for every frozen or previously measured player. Morphy/Philidor have no frozen book; supplementary PGNs are checked against the hashes of run 45884539314 and rebuilt FIT-only.
- Aggregate downloads the exact locked FIT artifact, independently reconstructs historical panels and books, checks FIT eligibility, replays adaptive reference stability and paired scores, verifies book/profile hashes and creates immutable *experimental* candidate manifests only for fully passing gates. No production registry update. Runtime currently supports global 5M node cap and max product depth 20; per-player node caps/deeper settings require integration before activation. No second selection using the same held-out results.
- Historical periods retain existing product study periods, not claimed unique optimum. Sparse support remains insufficient, never filled with fabricated games.

## Execution
Workflow bilateral-cp.yml schedules 64 FIT and 64 reserved jobs. Each reserved job checks the FIT artifact, exact profile hashes, source/book identity and recomputes selection. Table always lists all 64, including missing execution. Published table.json/table.md and frozen-candidates.json are experiment artifacts, not a production freeze.

Corpus expansion already verified: Morphy 250 standard unique peak games (174 + 76), Philidor 9 (6 + 3); Philidor has one globally reserved game, so currently cannot meet minimum sample. Historical source coverage is a hard blocker.

## Priority recovery (2026-11-26)
Rodent's pinned source uses PERSALIAS_ALEN=32 with strncpy(..., 31), causing the observed Capablanca, Botvinnik and Ding aliases to truncate. Keep all previously valid aliases unchanged; shorten only over-limit new aliases deterministically, reject collisions. The original full matrix remains on its immutable commit; new full runs are manual to avoid restarting it. The priority workflow reuses the exact 13 successful FIT artifacts by run and artifact ID and recomputes the three failed FIT jobs, then independently validates each immediately instead of waiting for the remaining 48 FIT jobs. No reserved results are used for selection. Priority aggregate is partial and marks every missing player explicitly.

## Superseding precision target: ±1 cp
The user's final instruction sets a uniform target for all 64, including Fischer. Strict mode requires |FIT delta| ≤1 cp, |reserved delta| ≤1 cp, and the reserved 95% game-cluster interval wholly inside ±1 cp; existing phase/coverage/severe/mate/repeat/clock gates remain unchanged. This is deliberately more stringent than a point estimate. No old five-cp approval may activate a new asset. If the estimate passes but the interval does not, status is uncertain, not rejected for direction. The old assets remain unchanged until a compatible replacement is independently reviewed and integrated. The full strict workflow reuses the 13 existing, exact FIT artifacts where available (never reserved results), recalibrates the remaining 51 with corrected aliases, and searches finer depths, node caps and evaluation interactions around a deterministic coarse FIT anchor if needed. The same reference is recomputed for all refined candidates. Each player proceeds to locked TEST in its own job; no 64-player barrier. A table reports both FIT and TEST columns for all 64, including missing executions. Fischer receives the same one-cp target, no preferentially relaxed threshold. No production promotion is implied by a FIT-only result.

## Fine calibration and real alias preflight
The strict matrix now reuses the exact Carlsen FIT artifact as well (14 total). It performs a real Rodent UCI smoke for every roster member before FIT, checking all available aliases and legal search moves. All long generated aliases are normalized, including legacy overlay aliases, without editing profile bytes or historical books. In addition to depth/node refinement, new integer parameter midpoints (25/50/75%) interpolate existing individual variants, deterministically reconstructed for reserved evaluation. The previous coarse recipe grid remains unchanged for reuse of measured artifacts. No reserved results influence refinement. Smoke reports are independent artifacts; failure is technical and not evidence of strength mismatch.