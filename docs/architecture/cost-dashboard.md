# Cost dashboard

Pages: `/costs` (all cases) and `/costs/<case>` (one case, also the **Costs** tab of the documentary workspace). API: `app/costs/api.py` (`/api/costs/...`).

## Where the numbers come from
One ledger: `provider_calls`. Since migration 0104 every row also has `language`, `step`, `vendor`, `subject`, `accuracy`, `attribution` ("live", "rule", "run", "job") and `duplicate_of`.
- **Labels** come from the rules in `config/cost_steps.json` (stage patterns → step, host/model/stage → vendor, a language token in the stage name). `app/costs/registry.py` is the one function used for new rows (`BudgetService.record`, `observability.record_call`) and for old rows (`app/costs/backfill.py`). A language the running step reports (the run context, or an explicit `language=`) wins over the stage name. Old rows without a language get the language of the agent run with the same tokens (±5 s) or of the host scene / short clip that asked for the avatar video. What stays unknown is shown as "Language unknown", never guessed.
- **Real money** = rows that are not test data, have a price and are not `duplicate_of` another row. A call is written twice (network layer and budget ledger); `app/costs/dedupe.py` marks the network row (same case, same tokens, ±10 s). Ledger rows are never deleted or edited. The budget **counter** (`/api/cases/<id>/costs`, cap) is unchanged and still adds every row; the dashboard shows both.
- **Accuracy**: reported (provider's own price), computed (reported usage × price table), estimated (HeyGen, music, search: no price reported), none. Sums show the estimated part next to them.
- **Shared work** (research, story, pictures, previews) has its own column; "shared evenly" spreads it over the case's languages as a calculation only.
- **Bills**: `cost_invoices` holds the vendors' real monthly bills typed in by the owner; the overview shows ledger against bill per vendor and month.

## Drill-down
Step → language → vendor → model → object → the single call (everything the ledger knows). Every figure in the matrix and the vendor table is a click.

## Open
- No vendor usage API has been read yet; real invoices are typed in by hand.
- Rows whose language was never recorded (about 11 % of the money of case 15, mostly voice-text LLM calls) stay "unknown".
