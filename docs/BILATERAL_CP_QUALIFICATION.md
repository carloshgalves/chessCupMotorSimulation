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
