# Pre-registered 512-game independent sample — frozen original models

Protocol and seed fixed in issue #157 before execution.

The original DSC-150M and original Maia3-79M, with immutable published
weights/checkpoints and code commits, are compared on 512 **different**
historical game trajectories of the four pilot players: 128 per Fischer,
Tal, Karpov and Kasparov. Every position is a move from a FIT-year
game selected with seed 3109 after ply 20; at most one per real game,
with canonical-position and trajectory deduplication across players.

No final HOLDOUT or previously inspected VALIDATION positions are
evaluated. The original motors themselves are not trained by this
project; however, the FIT historical examples *were used to fit the
separate V7 semantic personalization*, so this is **not** an
independent audit of individual behavior. The Maia and DSC upstream
pretraining sets were not fully decontaminated: game independence from
external pretraining cannot be established.

One preregistered H1: DSC vs Maia79 top-1 game-macro gain positive, paired
95% game-bootstrap lower bound >0, paired two-sided sign-flip p<=0.05;
2,000 repetitions with seed 4109. Top3, heterogeneity by player,
timing and Linux process peak memory are secondary/descriptive.
Ratings 2400 are distinct scenario scales (Maia Lichess vs DSC FIDE);
the study does not estimate historical Elo or tournament strength.

If the result fails, publish it as negative. Even if H1 succeeds it
does not establish four differentiated personalities. Any personal
candidate needs a separate true out-of-time HOLDOUT and blind identity
test #154; official Rodent settings remain intact.
