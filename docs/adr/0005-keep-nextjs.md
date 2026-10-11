# 0005 Keep Next.js; harden instead of rewriting the UI
**Context.** The question was whether a different UI technology would be better. The frontend is already Next.js 16 / React 19 / Tailwind 4 with typed API access.
**Decision.** No rewrite. Add what was missing: login, Operations page, contract test against the backend routes, accessibility gate, wider browser tests.
**Consequences.** Revisit only for a concrete limitation (for instance multi-user real-time editing), not for fashion.
