# Autopilot ("Start" button) — Checklist

One row per requirement. Status: TODO / IN_PROGRESS / DONE (test + live evidence) / WAITING_OWNER.
Built test first. The autopilot only chains stages that already exist; it never bypasses a gate.
Owner-only items (rights sign-off, any failed gate that needs a human) land on the "needs you" list.

| ID | Requirement | Status | Test (file::name) | Live evidence |
|---|---|---|---|---|
| AP1 | Tables `autopilot_runs`, `channel_settings`, `publish_requests` (title suggestions are the existing `case_title_candidates`) by Alembic migration 0055 | DONE (schema) | tests/test_autopilot_service.py (tables used) | `alembic upgrade head` on live Postgres after backup truecrime-backup-20261021-015438.dump; information_schema lists autopilot_runs, channel_settings, publish_requests |
| AP2 | `config/parameters/autopilot.json`: languages, platforms, schedule slots, approver label, render profile | DONE | tests/test_autopilot_channels.py::test_every_language_and_platform_has_a_placeholder_that_is_not_connected | config/parameters/autopilot.json loaded (live GET /api/channel-settings returned 16 rows) |
| AP3 | Title agent: 7 suggestions per language, stored, never confirmed by the autopilot | DONE (reuses existing naming stage, 7 per language via case_naming.candidates_per_language) | tests/test_autopilot_runner.py::test_titles_are_made_for_languages_that_lack_seven_and_never_confirmed | not yet shown on a live run |
| AP4 | Channel settings placeholders per language and platform (never a credential), API + Settings UI | DONE (API); UI by Playwright mock | tests/test_autopilot_channels.py (5), frontend/e2e/autopilot.spec.ts::Settings shows a not-connected placeholder | live GET /api/channel-settings on 8001 |
| AP5 | Dry-run publish requests and schedule queue per language and platform (nothing is sent) | DONE (tests) | tests/test_autopilot_publish.py (5) | not yet shown on a live run |
| AP6 | Orchestrator: case from candidate, research, language chains, automatic approval records, film jobs, shorts, titles, publish dry run | DONE (tests) | tests/test_autopilot_runner.py (10) | not yet shown on a live run |
| AP7 | Run state, step log, "needs you" list; a stage that stops always shows a plain reason | DONE (tests) | tests/test_autopilot_runner.py::test_a_stopped_film_is_listed_with_its_reason_and_clears_when_the_job_is_resumed | not yet shown on a live run |
| AP8 | API: estimate, start, get, list runs | DONE (tests) | tests/test_autopilot_api.py (6), tests/test_autopilot_service.py (8) | live API restarted with the code; no run started yet |
| AP9 | Discover UI: Start button, dialog (languages en/de/fa/ar, printed estimate), run page | DONE (build, Playwright mock) | frontend/e2e/autopilot.spec.ts::Start opens a dialog... | UI live check pending (T3) |
| AP10 | Playwright test for Start dialog and run page | DONE | frontend/e2e/autopilot.spec.ts (3 passed) | - |
