# Phase 14: SocialPublisher Abstraction

## Implemented Boundary

```text
Working: app.shortform.operations.ExportPackagePublisher
Stubs: app.shortform.publishers.YouTubeShortsPublisher
       app.shortform.publishers.InstagramReelsPublisher
       app.shortform.publishers.FacebookReelsPublisher
       app.shortform.publishers.TikTokPublisher
```

`ExportPackagePublisher` produces the local export handoff only after an
explicit approval boolean. The four platform classes raise `NotImplementedError`
and make no network call.

## Approval Proof

The status guard rejects both scheduled and publishing transitions without
approval, and rejects them from any current state other than `approved`.

```text
unapproved approved -> scheduled: PermissionError
approved human approval, review -> publishing: PermissionError
approved human approval, approved -> scheduled: scheduled
approved human approval, approved -> publishing: publishing
```

## Credential Isolation Proof

Credential references exist only in `app/shortform/publishers.py` as names such
as `YOUTUBE_SHORTS_API_KEY` and `META_GRAPH_API_TOKEN`. No credentials are read
there yet. The audit scans every Python file in `app/shortform`; publisher-layer
config references are allowed, while credential access and credential literals
in every other short-form file are rejected.

## Exact Verification

```text
PYTHONPATH=. pytest -q tests/test_longform_phase14.py
...                                                                      [100%]
3 passed, 1 warning in 0.03s

python - <<'PY'
from pathlib import Path
files = sorted(Path('app/shortform').rglob('*.py'))
access_tokens = ('os.environ', 'SECRET_KEY', 'secret_key')
credential_tokens = ('API_KEY', 'api_key', 'api-key')
hits = []
for path in files:
    text = path.read_text()
    for token in access_tokens:
        if token in text:
            hits.append((str(path), token))
    if path.name != 'publishers.py':
        for token in credential_tokens:
            if token in text:
                hits.append((str(path), token))
print('files_scanned', len(files))
print('credential_access_hits', hits)
print('publisher_config_only', sorted(str(p) for p in files if p.name == 'publishers.py'))
PY
files_scanned 9
credential_access_hits []
publisher_config_only ['app/shortform/publishers.py']

PYTHONPATH=. pytest -q tests/test_longform_phase*.py tests/test_phase8_rights.py tests/test_shortform_phase1.py tests/test_shortform_operations.py
...................................                                      [100%]
35 passed, 1 warning in 0.36s
```

Phase 14 is complete as an export-only boundary. Real platform integrations are
intentionally not implemented and require separate credential handling and
integration tests later.

The warning is the existing Starlette `BlockingPortal` deprecation warning. The
credential audit found no forbidden references in generation or director code;
the only credential strings are inert configuration references in
`app/shortform/publishers.py`.
