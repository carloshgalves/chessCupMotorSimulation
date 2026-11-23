# Mikhail Tal — first independent product-qualification slice (#204)

Fischer's Rodent IV **d16** personality is frozen as a qualified product asset
under PR #202 and source run #45097738965. This change does not edit that
asset, its benchmark, rating or competition search settings.

## Scientific and product target

Target: a plausible **competitive** Tal personality that exhibits some
recognizable preferences in real chess games, **not** a literal replication
of Mikhail Tal's human move-by-move choices. Rodent original Tal exists
in the project roster but **is not prequalified by existing Fischer data**.

### Slice 0 — full-source integrity before motor claims

- Use pinned PGN Mentor mirror and the existing Tal surname/roster mapping.
- Parse and replay **every accepted legal deduplicated historical game**,
  with exact whole-trajectory identity; no first-N-game or first-N-move
  shortcut. Reject missing games or illegal trajectories.
- Preserve immutable **global** FIT/TUNE/excluded TEST partition based on
  complete game SHA, as used in the earlier four-player pilot.
- Count **every focal decision** and every forced move, split by phase and
  source arm. Historical move feature frequencies (checks, forcing moves,
  pawn moves, nonpawn moves closer to opposing king) are diagnostics only.
  They do not establish Tal's psychological traits, tactical competence,
  or similarity of Rodent Tal to historical Tal.
- Save every source exclusion/replay count, source SHA, by-arm/by-phase
  counts, historical year distribution and distinct legal twelve-ply
  opening prefixes.
- Before testing any Tal UCI result, select twelve **distinct development
  FIT roots** by frozen SHA salt; pairing the two colors would create 24
  *engineering* games, **not** an adequate strength or Elo estimate.
  Outer TEST trajectories are **not used for model selection** in this
  stage, and their historical choice data is not read for tuning.
- No Tal motor game is played in this initial slice. The output says
  `FULL_SOURCE_INVENTORY_ONLY_STRENGTH_NOT_TESTED` and `frozen=false`.

## Next slices and acceptance philosophy

**Next:** use this manifest to build matched UCI games across **all
eligible development opening roots or a statistically preregistered
design** (Simple d12 and d16, both colors; legal terminal-only results,
resumable checkpoints). Establish Tal-specific objective competitive
strength and risk baselines before selecting a fixed depth.

Then run independent same-position move preference controls,
retrospective testing explicitly labeled nonblind, and 10+2 legal game
and stability verification. Only after **all Tal-specific product gates**
pass may we record a new Tal hash/depth under
`src/chesssim/data/qualified_game_assets.json`. Never copy Fischer's
numeric acceptance bands into Tal by default.

Historical datasets may have limited coverage and short/lossy recorded
games; report their limitations explicitly and do not invent unknown
opening metadata, ratings, Elo or forced draws.

The six shared skill attributes (0–110) and XP strength transitions
remain the **separate** research task #203, not a prerequisite for
freezing a useful Tal game personality.
