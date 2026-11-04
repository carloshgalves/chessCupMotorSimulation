import tempfile
import unittest
from pathlib import Path
from chesssim.registry import registered_aliases, add_to_first_set, RegistryError
from chesssim.rodent import setup_official, profile_status

class RegistryTest(unittest.TestCase):

    def test_legacy_exact_user_file_no_newline(self):
        raw = (
            "HIDE_OPTIONS\nPERSONALITY_BOOKS\nUCI_ELO\nHIDE_PERSFILE\n\n"
            "Amanda=amanda.txt\nAmpere=ampere.txt\nCloe=cloe.txt\n"
            "Morphy=morphy.txt\nNimzowitsch=nimzowitsch.txt\n"
            "Tal=tal.txt\nFischer=fischer.txt"
        )
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "basic.ini"
            p.write_text(raw, encoding="utf-8")
            self.assertEqual(registered_aliases(p)["Fischer"], "fischer.txt")
            self.assertEqual(registered_aliases(p)["Tal"], "tal.txt")
            new = add_to_first_set(raw, {"Karpov": "karpov.txt"})
            self.assertIn("Fischer=fischer.txt\nKarpov=karpov.txt\n", new)
            p.write_text(new, encoding="utf-8")
            self.assertEqual(registered_aliases(p)["Karpov"], "karpov.txt")
            self.assertFalse("PERSONALITY_SET" in new)

    def test_legacy_dry_run_preserves_disk(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            pers=root/"personalities"
            pers.mkdir()
            ini=pers/"basic.ini"
            raw=b"HIDE_OPTIONS\nPERSONALITY_BOOKS\nTal=tal.txt\nFischer=fischer.txt"
            ini.write_bytes(raw)
            for name in ("fischer", "tal"):
                (pers/f"{name}.txt").write_text("; preserved\n")
            report=setup_official(root,apply=False)
            self.assertTrue(report["missing_profiles"])
            self.assertIn("Karpov",report["new_aliases"])
            self.assertEqual(raw,ini.read_bytes())
            self.assertEqual(
                sum(s=="PRONTO_PARA_TESTE" for _,_,s in profile_status(root)),2)

    def test_legacy_install_mock_and_idempotence(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            pers=root/"personalities"
            pers.mkdir()
            ini=pers/"basic.ini"
            ini.write_bytes(b"HIDE_OPTIONS\nTal=tal.txt\nFischer=fischer.txt")
            for name in ("fischer", "tal"):
                (pers/f"{name}.txt").write_text("; preserved\nsetoption name OwnAttack value 123\n")
            with patch("chesssim.rodent._fetch",return_value=b"setoption name OwnAttack value 110\n") as fetch:
                report=setup_official(root,apply=True,books=False)
            self.assertEqual(fetch.call_count,19)
            self.assertEqual(len(report["downloads"]),19)
            self.assertEqual(len(registered_aliases(ini)),21)
            self.assertEqual(sum(s=="PRONTO_PARA_TESTE" for _,_,s in profile_status(root)),21)
            self.assertEqual((pers/"tal.txt").read_text(),"; preserved\nsetoption name OwnAttack value 123\n")
            self.assertIn("Fischer=fischer.txt\n",ini.read_text())
            self.assertIn("Karpov=karpov.txt",ini.read_text())
            self.assertTrue((pers/"_backups").exists())
            with patch("chesssim.rodent._fetch") as fetch:
                again=setup_official(root,apply=True,books=False)
            fetch.assert_not_called()
            self.assertFalse(again["downloads"])
            self.assertFalse(again["new_aliases"])

    def test_selected_group(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t)/'basic.ini'
            p.write_text('USE_PERSONALITY_SET_NO=0\nPERSONALITY_SET=all\nTal=tal.txt\nPERSONALITY_SET=famous\nFischer=fischer.txt\n')
            self.assertEqual(registered_aliases(p), {'Tal':'tal.txt'})

    def test_personality_set_with_spaces_like_existing_rodent(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "basic.ini"
            p.write_text(
                "HIDE_OPTIONS\n"
                "USE_PERSONALITY_SET_NO = 0\n"
                "PERSONALITY_SET = all\n"
                "Tal = tal.txt\n"
                "Fischer = fischer.txt\n",
                encoding="utf-8",
            )
            self.assertEqual(registered_aliases(p), {
                "Tal": "tal.txt", "Fischer": "fischer.txt"
            })
            updated = add_to_first_set(p.read_text(), {"Karpov": "karpov.txt"})
            p.write_text(updated)
            self.assertEqual(registered_aliases(p)["Karpov"], "karpov.txt")

    def test_check_and_setup_dry_run_with_spaces(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            pers = root / "personalities"
            pers.mkdir()
            ini = pers / "basic.ini"
            ini.write_text(
                "HIDE_OPTIONS\nUSE_PERSONALITY_SET_NO = 0\n"
                "PERSONALITY_SET = all\n"
                "Tal=tal.txt\nFischer=fischer.txt\n", encoding="utf-8"
            )
            (pers / "tal.txt").write_text("; sample\n")
            (pers / "fischer.txt").write_text("; sample\n")
            original = ini.read_bytes()
            report = setup_official(root, apply=False)
            self.assertIn("personalities/karpov.txt", report["missing_profiles"])
            self.assertEqual(original, ini.read_bytes())
            self.assertEqual(
                [row for row in profile_status(root) if row[2] == "PRONTO_PARA_TESTE"],
                [("Mikhail Tal", "Tal", "PRONTO_PARA_TESTE"),
                 ("Bobby Fischer", "Fischer", "PRONTO_PARA_TESTE")]
            )

    def test_addition_is_idempotent_by_ledger(self):
        text = 'PERSONALITY_SET=all\nTal=tal.txt\nPERSONALITY_SET=other\nFischer=fischer.txt\n'
        after = add_to_first_set(text, {"Karpov":"karpov.txt"})
        self.assertIn('Karpov=karpov.txt', after.split('PERSONALITY_SET=other')[0])
        self.assertEqual(after.split('PERSONALITY_SET=other')[1], text.split('PERSONALITY_SET=other')[1])

    def test_setup_preview_does_not_change_disk(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/'personalities').mkdir()
            ini = root/'personalities/basic.ini'
            ini.write_text('PERSONALITY_SET=all\nTal=tal.txt\n')
            before = ini.read_bytes()
            report = setup_official(root, apply=False)
            self.assertTrue(report['missing_profiles'])
            self.assertEqual(ini.read_bytes(), before)
            self.assertFalse((root/'personalities/steinitz.txt').exists())

    def test_setup_rejects_nonzero_selected_set(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/'personalities').mkdir()
            (root/'personalities/basic.ini').write_text('USE_PERSONALITY_SET_NO=1\nPERSONALITY_SET=one\nPERSONALITY_SET=two\n')
            with self.assertRaises(RegistryError):
                setup_official(root, apply=False)

if __name__ == "__main__": unittest.main()

class InstallationTest(unittest.TestCase):
    def test_mocked_install_adds_21_once_and_preserves_existing(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / 'personalities').mkdir()
            config = root / 'personalities/basic.ini'
            config.write_text('HIDE_OPTIONS\nUSE_PERSONALITY_SET_NO=0\nPERSONALITY_SET=all\nTal=tal.txt\n', encoding='utf-8')
            (root/'personalities/tal.txt').write_text('; existing customizations\nsetoption name OwnAttack value 401\n')
            fixture = b'setoption name OwnAttack value 120\n'
            with patch('chesssim.rodent._fetch', return_value=fixture) as f:
                report = setup_official(root, apply=True, books=False)
            self.assertEqual(f.call_count, 20)
            self.assertEqual(len(report['downloads']), 20)
            self.assertIn('personalities/kortchnoi.txt', report['downloads'])
            self.assertTrue((root/'personalities/_backups').exists())
            self.assertEqual(registered_aliases(config)['Kortchnoi'], 'kortchnoi.txt')
            self.assertEqual((root/'personalities/tal.txt').read_text(), '; existing customizations\nsetoption name OwnAttack value 401\n')
            self.assertEqual(len([x for x in profile_status(root) if x[2] == 'PRONTO_PARA_TESTE']), 21)
            with patch('chesssim.rodent._fetch') as f:
                again = setup_official(root, apply=True, books=False)
            self.assertEqual(f.call_count, 0)
            self.assertFalse(again['downloads'])
            self.assertFalse(again['new_aliases'])

    def test_setup_cannot_overwrite_divergent_alias(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t)
            (root/'personalities').mkdir()
            (root/'personalities/basic.ini').write_text('PERSONALITY_SET=all\nFischer=custom/fischer.txt\n')
            with self.assertRaises(RegistryError):
                setup_official(root, apply=False)


class BookReferenceRegressionTest(unittest.TestCase):
    def test_spassky_misspelled_book_is_downloaded_under_expected_name(self):
        from unittest.mock import patch
        from chesssim.rodent import BOOK_SOURCE_ALIASES
        self.assertEqual(BOOK_SOURCE_ALIASES["players/ph_spassky2.bin"], "players/ph-spassky2.bin")
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/"personalities").mkdir()
            ini = root/"personalities/basic.ini"
            ini.write_text("HIDE_OPTIONS\nTal=tal.txt\nFischer=fischer.txt", encoding="utf-8")
            for name in ("tal", "fischer"):
                (root/"personalities"/f"{name}.txt").write_text("; presente\n")
            calls = []
            upstream_book = b"\0" * 32  # 2 entradas Polyglot, apenas teste estrutural
            spassky_profile = (
                b"setoption name OwnAttack value 110\n"
                b"setoption name GuideBookFile value players/ph_spassky2.bin\n"
            )
            def fetch_mock(relative):
                calls.append(relative)
                if relative == "personalities/spassky.txt":
                    return spassky_profile
                if relative == "books/players/ph-spassky2.bin":
                    return upstream_book
                if relative.startswith("books/"):
                    raise AssertionError(f"Livro inesperado: {relative}")
                return b"setoption name OwnAttack value 120\n"
            with patch("chesssim.rodent._fetch", side_effect=fetch_mock):
                report = setup_official(root, apply=True, books=True)
            self.assertIn("books/players/ph-spassky2.bin", calls)
            self.assertNotIn("books/players/ph_spassky2.bin", calls)
            self.assertEqual((root/"books/players/ph_spassky2.bin").read_bytes(), upstream_book)
            self.assertIn(b"ph_spassky2.bin", (root/"personalities/spassky.txt").read_bytes())
            self.assertEqual(report["book_source_aliases"], {"players/ph_spassky2.bin": "players/ph-spassky2.bin"})
            self.assertEqual(len([r for r in profile_status(root) if r[2] == "PRONTO_PARA_TESTE"]),21)

    def test_failed_book_preflight_preserves_installation(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        from chesssim.rodent import _fetch, OFFICIAL_BASE
        from chesssim.registry import registered_aliases
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root/"personalities").mkdir()
            ini = root/"personalities/basic.ini"
            original = b"HIDE_OPTIONS\nTal=tal.txt\n"
            ini.write_bytes(original)
            (root/"personalities/tal.txt").write_text("; existente\n")
            spassky_profile = (
                b"setoption name OwnAttack value 110\n"
                b"setoption name GuideBookFile value players/ph_spassky2.bin\n"
            )
            def fail_book(relative):
                if relative == "personalities/spassky.txt":
                    return spassky_profile
                if relative.startswith("books/"):
                    raise RuntimeError(f"Falha no download: HTTP 404; arquivo: {relative}; URL: {OFFICIAL_BASE}{relative}")
                return b"setoption name OwnAttack value 120\n"
            with patch("chesssim.rodent._fetch", side_effect=fail_book):
                with self.assertRaisesRegex(RuntimeError, "HTTP 404.*books/players/ph-spassky2.bin"):
                    setup_official(root, apply=True, books=True)
            self.assertEqual(ini.read_bytes(), original)
            self.assertFalse((root/"personalities/spassky.txt").exists())
            self.assertFalse((root/"personalities/_backups").exists())
            with patch("urllib.request.urlopen", side_effect=HTTPError("example",404,"Not Found",{},None)):
                with self.assertRaisesRegex(RuntimeError, "HTTP 404.*personalities/steinitz.txt.*URL"):
                    _fetch("personalities/steinitz.txt")
            self.assertEqual(registered_aliases(ini), {"Tal": "tal.txt"})
