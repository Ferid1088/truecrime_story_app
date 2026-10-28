"""The deterministic half of checklist row C3: from a persisted detective plan, did the film pay off what it
opened and leave the viewer room to breathe (Section C, "question payoffs, meaningful connections, human
interest, and breathing room follow the text")?

Everything here reads rows the Detective Director already persisted (questions and their knowledge events,
scenes and their ordered actions); no model call. The Film Experience Reviewer judges the same things as an
agent; this makes the structural part checkable and repeatable.

* `question_payoffs`: each question's last state. Answered, qualified (partial / reframed), or left open by
  the text itself are all honest endings; a question that was opened and never developed is forgotten.
* `returns_without_reason`: a claim or question shown again by a later action must say why (`reuse_reason`),
  which separates a purposeful return from redundant replay.
* `dense_runs`: more than `pacing.max_consecutive_dense_actions` actions in a row that all ask the viewer to
  read, with no real picture, place or quiet hold between them."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import DetectiveQuestion, DetectiveScene, DetectiveToolAction
from app.documentary.detective.dispatch import binding_keys
from app.documentary.memory import replay_state

_QUALIFIED = ("partial", "reframed")
_LEFT_OPEN = "explicitly_unresolved"


def question_payoffs(db: Session, graph_id: int) -> list[dict]:
    final = replay_state(db, graph_id)["questions"]
    out = []
    for q in db.query(DetectiveQuestion).filter_by(graph_id=graph_id).order_by(DetectiveQuestion.id).all():
        record = final.get(q.question_key)
        status = record["state"].get("status", "open") if record else "open"
        if status == "answered":
            ending = "answered"
        elif status in _QUALIFIED:
            ending = "qualified"
        elif status == _LEFT_OPEN or q.kind == "explicit_unresolved":
            ending = "left_open_by_text"
        else:
            ending = "forgotten"
        out.append({"question_key": q.question_key, "text": q.text, "kind": q.kind, "status": status,
                    "ending": ending, "events": len(record["history"]) if record else 0})
    return out


def _ordered_actions(db: Session, graph_id: int) -> list[tuple[DetectiveScene, DetectiveToolAction | None]]:
    """Every action in film order; a scene with no action appears once, as None (a quiet hold)."""
    flat = []
    scenes = db.query(DetectiveScene).filter_by(graph_id=graph_id).order_by(DetectiveScene.scene_order).all()
    for scene in scenes:
        actions = (db.query(DetectiveToolAction).filter_by(scene_id=scene.id)
                   .order_by(DetectiveToolAction.order_index).all())
        if actions:
            flat.extend((scene, a) for a in actions)
        else:
            flat.append((scene, None))
    return flat


def returns_without_reason(db: Session, graph_id: int) -> list[dict]:
    first_shown: dict[str, str] = {}
    out = []
    for _scene, action in _ordered_actions(db, graph_id):
        if action is None:
            continue
        # a flat binding list holds IDs of every kind, so either lookup returns all of them
        keys = list(dict.fromkeys(binding_keys(action, "claim_key") + binding_keys(action, "question_key")))
        again = [k for k in keys if k in first_shown]
        if again and not (action.reuse_reason or "").strip():
            out.append({"action_key": action.action_key, "returns": again,
                        "first_shown_by": sorted({first_shown[k] for k in again})})
        for k in keys:
            first_shown.setdefault(k, action.action_key)
    return out


def dense_runs(db: Session, graph_id: int) -> list[dict]:
    cfg = ai_config.detective_toolkit.pacing
    runs, current = [], []
    for _scene, action in _ordered_actions(db, graph_id):
        if action is None or action.tool_id in cfg.breathing_tools:
            runs.append(current)
            current = []
        else:
            current.append(action.action_key)
    runs.append(current)
    return [{"actions": r, "length": len(r)} for r in runs if len(r) > cfg.max_consecutive_dense_actions]


def c3_report(db: Session, graph_id: int) -> dict:
    payoffs = question_payoffs(db, graph_id)
    returns = returns_without_reason(db, graph_id)
    runs = dense_runs(db, graph_id)
    findings = (
        [{"kind": "forgotten_question", "question_key": p["question_key"],
          "problem": f"{p['text']!r} was opened and never answered, qualified, or left open by the text"}
         for p in payoffs if p["ending"] == "forgotten"]
        + [{"kind": "return_without_reason", "action_key": r["action_key"],
            "problem": f"shows {r['returns']} again (first shown by {r['first_shown_by']}) with no reuse_reason"}
           for r in returns]
        + [{"kind": "dense_run", "actions": r["actions"],
            "problem": f"{r['length']} text-dense actions in a row with no picture, place, or quiet hold"}
           for r in runs])
    return {"question_payoffs": payoffs, "returns_without_reason": returns, "dense_runs": runs,
            "findings": findings}
