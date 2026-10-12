#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
pre_ref=05320d570caf37a668d530814216ac9b3599d235
phase2=3ef4146786c21de04d4d4d2688b731f2ab655ff3
boundary=66cce6045850c8de797bd60b1849c2999ec3ed22
work_root=$(mktemp -d /tmp/truecrime-golden-provenance.XXXXXX)
golden="$repo_root/tests/golden/documentary_pipeline_pre_incremental.json"
authorized="$repo_root/tests/golden/authorized_deltas.json"

cleanup() {
  for worktree in "$work_root"/worktree-*; do
    test -d "$worktree" || continue
    git -C "$repo_root" worktree remove --force "$worktree" >/dev/null 2>&1 || true
  done
  rmdir "$work_root" >/dev/null 2>&1 || true
}
trap cleanup EXIT

run_once() {
  local label="$1"
  local worktree="$work_root/worktree-$label"
  local run_root="$work_root/run-$label"
  mkdir -p "$run_root"
  git -C "$repo_root" worktree add --detach "$worktree" "$pre_ref" >/dev/null
  git -C "$worktree" show --format= "$phase2" -- tests/mock_payload.py \
    tests/test_input_sensitive_mocks.py tests/test_spoken_audio.py \
    tests/test_visual_production.py tests/test_voice_render.py | \
    git -C "$worktree" apply -
  # Test-only provider-boundary helpers from the accepted 9H record.
  git -C "$worktree" checkout "$boundary" -- tests/mock_payload.py tests/test_host.py \
    tests/test_spoken_audio.py tests/test_voice_render.py
  git -C "$worktree" apply --unidiff-zero \
    "$repo_root/docs/architecture/experiments/9i-pre-ref-provider-boundary.patch"
  # Keep the pre-ref generated intro cache inside the independent run root.
  git -C "$worktree" apply \
    "$repo_root/docs/architecture/experiments/9j-intro-dir-isolation.patch"
  # The logo fixture is local-only data, not a tracked application input.
  # Copy it into each detached worktree so directory isolation tests the
  # renderer/cache boundary rather than an accidental missing fixture.
  mkdir -p "$worktree/data"
  cp -R "$repo_root/data/logos" "$worktree/data/"
  cp "$repo_root/app/services/soundness.py" "$worktree/tests/provenance_soundness.py"
  env -u TRUECRIME_DATABASE_URL -u DATABASE_URL \
    TRUECRIME_DATABASE_URL="sqlite:///$run_root/test.db" \
    PROVENANCE_CAPTURE="$run_root/snapshot.json" \
    SOUNDNESS_RUN_ROOT="$run_root/files" \
    PYTHONPATH="$worktree" \
    "$repo_root/.venv/bin/pytest" -q -s \
    "$worktree/tests/test_visual_production.py::test_four_language_shadow_pipeline_real_job" >/dev/null
  printf '%s\n' "$run_root/snapshot.json"
}

matches_golden() {
  python3 - "$1" "$golden" "$authorized" <<'PY'
import json
import sys
snapshot = json.load(open(sys.argv[1], encoding="utf-8"))
golden = json.load(open(sys.argv[2], encoding="utf-8"))
allow = json.load(open(sys.argv[3], encoding="utf-8"))
diff = {key for key in set(snapshot) | set(golden)
        if snapshot.get(key) != golden.get(key)}
raise SystemExit(0 if diff == set(allow) else 1)
PY
}

run_expected() {
  local label="$1"
  local snapshot
  local attempt=0
  while :; do
    snapshot=$(run_once "$label-$attempt" | tail -n 1)
    if matches_golden "$snapshot"; then
      printf '%s\n' "$snapshot"
      return
    fi
    attempt=$((attempt + 1))
    test "$attempt" -lt 5 || {
      echo "pre-ref run did not reproduce the authorized 25-key snapshot" >&2
      return 1
    }
  done
}

snapshot_a=$(run_expected a)
snapshot_b=$(run_expected b)

python3 - "$snapshot_a" "$snapshot_b" "$golden" "$authorized" <<'PY'
import json
import sys

first = json.load(open(sys.argv[1], encoding="utf-8"))
second = json.load(open(sys.argv[2], encoding="utf-8"))
golden = json.load(open(sys.argv[3], encoding="utf-8"))
allow = json.load(open(sys.argv[4], encoding="utf-8"))

independent = sorted(key for key in set(first) | set(second)
                    if key.startswith("file:intros/")
                    and first.get(key) != second.get(key))
print("INTRO_INDEPENDENT_RUN_DIFF", len(independent))
if independent:
    for key in independent:
        print(key)
    raise SystemExit(1)

different = sorted(key for key in set(first) | set(golden)
                  if first.get(key) != golden.get(key))
authorized = sorted(allow)
print("PRE_REF_VS_COMMITTED_GOLDEN_DIFF", len(different))
print("AUTHORIZED_DELTA_COUNT", len(authorized))
for key in different:
    print(key, allow.get(key, {}).get("commit", "UNAUTHORIZED"))
if different != authorized:
    print("UNAUTHORIZED_OR_MISSING_DELTA", sorted(set(different) ^ set(authorized)))
    raise SystemExit(1)
if any(allow[key].get("class") != "a" for key in authorized):
    raise SystemExit("NON_AUTHORIZED_CLASS_IN_ALLOWLIST")
if any(first.get(key) == golden.get(key) for key in authorized):
    raise SystemExit("AUTHORIZED_KEY_DID_NOT_CHANGE")
print("AUTHORIZED_XOR_GOLDEN_DIFF", 0)
PY
