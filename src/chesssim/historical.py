"""Aquisição, validação e separação reprodutível de PGN histórico."""
from __future__ import annotations

from collections import defaultdict
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
import random
import re
from urllib.request import Request, urlopen
from zipfile import ZipFile
from io import BytesIO

MECKING_SOURCE = "https://www.pgnmentor.com/players/Mecking.zip"


def import_mecking(destination: Path, *, url: str = MECKING_SOURCE) -> dict:
    """Baixa fonte indicada pelo PGN Mentor; não executa arquivos do ZIP."""
    if destination.exists():
        raise FileExistsError(f"Fonte já existe: {destination}; não sobrescreveremos")
    with urlopen(Request(url, headers={"User-Agent": "ChessSimulation64/0.2 local research"}), timeout=40) as r:
        payload = r.read(15_000_001)
    if len(payload) > 15_000_000:
        raise ValueError("Download inesperadamente grande (>15 MB)")
    with ZipFile(BytesIO(payload)) as archive:
        candidates = [n for n in archive.namelist() if Path(n).name.lower() == "mecking.pgn"]
        if len(candidates) != 1:
            raise ValueError(f"ZIP sem único arquivo Mecking.pgn: {archive.namelist()[:10]}")
        info = archive.getinfo(candidates[0])
        if info.file_size > 30_000_000:
            raise ValueError("PGN demasiado grande")
        pgndata = archive.read(info)
    if b'[Event ' not in pgndata or b'[White ' not in pgndata:
        raise ValueError("Conteúdo não parece PGN")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('xb') as f:
        f.write(pgndata)
    report = {"url": url, "zip_sha256": sha256(payload).hexdigest(),
              "pgn_sha256": sha256(pgndata).hexdigest(), "bytes": len(pgndata),
              "output": str(destination)}
    destination.with_suffix(".source.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def player_side(game, match_name: str) -> bool | None:
    """True se brancas, False se pretas. Evita atribuir adversário ao alvo."""
    white = game.headers.get("White", "").casefold()
    black = game.headers.get("Black", "").casefold()
    rx = r"\b" + re.escape(match_name.casefold()) + r"\b"
    is_white = bool(re.search(rx, white))
    is_black = bool(re.search(rx, black))
    if is_white == is_black:
        return None
    return is_white


def extract_decisions(pgn: Path, *, player_name: str = "Mecking", from_year: int = 1973, to_year: int = 1978,
                      min_fullmove: int = 9, max_per_game: int = 120) -> tuple[list[dict], dict]:
    import chess
    import chess.pgn
    if not pgn.is_file():
        raise FileNotFoundError(pgn)
    games, rejected, decisions, seen_games = [], defaultdict(int), [], set()
    with pgn.open(encoding="utf-8-sig", errors="replace") as stream:
        while (game := chess.pgn.read_game(stream)) is not None:
            if game.errors:
                rejected["illegal_or_malformed"] += 1
                continue
            side = player_side(game, player_name)
            if side is None:
                rejected["not_unique_target"] += 1
                continue
            date = game.headers.get("Date", "")
            m = re.match(r"^(\d{4})", date)
            if not m:
                rejected["no_year"] += 1
                continue
            year = int(m.group(1))
            if not from_year <= year <= to_year:
                rejected["outside_peak"] += 1
                continue
            if game.headers.get("Variant", "Standard") not in ("Standard", "Chess"):
                rejected["variant"] += 1
                continue
            uci = ' '.join(move.uci() for move in game.mainline_moves())
            key = sha256(uci.encode()).hexdigest()
            if key in seen_games:
                rejected["duplicate_game"] += 1
                continue
            seen_games.add(key)
            board = game.board()
            rows = []
            for move in game.mainline_moves():
                if board.turn == side and board.fullmove_number >= min_fullmove and not board.is_game_over():
                    if len(list(board.legal_moves)) > 1:
                        rows.append({"fen": board.fen(), "move": move.uci(),
                                     "game": key, "year": year, "color": "white" if side else "black",
                                     "fullmove": board.fullmove_number,
                                     "event": game.headers.get("Event", "?"),
                                     "date": game.headers.get("Date", "????.??.??")})
                board.push(move)
            if len(rows) < 8:
                rejected["too_few_decisions"] += 1
                continue
            if len(rows) > max_per_game:
                # Deterministic sample spanning all phases, rather than only the first moves.
                rows = [rows[round(i*(len(rows)-1)/(max_per_game-1))] for i in range(max_per_game)]
            games.append(key)
            decisions.extend(rows)
    if not decisions:
        raise ValueError("Nenhuma partida válida da época alvo. Confira nomes, datas e conteúdo do PGN.")
    return decisions, {"games_accepted": len(games), "decisions": len(decisions),
                       "rejected": dict(rejected), "year_range": [from_year, to_year],
                       "pgn_sha256": sha256(pgn.read_bytes()).hexdigest()}


def split_positions(decisions: list[dict], *, seed: int = 2026, val_fraction: float = 0.2,
                    train_limit: int = 60, val_limit: int = 30):
    if not 0 < val_fraction < 1:
        raise ValueError("val_fraction deve estar entre 0 e 1")
    by_game = defaultdict(list)
    for d in decisions:
        by_game[d["game"]].append(d)
    game_ids = sorted(by_game)
    if len(game_ids) < 5:
        raise ValueError("Necessárias pelo menos 5 partidas independentes")
    random.Random(seed).shuffle(game_ids)
    n_val = max(1, round(len(game_ids) * val_fraction))
    val_games = set(game_ids[:n_val])
    # Deduplicar posições repetidas no mesmo grupo e remover vazamento entre grupos.
    train = [d for d in decisions if d["game"] not in val_games]
    val = [d for d in decisions if d["game"] in val_games]
    state_key = lambda d: " ".join(d["fen"].split()[:4])
    train_fens = {state_key(d) for d in train}
    val = [d for d in val if state_key(d) not in train_fens]
    def sample(rows, cap, salt):
        unique = {}
        for item in rows:
            unique.setdefault(state_key(item), item)
        vals = list(unique.values())
        random.Random(seed + salt).shuffle(vals)
        # Garantir representação de ambas as cores sempre que houver disponibilidade.
        return vals[:cap] if cap else vals
    t, v = sample(train, train_limit, 11), sample(val, val_limit, 19)
    if not t or not v:
        raise ValueError("Após deduplicação, treino ou validação ficou vazio")
    assert not {state_key(d) for d in t} & {state_key(d) for d in v}
    return t, v, {"seed": seed, "all_games": len(game_ids), "train_games": len(game_ids)-n_val,
                  "val_games": n_val, "train_positions": len(t), "val_positions": len(v),
                  # Backwards-compatible fields are SAMPLED game IDs, not all partition games.
                  "train_game_ids": sorted(set(d["game"] for d in t)),
                  "val_game_ids": sorted(set(d["game"] for d in v)),
                  "train_partition_game_ids": sorted(set(game_ids) - val_games),
                  "val_partition_game_ids": sorted(val_games),
                  "train_sampled_games": len({d["game"] for d in t}),
                  "val_sampled_games": len({d["game"] for d in v})}


# v0.3: frozen three-way partition. The final test MUST NOT be used to
# optimize parameters or select the model. All three partitions are disjoint
# at both the grouping and normalized-board-state levels.
def position_key(d: dict) -> str:
    return " ".join(d["fen"].split()[:4])


def _phase(d: dict) -> str:
    move = int(d.get("fullmove", 25))
    return "early" if move <= 20 else ("middle" if move <= 35 else "late")


def _balanced_sample(rows: list[dict], cap: int, *, seed: int) -> list[dict]:
    """Deterministic game-round-robin with color/phase balancing, no FEN repeats."""
    if cap < 1:
        raise ValueError("O limite de posições deve ser positivo")
    unique = {}
    for d in rows:
        unique.setdefault(position_key(d), d)
    buckets = defaultdict(list)
    rng = random.Random(seed)
    for d in unique.values():
        buckets[(d["color"], _phase(d))].append(d)
    queues = {}
    for k, vals in buckets.items():
        game_to_rows = defaultdict(list)
        for d in vals:
            game_to_rows[d["game"]].append(d)
        gids = sorted(game_to_rows)
        rng.shuffle(gids)
        for gid in gids:
            rng.shuffle(game_to_rows[gid])
        queue = []
        while any(game_to_rows.values()):
            for gid in gids:
                if game_to_rows[gid]:
                    queue.append(game_to_rows[gid].pop())
        queues[k] = queue
    result = []
    counts = defaultdict(int)
    while len(result) < cap:
        available = [k for k in sorted(queues) if queues[k]]
        if not available:
            break
        # Prefer the least represented color; then least sampled stage.
        color_count = lambda color: sum(counts[k] for k in counts if k[0] == color)
        k = min(available, key=lambda key: (color_count(key[0]), counts[key], key))
        result.append(queues[k].pop(0))
        counts[k] += 1
    return result


def split_three_ways(decisions: list[dict], *, seed: int = 2026,
                     train_limit: int = 120, val_limit: int = 50,
                     test_limit: int = 50, validation_fraction: float = .20,
                     test_fraction: float = .15,
                     group_by: str = "game") -> tuple[list[dict], list[dict], list[dict], dict]:
    if group_by not in ("game", "event"):
        raise ValueError("group_by deve ser game ou event")
    if not (0 < validation_fraction < 1 and 0 < test_fraction < 1
            and validation_fraction + test_fraction < .8):
        raise ValueError("Frações de validação e teste inválidas")
    for limit in (train_limit, val_limit, test_limit):
        if limit < 1:
            raise ValueError("Treino, validação e teste precisam de ao menos uma posição")
    groups = defaultdict(list)
    for d in decisions:
        if group_by == "event":
            # A shared event in a given year stays inside one partition.
            group = (str(d["year"]), str(d.get("event") or "UNKNOWN"))
        else:
            group = (str(d["game"]),)
        groups[group].append(d)
    gids = sorted(groups)
    if len(gids) < 15:
        raise ValueError("São necessários pelo menos 15 grupos independentes para três partições")
    rng = random.Random(seed)
    rng.shuffle(gids)
    n_val = max(3, round(len(gids) * validation_fraction))
    n_test = max(3, round(len(gids) * test_fraction))
    if len(gids) - n_val - n_test < 5:
        raise ValueError("Poucos grupos para treinamento após reservar validação e teste")
    partitions = {
        "test": [d for g in gids[:n_test] for d in groups[g]],
        "validation": [d for g in gids[n_test:n_test+n_val] for d in groups[g]],
        "train": [d for g in gids[n_test+n_val:] for d in groups[g]],
    }
    # Remove any transposition appearing in more than one group.
    # This also protects the sealed test set from overlap with training.
    keys = {part: {position_key(d) for d in rows} for part, rows in partitions.items()}
    overlaps = (keys["train"] & keys["validation"] |
                keys["train"] & keys["test"] |
                keys["validation"] & keys["test"])
    partitions = {k: [d for d in rows if position_key(d) not in overlaps]
                  for k, rows in partitions.items()}
    selected = {
        "train": _balanced_sample(partitions["train"], train_limit, seed=seed+11),
        "validation": _balanced_sample(partitions["validation"], val_limit, seed=seed+19),
        "test": _balanced_sample(partitions["test"], test_limit, seed=seed+23),
    }
    if not all(selected.values()):
        raise ValueError("Partição sem posições após deduplicação entre conjuntos")
    keys = {k: {position_key(d) for d in rows} for k, rows in selected.items()}
    if any(keys[a] & keys[b] for a, b in (("train", "validation"), ("train", "test"), ("validation", "test"))):
        raise AssertionError("Vazamento de estado entre partições")
    def partition_meta(k):
        vals = selected[k]
        return {"groups": len(gids[:n_test]) if k == "test" else (n_val if k == "validation" else len(gids)-n_val-n_test),
                "games_sampled": len({d["game"] for d in vals}), "positions": len(vals),
                "white": sum(d["color"] == "white" for d in vals),
                "black": sum(d["color"] == "black" for d in vals),
                "early": sum(_phase(d) == "early" for d in vals),
                "middle": sum(_phase(d) == "middle" for d in vals),
                "late": sum(_phase(d) == "late" for d in vals)}
    # No test labels, game IDs or positions in the public training report.
    report = {"version": "three-way-v1", "seed": seed, "group_by": group_by,
              "validation_fraction": validation_fraction, "test_fraction": test_fraction,
              "removed_cross_partition_states": len(overlaps),
              "train": partition_meta("train"), "validation": partition_meta("validation"),
              "test_sealed": {"groups": n_test, "positions": len(selected["test"]),
                              "manifest_sha256": sha256(json.dumps(
                                  [(d["game"], d["fen"]) for d in selected["test"]],
                                  sort_keys=True).encode()).hexdigest()},
              "partition_sha256": sha256(json.dumps(
                  {k: sorted(str(g) for g in (gids[:n_test] if k == "test" else
                       (gids[n_test:n_test+n_val] if k == "validation" else gids[n_test+n_val:])))
                   for k in ("train", "validation", "test")}, sort_keys=True).encode()).hexdigest()}
    return selected["train"], selected["validation"], selected["test"], report
