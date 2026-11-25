# Wave 02 — product release review

Calibration run 45597119316. Full-game validation run 45633448078 (9/9 jobs);
proposal artifact 15035146859. All 16 games completed with replayed book and
UCI evidence. Two seeds, paired colors, fixed Fischer anchor.

| Candidate | Depth | 10+2 vs Simple d16 W/D/L | Book duels vs Fischer W/D/L |
|---|---:|---|---|
| Spassky | 20 | 10/9/5 | 2/2/0 |
| LaskerWaveRestriction | 20 | 13/7/4 | 3/0/1 |
| RubinsteinWaveInitiative | 20 | 4/20/0 | 1/3/0 |
| AnandWaveRestriction | 20 | 13/11/0 | 1/3/0 |

All 60 FIT and 60 TUNE probes per player; 10/10 repeats. Requested depth
reached in approximately 57–68% of TUNE probes; the fixed node cap is part of
the product configuration. No human Elo equivalence or confirmatory identity
claim. The 16 anchor games validate integration, not an Elo ranking.
The eight earlier registry entries remain byte-for-byte equivalent as JSON
objects. Release pending integration CI and merge; no automatic promotion.
