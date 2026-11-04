"""v0.5 historical research: full-career PGNs and repeated grouped CV.

The original Mecking v3 test and validation games are embargoed from this
expanded-corpus cohort. Exploration NEVER evaluates v5 selection or final test.
Only an explicit one-time --select evaluates new validation positions.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from statistics import mean, pstdev

from .calibration import _diagnostics, _remove_inherited_opening_books, file_hash, tune_text
from .historical import extract_decisions, split_three_ways, position_key
from .corpus_v5 import extract_decisions_v5
from .research_v3 import _by_game, _safe_filename, evaluate_text
from .research_v4 import (BASELINES, DEFAULT_PROBES, _directed_search, _folds_by_game,
                          _load_bases, _objective)

PROTOCOL_V5 = {
    "version": "rodent-style-calibration-v5",
    "source": "all available PGNs supplied, not provably all recorded games",
    "isolation": "old v3 validation/test games embargoed; fresh v5 train/validation/test split",
    "cv": "repeated grouped folds inside TRAIN, fold-local base choice and optimization",
    "optimization": "train-only sensitivity-guided coordinate search; full training re-ranking",
    "selection": "CV-only by default; one-time explicit validation, ties retain strongest official baseline",
    "test": "new final TEST remains sealed and is never evaluated by calibrate-v5",
    "identity": "imitation top1 penalized by unnecessary move changes, not Elo or authenticity",
}


def _legacy_embargo(reference: Path | None, legacy_pgn: Path | None, *, target_slug: str) -> tuple[set[str], dict]:
    if reference is None:
        if target_slug == "mequinho":
            raise ValueError("Mequinho requires --legacy-v3 and --legacy-pgn to protect the earlier sealed test")
        return set(), {"status":"NO_EARLIER_SEAL_DECLARED"}
    if legacy_pgn is None:
        raise ValueError("--legacy-v3 requires --legacy-pgn")
    report_path = reference / "calibration.json" if reference.is_dir() else reference
    old = json.loads(report_path.read_text(encoding="utf-8"))
    if old.get("protocol",{}).get("version") != "rodent-style-calibration-v3":
        raise ValueError("legacy reference must be v0.3")
    if (report_path.parent / "final_test.json").exists() or old.get("final_test",{}).get("status") != "SEALED_NOT_EVALUATED":
        raise ValueError("Earlier final test is not sealed")
    spec = old["input"]
    if file_hash(legacy_pgn) != spec["pgn_sha256"]:
        raise ValueError("Original PGN checksum mismatch: cannot prove old test exclusion")
    rows, stats = extract_decisions(legacy_pgn, player_name=old.get("match_name", "Mecking"),
                                    from_year=spec["from_year"],to_year=spec["to_year"])
    train, val, test, manifest = split_three_ways(
        rows,seed=spec["seed"],train_limit=spec["train_positions"],
        val_limit=spec["val_positions"],test_limit=spec["test_positions"],
        group_by=spec["split_by"])
    if manifest["test_sealed"]["manifest_sha256"] != old["final_test"]["manifest_sha256"]:
        raise ValueError("Earlier final test manifest mismatch: embargo uncertain")
    # Entire game IDs are embargoed (not only the 30 sampled test positions).
    # Reproduce v3 shuffled GROUP assignment, not merely sampled-game IDs:
    from collections import defaultdict
    import random
    groups = defaultdict(list)
    for d in rows:
        group = (str(d["game"]),) if spec["split_by"] == "game" else (str(d["year"]),str(d.get("event") or "UNKNOWN"))
        groups[group].append(d)
    gids = sorted(groups)
    random.Random(spec["seed"]).shuffle(gids)
    n_test = max(3,round(len(gids)*.15))
    n_val = max(3,round(len(gids)*.20))
    withheld = set(g for group in gids[:n_test+n_val] for g in [d["game"] for d in groups[group]])
    if not {d["game"] for d in test}.issubset(withheld) or not {d["game"] for d in val}.issubset(withheld):
        raise AssertionError("Legacy reserved games did not match the reconstructed embargo")
    return withheld, {"status":"OLD_VALIDATION_AND_TEST_EMBARGOED",
                       "v3_report_sha256":file_hash(report_path),
                       "v3_manifest_sha256":manifest["test_sealed"]["manifest_sha256"],
                       "reserved_game_count":len(withheld),
                       "reserved_game_ids_sha256":sha256(json.dumps(sorted(withheld)).encode()).hexdigest(),
                       "policy":"all games in legacy validation and final-test groups excluded from v5 cohort"}


def _sticky_registry(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():
        try:
            with path.open("x",encoding="utf-8") as f:
                json.dump(payload,f,ensure_ascii=False,indent=2)
                f.write("\n")
        except FileExistsError:
            pass
    if json.loads(path.read_text(encoding="utf-8")) != payload:
        raise ValueError("V5 COHORT REGISTRY CONFLICT: PGN/seed/embargo changed; do not reuse the cohort")


def _choose_base(bases: dict, rows: list[dict], root: Path, depth: int) -> tuple[str,dict]:
    scores = {}
    for name,(profile,_) in bases.items():
        m = evaluate_text(root,profile,rows,depth)
        scores[name] = {"metric":m, "macro":_by_game(m,rows)["macro_top1_by_game"]}
    winner = max(bases,key=lambda name:(scores[name]["macro"],scores[name]["metric"]["correct"],-list(bases).index(name)))
    return winner,scores


def _agg_cv(folds: list[dict]) -> dict:
    # Repeats reuse the same underlying train games: these are NOT independent samples.
    grouped = {}
    for f in folds:
        grouped.setdefault(f["repeat"],[]).append(f)
    per_repeat=[]
    for r,parts in sorted(grouped.items()):
        total=sum(p["positions"] for p in parts)
        before=sum(p["before_correct"] for p in parts)
        after=sum(p["after_correct"] for p in parts)
        per_repeat.append({"repeat":r,"positions":total,"before":before,"after":after,
                           "gain_pp":100*(after-before)/total})
    gains=[p["gain_pp"] for p in per_repeat]
    return {"repeats":len(gains),"per_repeat":per_repeat,
            "mean_gain_pp":mean(gains),"std_gain_pp":pstdev(gains) if len(gains)>1 else 0.,
            "nonnegative_repeats":sum(v>=0 for v in gains),
            "positive_repeats":sum(v>0 for v in gains),
            "base_frequency_by_fold":dict(Counter(p["base"] for p in folds)),
            "warning":"repeated folds resample the same training games; not independent external evidence"}


def calibrate_v5(root: Path, pgn: Path, output: Path, *, target: str,
                 target_slug: str, match_name: str, base_files: tuple[str,...]=BASELINES,
                 legacy_v3: Path|None=None, legacy_pgn: Path|None=None,
                 cohort_ledger: Path|None=None, validation_ledger: Path|None=None,
                 from_year: int=1600, to_year: int=2100,
                 partition_seed: int=2028, search_seed: int=31415,
                 train_positions: int=240, val_positions: int=60, test_positions: int=45,
                 depth: int=4, folds: int=3, repeats: int=3, cv_trials: int=2,
                 trials: int=6, shortlist: int=3, probe_positions: int=60,
                 probe_params: tuple[str,...]=DEFAULT_PROBES, delta: int=20,
                 divergence_penalty: float=.08, select: bool=False) -> dict:
    slug = _safe_filename(target_slug)
    root,pgn,output=root.resolve(),pgn.resolve(),output.resolve()
    if output.exists():
        raise FileExistsError(output)
    if not root.joinpath("rodent-iv-plain.exe").is_file():
        raise FileNotFoundError("Rodent executable not found")
    if not (1<=depth<=20 and 2<=folds<=8 and 1<=repeats<=10 and
            1<=trials<=100 and 1<=cv_trials<=20 and 1<=shortlist<=trials and
            probe_positions>=5 and train_positions>=folds*8 and val_positions>=5 and
            test_positions>=5 and 0<=divergence_penalty<=1 and from_year<=to_year):
        raise ValueError("Invalid v5 parameters")
    from .calibration import PARAM_RANGES
    if not probe_params or any(k not in PARAM_RANGES for k in probe_params) or len(set(probe_params))!=len(probe_params):
        raise ValueError("Invalid probe parameters")
    bases=_load_bases(root,base_files)
    embargo, legacy=_legacy_embargo(legacy_v3,legacy_pgn,target_slug=slug)
    rows,source=extract_decisions_v5(pgn,player_name=match_name,from_year=from_year,to_year=to_year)
    eligible=[d for d in rows if d.get("legacy_key",d["game"]) not in embargo]
    excluded=len({d['game'] for d in rows if d.get('legacy_key',d['game']) in embargo})
    if excluded == 0 and embargo:
        raise ValueError("New corpus contains none of the original reserved games; inspect provenance before proceeding")
    if not eligible:
        raise ValueError("No games remain after legacy embargo")
    train, validation, _test, partition=split_three_ways(eligible,seed=partition_seed,
        train_limit=train_positions,val_limit=val_positions,test_limit=test_positions,group_by="game")
    # Double check all embargoed game IDs have been removed from every group.
    assert not any(d.get("legacy_key",d["game"]) in embargo for d in train+validation+_test)
    ledger_path=(cohort_ledger or Path("calibracoes/registry")/(slug+"-cohort-v05.json")).resolve()
    registration={"version":5,"target_slug":slug,"pgn_sha256":file_hash(pgn),
                  "partition_seed":partition_seed,"partition_sha256":partition["partition_sha256"],
                  "test_manifest_sha256":partition["test_sealed"]["manifest_sha256"],
                  "legacy_reserved_sha256":legacy.get("reserved_game_ids_sha256")}
    _sticky_registry(ledger_path,registration)
    # The repeated outer CV is FULLY inside training. Fold base selection, local
    # probing and tuning do not see that fold's test labels.
    records=[]
    for r in range(repeats):
        folds_data=_folds_by_game(train,folds,partition_seed+81+r*1009)
        for i,(dev,held) in enumerate(folds_data):
            local,scores=_choose_base(bases,dev,root,depth)
            tested,best,probes,_=_directed_search(root,bases[local][0],dev,
                trials=cv_trials,depth=depth,delta=delta,penalty=divergence_penalty,
                seed=search_seed+1009*r+37*i,probe_positions=probe_positions,
                probe_params=probe_params)
            before=evaluate_text(root,bases[local][0],held,depth)
            after=(evaluate_text(root,tune_text(bases[local][0],best["params"]),held,depth)
                   if best["params"] else before)
            records.append({"repeat":r,"fold":i,"base":local,"positions":len(held),
                            "development_games":len({d["game"] for d in dev}),
                            "heldout_games":len({d["game"] for d in held}),
                            "before_correct":before["correct"],"after_correct":after["correct"],
                            "params":best["params"],
                            "moves_changed_vs_local_base":sum(a!=b for a,b in zip(before["_predicted_moves"],after["_predicted_moves"]))})
            print(f"CV repeat {r+1}/{repeats}, fold {i+1}/{folds}: {local} "
                  f"{before['correct']} -> {after['correct']}/{len(held)}",flush=True)
    cv=_agg_cv(records)
    origin,training_scores=_choose_base(bases,train,root,depth)
    options,best,probes,baseline=_directed_search(root,bases[origin][0],train,
        trials=trials,depth=depth,delta=delta,penalty=divergence_penalty,
        seed=search_seed+100_000,probe_positions=probe_positions,probe_params=probe_params)
    ranked=sorted(options[1:],key=lambda item:(-item["objective"],item["trial"]))
    shortlist_items=ranked[:shortlist]
    selected=None
    selection_report={"status":"VALIDATION_SEALED_NOT_ACCESSED","requested":False}
    if select:
        # CV is a preselection gate, not a significance test. Never consume the
        # one-time validation merely to inspect a candidate with no evidence
        # of repeatable improvement across grouped folds.
        needed=max(2,(2*repeats+2)//3)
        if cv["positive_repeats"] < needed or cv["mean_gain_pp"] <= 0:
            raise ValueError("CV PRESELECTION GATE: insufficient repeated positive gains; validation remains sealed")
        # Once-per-cohort selection lock, atomically claimed *before* the validation
        # calls. A crash requires human review; it cannot silently try another run.
        claim_path=(validation_ledger or Path("calibracoes/registry")/(slug+"-v05-validation-used.json")).resolve()
        claim_path.parent.mkdir(parents=True,exist_ok=True)
        with claim_path.open("x",encoding="utf-8") as f:
            json.dump({"status":"CLAIMED","cohort":registration,
                       "requested_output":str(output),"utc":datetime.now(timezone.utc).isoformat()},f,indent=2)
        tested_bases={name:evaluate_text(root,text,validation,depth) for name,(text,_) in bases.items()}
        leader=max(bases,key=lambda name:(tested_bases[name]["correct"],
            _by_game(tested_bases[name],validation)["macro_top1_by_game"],-list(bases).index(name)))
        best_base=tested_bases[leader]
        checks=[]
        for item in shortlist_items:
            candidate=evaluate_text(root,tune_text(bases[origin][0],item["params"]),validation,depth)
            macro=_by_game(candidate,validation)["macro_top1_by_game"]
            base_macro=_by_game(best_base,validation)["macro_top1_by_game"]
            altered=sum(a!=b for a,b in zip(candidate["_predicted_moves"],best_base["_predicted_moves"]))
            adjusted=candidate["top1"]-best_base["top1"]-divergence_penalty*altered/len(validation)
            passed=(candidate["correct"]>best_base["correct"] and macro>=base_macro and adjusted>0)
            checks.append({"trial":item["trial"],"correct":candidate["correct"],
                           "macro_by_game":macro,"changed_vs_strongest":altered,
                           "penalized_gain":adjusted,"accepted":passed})
            if passed and (selected is None or (candidate["correct"],macro,-altered) >
                           (selected["raw"]["correct"],selected["macro"],-selected["changed"])):
                selected={"item":item,"raw":candidate,"macro":macro,"changed":altered}
        selection_report={"status":"PERSONALIZED_SELECTED" if selected else "BASELINE_RETAINED",
                          "requested":True,"strongest_official":leader,
                          "official_correct":best_base["correct"],"checks":checks,
                          "selected_trial":selected["item"]["trial"] if selected else 0,
                          "claim_file":str(claim_path)}
        # When no gain, persist the best unchanged reference, not the development leader.
        final_profile=tune_text(bases[origin][0],selected["item"]["params"]) if selected else bases[leader][0]
    else:
        final_profile=bases[origin][0]
    output.mkdir(parents=True,exist_ok=False)
    best_path=output/(slug+"_BEST_TRAIN_EXPERIMENTAL.txt")
    best_path.write_text("; TRAIN ONLY, not approved; no inherited opening books\n"+
        _remove_inherited_opening_books(tune_text(bases[origin][0],best["params"])),encoding="utf-8")
    selected_path=output/(slug+("_SELECTED_EXPERIMENTAL.txt" if selected else "_BASELINE_NAO_PERSONALIZADO.txt"))
    selected_path.write_text("; EXPERIMENTAL; NOT historically verified; TEST SEALED\n"+
        _remove_inherited_opening_books(final_profile),encoding="utf-8")
    report={"protocol":PROTOCOL_V5,"status":selection_report["status"],
            "target":target,"source":source,"legacy_embargo":{**legacy,"excluded_games_present_in_new_corpus":excluded},
            "partition":partition,"cohort_registry":registration,"cohort_registry_path":str(ledger_path),
            "inputs":{"pgn":str(pgn),"pgn_sha256":file_hash(pgn),"engine_sha256":file_hash(root/"rodent-iv-plain.exe"),
                      "base_profiles":{name:file_hash(path) for name,(_,path) in bases.items()},
                      "depth":depth,"folds":folds,"repeats":repeats,"cv_trials":cv_trials,
                      "trials":trials,"probe_positions":probe_positions,"probe_params":list(probe_params),
                      "train_positions":len(train),"validation_positions":len(validation)},
            "cv_summary":cv,"cv_folds":records,
            "training_baselines":[{"profile":name,"correct":v["metric"]["correct"],
                                   "game_macro":v["macro"]} for name,v in training_scores.items()],
            "training_origin":origin,"directed_sensitivity":probes,
            "training_candidates":[{"trial":x["trial"],"params":x["params"],
                                    "correct":x["raw"]["correct"],"objective":x["objective"]} for x in options],
            "best_training_profile":str(best_path),"best_training_sha256":file_hash(best_path),
            "selected_profile":str(selected_path),"selected_profile_sha256":file_hash(selected_path),
            "selection":selection_report,"final_test":{"status":"SEALED_NOT_EVALUATED",
                 "manifest_sha256":partition["test_sealed"]["manifest_sha256"]},
            "warnings":["Repeat CV folds reuse training games; not independent samples",
                        "No original identity or Elo inferred", "Earlier heldout groups are fully embargoed",
                        "New final test never used for training, validation or candidate selection"]}
    (output/"calibration.json").write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report
