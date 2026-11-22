"""Test the hosted recovery script with a fake authenticated gh client."""
import json
import os
from pathlib import Path
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/fischer_restore_shard.sh"
CASE = "a" * 64


@pytest.fixture
def setup(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake = bin_dir / "gh"
    fake.write_text("""#!/usr/bin/env python3
import json, os, pathlib, sys
a = sys.argv[1:]
if a[0] == 'api':
    if 'artifacts?' in a[2] if a[1] == '--paginate' else False:
        if os.environ.get('FAKE_API_FAIL'):
            sys.exit(1)
        run = a[2].split('/runs/')[1].split('/')[0]
        print(os.environ.get('ARTIFACT_' + run, ''))
    else:
        print(os.environ.get('FAKE_STATUS', 'workflow_dispatch\\tcompleted\\tmain\\t.github/workflows/fischer-full-development-games.yml'))
elif a[:2] == ['run', 'download']:
    path = pathlib.Path(a[a.index('--dir') + 1])
    (path / 'manifest.json').write_text(json.dumps({'case_ids': ['a'*64]}))
    (path / ('a'*64+'.json')).write_text(json.dumps({'source':a[2]}))
    (path / ('b'*64+'.tmp')).write_text('incomplete')
    if os.environ.get('FAKE_EXTRA'):
        (path / ('c'*64+'.json')).write_text('{}')
else:
    sys.exit(2)
""")
    fake.chmod(0o755)
    out = tmp_path / "shard-0"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               GITHUB_REPOSITORY="owner/repo", GITHUB_RUN_ID="200",
               GITHUB_RUN_ATTEMPT="1", RESUME_RUN_ID="", SHARD_INDEX="0",
               SHARD_OUT=str(out))
    return env, out


def execute(env, mode="restore"):
    return subprocess.run(["bash", str(SCRIPT), mode], env=env,
                          text=True, capture_output=True, check=False)


def test_fresh_run_and_missing_prior_artifact(setup):
    env, out = setup
    assert execute(env).returncode == 0 and not out.exists()
    env["RESUME_RUN_ID"] = "100"
    assert execute(env).returncode == 0 and not out.exists()


def test_resume_prior_run_removes_only_uncommitted_temp(setup):
    env, out = setup
    env.update(RESUME_RUN_ID="100", ARTIFACT_100="123")
    result = execute(env)
    assert result.returncode == 0, result.stderr
    assert set(p.name for p in out.iterdir()) == {"manifest.json", CASE + ".json"}
    assert json.loads((out / (CASE + ".json")).read_text()) == {"source": "100"}
    assert execute(env).returncode != 0


def test_retry_prefers_current_run(setup):
    env, out = setup
    env.update(RESUME_RUN_ID="100", ARTIFACT_100="123",
               GITHUB_RUN_ATTEMPT="2", ARTIFACT_200="456")
    result = execute(env)
    assert result.returncode == 0, result.stderr
    assert json.loads((out / (CASE + ".json")).read_text()) == {"source": "200"}


@pytest.mark.parametrize("resume", ["200", "abc", "0", "100;echo bad"])
def test_invalid_run_id(setup, resume):
    env, out = setup
    env["RESUME_RUN_ID"] = resume
    assert execute(env, "validate").returncode != 0 and not out.exists()


def test_reject_in_progress_prior_run(setup):
    env, out = setup
    env.update(RESUME_RUN_ID="100",
               FAKE_STATUS="workflow_dispatch\tin_progress\tmain\t.github/workflows/fischer-full-development-games.yml")
    assert execute(env).returncode != 0 and not out.exists()


def test_fail_closed_on_failed_lookup_or_unplanned_case(setup):
    env, out = setup
    env.update(RESUME_RUN_ID="100", ARTIFACT_100="123", FAKE_API_FAIL="1")
    assert execute(env).returncode != 0 and not out.exists()
    del env["FAKE_API_FAIL"]
    env["FAKE_EXTRA"]="1"
    assert execute(env).returncode != 0 and not out.exists()
    assert not list(out.parent.glob(".restore-*"))
