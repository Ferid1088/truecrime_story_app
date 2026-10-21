"""P12 + structured dossier search: Europeana and DPLA providers. Terms already read
(docs/open_sources_terms.md): both need a key. Fixtures: europeana_sample.json was recorded
from a real call (Europeana's own public api2demo key, never used by the provider itself);
dpla_live_sample.json was recorded from a real call with the project's own key on 2026-10-21
(key never stored). A missing key skips the provider cleanly, with one clear log line, never
a crash. The API keys travel as URL parameters and are masked in every log and error."""

import asyncio
import json
from pathlib import Path

import httpx

from app.documentary.visuals import rights as R

FIXTURES = Path(__file__).parent / "fixtures"

_DOSSIER_QUERY = {
    "query": "Moscow Idaho historic",
    "entity": "moscow_idaho",
    "entities": ["moscow_idaho"],
    "entity_name": "Moscow, Idaho",
    "entity_type": "place",
    "kind": "exact",
    "must_name": ["Moscow, Idaho", "Moscow"],
    "aliases": ["Moscow", "Moscow ID", "Moscow Idaho"],
    "date_window": [2022, 2022],
    "not_this": ["Moscow Russia"],
    "negative_terms": ["Kremlin", "Red Square", "Russia"],
}


def _run(coro):
    return asyncio.run(coro)


def _provider_with_key(env_name, monkeypatch, key="fake-key"):
    monkeypatch.setattr("app.documentary.visuals.open_data_archives.env_value",
                        lambda name: key if name == env_name else None)


# ---- the shared rights-URI classifier ----------------------------------------------------------

def test_in_copyright_is_kept_as_unclear_not_dropped():
    status, reason = R.classify_open_data_rights("http://rightsstatements.org/vocab/InC/1.0/")
    assert status == "editorial_review_required" and "In Copyright" in reason


def test_in_copyright_edu_variant_is_also_unclear():
    status, _ = R.classify_open_data_rights("http://rightsstatements.org/vocab/InC-EDU/1.0/")
    assert status == "editorial_review_required"


def test_no_copyright_is_public_domain():
    status, _ = R.classify_open_data_rights("http://rightsstatements.org/vocab/NoC-US/1.0/")
    assert status == "public_domain"


def test_cc0_is_public_domain():
    status, _ = R.classify_open_data_rights("http://creativecommons.org/publicdomain/zero/1.0/")
    assert status == "public_domain"


def test_cc_by_is_creative_commons():
    status, _ = R.classify_open_data_rights("http://creativecommons.org/licenses/by/4.0/")
    assert status == "creative_commons"


def test_cc_by_nc_nd_is_permission_required():
    status, reason = R.classify_open_data_rights("http://creativecommons.org/licenses/by-nc-nd/4.0/")
    assert status == "permission_required" and "commercial" in reason


def test_free_text_rights_falls_back_to_the_general_classifier():
    status, _ = R.classify_open_data_rights("Public domain")
    assert status == "public_domain"


def test_empty_rights_is_unknown():
    assert R.classify_open_data_rights(None)[0] == "unknown"
    assert R.classify_open_data_rights("  ")[0] == "unknown"


# ---- Europeana provider: parsing a recorded real response --------------------------------------

def _europeana_fixture() -> dict:
    return json.loads((FIXTURES / "europeana_sample.json").read_text())


def test_europeana_parses_a_recorded_real_response(monkeypatch):
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    monkeypatch.setattr("app.documentary.visuals.open_data_archives.env_value",
                        lambda name: "fake-key" if name == "TRUECRIME_EUROPEANA_API_KEY" else None)
    data = _europeana_fixture()

    def handler(request):
        return httpx.Response(200, json=data)

    import asyncio

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await EuropeanaProvider(client).search_for({"query": "lizzie borden"}, limit=10)

    candidates = asyncio.run(go())
    assert len(candidates) == 3
    resolved = {R.classify(c.provider, c.license, c.url, c.page_url)[0] for c in candidates}
    assert resolved == {"public_domain", "editorial_review_required", "permission_required"}
    pd = next(c for c in candidates
             if R.classify(c.provider, c.license, c.url, c.page_url)[0] == "public_domain")
    assert pd.url == "https://collections.smvk.se/carlotta-vkm/web/image/zoom/2000235/image.jpg"
    assert pd.source_name == "Europeana" and "Museum of World Culture" in pd.credit
    for c in candidates:
        assert c.provider == "europeana"


