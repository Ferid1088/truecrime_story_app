"""CaseNamingAgent / critic / gates / approval with a scripted LLM (no network)."""

import asyncio
import json

import pytest

from app.db.models import (Case, CaseTitleCandidate, EditorialBlueprint, Fact, StoryVersion)
from app.identity import titles as T
from app.lifecycle.status import set_resolution
from app.naming import agent as A
from app.naming import gates as G
from app.naming.context import build_context

EN = ["The Lantern Of Celle", "Seven Doors Harbor", "The Borrowed Boat", "A Lamp For Rita",
      "The Salt Road Clock", "Nine Steps Upstream", "The Quiet Ferryman", "Window Seat Eleven",
      "The Copper Kettle Night", "Harbor Light Eleven"]
DE = ["Die Laterne von Celle", "Das geliehene Boot", "Der stille Fährmann", "Hafenlicht Nummer elf",
      "Die Salzstraße", "Neun Stufen flussauf", "Das Kupferkessel-Rätsel", "Lampe für Rita"]
FA = ["فانوس سلله", "قایق قرضی", "پایاب خاموش", "چراغ بندر یازده", "جاده‌ی نمک",
      "نه پله تا رود", "کتری مسی", "پل بی‌نشان"]
AR = ["فانوس سيله", "القارب المستعار", "العبّارة الصامتة", "مصباح الميناء", "طريق الملح",
      "تسع درجات", "غلاية النحاس", "جسر بلا اسم"]
ES = ["La Linterna de Celle", "El Puerto de las Siete Puertas", "El Barco Prestado", "La Lámpara de Rita",
      "El Reloj de la Ruta de la Sal", "Nueve Pasos Río Arriba", "El Barquero Silencioso"]
IT = ["La Lanterna di Celle", "Il Porto delle Sette Porte", "La Barca in Prestito", "La Lampada di Rita",
      "L'Orologio della Strada del Sale", "Nove Passi Controcorrente", "Il Traghettatore Silenzioso"]
FR = ["La Lanterne de Celle", "Le Port aux Sept Portes", "Le Bateau Emprunté", "La Lampe de Rita",
      "L'Horloge de la Route du Sel", "Neuf Pas en Amont", "Le Passeur Silencieux"]
PT = ["A Lanterna de Celle", "O Porto das Sete Portas", "O Barco Emprestado", "A Lâmpada de Rita",
      "O Relógio da Estrada do Sal", "Nove Passos Rio Acima", "O Barqueiro Silencioso"]
TR = ["Celle Feneri", "Yedi Kapılı Liman", "Ödünç Tekne", "Rita'nın Lambası",
      "Tuz Yolu Saati", "Yukarı Nehirde Dokuz Adım", "Sessiz Kayıkçı"]


class ScriptedGen:
    def __init__(self, proposals, bad=None):
        self.proposals = {k: list(v) for k, v in proposals.items()}
        self.bad = bad or {}
        self.calls = []

    def is_configured(self):
        return True

    async def generate_structured(self, role, system, user, images=None):
        self.calls.append(role)
        d = json.loads(user)
        if role == "case_naming_agent":
            lang = {"English": "en", "German": "de", "Persian (Farsi)": "fa",
                    "Arabic": "ar", "Mexican Spanish": "es", "Italian": "it", "French": "fr",
                    "Brazilian Portuguese": "pt", "Turkish": "tr"}[d["language"]]
            pool = self.proposals[lang]
            take, self.proposals[lang] = pool[:d["count"]], pool[d["count"]:]
            return {"editorial_concept": "a lamp that was left burning",
                    "candidates": [{"title": t, "angle": "lamp"} for t in take]}, None
        out = []
        for t in d["titles"]:
            b = self.bad.get(t, {})
            if role == "case_title_critic":
                out.append({"title": t, "clarity": 0.8, "specificity": 0.8, "memorability": 0.7, "curiosity": 0.7,
                            "documentary_tone": 0.8, "sensationalism_risk": 0.1,
                            "spoiler_risk": 0.1, "epistemic_risk": 0.1, "reason": "ok", **b})
            else:
                out.append({"title": t, "native_quality": 0.9, "literal_translation": False,
                            "reason": "ok", **b})
        return {"scores": out}, None


