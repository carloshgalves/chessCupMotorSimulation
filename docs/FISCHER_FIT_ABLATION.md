# Fischer FIT-only book/residual ablation (#191)

Following merged #195: 108 paired positions (58 peak FIT, 50 TUNE).
Fischer d16 reproduced 58.62%/56%, but excess CP over the historical
move was +10.61/+3.58. It reached d16 in 107/108 searches. There is no
demonstrated historical equivalence. Deepening is not uniformly better.

This prospective diagnostic keeps exactly the same game/hash selection,
position sampling and d18 independent reference as #195. It reuses the
inspected FIT/TUNE panel, not an independent test. At each depth cap 12
and 16 (5M requested nodes), one original Fischer bestmove is shared across
four variants: original, guarded FIT book, guarded residual, and combined.
Book only before ply 40 (0-indexed board ply); residual only from decision
ply 41 onward. No fallback to source TEST games. Build the book and model
from all verified FIT games; reconcile source PGN hash, game identities,
focal colors and model manifest. The residual is the existing CPU-trained
rank-6 learner, not Maia or DSC weights. TUNE diagnostic does not fit weights.

Book chooses with fixed seed 2033, min support 2; if different from original,
check both against SF d14 best and original (25cp tolerance; reject mates).
Residual uses existing guard d10 + confirmation d14, 25cp budget.
Guard and reference are separate engine processes. The fixed audit uses
d18 MultiPV2 and same-depth forced-root scoring for each distinct choice.
Report change counts, d18 losses >25/100cp versus original and mate
regressions; guards are not proof of safety. Report matched historical
agreement and paired gaps by phase and competency proxy (not scores).
A change can be historically faithful but weaker; do not select on agreement
alone. Full games, independent validation, common difficulty anchors and
actual user-machine latency still pending. No official profiles, Arena,
competitive rating or competency values modified.

No raw historical moves, FENs or weights redistributed in artifacts. The
goal is to identify whether existing learned components improve the
original motor, before tuning/promotion or moving to another player.
