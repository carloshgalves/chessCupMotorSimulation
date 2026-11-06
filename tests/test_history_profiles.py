"""Biographical history packs are sourced, normalized and read-only."""
from __future__ import annotations

import copy
import json
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from chesssim import arena
from chesssim.catalog import load_roster
from chesssim.history_profiles import (
    SCHEMA, list_history_players, load_history_profile, validate_history_pack,
)


def test_all_64_are_accessible_and_have_historical_packs():
    roster = load_roster()
    index = list_history_players()
    assert index["schema"] == "history-index-v1"
    assert len(index["players"]) == 64
    assert [p["slug"] for p in index["players"]] == [p.slug for p in roster]
    assert sum(p["has_historical_pack"] for p in index["players"]) == 64
    assert {p.slug for p in roster if p.rodent_alias is not None or p.slug == "mequinho"}.issubset(
        {p["slug"] for p in index["players"] if p["has_historical_pack"]}
    )
    for player in roster:
        pack = load_history_profile(player.slug)
        assert pack["slug"] == player.slug
        assert pack["name"] == player.name
        assert pack["historically_validated_model"] is False
        if pack["portrait"] is not None:
            assert pack["portrait"]["approval_status"] == "approved"
            assert pack["portrait"]["local_asset"] == f"history/portraits/{player.slug}.jpg"
            assert pack["portrait"]["source_url"].startswith("https://commons.wikimedia.org/wiki/File:")
        if pack["has_historical_pack"]:
            assert pack["schema"] == SCHEMA
            assert pack["narrative_voice"] == "first_person_editorial"
            assert pack["review_status"] in {"pending", "reviewed"}
            assert len(pack["highlights"]) >= 2
            assert len(pack["timeline"]) >= 2
            assert pack["short_bio"]
            assert pack["sources"]
            assert validate_history_pack({k: v for k, v in pack.items()
                    if k not in {"name", "group", "has_historical_pack",
                                 "historically_validated_model"}}, slug=player.slug)
        else:
            assert pack["review_status"] == "pending"
            assert not pack["highlights"] and not pack["sources"]
            assert pack["short_bio"] is None


def test_mequinho_career_dates_are_not_inflated():
    profile = load_history_profile("mequinho")
    assert profile["has_historical_pack"] and profile["historically_validated_model"] is False
    assert [x["year"] for x in profile["highlights"]] == [1965, 1972, 1973, 1974, 1976]
    assert "13 anos" in profile["highlights"][0]["text"]
    assert "19 anos" in profile["highlights"][1]["text"]
    assert "21 anos" in profile["highlights"][2]["text"]
    assert "22 anos" in profile["highlights"][3]["text"]


@pytest.mark.parametrize("bad", [
    "unknown", "Kasparov", "../kasparov", "%2e%2e", "kasparov/other",
    "kasparov.json", "", "kasparov\\x00", "not-in-roster",
])
def test_rejects_unknown_or_noncanonical_slugs(bad):
    with pytest.raises(ValueError):
        load_history_profile(bad)


def test_invalid_sources_first_person_and_licensing_are_rejected():
    example = load_history_profile("fischer")
    original = {k: v for k, v in example.items()
                if k not in {"name", "group", "has_historical_pack",
                             "historically_validated_model"}}
    cases = [
        ("orphan source", lambda d: d["highlights"][0].update(source_ids=["missing"])),
        ("invented voice", lambda d: d["highlights"][0].update(text="Ele conquistou o título.")),
        ("unlicensed portrait", lambda d: d.update(portrait={"local_asset": "../../secrets"})),
        ("external script", lambda d: d["sources"][0].update(url="javascript:alert(1)")),
        ("wrong slug", lambda d: d.update(slug="tal")),
        ("missing key", lambda d: d.pop("sources")),
        ("duplicate source", lambda d: d["sources"].append(d["sources"][0])),
    ]
    for name, mutate in cases:
        broken = copy.deepcopy(original)
        mutate(broken)
        with pytest.raises((ValueError, TypeError)):
            validate_history_pack(broken, slug="fischer")


