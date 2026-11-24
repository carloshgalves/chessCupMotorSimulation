import pytest

from chesssim.product_peak_profiles import peak, render, split_label, summarize


def test_peak_boundaries():
    assert peak({"date": 1975}, "karpov")
    assert peak({"date": 1985}, "karpov")
    assert not peak({"date": 1986}, "karpov")
    assert peak({"date": 2000}, "kasparov")
    assert not peak({"date": None}, "kasparov")


def test_render_preserves_unmodified_lines():
    source = "setoption name OwnAttack value 110\nsetoption name Material value 105\n"
    assert render(source, {"OwnAttack": 120}) == source.replace("110", "120")
    with pytest.raises(ValueError):
        render(source, {"Unknown": 120})
    with pytest.raises(ValueError):
        render(source + source, {"OwnAttack": 120})
    assert split_label("balanced16") == ("balanced", 16)


def test_descriptive_objective_prefers_close_style_and_quality():
    def value(loss, forcing):
        return {"score": {"numeric": True, "cp_loss": loss},
                "features": {"forcing": forcing}, "match": False}

    row = {"values": {"human": value(10, True), "original16": value(20, True),
                     "balanced16": value(50, False)},
           "tags": ["tactics"], "reference": [{"inconsistent": False}],
           "telemetry": {k: {"reached_requested": True}
                         for k in ("original16", "balanced16")}}
    stats = summarize([row])
    assert stats["original16"]["objective"] < stats["balanced16"]["objective"]
    assert stats["original16"]["competencies"]["tactics"]["support"] == 1
