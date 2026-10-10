# Short-Form Engine Phases 5-14 Loop Log

This pass establishes the deterministic contracts that later provider-backed UI work uses.

- Phase 5: critic decisions are represented as reviewable records.
- Phase 6: EN/DE/FA/AR voice IDs resolve through central `ai_config.voice`; native hook records retain language and direction.
- Phase 7: platform variants carry platform duration, CTA, destination, disclosure, rights, policy, and campaign identifiers.
- Phase 8: export records expose rights status; unknown rights remain a hard-gate condition in the existing rights checker.
- Phase 9: reveal and epistemic hard gates remain deterministic and are covered by positive and negative tests.
- Phase 10: repetition checks cover hook structure, CTA, opening visual, and host pose.
- Phase 11: publishing plans enforce a 10-14 day window, counts, and approval-only slots.
- Phase 12: the pilot review page supports preview, approve, reject, caption, CTA, and destination edits.
- Phase 13: metric summaries preserve platform context and never invent conversion when attribution is unavailable.
- Phase 14: `ExportPackagePublisher` refuses unapproved work and returns an export handoff only after approval; no credentials are used.

Verification: `tests/test_shortform_operations.py` passed 5 tests. Provider-backed voice generation, social APIs, and persistent phase-specific review tabs remain intentionally outside this offline contract pass.
