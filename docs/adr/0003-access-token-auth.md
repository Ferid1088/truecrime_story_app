# 0003 One shared access token, not user accounts
**Context.** Single operator. The app must be safe to expose beyond localhost without building identity management.
**Decision.** A `TRUECRIME_ACCESS_TOKEN` gates everything (Bearer header or signed HttpOnly SameSite=Strict cookie), with lockout and origin checks. With no token the API answers loopback only.
**Consequences.** No per-user audit trail or roles. If a second person joins, replace this with real accounts or put an identity-aware proxy in front; the middleware is the single place to change.
