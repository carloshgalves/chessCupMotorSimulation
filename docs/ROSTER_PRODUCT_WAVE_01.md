# Product expansion wave 01

The qualified runtime stays frozen at Fischer, Tal, Karpov and Kasparov. No
release or Elo modification is automatic.

Inventory all 60 remaining players from the pinned PGN snapshot; report missing
sources, observed dates, global trajectory overlap, base profile readiness and
FIT/TUNE/test counts. A source filename and a catalog alias are not proof of
a valid historical motor. Missing profiles need a separate individual
candidate-generation track.

Wave 01 candidate windows (operational hypotheses): Alekhine 1927–1934,
Botvinnik 1948–1958, Smyslov 1953–1958, Petrosian 1963–1969. Historical
justification is based on title-cycle periods; not a claim of exact peak dates.
Alekhine, Botvinnik and Petrosian have individual Rodent profiles. Smyslov
has no upstream profile; compare explicitly labelled Simple seed and local
variants. A generic seed cannot be selected for qualification.

Product development v2 preserves the exact existing global TEST trajectories.
The remaining 85% are divided by a new deterministic trajectory hash: nominal
65% FIT, 20% TUNE, 15% TEST. Existing four qualified assets retain their v1
books and hashes. The new book uses the same v2 FIT split as the calibration.

For each: target 60 nonforced FIT positions (20 per phase), minimum 36
(12 per phase) when support is limited; original and two
small local variants at d12/16/20; pinned adaptive Stockfish reference. Keep
one candidate per depth, screen each in 24 paired 10+2 games versus Simple d16.
The 40% threshold is a provisional product screening criterion for this wave,
not historical Elo equivalence or a universal band for all 60. At most 288
screen games. Select by FIT objective among eligible candidates, then evaluate
up to 60 separate TUNE positions (minimum 36) and ten phase-spanning repeats. Do not switch to
another candidate based on TUNE. Insufficient support is reported, not hidden.
The external book uses only dated FIT games and is audited; complete games
with the selected book, common strength ladder and explicit human review are
still required before full release. No holdout predictions or age segmentation.

Run via `Remaining roster inventory and product wave 01` in GitHub Actions.
Artifacts contain source and profile hashes, raw probes, paired clock games,
book, and final reports. No automatic runtime promotion.
