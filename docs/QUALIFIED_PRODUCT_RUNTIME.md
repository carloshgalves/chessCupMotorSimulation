# Qualified product runtime v1

This opt-in mode uses four frozen motor bases: Fischer original d16, Tal Balanced
d16, Karpov Restriction d16, Kasparov Initiative d20. All have a 5M node cap,
internal books disabled, and frozen historical FIT-only external books. No
neural override, rating conversion or age segmentation.

Prepare from the exact Rodent binary and the pinned PGN snapshot:

    python -m chesssim.qualified_runtime prepare --rodent /path/to/rodent
    python -m chesssim.qualified_runtime check --rodent /path/to/rodent

The preparation needs network access to the pinned source, and creates a new
qualified-product-v1 directory alongside the original engine files. A mismatch
in binary, profile or historical book aborts without publishing the directory.
No network is used when playing a qualified game. Do not replace the frozen
bundle with another engine build.

Arena: select Personalidades > Homologadas in Duelo, Copa or Liga. Duelo uses
the previously validated unclocked product integration; Cup and League retain
their respective clocks. Only the four qualified participants may compete in
this mode. Legacy options are unchanged; qualified competition results are
excluded from the legacy experimental Elo ledger, not silently mixed in.

PGNs record the personality mode, per-side book and profile identity, depth,
node cap and opening seed/game number. League and Cup snapshots bind resources
on first execution and require the same identity upon resumption. Historical
PGNs remain readable without the installed runtime. A missing or mismatched
bundle fails closed.

The integration workflow checks real bounded runs through all three execution
paths; bounded smoke games are not competitive qualification results. Complete
game evidence comes from runs 45401078247, 45408582198 and 45439726540. The
human-play mode and the separate legacy CLI tournament remain unchanged.