@pytest.fixture(autouse=True)
def _short_fixture_titles(monkeypatch):
    """The mechanics tests (gates, replacement loop, approval) use short fixture titles and seven candidates; the quality rules (a title says what happened) have their own tests below."""
    from app.core.ai_config import ai_config

    monkeypatch.setattr(ai_config.case_naming, "min_words", 2)
    monkeypatch.setattr(ai_config.case_naming, "max_words", 6)
    monkeypatch.setattr(ai_config.case_naming, "candidates_per_language", 7)


@pytest.fixture(autouse=True)
def _clean_titles(db_session):
    """Every test starts without earlier tests' candidates and identities."""
    from app.db.models import EpisodeIdentity

    db_session.query(CaseTitleCandidate).delete()
    db_session.query(EpisodeIdentity).delete()
    db_session.commit()


def _case(db, name="Celle lantern", status="UNSOLVED"):
    case = Case(canonical_title=name, slug=name.lower().replace(" ", "-") + "-nm",
                location="Celle", people_json=json.dumps(["Rita Haas"]))
    db.add(case)
    db.flush()
    set_resolution(db, case, status, changed_by="user", reason="t", confidence=0.9, commit=False)
    db.commit()
    return case


def _run(coro):
    return asyncio.run(coro)


def test_exactly_seven_per_language_with_replacement(db_session):
    case = _case(db_session)
    gen = ScriptedGen({"en": EN, "de": DE, "fa": FA, "ar": AR, "es": ES, "it": IT, "fr": FR, "pt": PT, "tr": TR},
                      bad={EN[0]: {"spoiler_risk": 0.9}})
    out = _run(A.generate_case_titles(db_session, case, gen))
    for lang in ("en", "de", "fa", "ar"):
        assert out["languages"][lang]["eligible"] == 7 and out["languages"][lang]["short_by"] == 0
        rows = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language=lang)
        assert rows.filter(CaseTitleCandidate.status != "rejected").count() == 7
    en_rows = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, language="en").all()
    rejected = [r for r in en_rows if r.status == "rejected"]
    assert any(r.title == EN[0] and "spoiler" in r.rejection_reason for r in rejected)
    assert len({r.title_family_id for r in db_session.query(CaseTitleCandidate).all()
                if r.case_id == case.id}) == 1
    best = [json.loads(r.critic_json) for r in en_rows if r.status != "rejected"]
    assert sum(1 for b in best if b["recommended"]) == 1
    assert "native_title_critic" in gen.calls and gen.calls.count("native_title_critic") == 8


def test_shortfall_is_reported_not_padded(db_session):
    case = _case(db_session, "Celle shortfall")
    gen = ScriptedGen({"en": EN[:3]})
    out = _run(A.generate_case_titles(db_session, case, gen, languages=["en"]))
    assert out["languages"]["en"]["eligible"] == 3 and out["languages"]["en"]["short_by"] == 4


def test_rejected_title_is_not_regenerated(db_session):
    case = _case(db_session, "Celle history")
    gen = ScriptedGen({"en": EN}, bad={EN[0]: {"specificity": 0.1}})
    _run(A.generate_case_titles(db_session, case, gen, languages=["en"]))
    gen2 = ScriptedGen({"en": [EN[0].lower(), "The Brass Ferry Bell"]})
    row = _run(A.evaluate_manual(db_session, case, "en", EN[0].lower(), gen2))
    assert row.status == "rejected" and "previously rejected" in row.rejection_reason


def test_one_recorded_generate_payload_deduplicates_rejected_titles(db_session):
    case = _case(db_session, "Celle recorded payload")
    verdict = A.Verdict("The Same Title", reasons=["not case-specific"])
    first = A._store(db_session, case, "en", verdict, round_no=1, family="TF_one",
                     concept="one", origin="generated")
    second = A._store(db_session, case, "en", verdict, round_no=2, family="TF_one",
                      concept="one", origin="generated")
    db_session.commit()
    assert first.id == second.id
    assert db_session.query(CaseTitleCandidate).filter_by(case_id=case.id).count() == 1


@pytest.mark.parametrize("title,language,reason", [
    ("The Dark Secret", "en", "generic"),
    ("A Very Long Title That Keeps On Going Forever", "en", "too long"),
    ("Single", "en", "too short"),
    ("THE MISSING GIRL", "en", "ALL CAPS"),
    ("Where Did Rita Go?", "en", "question"),
    ("Episode 12 The Lamp", "en", "episode number"),
    ("Das dunkle Geheimnis", "de", "generic"),
    ("راز تاریک", "fa", "generic"),
    ("The Silent Orchard", "fa", "Arabic script"),
    ("Rad Khamoosh", "ar", "Arabic script"),
])
def test_deterministic_gates(title, language, reason):
    assert reason in (G.run_gates(title, language, {"claim_limits": {}}) or "")