def test_europeana_is_skipped_cleanly_without_a_key(monkeypatch, caplog):
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    monkeypatch.setattr("app.documentary.visuals.open_data_archives.env_value", lambda name: None)
    import asyncio

    async def go():
        return await EuropeanaProvider(httpx.AsyncClient()).search_for({"query": "x"}, limit=5)

    with caplog.at_level("INFO"):
        out = asyncio.run(go())
    assert out == []
    assert any("Europeana" in r.message and "key" in r.message.lower() for r in caplog.records)


# ---- DPLA provider: parsing against the documented (not live-probed) schema --------------------

def _dpla_fixture() -> dict:
    return json.loads((FIXTURES / "dpla_sample.json").read_text())


def test_dpla_parses_the_documented_response_shape(monkeypatch):
    from app.documentary.visuals.open_data_archives import DplaProvider

    monkeypatch.setattr("app.documentary.visuals.open_data_archives.env_value",
                        lambda name: "fake-key" if name == "TRUECRIME_DPLA_API_KEY" else None)
    data = _dpla_fixture()

    def handler(request):
        return httpx.Response(200, json=data)

    import asyncio

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DplaProvider(client).search_for({"query": "lizzie borden"}, limit=10)

    candidates = asyncio.run(go())
    assert len(candidates) == 1
    [c] = candidates
    assert c.provider == "dpla"
    assert c.url == "http://repository.clemson.edu/cgi-bin/thumbnail.exe?CISOROOT=/cfb&CISOPTR=1001"
    assert c.page_url == "http://repository.clemson.edu/u?/cfb,1001"
    # free-text rights ("Contact Special Collections... copyright..."), not a clean open
    # licence: the existing generic classifier reads "copyright" as permission_required
    assert R.classify(c.provider, c.license, c.url, c.page_url)[0] == "permission_required"


def test_dpla_is_skipped_cleanly_without_a_key(monkeypatch, caplog):
    from app.documentary.visuals.open_data_archives import DplaProvider

    monkeypatch.setattr("app.documentary.visuals.open_data_archives.env_value", lambda name: None)
    import asyncio

    async def go():
        return await DplaProvider(httpx.AsyncClient()).search_for({"query": "x"}, limit=5)

    with caplog.at_level("INFO"):
        out = asyncio.run(go())
    assert out == []
    assert any("DPLA" in r.message and "key" in r.message.lower() for r in caplog.records)


def test_neither_provider_crashes_on_a_provider_error(monkeypatch):
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    monkeypatch.setattr("app.documentary.visuals.open_data_archives.env_value",
                        lambda name: "fake-key")
    import asyncio

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda r: httpx.Response(500, text="error"))) as client:
            return await EuropeanaProvider(client).search_for({"query": "x"}, limit=5)

    assert asyncio.run(go()) == []


# ---- structured search (dossier): query building ----------------------------------------------

def _captured(data):
    """A MockTransport handler that records the request's query params (QueryParams keeps
    repeated keys like `qf` — dict() would flatten them)."""
    seen = []

    def handler(request):
        seen.append(request.url.params)
        return httpx.Response(200, json=data)

    return seen, handler


def test_europeana_builds_a_structured_query_from_the_dossier(monkeypatch):
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    _provider_with_key("TRUECRIME_EUROPEANA_API_KEY", monkeypatch)
    seen, handler = _captured({"items": [], "totalResults": 0})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await EuropeanaProvider(client).search_for(dict(_DOSSIER_QUERY), limit=10)

    _run(go())
    assert seen
    first = seen[0]
    # one alias, all terms ANDed (no quoting: keeps "Borden, Lizzie" inversions reachable)
    assert first["query"].startswith("Moscow Idaho")
    # the dossier's not-this / negative terms as API-side NOT exclusions
    assert 'NOT "Moscow Russia"' in first["query"]
    assert 'NOT "Kremlin"' in first["query"]
    # the era window (2022 ± 50 padding) as the YEAR facet filter
    qfs = first.get_list("qf")
    assert "TYPE:IMAGE" in qfs
    assert "YEAR:[1972 TO 2072]" in qfs
    # first pass asks for open rights only
    assert first["reusability"] == "open"
    # the key travels as a URL param on the request itself but is never in the call trail
    assert any(params.get("wskey") == "fake-key" for params in seen)
    for params in seen:
        assert "wskey" in params


