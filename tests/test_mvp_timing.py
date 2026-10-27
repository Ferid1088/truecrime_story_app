"""Story timing: a picture or clip is shown from the beat that has told what it shows, never before."""
import asyncio
import json
from uuid import uuid4

from app.db.models import Case, VisualAsset
from app.documentary.mvp import direct as D
from app.documentary.mvp import timing as TM
from app.documentary.visuals.spoilers import Firewall

BLUEPRINT = {"beats": [{"id": f"B{n:02d}", "summary": f"summary {n}"} for n in range(1, 31)]}
ORDER = {b["id"]: i for i, b in enumerate(BLUEPRINT["beats"])}
COMPLAINT = ("A scanned criminal complaint from Miami County Municipal Court naming the defendant, charged with "
             "murder of his wife")


def _case(db):
    case = Case(canonical_title="Timing proof", slug=f"tm-{uuid4().hex[:8]}", language="en")
    db.add(case)
    db.commit()
    return case


def _asset(db, case, code="A1", description=COMPLAINT, sha="s1"):
    asset = VisualAsset(case_id=case.id, asset_code=code, asset_type="photo", title=code, sha256=sha,
                        description=description, local_path=f"{code}.jpg", relevance_tier=2, asset_role="evidence")
    db.add(asset)
    db.commit()
    return asset


def label(asset, phase, beat, sha="s1"):
    spec = json.loads(asset.spec_json or "{}")
    spec["timing"] = {"version": 1, "sha256": sha, "phase": phase, "not_before": beat, "why": "test"}
    asset.spec_json = json.dumps(spec)


def test_a_charge_document_is_not_shown_before_the_beat_that_tells_of_the_charge(db_session):
    asset = _asset(db_session, _case(db_session))
    label(asset, "arrest_charge", "B21")
    assert TM.blocks(asset, ORDER, "B05") and TM.blocks(asset, ORDER, "B14") and TM.blocks(asset, ORDER, "B20")
    assert not TM.blocks(asset, ORDER, "B21") and not TM.blocks(asset, ORDER, "B30")


def test_the_planner_and_the_script_both_refuse_it_early(db_session):
    asset = _asset(db_session, _case(db_session))
    label(asset, "arrest_charge", "B21")
    assert D.reveal_blocked(None, None, ORDER, "B05", asset) and not D.reveal_blocked(None, None, ORDER, "B22", asset)
    firewall = Firewall(order=list(ORDER))
    assert "tells only later" in firewall.why("B05", asset) and firewall.why("B25", asset) is None
    assert Firewall.from_json(firewall.as_json()).blocks("B05", asset)


def test_a_background_picture_and_an_unlabelled_one_are_never_held_back(db_session):
    case = _case(db_session)
    family, unlabelled = _asset(db_session, case, "A2"), _asset(db_session, case, "A3")
    label(family, "background", "any")
    assert not TM.blocks(family, ORDER, "B01") and not TM.blocks(unlabelled, ORDER, "B01")


def test_a_label_made_for_another_file_or_version_is_not_used(db_session):
    asset = _asset(db_session, _case(db_session))
    label(asset, "arrest_charge", "B21", sha="other-file")
    assert TM.stored(asset) is None and not TM.blocks(asset, ORDER, "B01")


def test_only_labels_that_follow_the_rules_are_kept():
    beats = set(ORDER)
    assert TM.valid({"phase": "trial", "not_before": "B25", "why": "x"}, beats).not_before == "B25"
    assert TM.valid({"phase": "trial", "not_before": "B99"}, beats) is None
    assert TM.valid({"phase": "nonsense", "not_before": "B25"}, beats) is None
    assert TM.valid({"phase": "trial", "not_before": "any"}, beats) is None       # a trial picture is never "any time"
    assert TM.valid({"phase": "background", "not_before": "any"}, beats).not_before == "any"


def test_the_classifier_labels_each_asset_once_and_asks_again_only_for_the_unlabelled(db_session):
    case = _case(db_session)
    one, two = _asset(db_session, case, "A1"), _asset(db_session, case, "A2", "Family photo in front of a tree")
    asked = []

    async def asker(payload):
        asked.append([a["asset"] for a in payload["assets"]])
        answers = {"A1": {"asset": "A1", "phase": "arrest_charge", "not_before": "B21", "why": "complaint"},
                   "A2": {"asset": "A2", "phase": "trial", "not_before": "any"}}      # the second answer breaks a rule
        return {"assets": [answers[a["asset"]] for a in payload["assets"]]}

    first = TM.TimingClassifier(db_session, case, BLUEPRINT, asker=asker)
    out = asyncio.run(first.run([one, two]))
    assert out["labelled"] == 1 and out["failed"] == 1 and TM.stored(one).not_before == "B21" and TM.stored(two) is None
    again = TM.TimingClassifier(db_session, case, BLUEPRINT, asker=asker)
    asyncio.run(again.run([one, two]))
    assert asked == [["A1", "A2"], ["A2"]]