def test_specific_title_passes_and_generic_is_penalized():
    ctx = {"claim_limits": {}, "canonical_case_name": "Celle lantern"}
    assert G.run_gates("The Lantern Of Celle", "en", ctx) is None
    assert G.run_gates("The Final Night", "en", ctx)
    assert G.brevity_score("The Lantern Of Celle Burned All Night Long") > G.brevity_score("The Lantern")
    assert G.brevity_score("The Lantern Of Celle Burned All Night Long") > G.brevity_score(" ".join(["Lantern"] * 20))


def test_spoiler_and_epistemic_gates():
    ctx = {"reveal_terms": ["brother", "basement"],
           "claim_limits": {"may_state_guilt": False}}
    assert "withheld" in G.run_gates("The Basement Door", "en", ctx)
    assert G.run_gates("The Lantern Door", "en", ctx) is None
    assert "not established" in G.run_gates("The Father Who Killed Them", "en", ctx)
    assert "not established" in G.run_gates("Der Mörder von Celle", "de", ctx)
    assert "not established" in G.run_gates("قاتل سله", "fa", ctx)
    assert "not established" in G.run_gates("القاتل الصامت", "ar", ctx)
    assert G.run_gates("The Father Who Disappeared With Them", "en", ctx) is None
    solved = {**ctx, "claim_limits": {"may_state_guilt": True}}
    assert G.run_gates("The Killer Next Door", "en", solved) is None


def test_non_native_copy_is_rejected():
    assert "not native" in G.run_gates("Harbor Light", "de", {"claim_limits": {}},
                                       {"en": ["Harbor Light"]})


def test_native_critic_rejects_literal_translation(db_session):
    case = _case(db_session, "Celle native")
    lit = DE[0]
    gen = ScriptedGen({"en": EN, "de": DE},
                      bad={lit: {"literal_translation": True}})
    _run(A.generate_case_titles(db_session, case, gen, languages=["en", "de"]))
    row = db_session.query(CaseTitleCandidate).filter_by(case_id=case.id, title=lit).one()
    assert row.status == "rejected" and "literal translation" in row.rejection_reason


def test_context_hides_nothing_public_and_holds_back_late_reveals(db_session):
    case = _case(db_session, "Celle context")
    f1 = Fact(case_id=case.id, claim="Rita Haas vanished from Celle in 1994", confidence=0.9)
    f2 = Fact(case_id=case.id, claim="Her brother Jonas hid a ledger in the cellar", confidence=0.9)
    db_session.add_all([f1, f2])
    sv = StoryVersion(case_id=case.id, version=1, narrative_angle="{}", story_text="x",
                      engagement_score=0.5)
    db_session.add(sv)
    db_session.flush()
    bp = {"beats": [{"id": "B01", "reveals": ["F001"]}, {"id": "B02", "reveals": []},
                    {"id": "B03", "purpose": "reveal", "reveals": ["F002"]}]}
    db_session.add(EditorialBlueprint(case_id=case.id, story_version_id=sv.id, status="valid",
                                      blueprint_json=json.dumps(bp),
                                      central_question="What happened to Rita?"))
    db_session.commit()
    ctx = build_context(db_session, case)
    assert "ledger" in ctx["reveal_terms"] and "cellar" in ctx["reveal_terms"]
    assert "celle" not in ctx["reveal_terms"] and "vanished" not in ctx["reveal_terms"]
    assert ctx["claim_limits"]["may_state_guilt"] is False
    assert ctx["resolution_status"] == "unsolved"
    assert G.run_gates("The Ledger Door", "en", ctx)


