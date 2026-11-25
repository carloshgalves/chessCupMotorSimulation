"""Ephemeral competition observation; never a scoring or recovery checkpoint."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from hashlib import sha256
from io import StringIO
from threading import RLock
from time import monotonic

import chess.pgn

_sink = ContextVar("competition_observer", default=None)


def playback(game, initial=None):
    board = game.board()
    clocks = list(initial) if initial is not None else [None, None]
    states = [{"fen": board.fen(), "last": None, "turn": "white" if board.turn else "black",
               "clocks": clocks.copy()}]
    moves = []
    for node in game.mainline():
        uci = node.move.uci()
        san = board.san(node.move)
        idx = 0 if board.turn else 1
        if node.clock() is not None:
            clocks[idx] = node.clock()
        board.push(node.move)
        moves.append({"san": san, "uci": uci, "clock": node.clock()})
        states.append({"fen": board.fen(), "last": [uci[:2], uci[2:4]],
                       "turn": "white" if board.turn else "black", "clocks": clocks.copy()})
    return {"states": states, "moves": moves}


def publish(game, clocks):
    sink = _sink.get()
    if sink is not None:
        sink(game, clocks)


def verified_playback(path, entry, initial=None):
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("PGN redirecionado")
    raw = path.read_bytes()
    if sha256(raw).hexdigest() != entry["pgn_sha256"]:
        raise ValueError("Hash do PGN divergente")
    game = chess.pgn.read_game(StringIO(raw.decode("utf-8")))
    if game is None or game.errors or game.headers["Result"] != entry["result"]:
        raise ValueError("PGN inválido")
    return {"game": entry, "status": "COMPLETED", **playback(game, initial)}


class Observer:
    def __init__(self):
        self.lock = RLock()
        self.active = {}

    @contextmanager
    def capture(self, ident, spec, initial):
        key = (ident, spec["id"])
        def update(game, clocks):
            data = playback(game, initial)
            if clocks is not None:
                data["states"][-1]["clocks"] = list(clocks)
            with self.lock:
                self.active[key] = (monotonic(), {"game": dict(spec), "status": "RUNNING", **data})
        token = _sink.set(update)
        try:
            update(chess.pgn.Game(), initial)
            yield
        finally:
            _sink.reset(token)
            with self.lock:
                self.active.pop(key, None)

    def get(self, ident, game_id):
        with self.lock:
            item = self.active.get((ident, game_id))
            if item is None:
                return None
            stamp, data = item
            return {**deepcopy(data), "elapsed_since_move": max(0, monotonic() - stamp)}

    def ids(self, ident):
        with self.lock:
            return [game for owner, game in self.active if owner == ident]
