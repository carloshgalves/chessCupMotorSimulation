# Historical personalities — product qualification v1

Goal: 64 playable, competitive, distinguishable, historically plausible personalities. A person changes over a career; a motor is an approximation, not a resurrected human or an estimate of FIDE Elo. Statistical research remains optional and its negative evidence must remain visible.

## Architecture

Identity + explicit career period + provenance-checked PGN corpus → historically conditioned opening repertoire + Rodent IV evaluation parameters + search budget + individual competencies → customized UCI motor and opening policy → empirical game evaluation → versioned product asset.

Preferences (e.g. desire to attack) are distinct from competence (e.g. accuracy of attack). Calibrate parameters against observed historical behavior, not stereotypes; use small parameter changes, compare against baseline on same boards, retain only useful changes. Record unknowns rather than inventing 0–110 values. Separate motor-base freeze from full personality release and later XP modifications.

## Shared product reference v1

Use the pinned Rodent binary c2ccc7a99b442dcaa2aa2dc06e020f0182fe7a198c6f080f45f555c126cdcd95 and Simple profile 19f83fb05f96039a34e0e9a36bf66fc841693f70d73ee2a243df42e90caf1613. Baseline Simple depths 8/12/16/20 are proposed common ladder controls; only d16 has verified comparison results so far. Use matched opening roots, both colors, 10+2 clock, report WDL and score, actual depth and node budgets, censored/illegal games and clock flags. Do not convert depths or relative scores to FIDE ratings. Choose each personality's target band from historical evidence and common controls, not a universal d16 or a universal win rate. Historical strength comparability is approximate; ladder calibration still needs execution.

Quality reference: pinned Stockfish 250ff9c3548d06b06616356b01972230607469c95fb187e262f7044c4f6cfbe9, d24 initially with d28/d32 escalation on discrepancies >35cp or mate conflicts. Evaluate identical boards and all candidates at common depth, report loss vs human, severe error rate, phase breakdown, numeric coverage and unresolved reference disagreements. Report conflicting cases separately; do not hide them or pretend the reference is exact. Use descriptive metrics, not a universal 2% instability gate or mandatory confidence interval. Preserve #214/#215/#216 as research. Fischer and Tal current anchors: Fischer 66.67% vs Simple d16 (24 games); Tal Balanced 47.92% (96 games), original 21.35% and direct 76.04%. Panels differ, so these are not a direct Fischer/Tal ranking.

## Minimum acceptance checklist (every player)

| Dimension | Required product evidence |
| --- | --- |
| Strength | Recorded target band, paired control games (at least 24 completed games, both colors), WDL, no serious systematic weakness; justify exceptions before release. |
| Repertoire | Source SHA, player color, period, weighted recorded legal choices, out-of-book UCI fallback; no modern theory injected into older profiles. |
| Style | Compare original and tuned profiles against historical move features (initiative, forcing moves, material, activity, defense, structure), at least 20 nonforced historical positions with recorded support; no exact-move quota. |
| Historical context | Explicit period and date cutoff; exclude unknown/late dates from book. New moves discovered by search are allowed; later preparation is not. |
| Competencies | Record descriptive evidence by opening, tactics, attack, defense, strategy and endgame; mark sparse categories unknown; separate skill from preference. |
| Stability | Zero illegal moves, no hidden censored games, record clock failures; repeat at least 10 fixed-budget probes and investigate any differences. |
| Identity | Immutable source/profile/binary hashes, budget, book version, report and explicit review; no silent changes to an approved asset. |

Motor-base release requires strength, descriptive style/quality, stability and identity. Full personality additionally requires repertoire and historical context and documented competency coverage. Minimum counts are engineering defaults for future work, not retrospective statistical certification. Existing Fischer and Tal evidence is accepted for motor-base scope; opening-layer integration is a separate release. Any failure or waiver must be visible in the decision record.

Next: complete Fischer/Tal repertoire integration and ladder measurement; then Karpov and Kasparov, then remaining roster. Do not begin their calibration in this slice.