def test_approval_writes_identity_and_manual_titles_are_rechecked(db_session):
    case = _case(db_session, "Celle approve")
    gen = ScriptedGen({"en": EN, "de": DE})
    _run(A.generate_case_titles(db_session, case, gen, languages=["en", "de"]))
    de = db_session.query(CaseTitleCandidate).filter_by(
        case_id=case.id, language="de").filter(CaseTitleCandidate.status == "candidate").first()
    ident = A.approve_candidate(db_session, case, de.id)
    assert ident.editorial_title == de.title and ident.title_family_id == de.title_family_id
    assert ident.youtube_title.endswith("(Ungelöst) | Fallspur") and "Folge" not in ident.youtube_title
    # a manual title goes through gates + collision + critics before approval
    bad = _run(A.evaluate_manual(db_session, case, "en", "The Killer Next Door", gen))
    assert bad.status == "rejected" and "not established" in bad.rejection_reason
    with pytest.raises(A.ApprovalRefused):
        A.approve_candidate(db_session, case, bad.id)
    T.mark_published(db_session, ident)
    other = db_session.query(CaseTitleCandidate).filter(
        CaseTitleCandidate.case_id == case.id, CaseTitleCandidate.language == "de",
        CaseTitleCandidate.status == "candidate").first()
    with pytest.raises(T.TitleLocked):
        A.approve_candidate(db_session, case, other.id)
    assert A.approve_candidate(db_session, case, other.id, revise=True).title_version == 2


def test_api_flow(client, db_session, monkeypatch):
    import app.naming.api as N

    case = _case(db_session, "Celle api")
    gen = ScriptedGen({"en": EN, "de": DE, "fa": FA, "ar": AR, "es": ES, "it": IT, "fr": FR, "pt": PT, "tr": TR})
    monkeypatch.setattr(N, "_gen", lambda: gen)
    monkeypatch.setattr(N, "_embedder", lambda: None)
    started = client.post(f"/api/cases/{case.id}/naming/generate", json={}).json()
    assert started["status"] == "queued" and started["job_id"]
    r = client.get(f"/api/jobs/{started['job_id']}").json()
    assert r["status"] == "done"
    assert all(r["result"]["languages"][l]["eligible"] == 7 for l in ("en", "de", "fa", "ar"))
    state = client.get(f"/api/cases/{case.id}/naming").json()
    assert state["externally_verified"] is False and state["title_family_id"]
    fa = state["languages"]["fa"]["candidates"]
    assert len(fa) == 7 and fa[0]["recommended"] and fa[0]["collision_status"] == "clear"
    ok = client.post(f"/api/cases/{case.id}/naming/{fa[0]['id']}/approve").json()
    assert ok["youtube_title"].endswith("(حل‌نشده) | رد خاموش")


def test_all_production_languages_are_titled_in_a_fixed_order():
    assert A.title_languages(None) == ["en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"]
    assert A.title_languages(["tr", "es", "en"]) == ["en", "es", "tr"]
    assert A.title_languages(["xx"]) == []


@pytest.mark.parametrize("title,language,blocked", [
    ("El asesino de la carretera", "es", True), ("Lo que se sabe del caso Hale", "es", False),
    ("L'assassino di Via Roma", "it", True), ("Il caso della villa sul lago", "it", False),
    ("Le meurtrier de la rue Caron", "fr", True), ("Disparition à Lyon", "fr", False),
    ("Quem matou Anna Berg", "pt", True), ("O desaparecimento de Lena Voss", "pt", False),
    ("Katillerin Peşinde", "tr", True), ("Zeynep Kaya Dosyası", "tr", False)])
def test_a_title_that_names_a_killer_is_refused_in_every_new_language(title, language, blocked):
    assert bool(G.gate_epistemic(title, language, {})) is blocked


def test_the_script_gate_asks_arabic_script_only_of_the_right_to_left_languages():
    assert G.gate_script("El caso Hale", "es") is None and G.gate_script("Zeynep Kaya Dosyası", "tr") is None
    assert "Arabic script" in G.gate_script("قضية هيل", "es")
    assert G.gate_script("قضية هيل", "ar") is None and G.gate_script("Hale case", "ar")


# ---------------------------------------------------------------- the rules of a title that says what happened (the mechanics above run with short fixtures)


@pytest.fixture()
def real_rules(monkeypatch):
    from app.core.ai_config import ai_config

    monkeypatch.undo()
    return ai_config.case_naming


@pytest.mark.parametrize("title,language,fits", [
    ("She Called Police at 2 AM. Hours Later, She Was Missing", "en", True),
    ("A Man Was Found Dead in His Hotel Room. What Happened?", "en", True),
    ("Um 2 Uhr rief sie die Polizei – Stunden später war sie verschwunden", "de", True),
    ("The Midnight Mystery", "en", False),
    ("She Called Police at 2 AM and Hours Later She Was Missing From Her Home After a Strange Night", "en", False)])
