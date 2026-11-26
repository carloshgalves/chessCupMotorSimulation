# Third wave product release review

The seven original wave-three candidates and recovered Topalov have passed the full-game release gate. Calibration run 45633448337 (seven) and recovery run 45694933667 (Topalov); final release runs 45694933683 and 45694933667 respectively. Aggregate artifacts 15065387366 and 15079127703 list all eight ready, no blocked entries. The exact logged release reports and artifact IDs are retained in wave-03-release-results.json.

| Player | Profile | 10+2 score vs Simple d16 | Full game W/D/L vs Fischer | Calibration artifact | Release artifacts (2029, 2030) |
|---|---|---:|---|---|---|
| topalov | TopalovWaveBalanced d20 | 72.92% | 1/1/2 | 15078198025 | 15079171094, 15078743995 |
| steinitz | Steinitz d20 | 64.58% | 1/1/2 | 15054926890 | 15060791931, 15060377057 |
| anderssen | Anderssen d16 | 50.00% | 1/1/2 | 15049399796 | 15059899433, 15061127226 |
| tarrasch | TarraschWaveInitiative d20 | 68.75% | 3/1/0 | 15052430360 | 15061990507, 15060953708 |
| marshall | Marshall d16 | 41.67% | 2/0/2 | 15049839584 | 15060462441, 15062045719 |
| nimzowitsch | Nimzowitsch d20 | 85.42% | 4/0/0 | 15049319826 | 15061832858, 15062271368 |
| reti | Reti d20 | 70.83% | 3/1/0 | 15051882168 | 15061339609, 15061784615 |
| larsen | LarsenWaveInitiative d16 | 64.58% | 1/1/2 | 15050556789 | 15062546720, 15062651536 |

All sixteen release jobs reported FULL_PRODUCT_REVIEW_READY. Across 32 complete games, the reports identify both external historical book moves and UCI decisions for each player; each seed uses reversed colors. The selected screen contains 24 games per player, without incomplete games or flags, and all eight match 10/10 repeat probes. FIT uses 60 positions each; TUNE 60 except Nimzowitsch (59). Reserved TEST remains unscored. Full-game outcomes are integration evidence, not Elo estimates; repeated openings may produce identical games. Topalov uses the exact recovered balanced recipe, with an independent reconstruction path guarded by pinned hashes. The previous twelve registry entries are unchanged. Effective product availability requires the real twenty-player bundle check, merge and local installation. No wave-four or later candidate is promoted here.
