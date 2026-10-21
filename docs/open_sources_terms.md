# Open sources for places: terms read on 2026-10-21 and what was built

## Structured archive search (read and LIVE-PROBED 2026-10-21, both real keys configured)

Sources: Europeana Search API docs (europeana.atlassian.net "Search API Documentation" / API FAQ;
github.com/europeana/labs-preview `api/query.md`, `api/search.md`) AND real calls against
api.europeana.eu/record/v2/search.json with the project's own key (masked in every log); DPLA docs
(pro.dp.la/developers/* — the pages still 403 this environment, so read via search-cache excerpts)
and DPLA's own public spec (github.com/dpla-attic/platform `develop` README) AND real calls against
api.dp.la/v2/items with the project's own key. Parameters not confirmed by the docs AND a real
response are not used.

| parameter | provider | confirmed by |
|---|---|---|
| `wskey` | Europeana | docs + live 200 |
| `query` | Europeana | Lucene syntax: bare terms are ANDed ("two consecutive terms ... default to the AND operator"), `"phrase"` quoted form works live, `AND`/`OR`/`NOT` (NOT only in conjunction; `moscow NOT "red square"` → 9825 vs `moscow` → 9909, live), `YEAR:[a TO b]` range (`api/query.md` time-range example) |
| `qf` | Europeana | repeatable facet filter; live-confirmed `qf=TYPE:IMAGE` and `qf=YEAR:[1970 TO 2000]` (returned only 1970-2000 items) |
| `reusability` | Europeana | `open` = Public Domain Mark, CC0, CC BY, CC BY-SA (API FAQ); `restricted`, `permission` are the other documented values; live-confirmed `reusability=open` returns only those licences |
| `rows` | Europeana | max 100 (docs); `start`, `profile`, `facet` exist but are not needed |
| `api_key` | DPLA | docs + live 200 |
| `q` | DPLA | all terms are ANDed; quoted phrases work live (`"lizzie borden"` → 37 vs `lizzie borden` → 82). **NOT is NOT supported**: `q=moscow NOT russia`/`moscow -russia`/`moscow AND NOT russia` all returned 20591 = every token treated as an ANDed literal term, live-probed — negative terms are left to the verifier |
| `sourceResource.date.after` / `sourceResource.date.before` | DPLA | creation date of the original object, YYYY or YYYY-MM-DD, combined = "between" (README + pro.dp.la/requests); live-confirmed (`moscow idaho` + after=1990&before=2020 → 288, items dated in-window) |
| `sourceResource.spatial*` | DPLA | `.name`, `.city`, `.state`, `.country`, `.coordinates`, `.distance`, bounding box (README + pro.dp.la/requests) — documented but NOT used: the dossier carries no coordinates and name/state alone is ambiguous ("Moscow") |
| `sort_by`, `sort_order`, `sort_by_pin` | DPLA | docs only (pro.dp.la/requests) — not needed |
| `page`, `page_size`, `facets`, `fields` | DPLA | docs; page_size max 100 per README |
| NOT / open-rights filter | DPLA | **none exists**: `sourceResource.rights` is free text ("Contact Special Collections for copyright information"), there is no `reusability` equivalent. Open-first is therefore a LOCAL filter: the one response is partitioned by the stored rights label, and the restricted share is kept only when the open share is below the configured threshold (else it is not returned, exactly as if the API had filtered it) |

Europeana's `COUNTRY` facet and `where:`/`who:`/`what:`/`when:`/`title:` field names are documented;
`where:` etc. were left out deliberately (phrase-free AND-terms already hit the same text, and
`COUNTRY` is the providing institution's country — filtering it would wrongly drop US-case material
held in Europe). Note from the probes: quoted phrases miss inverted catalog names
(`"Lizzie Borden"` → 0 while `lizzie borden` → 1, the item catalogued "Borden, Lizzie"), so alias
queries are sent as bare ANDed terms, not phrases.

Rule: implement only what the current terms allow; where they are unclear, stop and report. "Read" means the
page was fetched on 2026-10-21; quotes are from those pages. This is not legal advice (owner, not a lawyer).

## P12 (added 2026-10-21, built 2026-10-22): five archives were named; two are built

Terms read AND the live endpoint actually probed (not just the docs) on 2026-10-21. Of the five named
archives, **Europeana and DPLA are now built** (`app/documentary/visuals/open_data_archives.py`,
`EuropeanaProvider`/`DplaProvider`) — the owner is registering one free key for each. **Library of
Congress, Openverse and Flickr are dropped for now**, not built, for the reasons recorded below.

| archive | live probe | terms found | status |
|---|---|---|---|
| Europeana (api.europeana.eu) | `curl ".../search.json?wskey=api2demo&query=lizzie+borden"` → **200**, 3 real results, each with a real `rights` field (`http://rightsstatements.org/vocab/InC/1.0/` — **In Copyright**, i.e. NOT usable, for this sample query); further probes on 2026-10-21 for `qf=TYPE:IMAGE` queries also turned up real `by-nc-nd` (restricted) and `publicdomain/zero` (public domain) items — the full rights variety the provider must handle | the API clearly exists and reports machine-readable rights per item; `api2demo` is Europeana's own published demo key, documented for testing only, shared and rate-limited — used ONLY to record `tests/fixtures/europeana_sample.json` (3 real, trimmed items, one per rights class seen live), never for the provider's own real calls | **built**: `EuropeanaProvider`, 7 tests against the recorded real fixture + the rights-classifier unit tests. Every item's `rights` field is read and stored as the candidate's `license`; `rights.classify_open_data_rights()` keeps an "In Copyright" item as `editorial_review_required` (shown, credited, never silently dropped) and a `by-nc-nd`/`by-nc`/`by-nd` item as `permission_required`. **No live proof of a real run**: `TRUECRIME_EUROPEANA_API_KEY` is not yet configured (the owner is registering it); confirmed live against the real app that the provider is skipped cleanly with one log line ("Europeana skipped: ... is not configured (no key)"), not a crash |
| DPLA (api.dp.la) | `curl "https://api.dp.la/v2/items?q=lizzie+borden"` → **403** (confirms a registered key is required); the docs page (dp.la/info/developers/codex/...) also returned 403 to the fetch tool | DPLA's API and its own docs page are both key-gated/blocked from this environment; its response SHAPE was instead read from DPLA's own public, non-key-gated source code repository (`github.com/dpla/platform`, `develop` branch `README.md`, "Sample Record" and "a list of records" sections) — a real DPLA-published example (a Clemson University football program item), confirming the wrapper (`count`/`start`/`limit`/`docs`/`facets`) and item fields (`sourceResource.rights` as free text, `isShownAt.@id`, `object.@id`, `dataProvider`, `provider.name`) exactly | **built**: `DplaProvider`, parsed against `tests/fixtures/dpla_sample.json` (built from that real documented example, clearly marked in the fixture as NOT a live probe). `sourceResource.rights` is often free text, not a URI (DPLA's own sample: "Contact Special Collections for copyright information") — `classify_open_data_rights()` falls back to the existing free-text classifier for it, which reads "copyright" as `permission_required`. **No live proof at all**: neither a real API call nor a real key-gated docs fetch succeeded this session; this is explicitly NOT live-verified, only built against documented structure, and will need a real run once the owner's key arrives |
| Library of Congress (loc.gov JSON API) | `curl "https://www.loc.gov/search/?q=lizzie+borden&fo=json"` → **403** (a Cloudflare "Just a moment..." challenge page, confirmed by re-reading the raw response body — bot mitigation, not the documented rate limit), with and without a User-Agent header, despite the docs page saying "no API key or authentication required" | loc.gov/apis/json-and-yaml: no key needed, 20 requests/minute, 1-hour block over that; the docs show no `rights`/`license` field on an item, only an undocumented `access_restricted: true/false` | **dropped, not built**: the live service refused every request from this environment; even if it answered, no rights field means every image would need a second, unverified judgement call before use |
| Openverse (api.openverse.org) | first request: **504** (gateway timeout); retried 20s later: **connection failed** (DNS/network, curl exit "000") | the terms-of-service and licence-field pages returned no body to the fetch tool (JS-rendered docs); Openverse is known to aggregate CC-licensed images from many sources with a `license`/`license_version`/`attribution` field per result, but this could not be confirmed live | **dropped, not built**: the service was not reachable twice in a row from this machine; stopped rather than built against an API that could not be exercised |
| Flickr (api.flickr.com, Flickr Commons / CC search) | `curl ".../rest/?method=flickr.photos.search&..."` → HTTP 200 with `{"stat":"fail","code":100,"message":"Invalid API Key (Key has invalid format)"}` — confirms a real key is required | flickr.com/services/api/tos: a **commercial** application (this is one) needs a **commercial API key**, possibly subject to fees; a photo is usable commercially only when "marked with a Creative Commons license that allows for such use"; **storage is restricted** — "cache or store any Flickr user photos other than for reasonable periods," which sits uneasily against archiving a picture permanently inside a finished film | **dropped, not built**: no commercial key configured, and the caching-duration language is the kind of unclear term that stopped Mapillary — an owner decision, not a judgement call to make alone |

### What the owner has to decide for P12
Finish registering the Europeana and DPLA keys (free) and put them in `.env` as `TRUECRIME_EUROPEANA_API_KEY` /
`TRUECRIME_DPLA_API_KEY` (names only are in `.env.example`; never commit the real values) — both providers already
run as soon as a key appears. Library of Congress and Openverse were blocked by bot mitigation and an outage
respectively, not a policy limit; worth retrying from a different network/host if wanted. Flickr needs a
**commercial** key (SmugMug may charge) and an explicit decision on the "reasonable periods" caching language.

| source | built | terms in short | credit in the film |
|---|---|---|---|
| USGS / USDA NAIP aerial imagery | yes: `UsgsNaipProvider` (US places only) | "USGS-authored or produced data and information are considered to be in the U.S. Public Domain." (usgs.gov copyrights-and-credits). The image service (imagery.nationalmap.gov, `USGSNAIPImagery/ImageServer`) lists copyright "USGS, USDA, The National Map: Orthoimagery", "Copying and analysis are allowed", max 4,000 px per export. | "USGS, USDA, The National Map: Orthoimagery" (`rights.credit_providers`) |
| NASA Image and Video Library | yes: `NasaProvider` | NASA content "generally are not subject to copyright in the United States"; editorial use in non-promotional works is fine; "NASA should be acknowledged as the source of the material"; third-party copyrighted items are marked and "Those wishing to use copyright protected material of third parties must contact the copyright holder directly" (nasa.gov media usage guidelines). The API (`images-api.nasa.gov/search`, `/asset/{nasa_id}`) answered without a key when probed live; an item that names a copyright (©, "(c)", "copyright") in its description or secondary creator is skipped. | "NASA" |
| Wikimedia Commons (existing provider) | already | each file's own licence applies; "Other restrictions may apply" (trademarks, personality and privacy rights); credit goes to the creator; "attribution is recommended" for public domain; ShareAlike applies to derivatives (Commons: Reusing content outside Wikimedia). The provider stores the licence and credit; CC licences are credited in the film (`attribution_required`). | creator and licence (existing credit overlay) |
| Internet Archive (existing provider) | already | the Terms of Use page could not be read by the fetch tool on 2026-10-21 (script-rendered page): current terms NOT verified here. The existing behaviour is unchanged: only items with stored open-licence evidence are film candidates (`video_rights`). | title / archive (existing) |
| Mapillary (CC BY-SA street images) | **not implemented** | stopped: the terms are unclear for this use and an access token is missing. Terms (mapillary.com/terms): images are CC BY-SA 4.0 (3(b)) but **Section 12** limits commercial use of the Mapillary Services to "product development and training" and "providing services to clients"; an application must be registered for a client id and must "materially supplement" Mapillary's services, not "merely redistribute Content" (Section 11); Section 5 bars "data mining, robots or similar data gathering or extraction methods not approved by Mapillary"; the logo and a link back are required (Section 11). Every request to graph.mapillary.com needs a client or user access token (developer docs), and none is configured here. A monetised film that reuses street images is not clearly any of the permitted commercial purposes. | would need the Mapillary logo and link plus CC BY-SA attribution |

## What the owner has to decide for Mapillary
Whether the use is acceptable under Section 12, and (if yes) to register an application and put its token into
`.env` as `TRUECRIME_MAPILLARY_ACCESS_TOKEN`. Until then the provider stays out: not implemented, not in `visual_search.providers`.

## How the two built sources work
* `usgs_naip`: only for entities of type place, building or landscape; the query (else the entity's name) is geocoded
  with the app's own geocoder (`maps.geocode`); a point inside the service extent gets one aerial image per
  `visual_search.open_data.usgs_naip.view_meters` (a square of that many metres, `size_px` pixels), made with
  `exportImage`. Outside the United States nothing is requested.
* `nasa`: only for place or landscape entities; text search, the image file is chosen by size marker.
* Both: licence "Public Domain", rights status `public_domain`; the pictures are verified like any other picture
  (they can be rejected or become context pictures) and the credit is shown while they are on screen
  (`rights.credit_providers`, `R.credit_due`).
