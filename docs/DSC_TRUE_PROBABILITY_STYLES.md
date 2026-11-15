# DSC true probability instead of ordinal rank — four historical players

The prior experiment #153 observed 0/4 personal adaptations passing the gate;
the four policies did not even produce distinct top1 moves on 192 tested
historical positions. This new DEVELOPMENT experiment investigates whether
the arbitrary -log(rank+1) proxy discarded information needed to learn
human preference.

This implementation uses the ORIGINAL DSC-150M Python API
\`DSC.load("dsc-150m",device="cpu")\` and \`predict_batch(PositionInput)\`.
It passes genuine full PGN game-move history, fixed 2400 FIDE scenario,
classical timing and all legal moves. Unlike UCI MultiPV rank, the resulting
\`Prediction.moves\` dictionary contains normalised probabilities from
the released DSC policy network (verified for legal-move coverage and sum1).
Backbone is frozen. Each personal preference residual remains fitted to
all eligible FIT histories via V7, never on TUNE/AUDIT. Its contribution is
\`p(move)*exp(alpha*residual_score(move))\`, normalised over every legal move.

The alpha grid [0,.25,.5,1,2] is chosen solely on historical
TRAIN-year TUNE. AUDIT is distinct at the trajectory level and uses a
canonical FEN embargo. A 4-way classifier uses the TRUE adjusted
probability of the eventual human move under each candidate profile;
the heldout move label is used solely when computing evaluation scores,
never for fitting or for selecting the candidates. Classifier interval
uses global-game bootstrap and class balancing; 25% is nominal chance.

This is NOT a neural LoRA fine-tune, not an independent final test, and not
evidence that any historical personality is already valid. The train-year
AUDIT cohort was inspected by #153; this follow-up is explicitly
exploratory and may not justify a confirmatory claim or promotion.
Never read the final held-year HOLDOUT without a frozen, independently
reviewed protocol. No changes to official Rodent personalities.