def test_a_title_may_say_what_happened_in_a_sentence_or_two(real_rules, title, language, fits):
    assert (G.gate_length(title, language) is None) is fits and (G.gate_style(title, language) is None) is True


def test_only_a_bare_question_is_clickbait_a_statement_before_it_is_fine(real_rules):
    assert G.gate_style("What Happened to the Woman in Room 348?", "en") and "bare question" in G.gate_style("What Happened to the Woman in Room 348?", "en")
    assert G.gate_style("A Man Was Found Dead in His Hotel Room. What Happened?", "en") is None
    assert G.gate_style("ساعت ۲ بامداد زنگ زد؛ بعد چه شد؟", "fa") is None


def test_no_title_is_longer_than_what_youtube_leaves_once_status_and_channel_are_added(real_rules):
    from app.identity.titles import build_youtube_title, title_budget

    for language in ("en", "de", "fa", "ar", "es", "it", "fr", "pt", "tr"):
        title = "x" * title_budget(language)
        assert "…" not in build_youtube_title(title, "UNSOLVED", language) and G.gate_length("word " * 4 + "x" * (title_budget(language) - 20), language) is None
        assert G.gate_length("word " * 4 + "x" * (title_budget(language) - 18), language)


def test_the_critic_rejects_an_abstract_title_and_the_ranking_prefers_clear_over_short(real_rules):
    scores = {"clarity": 0.9, "curiosity": 0.7, "specificity": 0.8, "memorability": 0.5, "documentary_tone": 0.8}
    vague = {**scores, "clarity": 0.2}
    long_clear = "She Called Police at 2 AM. Hours Later, She Was Missing"
    assert A.rank_score(scores, long_clear) > A.rank_score(vague, "Her Classroom His Pulpit")
    assert A.rank_score(scores, long_clear) > A.rank_score({**scores, "clarity": 0.7}, "The Lantern Burned")
    leaning = {**scores, "spoiler_risk": 0.45, "epistemic_risk": 0.1}
    assert A.rank_score({**scores, "spoiler_risk": 0.05}, long_clear) > A.rank_score(leaning, long_clear)


def test_the_title_writer_sees_the_opening_of_the_story_and_never_the_findings_of_the_investigation(db_session):
    from test_chapters import _setup

    from app.naming.context import build_context, naming_view

    case, _, _ = _setup(db_session)
    view = naming_view(build_context(db_session, case))
    assert view["opening"] == ["A car at the lake at dawn."]  # one fifth of five beats
    assert not {"central_question", "editorial_thesis", "contradictions", "hold_back", "reveal_terms", "timeline", "key_facts", "summary"} & set(view)
    assert "arrest" not in json.dumps(view, ensure_ascii=False).lower()


@pytest.mark.parametrize("language,title,blocked", [
    ("en", "Her Husband Said an Alleged Intruder Shot Her", True), ("en", "He Told 911 Someone Had Broken In and Shot His Wife", False),
    ("de", "Lehrerin im Schlaf erschossen, angeblich von einem Einbrecher", True), ("de", "Lehrerin im Schlaf erschossen: Der Ehemann rief den Notruf", False),
    ("es", "Caso Ashley Flynn: un supuesto intruso y una esposa muerta", True), ("fr", "Caleb Flynn affirme qu'un cambrioleur a tiré sur sa femme", True),
    ("pt", "Professora é baleada dormindo e marido culpa invasor", True), ("tr", "Öğretmen yatağında vuruldu, kocası hırsız girdiğini söyledi", True),
    ("ar", "هل دخل لص منزل أشلي فلين فعلًا؟ مكالمة طوارئ في تروي", True), ("it", "Caso Ashley Flynn: il marito parla di un'effrazione notturna", True)])
def test_a_title_states_what_was_reported_without_a_word_that_casts_doubt_on_it(language, title, blocked):
    assert (G.gate_hint(title, language) is not None) is blocked


def test_quotation_marks_that_wrap_the_whole_title_go_a_quotation_inside_it_stays():
    assert A.unquote("«Qualcuno ha sparato a mia moglie»: la chiamata di Caleb Flynn al 911") == "«Qualcuno ha sparato a mia moglie»: la chiamata di Caleb Flynn al 911"
    assert A.unquote('"She Called Police at 2 AM"') == "She Called Police at 2 AM" and A.unquote("«Der Anruf um 2 Uhr»") == "Der Anruf um 2 Uhr" and A.unquote("  Plain title ") == "Plain title"
