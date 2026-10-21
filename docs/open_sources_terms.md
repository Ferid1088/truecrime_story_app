# Open sources for places: terms read on 2026-10-21 and what was built

Rule: implement only what the current terms allow; where they are unclear, stop and report. "Read" means the
page was fetched on 2026-10-21; quotes are from those pages. This is not legal advice (owner, not a lawyer).

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
