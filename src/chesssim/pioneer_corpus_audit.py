"""Audit supplemental real Morphy and Philidor games; do not publish raw PGNs."""
import argparse
from collections import Counter
from hashlib import sha256
from io import StringIO
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import chess
import chess.pgn

from .fischer_full_games import atomic_json
from .game_diversity import mainline_identity
from .historical_real_corpus import _decode_pgn, _played_year
from .real_training_cohorts import _scan, _target_color
from .roster_product_wave import WAVE, product_arm, sample, source

SOURCES = {
    "morphy": "https://classicchess.com/players/Paul_Morphy/games/pgn/",
    "philidor": "https://classicchess.com/players/Francois_Andre_Philidor/games/pgn/",
}
LIMIT = 8 * 1024 * 1024


def fetch(slug):
    url = SOURCES[slug]
    with urlopen(Request(url, headers={"User-Agent": "ChessSimHistoricalAudit/1.0"}), timeout=60) as response:
        if urlsplit(response.url) != urlsplit(url):
            raise ValueError("Unexpected source redirect")
        data = response.read(LIMIT + 1)
    if not data or len(data) > LIMIT:
        raise ValueError("Invalid supplementary archive size")
    return data


def merge_standard(slug, archives):
    seen, games, sources = set(), [], []
    for name, blob in archives:
        records = Counter()
        decoded, _ = _decode_pgn(blob)
        stream = StringIO(decoded)
        while (game := chess.pgn.read_game(stream)) is not None:
            records["records"] += 1
            if records["records"] > 2000:
                raise ValueError("Too many source records")
            if game.errors or _target_color(game, slug) is None:
                records["invalid_or_wrong_identity"] += 1
                continue
            event = game.headers.get("Event", "").casefold()
            if any(word in event for word in ("analysis", "study", "composition", "problem")):
                records["analysis_excluded"] += 1
                continue
            if game.board().fen() != chess.STARTING_FEN or "odds" in event:
                records["odds_or_nonstandard"] += 1
                continue
            year, _ = _played_year(game.headers.get("Date", ""))
            if year is None or not WAVE[slug][0] <= int(year) <= WAVE[slug][1]:
                records["outside_dated_window"] += 1
                continue
            if game.headers.get("Result") not in ("1-0", "0-1", "1/2-1/2"):
                records["unknown_result"] += 1
                continue
            gid, plies = mainline_identity(game)
            if not plies:
                records["empty"] += 1
                continue
            if gid in seen:
                records["duplicate"] += 1
                continue
            seen.add(gid)
            records["new_standard_games"] += 1
            games.append(game.accept(chess.pgn.StringExporter(headers=True, variations=False, comments=False)))
        sources.append({"source": name, "sha256": sha256(blob).hexdigest(), "counts": dict(records)})
    return ("\n\n".join(games) + "\n\n").encode(), sources


def audit(slug, out):
    original = source(slug)
    merged, provenance = merge_standard(slug, [(original[3], original[0]), (SOURCES[slug], fetch(slug))])
    rows, scan = _scan(merged, slug, slug)
    data = (merged, rows, scan, "supplemental-review-only", slug)
    arms = Counter(product_arm(r["mainline_sha256"]) for r in rows)
    phases = {}
    for arm in ("fit", "tune"):
        try:
            probes, roots = sample(slug, data, arm)
            phases[arm] = {"ready": True, "probes": len(probes), "roots": len(roots)}
        except ValueError as exc:
            if not str(exc).startswith("Insufficient "):
                raise
            phases[arm] = {"ready": False, "reason": str(exc)}
    report = {"slug": slug, "sources": provenance, "standard_unique_games": len(rows),
              "merged_sha256": sha256(merged).hexdigest(), "arms": dict(arms),
              "phase_coverage": phases, "status": "PIN_AND_REVIEW_REQUIRED",
              "raw_pgn_published": False}
    atomic_json(out, report)
    print("CORPUS_EXPANSION", report)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--player", choices=sorted(SOURCES), required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    audit(a.player, a.out)


if __name__ == "__main__":
    main()
