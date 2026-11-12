"""Deterministic, editorial-only chess narration for the read-only watch UI.

The generated text is explicitly fictitious editorial commentary. It never
impersonates historical players, infers thoughts, edits games, or calls UCI.
Only legal PGN moves and independently reconciled completed-game receipts
may determine statements.
"""
from __future__ import annotations

from hashlib import sha256

import chess
import chess.pgn

from .game_diversity import mainline_identity

SCHEMA = "chesssim-fictional-editorial-v1"
MAX_EVENTS = 800

_TEMPLATES = {
    "opening": (
        "O lance {san} iniciou a partida registrada na simulação.",
        "A partida começou com {san}, conforme o registro de lances.",
    ),
    "capture": (
        "No meio-lance {ply}, {san} registrou uma captura.",
        "Uma peça foi capturada com {san} no meio-lance {ply}.",
    ),
    "check": (
        "O lance {san} colocou o rei adversário em xeque.",
        "O registro mostra um xeque com {san}.",
    ),
    "castle": (
        "O lance {san} foi um roque.",
        "O jogador realizou o roque indicado por {san}.",
    ),
    "promotion": (
        "A jogada {san} promoveu um peão.",
        "Houve promoção de peão no lance {san}.",
    ),
    "checkmate": (
        "O lance {san} produziu xeque-mate no tabuleiro.",
        "A sequência registrada terminou em xeque-mate após {san}.",
    ),
    "milestone": (
        "A sequência chegou ao meio-lance {ply}, com {san}.",
        "O meio-lance {ply} foi registrado como {san}.",
    ),
}


def _choose(kind: str, *, ply: int, fen: str, uci: str, san: str) -> str:
    # Hash is based on *pre-move position*, not the finished PGN; updating
    # a live checkpoint cannot rewrite a commentary already displayed.
    key = f"{fen}|{uci}|{kind}".encode("utf-8")
    digest = sha256(key).digest()
    template = _TEMPLATES[kind][digest[0] % len(_TEMPLATES[kind])]
    return template.format(ply=ply, san=san)


def _completed_receipt(game: chess.pgn.Game, metadata: dict,
                       *, final_file: bool) -> bool:
    if not final_file or metadata.get("status") != "COMPLETED":
        return False
    result = game.headers.get("Result")
    if result not in ("1-0", "0-1", "1/2-1/2"):
        return False
    if metadata.get("result") != result:
        return False
    digest, plies = mainline_identity(game)
    if (type(metadata.get("plies")) is not int or metadata["plies"] != plies or
            metadata.get("mainline_sha256") != digest):
        return False
    # The first slice deliberately does not interpret time forfeits or
    # adjudications from unaudited metadata. A legal terminal board is needed
    # for a factually grounded postgame result, not just a Result tag.
    outcome = game.end().board().outcome(claim_draw=True)
    return outcome is not None and outcome.result() == result


def editorial_for_game(game: chess.pgn.Game, *, metadata: dict,
                       final_file: bool = False) -> dict:
    """Return no future move text on the live route; UI gates by visible ply.

    Events are derived from the legal mainline, not PGN annotations, user
    names, evaluations, or untrusted comments. Interview is omitted unless
    the exact final trajectory reconciles to a terminal completed receipt.
    """
    board = game.board()
    events = []
    for ply, move in enumerate(game.mainline_moves(), 1):
        if move not in board.legal_moves:
            raise ValueError("Cannot narrate illegal PGN move")
        fen_before = board.fen()
        san = board.san(move)
        capture = board.is_capture(move)
        castle = board.is_castling(move)
        promotion = move.promotion is not None
        uci = move.uci()
        board.push(move)
        if board.is_checkmate():
            kind = "checkmate"
        elif promotion:
            kind = "promotion"
        elif castle:
            kind = "castle"
        elif board.is_check():
            kind = "check"
        elif capture:
            kind = "capture"
        elif ply == 1:
            kind = "opening"
        elif ply % 10 == 0:
            kind = "milestone"
        else:
            continue
        if len(events) >= MAX_EVENTS:
            continue
        events.append({
            "ply": ply, "uci": uci, "kind": kind,
            "text": _choose(kind, ply=ply, fen=fen_before,
                            uci=uci, san=san),
        })
    interview = None
    if _completed_receipt(game, metadata, final_file=final_file):
        result = game.headers["Result"]
        conclusion = {
            "1-0": "As brancas venceram a partida registrada, por 1–0.",
            "0-1": "As pretas venceram a partida registrada, por 0–1.",
            "1/2-1/2": "A partida registrada terminou empatada, por ½–½.",
        }[result]
        interview = {
            "question": "Qual foi o resultado deste confronto no tabuleiro?",
            "answer": conclusion,
            "basis": "PGN terminal e recibo reconciliado por SHA-256 da linha principal",
        }
    return {
        "schema": SCHEMA,
        "label": "Comentário editorial fictício — não são falas de enxadristas reais",
        "events": events,
        "postgame": interview,
    }
