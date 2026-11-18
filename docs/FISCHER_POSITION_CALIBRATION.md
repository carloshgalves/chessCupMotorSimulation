# Fischer: paired position calibration (#191)

Prospective protocol after the merged #192. Baseline: 827 distinct games,
683 FIT, 23 TUNE, 121 excluded TEST. #192 used Stockfish d12; this separate
protocol uses Stockfish d16 (version and binary hash recorded, one thread,
64 MiB hash). Do not pool its CP values with d12.

- Fixed cohort: 126 FIT games dated 1970–1972 (predeclared peak hypothesis)
  plus all 23 TUNE games; report separately, never treat TUNE as final test.
- One nonforced decision per game and phase, selected by min-hash with a
  fixed salt before running the engine. Replay the real move history.
- Same root and side for Fischer's historical move, original Rodent Fischer
  and Simple each at d4/d6/d8, with books off and no neural intervention.
  Score all distinct moves with forced-root Stockfish d16, including its
  own preferred move. Record signed gaps (negative gaps can occur); mates
  separate. Compare the candidates to the **human on the same positions**.
- Report exact move agreement, quality gap, paired excess gap, large losses,
  missed positive mates, number of games and distinct FENs by phase and
  context. Exploratory 95% game-cluster bootstrap for paired gap when at
  least 20 distinct games; smaller samples remain unscaled. Repeated FENs
  across games may still correlate. Contexts (in check, advantage,
  disadvantage, balanced/not in check) are proxies, not a proof of isolated
  tactical/positional/defensive/dynamic ability.
- No official profile modification; no use of outer TEST; aggregate outputs
  only, no source positions or moves published. The source is the existing
  pinned snapshot; its historical time controls remain largely unknown.
- Do not infer human Elo, assign competencies, or claim historical validation
  from these results. Next: candidate-policy comparisons with the FIT-only
  book and residual, Maia/DSC comparison, reference anchors and real full
  games, then new confirmatory split and opt-in release.

The goal is a search-budget diagnosis and a matched quality reference,
not to optimize the engine to beat Fischer or maximize a single score.
