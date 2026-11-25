# Parallel historical product waves 05–07

Three disjoint batches of eight players, stacked on PR #226. All 24 lack individual catalog aliases. Existing 12 frozen assets and the prior wave recipes remain unchanged. Predeclared windows and weights are modeling hypotheses, not exact peak claims. Counts below come from the pinned roster inventory (run 45513328071), before partitioning.

## Wave 5

| Player | Period | Dated games |
|---|---|---:|
| keres | 1937–1962 | 1048 |
| bronstein | 1948–1960 | 524 |
| najdorf | 1946–1956 | 567 |
| geller | 1952–1972 | 1096 |
| gligoric | 1953–1970 | 1450 |
| portisch | 1964–1980 | 1172 |
| polugaevsky | 1967–1981 | 813 |
| timman | 1981–1993 | 1128 |

## Wave 6

| Player | Period | Dated games |
|---|---|---:|
| caruana | 2014–2022 | 2297 |
| nakamura | 2011–2019 | 2093 |
| aronian | 2005–2017 | 1870 |
| nepomniachtchi | 2016–2022 | 1759 |
| wesley-so | 2015–2022 | 2476 |
| vachier-lagrave | 2014–2021 | 2450 |
| grischuk | 2009–2019 | 1989 |
| mamedyarov | 2013–2019 | 1553 |

## Wave 7

| Player | Period | Dated games |
|---|---|---:|
| mequinho | 1970–1978 | 232 |
| short | 1987–1995 | 765 |
| ivanchuk | 1989–2013 | 3192 |
| shirov | 1992–2002 | 1400 |
| gelfand | 2004–2013 | 1223 |
| leko | 2000–2009 | 1011 |
| gaprindashvili | 1962–1985 | 429 |
| chiburdanidze | 1978–1991 | 615 |

## Experimental and release scope

Each wave has up to 576 paired 10+2 games and 32 complete historical-book games for eligible candidates, with separate artifacts and independent workflow concurrency groups. Same-depth Simple comparison in FIT and TUNE remains mandatory, with the generic baseline excluded from candidate selection. Target 60 FIT and 60 TUNE positions per player, minimum 12 per phase; the original global TEST remains untouched. The full-game gate uses the frozen Fischer and two paired color seeds. Release proposals are inactive until explicit review, registry commit, and real integration checks. Job success alone is not a promotion. The three waves cap measure at four and screen at eight concurrent jobs each to reduce contention; GitHub capacity can still queue jobs. Prior wave workflows remain manually dispatchable; their existing runs are not cancelled by this branch.

No human Elo equivalence or unique historical identity claim. Corpus coverage includes potentially mixed time controls and is not a guarantee of phase support. Remaining 12 players outside these batches require later calibration or source review.
