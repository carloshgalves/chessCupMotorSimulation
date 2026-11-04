import unittest
from collections import Counter
from chesssim.catalog import load_roster, find_player
from chesssim.bracket import generate_draw

class RosterTest(unittest.TestCase):
    def test_roster_has_exactly_64_distinct_players_and_21_original_profiles(self):
        p = load_roster()
        self.assertEqual(len(p), 64)
        self.assertEqual(len(set(x.slug for x in p)), 64)
        self.assertEqual(sum(x.rodent_alias is not None for x in p), 21)
        groups = Counter(x.group for x in p)
        self.assertEqual(groups, dict(mundiais_e_convites=22, pioneiros=12, gigantes_sem_coroa=11, modernos=14, rainhas=5))

    def test_user_decisions_are_preserved(self):
        slugs = {p.slug for p in load_roster()}
        for slug in ("mequinho", "taimanov", "polugaevsky", "ding-liren", "topalov"):
            self.assertIn(slug, slugs)
        for slug in ("gukesh", "khalifman", "ponomariov", "kasimdzhanov", "giri", "firouzja"):
            self.assertNotIn(slug, slugs)
        self.assertEqual(find_player("Kortchnoi").slug, "korchnoi")
        self.assertEqual(find_player("Kortchnoi").profile_filename, "kortchnoi.txt")

    def test_draw_deterministic_and_everyone_appears_once(self):
        a = generate_draw(42)
        b = generate_draw(42)
        self.assertEqual(a, b)
        self.assertNotEqual(a, generate_draw(43))
        self.assertEqual(len(a["round_1"]), 32)
        seen = [slug for m in a["round_1"] for slug in (m["player_a"],m["player_b"])]
        self.assertEqual(len(seen), 64)
        self.assertEqual(set(seen), {p.slug for p in load_roster()})

if __name__ == "__main__": unittest.main()
