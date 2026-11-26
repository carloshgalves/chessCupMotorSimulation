# Bilateral peak Elo calibration

Issue #235. This experiment does not modify the production qualified registry.

- Nominal peak reference is separate from the empirical human decision-quality estimate. Historical five-year estimates are not official FIDE peaks; Philidor is unresolved. Reference transcriptions are provisional and must be verified before release.
- Classical 2024 Olympiad PGNs with rated humans provide a first calibration cohort, split by whole trajectory, with disjoint selected FENs, one position per game and stratification by rating. Same-event validation is not external population validation. No raw PGNs are redistributed. Record source SHA256.
- Fit a human-only regression using Stockfish expected-score loss and difficulty covariates; invert it to obtain an Elo-equivalent decision-quality proxy, not official FIDE or the published Regan IPR implementation. Reject unsupported slopes, missing bands and excessive validation error; bootstrap by human identity and focal games. No fixed cp/Elo conversion.
- Search depth, node budget and existing profile variants by absolute paired human/motor proxy gap on development positions. Selected recipe is evaluated on reserved historical trajectories and common positions. Prior historical TEST may have been seen in earlier experiments; new external confirmation remains required.
- Play 24 paired-color games per player against common Simple depths 8/12/16/20 at 10+2. This is relative competitive evidence, not classical human Elo. Absolute competitive anchoring and common-control cross-play remain necessary.
- Require verified source/scale, independent evidence, 95% bilateral difference interval contained in +/-50 and complete clock games before proposing any release. No new frozen version is written by this workflow; product integration requires versioned runtime/book and independent approval.
- Table artifacts report every player, including missing execution; provisional estimates must not be presented as certified ratings.