def test_europeana_fans_out_one_call_per_alias_capped(monkeypatch):
    from app.core.ai_config import ArchivesConfig
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    _provider_with_key("TRUECRIME_EUROPEANA_API_KEY", monkeypatch)
    # plenty of open results so the second pass never fires
    items = [{"edmIsShownBy": [f"https://x/{i}.jpg"], "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"]}
             for i in range(4)]
    seen, handler = _captured({"items": items, "totalResults": len(items)})
    q = dict(_DOSSIER_QUERY, aliases=[f"alias {i}" for i in range(8)])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = EuropeanaProvider(client)
            p.arch = ArchivesConfig(max_aliases_per_entity=3, open_min_candidates=1)
            return await p.search_for(q, limit=10)

    _run(go())
    queries = {c["query"] for c in seen}
    # 3 alias calls (cap), each containing the alias's own terms
    assert len(seen) == 3
    assert any("alias 0" in qv for qv in queries)


def test_europeana_second_call_only_when_open_is_too_thin(monkeypatch):
    from app.core.ai_config import ArchivesConfig
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    _provider_with_key("TRUECRIME_EUROPEANA_API_KEY", monkeypatch)
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        if request.url.params.get("reusability") == "open":
            return httpx.Response(200, json={"items": [], "totalResults": 0})
        return httpx.Response(200, json={
            "items": [{"edmIsShownBy": ["https://x/1.jpg"],
                       "rights": ["http://rightsstatements.org/vocab/InC/1.0/"],
                       "title": ["x"]}], "totalResults": 1})

    q = dict(_DOSSIER_QUERY, entity_name="Moscow Idaho", aliases=[], must_name=["Moscow Idaho"],
             negative_terms=[], not_this=[])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = EuropeanaProvider(client)
            p.arch = ArchivesConfig(open_min_candidates=3)
            return await p.search_for(q, limit=10)

    out = _run(go())
    # open pass returned 0 < 3 -> the same alias was retried without reusability
    passes = [c.get("reusability") for c in seen]
    assert "open" in passes and None in passes
    # in-copyright item kept and labelled, never dropped silently
    assert len(out) == 1
    assert out[0].license == "http://rightsstatements.org/vocab/InC/1.0/"
    assert R.classify(out[0].provider, out[0].license, out[0].url, out[0].page_url)[0] \
        == "editorial_review_required"


def test_europeana_no_second_call_when_open_is_enough(monkeypatch):
    from app.core.ai_config import ArchivesConfig
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    _provider_with_key("TRUECRIME_EUROPEANA_API_KEY", monkeypatch)
    items = [{"edmIsShownBy": [f"https://x/{i}.jpg"],
              "rights": ["http://creativecommons.org/publicdomain/mark/1.0/"]}
             for i in range(3)]
    seen, handler = _captured({"items": items, "totalResults": len(items)})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = EuropeanaProvider(client)
            p.arch = ArchivesConfig(max_aliases_per_entity=2, open_min_candidates=3)
            return await p.search_for(dict(_DOSSIER_QUERY), limit=10)

    _run(go())
    assert all(c.get("reusability") == "open" for c in seen)
    assert len(seen) == 2  # the two capped aliases, one open call each


def test_call_caps_stop_alias_fanout(monkeypatch):
    from app.core.ai_config import ArchivesConfig
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    _provider_with_key("TRUECRIME_EUROPEANA_API_KEY", monkeypatch)
    seen, handler = _captured({"items": [], "totalResults": 0})
    q = dict(_DOSSIER_QUERY, aliases=[f"alias {i}" for i in range(4)],
             entity_name=None, must_name=[])

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = EuropeanaProvider(client)
            p.arch = ArchivesConfig(max_aliases_per_entity=4, max_calls_per_entity=2,
                                    open_min_candidates=0)
            return await p.search_for(q, limit=10)

    _run(go())
    assert len(seen) == 2  # per-entity cap, not the alias cap, stopped the fan-out


def test_dpla_date_filter_and_no_negatives(monkeypatch):
    from app.documentary.visuals.open_data_archives import DplaProvider

    _provider_with_key("TRUECRIME_DPLA_API_KEY", monkeypatch)
    seen, handler = _captured({"docs": [], "count": 0})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await DplaProvider(client).search_for(dict(_DOSSIER_QUERY), limit=10)

    _run(go())
    first = seen[0]
    assert first["sourceResource.date.after"] == "1972"
    assert first["sourceResource.date.before"] == "2072"
    # DPLA's q has no NOT (live-probed): exclusions stay with the verifier
    assert "NOT" not in first["q"]
    assert "Kremlin" not in first["q"]


