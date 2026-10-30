"""A small film for the toolkit tests: a graph with narration units, a place and dated events."""
from __future__ import annotations

import json

from app.db.models import NarrationUnit, RevealNode
from app.documentary import memory as MEM
from tests.test_detective_section_d import _graph

SENTENCES = {
    "B01": ["A man called 911 at night.", "He said someone had shot his wife."],
    "B02": ["Officers found two casings near the bed.", "The garage door stood open."],
    "B03": ["The sisters were nine and eleven.", "They had slept through it."],
    "B04": ["The gun was never found.", "Prosecutors said he hid it."],
    "B05": ["Months later a letter arrived.", "It named a second person."],
}


def film(db_session, beats=("B01", "B02", "B03", "B04", "B05"), repeat_words=150):
    """Five beats of two sentences each (each long enough to hold a card), a place, and two dated events."""
    graph, _, _ = _graph(db_session, beats=[{"id": b} for b in beats])
    order = 0
    for beat in beats:
        for n, text in enumerate(SENTENCES[beat], 1):
            MEM.add_narration_unit(db_session, graph.id, f"{beat}-{n}", beat, order, "en",
                                   text + " " + " ".join(["and"] * repeat_words))
            order += 1
    db_session.add(RevealNode(graph_id=graph.id, node_key="loc1", label="Cunningham Court, Tipp City, Ohio",
                              category="place", first_revealed_beat_id="B01", first_revealed_order=0,
                              aliases_json="[]", metadata_json="{}"))
    for key, label, beat, date in (("T001", "He called 911", "B01", "2026-02-15"), ("T002", "Officers found the casings",
                                                                                 "B02", "2026-02-15"),
                                   ("T003", "A letter arrived", "B05", "2026-06")):
        db_session.add(RevealNode(graph_id=graph.id, node_key=key, label=label, category="timeline",
                                  first_revealed_beat_id=beat, first_revealed_order=int(key[-1]), aliases_json="[]",
                                  metadata_json=json.dumps({"evidence": {"event_date": date}})))
    db_session.commit()
    units = db_session.query(NarrationUnit).filter_by(graph_id=graph.id).order_by(NarrationUnit.story_order).all()
    return graph, units
