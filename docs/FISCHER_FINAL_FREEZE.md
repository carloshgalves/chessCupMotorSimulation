# Fischer — final game-asset qualification gate

## Product target, not resurrection

The goal is an enjoyable and stable **representation** of Bobby
Fischer: broadly comparable decision quality and a few recognizable
move-selection preferences. Neither exact human move reproduction nor
a fictional "FIDE Elo" is required. The official Rodent IV Fischer
personality is unchanged. The evaluation does not automatically edit
the game's roster, skill levels, XP rules or Arena defaults.

### Locked, existing strength evidence

The finished exhaustive 3,616-continuation experiment was independently
validated in run [44970751726](https://github.com/carloshgalves/chessCupMotorSimulation/actions/runs/44970751726).
It contains real Rodent Fischer d16 versus Rodent Simple d12/d16
starting after historical twelve-ply openings, including all 22 legally
recovered fifty-move draws. The panel and exact engine/profile hashes
are frozen. Do not re-run thousands of games or substitute the
historical FIT opening book: it did not measurably improve competitive
performance.

Existing descriptive scores, original personality:

- FIT x Simple d12: 749.5/858 = 87.35%.
- FIT x Simple d16: 456.5/858 = 53.21%.
- TUNE x Simple d12: 39.5/46 = 85.87%.
- TUNE x Simple d16: 26/46 = 56.52%.

Locked **engineering/game-balance** acceptance bands, not statistically
calibrated human-strength equivalence: FIT d12 >=75%, FIT d16 45–65%;
TUNE d12 >=75%, TUNE d16 40–70%. The source record must also show
the full 3,616 games completed with SHA-bound protocol and original
unmodified Rodent profiles. These bounds are *not* thresholds for
Fischer's historical FIDE Elo and must not be used as such.

### One limited 10+2 live-play control

Choose twelve distinct 12-ply openings from the 121 original historical
TEST games by a frozen SHA salt, never by the opponent's observed
result. Play **24 real clocked games**, once with Fischer on each
color, against Simple at depth 16. Both use Rodent IV, one thread and
identical settings; internal books disabled. Time control **10+2**
matches the game's official classical format. Engine depth is at most
16 and search node cap at five million per move, so this still does
not establish a traditional human Elo.

A move after a flagged clock is never accepted, a legitimate time
loss or checkmate counts as a terminal result, and any illegal move,
engine crash, missing UCI response, unexplained timeout or nonterminal
ply cap blocks completion. No automatic draw is assigned to a game
that stops because of an engineering limit. Every move and elapsed
time is independently replayed from the authentic root before the
aggregator accepts the record.

Accept only if all 24 finish legally, Fischer scores >=30% against
Simple d16 in these 24 games and suffers no time losses. Publish the
game-root cluster bootstrap interval and the exact W/D/L; this small
panel is a **stability smoke test**, not a replacement for the
3,616-game strength evidence. No benchmark-specific profile tuning.

### Historical move preferences, not mind-reading

From *all* 121 original TEST trajectories, select exactly one
deterministically salted **nonforced** Fischer position for each
available phase (opening, middlegame, endgame). Replay the same
position with human Fischer's move, unmodified Rodent Fischer d16 and
Simple d16. Evaluate predeclared, overlapping proxies:

- Forcing move (check or capture)
- Check given
- Pawn move
- Nonpawn move closer to the opponent's king

These are observational frequency proxies, not validated
psychological labels. Record exact historical move agreement for both
motor profiles, frequencies per role, and whether Fischer is at least
as close to historical frequencies as Simple (tolerance 2.5
percentage points). Require >=100 observations from >=100 historical
games, >=2/4 such descriptive proxy comparisons, >=5% distinct move choices
between Fischer and Simple, and >=40%
historical move agreement (not worse than Simple by >3 percentage
points). One position may contribute to several features; no
pseudo-independent significance test is made. *TEST was previously
inspected during development*; it is not a blind validation.

### Repeatability, integrity, and freeze

A fixed SHA-derived subset of style positions is repeated at the
same depth with a fresh UCI game identity. Require >=12 repetitions,
>=95% exact move repeatability, and >=90% achieved depth16 on the
style probes. Unexpected engine binary, profile, registry mapping,
PGN source, protocol version, missing positions or checkpoint
mismatches are fatal.

A candidate passes only if the **locked existing strength control,
24 clocked games, historical preferences, and reproducibility** all
pass. The result is
`ELIGIBLE_FOR_FISCHER_GAME_ASSET_FREEZE_REVIEW`, not a claim of
human-equivalent playing strength. On success, a read-only
`fischer-freeze-proposal.json` artifact binds the source,
motor/profile hashes and SHA-256 of the complete final report.
**Human release review** remains necessary to freeze the asset in
the repository; the workflow has `contents: read` and cannot
silently change main.

A failed criterion publishes the diagnostic JSON, leaves the current
personality untouched and must be investigated, never hidden by
relaxing gates after seeing results. This is the **last directed
Fischer benchmark** before product freeze consideration. No more
open-ended historical similarity sweeps unless the gate reveals a
specific defect.

### Execute

PR runs the unit tests plus **real source inventory** but skips the
expensive UCI stages. After reviewing and merging the PR, the user
can dispatch the final check:

    gh workflow run fischer-final-freeze.yml \
      --repo carloshgalves/chessCupMotorSimulation --ref main

Eight jobs execute independent shards and publish immutable
per-game/per-style records. Aggregation replays every case, the
pinned prior benchmark and official hashes, then emits the final
freeze proposal only upon passing. A status of `success` for a PR
*plan* is **not** proof that the live-play benchmark ran.

Only after a verified report and separate release sign-off should
the team freeze Rodent Fischer's existing .txt profile, record its
content hash as the stable game asset and move on to the next
historical player. This does not conclude the separate six
competency 0–110 calibration research, nor claim a FIDE Elo or a
perfect reconstruction of Fischer.