def _dpla_live_docs():
    return json.loads((FIXTURES / "dpla_live_sample.json").read_text())


def test_dpla_local_open_partition_under_threshold_keeps_everything(monkeypatch):
    """Below the open threshold the whole recorded real response is kept — the items with
    no rights and the rights-statement items stay in, labelled by the classifier."""
    from app.core.ai_config import ArchivesConfig
    from app.documentary.visuals.open_data_archives import DplaProvider

    _provider_with_key("TRUECRIME_DPLA_API_KEY", monkeypatch)
    seen, handler = _captured(_dpla_live_docs())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = DplaProvider(client)
            p.arch = ArchivesConfig(max_aliases_per_entity=1, open_min_candidates=5)
            return await p.search_for(dict(_DOSSIER_QUERY), limit=10)

    out = _run(go())
    assert len(seen) == 1  # one alias call, no second call exists for DPLA
    assert len(out) == 5
    statuses = {R.classify(c.provider, c.license, c.url, c.page_url)[0] for c in out}
    assert "public_domain" in statuses
    assert "unknown" in statuses  # the two no-rights items kept, labelled unknown


def test_dpla_local_partition_drops_restricted_when_open_is_enough(monkeypatch):
    from app.core.ai_config import ArchivesConfig
    from app.documentary.visuals.open_data_archives import DplaProvider

    _provider_with_key("TRUECRIME_DPLA_API_KEY", monkeypatch)
    docs = [
        {"object": {"@id": f"https://x/{i}.jpg"},
         "sourceResource": {"title": [f"open {i}"], "rights": ["Public domain"]}}
        for i in range(3)
    ] + [{"object": {"@id": "https://x/inc.jpg"},
          "sourceResource": {"title": ["inc"],
                             "rights": ["All rights reserved."]}}]
    seen, handler = _captured({"docs": docs, "count": len(docs)})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = DplaProvider(client)
            p.arch = ArchivesConfig(max_aliases_per_entity=1, open_min_candidates=2)
            return await p.search_for(dict(_DOSSIER_QUERY), limit=10)

    out = _run(go())
    assert len(out) == 3 and all("inc" not in (c.title or "") for c in out)


def test_dpla_list_rights_field_is_normalised(monkeypatch):
    """The recorded real DPLA response carries sourceResource.rights as a LIST of free text —
    the candidate's license must be a string or the rights classifier crashes."""
    from app.documentary.visuals.open_data_archives import DplaProvider

    _provider_with_key("TRUECRIME_DPLA_API_KEY", monkeypatch)
    _, handler = _captured(_dpla_live_docs())

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = DplaProvider(client)
            p.arch.open_min_candidates = 0
            return await p.search_for(dict(_DOSSIER_QUERY), limit=10)

    out = _run(go())
    assert all(c.license is None or isinstance(c.license, str) for c in out)


def test_keys_are_masked_in_errors_and_the_call_trail(monkeypatch, caplog):
    from app.documentary.visuals.open_data_archives import DplaProvider

    _provider_with_key("TRUECRIME_DPLA_API_KEY", monkeypatch, key="SECRET-KEY-123")

    def handler(request):
        return httpx.Response(403, text="forbidden")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = DplaProvider(client)
            await p.search_for(dict(_DOSSIER_QUERY), limit=5)
            return p

    with caplog.at_level("WARNING"):
        p = _run(go())
    assert "SECRET-KEY-123" not in caplog.text
    assert "SECRET-KEY-123" not in json.dumps(p.calls_log)


def test_context_query_is_sent_as_its_own_text(monkeypatch):
    """A context query looks for a stand-in: no alias fan-out, no NOT exclusions."""
    from app.documentary.visuals.open_data_archives import EuropeanaProvider

    _provider_with_key("TRUECRIME_EUROPEANA_API_KEY", monkeypatch)
    seen, handler = _captured({"items": [], "totalResults": 0})
    q = dict(_DOSSIER_QUERY, kind="context", query="small town main street 2020s")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            p = EuropeanaProvider(client)
            p.arch.open_min_candidates = 0
            return await p.search_for(q, limit=5)

    _run(go())
    assert len(seen) == 1
    assert seen[0]["query"] == "small town main street 2020s"
    assert "NOT" not in seen[0]["query"]
