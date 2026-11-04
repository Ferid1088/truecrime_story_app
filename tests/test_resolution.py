"""The resolution of words a film reads in more than one way: each rule with its evidence, never frequency; what stays for the owner and why; the
store (kept for the case, applied by the film table below the owner's approval, a resolved name still a name); and the normalisation bugs found on
Case 15 (sa'y is not sa'i, key is not ki, an ayn's vowel and a written ezafe are not an unhyphenated ezafe)."""

from __future__ import annotations

import json

import pytest

from app.db.models import Case, ReadingResolution
from app.documentary.pronunciation_planner import settled_reading
from app.documentary.reading import consistency as CS
from app.documentary.reading import resolution as RES
from app.documentary.reading import stage as RS
from app.documentary.reading.ezafe import EzafeAnalyzer
from app.documentary.reading.table import FilmTable
from app.documentary.reading.words import key_of, tokenize


def family(word: str, candidates: dict[str, int], kind: str = "word") -> RES.Family:
    return RES.Family(key_of(word), kind, {r: [f"b{i}" for i in range(n)] for r, n in candidates.items()}, {key_of(word): []})


def test_each_rule_decides_only_on_its_evidence_and_never_by_frequency():
    found = RES.resolve([
        family("پلیس", {"polis": 1, "pulis": 20}),                          # the minority reading wins: the letters write it
        family("کالب", {"kālb": 31, "kāleb": 115}, kind="name"),
        family("اسمش", {"esmesh": 3, "esmash": 1}),
        family("مرد", {"mard": 3, "mord": 2}),
        family("بررسی", {"barresi": 15, "barrasi": 1}),
        family("شلیک", {"shelik": 17, "shellik": 3}),
    ], {}, {key_of("کالب"): "Caleb"})
    by = {f.key: f for f in found}
    assert (by[key_of("پلیس")].status, by[key_of("پلیس")].rule, by[key_of("پلیس")].reading) == ("resolved", "orthography", "polis")
    assert (by[key_of("کالب")].rule, by[key_of("کالب")].reading) == ("source_syllables", "kāleb") and "2 syllable" in by[key_of("کالب")].evidence
    assert (by[key_of("اسمش")].status, by[key_of("اسمش")].rule) == ("retained_variant", "enclitic")
    assert (by[key_of("مرد")].status, by[key_of("مرد")].rule) == ("retained_variant", "homograph")
    assert by[key_of("بررسی")].status == "unresolved" and "how often" in by[key_of("بررسی")].why_undecided
    assert by[key_of("شلیک")].status == "unresolved", "a doubled consonant is not decided by a rule: Persian has both (bachche, ettefāgh)"


def test_the_owners_approval_decides_first_and_a_homograph_reading_off_its_list_is_not_retained():
    by = {f.key: f for f in RES.resolve([family("پرت", {"prat": 6, "pat": 1}, kind="name"), family("کشتی", {"kashti": 2, "keshti": 1})],
                                        {key_of("پرت"): "pat"}, {})}
    assert (by[key_of("پرت")].rule, by[key_of("پرت")].reading) == ("owner", "pat")
    assert by[key_of("کشتی")].status != "retained_variant" and "keshti is none of its readings" in by[key_of("کشتی")].evidence


def test_the_english_syllables_of_a_source_name():
    assert [RES.english_syllables(n) for n in ("Caleb", "Kate", "Ashley", "Rismiller")] == [2, 1, 2, 3]
    assert RES.sources_of("Caleb Flynn called 911. Ashley was shot.", [family("کالب", {"kālb": 1, "kāleb": 1}, kind="name")]) == {key_of("کالب"): "Caleb"}


def test_normalisation_keeps_what_differs_and_merges_only_notation():
    assert CS.normal("sa'y") != CS.normal("sa'i") and CS.normal("key") != CS.normal("ki"), "a y after e or a is a sound, not an ezafe's carrier"
    assert CS.normal("jāy") == CS.normal("jā") and CS.normal("masnuyi") == CS.normal("masnu'i") and CS.normal("dombāl") == CS.normal("donbāl")


