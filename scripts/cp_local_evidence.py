#!/usr/bin/env python3
"""Archive Actions artifacts by ID and digest; never trust a name alone."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import zipfile

REPO = "carloshgalves/chessCupMotorSimulation"
INVENTORIES = Path("experiments/bilateral-cp")
CP_RUNS = (45985663890, 45918461269, 45913126242, 45888949887)
REVIEW_RUN = 45884539347
EXTRA_RUNS = (45884163255, 45887532358, 45887526179)


def gh(path):
    return subprocess.check_output(["gh", "api", path])


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


def inventories():
    result = {}
    for path in sorted(INVENTORIES.glob("*evidence-inventory.json")):
        for run in json.loads(path.read_text())["runs"]:
            result[run["run_id"]] = run["artifacts"]
    return result


def live(run):
    page = 1
    result = []
    while True:
        data = json.loads(gh(f"repos/{REPO}/actions/runs/{run}/artifacts?per_page=100&page={page}"))
        result.extend(data["artifacts"])
        if len(data["artifacts"]) < 100:
            return result
        page += 1


def archive(root, run, record):
    if record.get("expired"):
        raise ValueError(f"Expired artifact {record['id']}")
    expected = record.get("digest")
    if not expected or not expected.startswith("sha256:"):
        raise ValueError(f"Missing SHA-256 for artifact {record['id']}")
    folder = root / "archive" / str(run)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{record['id']}.zip"
    if target.exists():
        if digest(target) != expected:
            raise ValueError(f"Archive digest mismatch: {target}")
        return target
    fd, tmp = tempfile.mkstemp(dir=folder)
    try:
        with os.fdopen(fd, "wb") as stream:
            subprocess.run(["gh", "api", f"repos/{REPO}/actions/artifacts/{record['id']}/zip"],
                           stdout=stream, check=True)
            stream.flush()
            os.fsync(stream.fileno())
        path = Path(tmp)
        if digest(path) != expected:
            raise ValueError(f"Downloaded digest mismatch: {record['id']}")
        os.replace(path, target)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return target


def extract(archive_path, destination):
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive_path) as z:
        for member in z.infolist():
            path = (destination / member.filename).resolve()
            if not path.is_relative_to(destination.resolve()):
                raise ValueError("Unsafe ZIP member")
            if (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("Unexpected ZIP symlink")
        z.extractall(destination)


def sync(root):
    known = inventories()
    records = []
    errors = []
    for run in sorted(set(known) | set(EXTRA_RUNS)):
        frozen = {a["id"]: a for a in known.get(run, [])}
        try:
            for a in live(run):
                old = frozen.get(a["id"])
                if old and (old["name"] != a["name"] or old["digest"] != a["digest"]):
                    raise ValueError("Artifact identity changed")
                frozen[a["id"]] = a
        except Exception as exc:
            errors.append(f"run {run}: {exc}")
        for a in sorted(frozen.values(), key=lambda a: a["id"]):
            try:
                path = archive(root, run, a)
                records.append({"run_id": run, "id": a["id"], "name": a["name"],
                                "digest": a["digest"], "path": str(path.relative_to(root))})
            except Exception as exc:
                errors.append(f"artifact {a['id']}: {exc}")
    # Atomic manifest; includes only actually downloaded/verified data.
    root.mkdir(parents=True, exist_ok=True)
    temp = root / "archive-manifest.tmp"
    temp.write_text(json.dumps({"records": records, "errors": errors}, indent=2))
    os.replace(temp, root / "archive-manifest.json")
    print(f"Archived {len(records)} artifacts; {len(errors)} failures.")
    for error in errors:
        print(error)
    return not errors


def index(root):
    records = json.loads((root / "archive-manifest.json").read_text())["records"]
    result = {}
    for a in records:
        path = root / a["path"]
        if digest(path) != a["digest"]:
            raise ValueError(f"Archive digest mismatch: {path}")
        result[a["run_id"], a["name"]] = path
    return result


def prepare(root):
    files = index(root)
    # Never overwrite a previously selected FIT input or finished local report.
    for (run, name), path in files.items():
        if run in CP_RUNS and (name.startswith("cp-fit-") or name.startswith("cp-reserved-")
                               or name.startswith("cp-recovery-")):
            extract(path, root / "imported" / str(run) / name)
        elif run == REVIEW_RUN and name.startswith("review-"):
            extract(path, root / "reviews" / name)
    # Old FIT artifacts are candidates, not automatically qualified profiles.
    print("Verified CP and review inputs extracted.")


def review_inputs(root, slug, run):
    files = index(root)
    folder = root / "review-inputs" / slug
    targets = {f"wave-measure-{slug}": "measure", f"wave-final-{slug}": "final",
               f"release-{slug}-2029": "release-2029", f"release-{slug}-2030": "release-2030",
               "release-proposals": "proposals"}
    for name, target in targets.items():
        extract(files[run, name], folder / target)
    screens = [(name, path) for (r, name), path in files.items()
               if r == run and name.startswith(f"screen-{slug}-")]
    if not screens:
        raise ValueError("No archived screen shards")
    for name, path in screens:
        extract(path, folder / "screen" / name)
    print(f"{slug}: extracted {len(screens)} screen shards.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=("sync", "prepare", "review"))
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--player")
    p.add_argument("--run", type=int)
    a = p.parse_args()
    root = a.root.resolve()
    if a.action == "sync":
        raise SystemExit(0 if sync(root) else 1)
    if a.action == "prepare":
        prepare(root)
    else:
        review_inputs(root, a.player, a.run)


if __name__ == "__main__":
    main()
