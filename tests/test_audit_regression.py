"""Regressões da falha observada nas auditorias do WSL em 2026-10-12.

python-chess controla Ponder; nunca enviar e.configure({'Ponder': False}).
A auditoria rápida precisa abrir uma única instância por jogador, testar
ambas as cores e fechar o processo até mesmo se um teste falhar.
"""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock

from chesssim.catalog import find_player
from chesssim.matches import _open_engine
from chesssim.audit import audit_roster, _quick_probe


class _Options(dict):
    pass


class _MockEngine:
    def __init__(self):
        self.options = _Options({"Personality": Mock(var=["Fischer", "Tal", "Karpov"]),
                                 "Hash": Mock(), "Threads": Mock(),
                                 "UCI_LimitStrength": Mock(), "Ponder": Mock(),
                                 "UseBook": Mock()})
        self.configurations = []
        self.quits = 0
    def configure(self, settings):
        if "Ponder" in settings:
            raise ValueError("cannot set Ponder which is automatically managed")
        self.configurations.append(settings.copy())
    def quit(self):
        self.quits += 1


class AuditRegression(unittest.TestCase):
    def test_open_engine_never_sets_managed_ponder(self):
        fake = _MockEngine()
        with patch("chesssim.matches.chess.engine.SimpleEngine.popen_uci", return_value=fake):
            opened = _open_engine(Path("/fake"), Path("/fake/rodent-iv-plain.exe"),
                                  find_player("tal"), book=False)
        self.assertIs(opened, fake)
        self.assertEqual(fake.configurations[0], {"Personality": "Tal"})
        self.assertEqual(fake.configurations[1]["UseBook"], False)
        self.assertNotIn("Ponder", fake.configurations[1])
        self.assertFalse(fake.configurations[1]["UCI_LimitStrength"])

    def test_quick_probe_sets_ponder_false_in_play_not_configure(self):
        fake_board = Mock()
        fake_board.fen.return_value = "starting fen"
        fake_move = Mock()
        fake_move.uci.return_value = "e2e4"
        fake_board.legal_moves = [fake_move]
        fake_board.san.return_value = "e4"
        reply = Mock(move=fake_move)
        engine = Mock()
        engine.play.return_value = reply
        with patch("chesssim.audit.chess.Board", return_value=fake_board, create=True):
            result = _quick_probe(engine, color="black", depth=4)
        fake_board.push_uci.assert_called_once_with("e2e4")
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["move"], "e2e4")
        self.assertEqual(engine.play.call_args.kwargs["ponder"], False)

    def test_quick_opens_one_engine_per_profile_and_reports_both_colors(self):
        fischer, tal = find_player("fischer"), find_player("tal")
        created = []
        def factory(*args, **kwargs):
            eng = _MockEngine()
            created.append(eng)
            return eng
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "personalities").mkdir()
            for player in (fischer, tal):
                (root / "personalities" / player.profile_filename).write_text("; mock profile\n")
            (root / "rodent-iv-plain.exe").write_bytes(b"stub")
            with patch("chesssim.audit.load_roster", return_value=[fischer, tal]), \
                 patch("chesssim.audit.validate", return_value=(root / "rodent-iv-plain.exe", {})), \
                 patch("chesssim.audit._open_engine", side_effect=factory), \
                 patch("chesssim.audit._quick_probe", side_effect=lambda e, color, depth: {"color": color, "status": "PASS"}):
                out = root / "auditorias" / "quick"
                report = audit_roster(root, out, depth=4, both_colors=True)
            self.assertEqual(report["passed"], 2)
            self.assertEqual(len(created), 2)  # era 8 subprocessos na v0.2.0
            self.assertTrue(all(e.quits == 1 for e in created))
            self.assertEqual([len(row["games"]) for row in report["entries"]], [2, 2])
            self.assertEqual(report["status"], "PASS")
            self.assertTrue((out / "audit.json").is_file())

    def test_full_reuses_the_two_processes_for_reversed_colors(self):
        fischer, tal = find_player("fischer"), find_player("tal")
        launched = []
        def factory(*args, **kwargs):
            e = _MockEngine()
            launched.append(e)
            return e
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "personalities").mkdir()
            (root / "rodent-iv-plain.exe").write_bytes(b"stub")
            (root / "personalities" / tal.profile_filename).write_text("; stub")
            with patch("chesssim.audit.load_roster", return_value=[fischer, tal]), \
                 patch("chesssim.audit.validate", return_value=(root / "rodent-iv-plain.exe", {})), \
                 patch("chesssim.audit._open_engine", side_effect=factory), \
                 patch("chesssim.audit._full_game", return_value={"status": "PASS", "termination": "CHECKMATE"}):
                report = audit_roster(root, root / "audit_full", full=True, both_colors=True)
            self.assertEqual(len(launched), 4)  # dois processos por jogador
            self.assertTrue(all(e.quits == 1 for e in launched))
            self.assertEqual(report["passed"], 2)
            self.assertEqual([len(e["games"]) for e in report["entries"]], [2, 2])

    def test_quick_failure_is_not_silenced_and_process_is_closed(self):
        fischer = find_player("fischer")
        engine = _MockEngine()
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "personalities").mkdir()
            (root / "personalities" / fischer.profile_filename).write_text("; mock profile\n")
            (root / "rodent-iv-plain.exe").write_bytes(b"stub")
            with patch("chesssim.audit.load_roster", return_value=[fischer]), \
                 patch("chesssim.audit.validate", return_value=(root / "rodent-iv-plain.exe", {})), \
                 patch("chesssim.audit._open_engine", return_value=engine), \
                 patch("chesssim.audit._quick_probe", side_effect=RuntimeError("lance ilegal")):
                report = audit_roster(root, root / "out", both_colors=False)
            self.assertEqual(report["passed"], 0)
            self.assertIn("lance ilegal", report["entries"][0]["games"][0]["error"])
            self.assertEqual(engine.quits, 1)


if __name__ == "__main__":
    unittest.main()
