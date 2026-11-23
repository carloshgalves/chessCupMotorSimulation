# Tal: initial graded-strength game benchmark (issue #204)

## Scope and hypothesis

Tal should become a **competitively credible, stable game personality with
some distinctive preferences**, not a digital simulation that reproduces
every historical choice. The original Rodent IV `Tal` profile is our
*untuned candidate*. The **already frozen Fischer d16** asset is a locked
control: this PR does not modify it, the Arena, roster ratings, XP or engine.

**This slice tests only game strength and engineering stability**, not Tal
style fidelity, six 0–110 skills or equivalence to any historical human Elo.

## Preregistered opponent, roots and denominators

- The accepted pinned Tal source contains **2,431** unique games:
  FIT **2,024**, TUNE **66**, reserved historical TEST **341**.
- All FIT and TUNE games are replayed to inventory **distinct legal
  twelve-half-move openings**. Original counts: FIT **1,352**, TUNE
  **56**. Invalid or short starts are counted as exclusions, not silently
  dropped from historical corpus coverage.
- All **56 distinct TUNE roots** are held out from the FIT search
  selection and scored as a retrospective cross-root diagnostic.
  Any identical FIT/TUNE root is removed from the FIT sampling frame.
  An explicitly salted SHA256 rank then selects **96 distinct
  FIT-only roots** without evaluating engine outcomes.
  The 96 is a bounded *preregistered* randomizing panel, not an
  assertion that all 1,352 historical openings were played.
  No TEST move choices or TEST engine games are examined here.
- For each of the **152** selected roots, play Tal d16 versus
  Simple **d12 and d16**, both colors: **152 × 2 × 2 = 608**
  full-game continuations. FIT has 384 games (192 vs each depth);
  TUNE has 224 games (112 vs each depth).
- The UCI binary is pinned to
  `nescitus/rodent-iv@e8d84c8c8c189a1cf4eb27c578fc573af3b916d2`
  and its exact reconstructed SHA is checked against Fischer's
  approved immutable asset. Tal, Simple, registry and Fischer hashes
  are included in every manifest and must match across 32 runner
  shards. Both profiles use exactly the same executable,
  one thread, fixed Hash and no separate opening-book override.
- Each move requests fixed depth with the cap of five million nodes.
  Report the fraction of searches that **actually reached** the
  requested depth. Failures to reach d16 are not disguised as d16.
- Full game history is preserved for threefold repetition and the
  fifty-move rule. Results are accepted only if legal termination is
  independently confirmed by replay. Reaching an engineering safety
  limit of **1,800 continuation plies** leaves a game
  `INCOMPLETE`, **never** scored as a draw or win.

## Statistical reporting

Game-cell summaries (FIT/TUNE × Simple d12/d16): W/D/L,
points/possible points, mean score and **95% bootstrap confidence
interval resampled by independent opening root** (not by ply or
individual color games). Both colors for each root stay in the same
cluster and in the same deterministic shard. Publish actual depth
and node-cap compliance by Tal and Simple.

If *any* of 608 cases is missing, illegal, or remains nonterminal,
the report is `INCOMPLETE_DO_NOT_SCORE`. A separate, explicit
terminal-only recovery would be required; no weakening of the
scientific gate or silent partial-case averages.

The baseline depth16 is a **candidate**, not copied from the
homologation parameters of Fischer. Its results against Simple
measure a relative in-project control, **not human FIDE Elo**.
Development FIT and retrospective TUNE are correlated by opening
family even when exact prefixes are disjoint; neither constitutes
an independent human-strength benchmark.

## Operational execution

The PR runs the tests and a **real full historical source audit**,
publishing `tal-graded-plan`. The costly complete UCI matches
require a manual workflow dispatch after review/merge:

```bash
gh workflow run tal-graded-games.yml \
  --repo carloshgalves/chessCupMotorSimulation --ref main
```

The experiment uses 32 shards, max 16 concurrent, with one
**atomic complete-case** checkpoint per scored game. If an
eligible prior dispatch completed but some jobs were interrupted,
resume with its run ID:

```bash
gh workflow run tal-graded-games.yml \
  --repo carloshgalves/chessCupMotorSimulation --ref main \
  -f resume_run_id=PREVIOUS_RUN_ID
```

The shard restore script accepts only a **completed previous
workflow_dispatch** of this same workflow on `main`.
Downloaded JSON has file/type checks and the executor independently
revalidates source/binary/protocol fingerprints and each legal game.
Manual artifacts include all shards and
`tal-graded-results.json`.

**Exit criteria for this slice:** reproducible, source-bound,
terminal-only game scorecards with clustered intervals. Do not
freeze Tal or modify his official profile merely because the
workflow returns success. Use these force/stability results to
preregister the separate final product gate for Tal's time-control
performance, stylistic differences and repeatability. Fischer remains
frozen regardless of Tal's outcome.