def test_history_http_endpoint_is_get_only_and_has_no_path_access(tmp_path):
    server = arena.create_server(tmp_path, tmp_path / "arena", port=0)
    thread = threading.Thread(target=server.serve_forever,
                              kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urlopen(base + "/api/history/players") as response:
            players = json.load(response)
            assert len(players["players"]) == 64
        with urlopen(base + "/api/history/players/kasparov") as response:
            profile = json.load(response)
            assert profile["name"] == "Garry Kasparov"
            assert profile["has_historical_pack"]
            assert profile["historically_validated_model"] is False
        with urlopen(base + "/api/history/players/mequinho") as response:
            profile = json.load(response)
            assert profile["has_historical_pack"]
            assert profile["historically_validated_model"] is False
        with urlopen(base + "/api/history/players/capablanca") as response:
            profile = json.load(response)
            assert profile["has_historical_pack"]
            assert profile["review_status"] == "pending"
        photo = load_history_profile("mequinho")["portrait"]
        if photo:
            with urlopen(base + "/api/history/portraits/mequinho.jpg") as response:
                data = response.read()
                assert response.status == 200
                assert response.headers["Content-Type"].startswith("image/jpeg")
                assert data[:3] == bytes.fromhex("ffd8ff")
        else:
            with pytest.raises(HTTPError) as err:
                urlopen(base + "/api/history/portraits/mequinho.jpg")
            assert err.value.code == 404
        for endpoint in (
            "/api/history/portraits/../other.jpg",
            "/api/history/portraits/unknown.jpg",
            "/api/history/portraits/mequinho.jpg?token=invalid",
        ):
            with pytest.raises(HTTPError) as err:
                urlopen(base + endpoint)
            assert err.value.code == 404
        for endpoint in (
            "/api/history/players/unknown",
            "/api/history/players/kasparov?token=secret",
            "/api/history/players/kasparov.json",
            "/api/history/players/kasparov/extra",
        ):
            with pytest.raises(HTTPError) as exc:
                urlopen(base + endpoint)
            assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        server.arena_manager.shutdown()
        thread.join(timeout=2)


def test_historical_corrections_are_documented():
    fischer = load_history_profile("fischer")
    assert fischer["highlights"][0]["year"] == 1958
    assert "1957/58" in fischer["highlights"][0]["text"]
    assert "14 anos" in fischer["highlights"][0]["text"]
    assert load_history_profile("alekhine")["era_label"].startswith("1927–1935; 1937–1946")
    assert load_history_profile("botvinnik")["era_label"].startswith("1948–1957; 1958–1960; 1961–1963")
    assert load_history_profile("anand")["era_label"].startswith("2000–2002 (FIDE); 2007–2013")
    nimz = load_history_profile("nimzowitsch")
    assert [x["year"] for x in nimz["timeline"]] == [1925, 1927, 1929]
    assert "fascículos" in nimz["highlights"][0]["text"]
    assert any("Chess Notes" == s["publisher"] for s in nimz["sources"])
    mecking = load_history_profile("mequinho")
    assert any("BrasilBase" == s["publisher"] for s in mecking["sources"])
    assert any("Folha de S.Paulo" == s["publisher"] for s in mecking["sources"])


def test_tournament_photo_candidates_are_offline_and_license_checked():
    from pathlib import Path
    import sys

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "scripts"))
    from materialize_history_portraits import manifest_rows

    rows = manifest_rows()
    assert len(rows) >= 7
    names = {r["slug"] for r in rows}
    assert {"mequinho", "larsen", "spassky", "petrosian",
            "karpov", "anand", "topalov"}.issubset(names)
    assert all(r["license"] in ("CC0 1.0", "CC BY-SA 3.0") for r in rows)
    assert all(r["event"] and r["author"] and r["rights_holder"] for r in rows)
    # Additional photos are acquired in independently reviewable batches.
    for row in rows:
        profile = load_history_profile(row["slug"])
        assert profile["has_historical_pack"]
        if profile["portrait"] is not None:
            assert profile["portrait"]["source_url"] == row["source_url"]
            assert profile["portrait"]["license"] == row["license"]


def test_pre_1800_historical_year_is_supported():
    from chesssim.history_profiles import _assert_year
    _assert_year(1749)
    with pytest.raises(ValueError):
        _assert_year(1699)


def test_all_64_biographies_are_sourced_first_person_and_ordered():
    index = list_history_players()
    for player in index["players"]:
        pack = load_history_profile(player["slug"])
        assert pack["has_historical_pack"]
        assert pack["short_bio"] and pack["era_label"]
        assert pack["narrative_voice"] == "first_person_editorial"
        assert len(pack["highlights"]) >= 2
        assert [item["year"] for item in pack["timeline"]] == sorted(
            item["year"] for item in pack["timeline"]
        )
        assert all(item["source_ids"] for item in pack["highlights"])
        assert all(source["url"].startswith("https://") for source in pack["sources"])
    assert load_history_profile("philidor")["highlights"][1]["year"] == 1749
