## What and why

## Checks
- [ ] `scripts/gate.sh` is green (both Python suites, secret scan, frontend, e2e)
- [ ] Database change? A migration is included and was rehearsed on a COPY of the real database (`docs/migrations.md`)
- [ ] New outside call, secret or permission? `docs/security.md` updated
- [ ] User-visible change? `CHANGELOG.md` updated