@pytest.mark.parametrize("text, reading, settled", [
    ("بدون هیچ شکی", "bedune", "bedun-e"),                   # a final e no letter writes: an ezafe without its hyphen
    ("موقع رفتن", "moghe", "moghe"),                         # the e is the ayn's vowel
    ("اداره‌ی پلیس", "edāreye", "edāre-ye"),                  # the text writes the ezafe itself: not a second one
    ("خانه ما", "khāne", "khāne"),                           # the word's own ه
])
def test_an_ezafe_written_without_its_hyphen(text, reading, settled):
    words = tokenize(text)
    b = EzafeAnalyzer().analyze(words, [reading] + ["x"] * (len(words) - 1))[0]
    assert settled_reading(reading, b) == settled


def test_a_resolution_reaches_the_table_below_the_owner_and_a_resolved_name_stays_a_name():
    reader = {"people": [{"id": "c", "first": {"fa": "کالب", "latin": "kālb"}, "last": {"fa": "فلین", "latin": "flin"}}], "places": [], "foreign": []}
    table = FilmTable.build(reader, [{"surface": "کالب", "latin": "kāleb", "kind": "resolved_name"}, {"surface": "بررسی", "latin": "barresi", "kind": "resolved"}])
    assert table.fixed_readings(tokenize("کالب فلین بررسی کرد")) == {0: "kāleb", 1: "flin", 2: "barresi"}, "the full name takes the resolved part"
    assert key_of("کالب") in RS.names_of(table) and key_of("بررسی") not in RS.names_of(table)
    owned = FilmTable.build(reader, [{"surface": "کالب", "latin": "kayleb", "kind": "person"}, {"surface": "کالب", "latin": "kāleb", "kind": "resolved_name"}])
    assert owned.fixed_readings(tokenize("کالب آمد")) == {0: "kayleb"}, "the owner outranks a resolution"


def test_a_resolved_name_is_no_longer_unresolved():
    raw = CS.variants({"a": [(tokenize("کالب")[0], "kāleb")], "b": [(tokenize("کالب")[0], "kālb")]}, frozenset({key_of("کالب")}))
    table = FilmTable.build({"people": [{"id": "c", "first": {"fa": "کالب", "latin": "kālb"}}], "places": [], "foreign": []})
    assert CS.unresolved_names(raw, table, {}) and not CS.unresolved_names(raw, table, {key_of("کالب"): "kāleb"})


def test_resolutions_and_the_owners_decision_are_kept_for_the_case(db_session):
    case = Case(canonical_title="Scratch: resolution test", slug=f"scratch-resolution-{id(db_session)}", status="new", language="fa", origin="scratch")
    db_session.add(case)
    db_session.commit()
    try:
        f = RES.resolve([family("پلیس", {"polis": 1, "pulis": 2})], {}, {})[0]
        assert RES.save(db_session, case.id, "fa", [f]) == 1
        row = db_session.query(ReadingResolution).filter_by(case_id=case.id).one()
        assert (row.surface, row.reading, row.rule, json.loads(row.candidates)) == (key_of("پلیس"), "polis", "orthography", {"polis": ["b0"], "pulis": ["b0", "b1"]})
        assert RES.resolved_entries(db_session, case.id, "fa") == [{"surface": key_of("پلیس"), "latin": "polis", "kind": "resolved"}]
        shown = RES.stored_family(RES.stored(db_session, case.id, "fa")[0]).describe()
        assert (shown["word"], shown["status"], shown["rule"], shown["reading"]) == (key_of("پلیس"), "resolved", "orthography", "polis")
        open_family = family("بررسی", {"barresi": 2, "barrasi": 1})
        with pytest.raises(ValueError):
            RES.decide(db_session, case.id, "fa", open_family, "borrosi", "owner")
        RES.decide(db_session, case.id, "fa", open_family, "barresi", "owner")
        decided = db_session.query(ReadingResolution).filter_by(case_id=case.id, surface=key_of("بررسی")).one()
        assert (decided.rule, decided.reading, decided.evidence) == ("owner_decision", "barresi", "the owner (owner) decided")
    finally:
        db_session.query(ReadingResolution).filter_by(case_id=case.id).delete()
        db_session.delete(case)
        db_session.commit()
