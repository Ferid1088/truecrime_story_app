# Short-Form Engine Phase 4 Loop Log

## Loop A - Iteration 1

- Failure: FFmpeg rejected the absolute SRT path because the installed build had no `subtitles` filter.
- Root cause: The local macOS FFmpeg build does not include libass/subtitles support.
- Fix: Keep the timed SRT as the canonical subtitle track, render subtitle proof into the cover frame, and record `static_preview_plus_timed_srt` in the manifest instead of claiming burned subtitles.
- Result: Export tests passed: 3 passed; probe confirmed 1080x1920, 8.0 seconds.

## Loop A - Iteration 2

- Failure: None.
- Root cause: Not applicable.
- Fix: Reran export tests and full Playwright suite after adding the review page.
- Result: Export tests passed again: 3 passed; browser suite passed: 27 passed.

## Loop B - Iteration 1

- Failure: None.
- Root cause: Not applicable.
- Fix: Added Phase 4 tests to the cumulative runner.
- Result: `scripts/shortform_verify.sh --through 4` passed: 47 passed.

## Loop B - Iteration 2

- Failure: None.
- Root cause: Not applicable.
- Fix: Reran the cumulative runner and focused export tests.
- Result: Cumulative verification passed again: 47 passed; focused export tests passed again: 3 passed.

## Gate Status

Passed twice in a row for the local pilot artifact. The package contains MP4, cover, timed SRT, manifest, disclosure metadata, rights status, and review controls. Full provider-backed voice rendering and libass-burned subtitle validation remain external-provider work, not silently marked complete.
