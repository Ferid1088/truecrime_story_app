#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
pre_ref=05320d570caf37a668d530814216ac9b3599d235
phase2=3ef4146786c21de04d4d4d2688b731f2ab655ff3
boundary=66cce6045850c8de797bd60b1849c2999ec3ed22
work_root=$(mktemp -d /tmp/truecrime-golden-provenance.XXXXXX)
worktree="$work_root/worktree"
run_root="$work_root/run"
mkdir -p "$run_root"

cleanup() {
  git -C "$repo_root" worktree remove --force "$worktree" >/dev/null 2>&1 || true
  rmdir "$work_root" >/dev/null 2>&1 || true
}
trap cleanup EXIT

git -C "$repo_root" worktree add --detach "$worktree" "$pre_ref" >/dev/null
git -C "$worktree" show --format= "$phase2" -- tests/mock_payload.py \
  tests/test_input_sensitive_mocks.py tests/test_spoken_audio.py \
  tests/test_visual_production.py tests/test_voice_render.py | \
  git -C "$worktree" apply -
# These are test-only provider-boundary helpers from the accepted 9H commit.
git -C "$worktree" checkout "$boundary" -- tests/mock_payload.py tests/test_host.py \
  tests/test_spoken_audio.py tests/test_voice_render.py
git -C "$worktree" apply --unidiff-zero \
  "$repo_root/docs/architecture/experiments/9i-pre-ref-provider-boundary.patch"
cp "$repo_root/app/services/soundness.py" "$worktree/tests/provenance_soundness.py"

env -u TRUECRIME_DATABASE_URL -u DATABASE_URL \
  TRUECRIME_DATABASE_URL="sqlite:///$run_root/test.db" \
  PROVENANCE_CAPTURE="$run_root/snapshot.json" \
  SOUNDNESS_RUN_ROOT="$run_root/files" \
  PYTHONPATH="$worktree" \
  "$repo_root/.venv/bin/pytest" -q -s \
  "$worktree/tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job"

python3 - "$run_root/snapshot.json" "$repo_root/tests/golden/documentary_pipeline_pre_incremental.json" <<'PY'
import json
import sys

left = json.load(open(sys.argv[1], encoding="utf-8"))
right = json.load(open(sys.argv[2], encoding="utf-8"))
different = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
print("PRE_REF_VS_COMMITTED_GOLDEN_DIFF", len(different))
for key in different:
    print(key)
if different:
    raise SystemExit(1)
PY
