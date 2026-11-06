"""The language of the toolkit's cards: a card of a film is written in the film's language and in nothing else.

A card is foreign to its film when it was written in another language (a builder makes it from the English claims, a timeline
from the graph's English labels) or, in a language written in its own script, when it holds Latin words (a name or phrase left
untranslated). The card makers are told such a card is a problem and write it again; a card already stored that is foreign is
isolated (kept, read by nobody) so the toolkit makes another one in its place."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.documentary import names as NS
from app.documentary.detective import dispatch as D
from app.documentary.mvp.evidence import collect_texts

CHECKED = ("fact_card", "recap", "conflict", "evidence_check", "forensic", "timeline")


def script_problem(kind: str, data: dict, language: str) -> str | None:
    """Why the text of a card cannot be shown in a film of this language, or None: Latin words in a language of its own script."""
    if not NS.uses_native_script(language):
        return None
    found = [w for holder, key in collect_texts(data, kind) for w in NS.LATIN_WORD.findall(str(holder[key]))]
    return f"Latin words {found[:4]}: write every name and word in the language's own script" if found else None


def reason_of(db: Session, action, language: str) -> str | None:
    """Why this stored card is not in the film's language, or None."""
    if action.tool_id not in CHECKED:
        return None
    data = D.build_data(db, action)
    if data is None:
        return None
    written = D.data_language_of(action)
    if written is None and action.tool_id == "timeline" and language != D.SOURCE_LANGUAGE:
        written = D.SOURCE_LANGUAGE          # a timeline card stored before the language was kept holds the graph's English labels
    if written not in (None, language):
        return f"written in {written}, the film is in {language}"
    return script_problem(action.tool_id, data, language)


def isolate_foreign_cards(db: Session, graph_id: int, language: str) -> list[dict]:
    """Isolate every stored card of the graph that is not in the film's language: [{action_key, kind, why}]."""
    isolated = []
    for action in D.actions_for_graph(db, graph_id):
        why = reason_of(db, action, language)
        if why:
            isolated.append({"action_key": action.action_key, "kind": action.tool_id, "why": why})
            D.isolate(action, why)
    db.commit()
    return isolated

