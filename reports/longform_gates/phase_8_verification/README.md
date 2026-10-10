# Phase 8 Verification

## Scope Result

The requested short with real original audio could not be built from the real
database state. This is an explicit blocker, not a substituted synthetic
result.

Command:

```text
PYTHONPATH=. python scripts/phase8_verify.py
```

Real output:

```json
{
  "original_media_segments": 0,
  "assets_with_original_audio": [],
  "short_build": {
    "status": "BLOCKED_NO_REAL_ORIGINAL_AUDIO",
    "reason": "No persisted original media segment or asset with original audio exists; generated narration files are not substituted."
  },
  "rights_checker_unknown_asset": {
    "asset_code": "VIS_000027",
    "rights_status": "unknown",
    "passed": false,
    "offending_ids": ["VIS_000027"],
    "reasons": [
      "VIS_000027: platform warning - rights are unknown; explicit human sign-off is required"
    ],
    "metadata": {
      "platform": "tiktok_video",
      "platform_warnings": [
        "VIS_000027: platform warning - rights are unknown; explicit human sign-off is required"
      ]
    }
  }
}
```

There are zero `OriginalMediaSegment` rows globally and zero `VisualAsset`
rows with `has_original_audio=True`. Existing audio files under `Claude
outputs/` are generated narration or test artifacts and were not treated as
case-original audio.

## Rights Gate

The real case-6 asset `VIS_000027` was loaded from `truecrime.db`. Its
`rights_status` is `unknown`. On `tiktok_video`, `RightsChecker` rejected it,
listed it as an offending asset, and emitted a platform warning requiring
explicit human sign-off. It did not silently pass.

## Verification

```text
PYTHONPATH=. pytest -q tests/test_phase8_rights.py tests/test_shortform_phase1.py
11 passed, 1 warning in 0.04s
```

Implementation:

```text
scripts/phase8_verify.py
tests/test_phase8_rights.py
app/shortform/gates.py
```

Phase 8 is not complete: the real-original-audio short and narrator/original-
speech non-overlap verification remain blocked until an actual source media
segment is persisted.

## Known-Gap Decision

Repository and database inspection found no scheduled or active import workflow
for police bodycam audio, 911 calls, interviews, or other original media, and
there are no original-media rows for any case. This sub-check is therefore
downgraded from a blocking implementation defect to a documented known gap for
the pilot. The narrator-pause/non-overlap logic remains implemented and
testable when real original media is later imported; no generated or synthetic
audio is used to claim end-to-end verification.
