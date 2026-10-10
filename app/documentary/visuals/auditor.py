"""Visual auditor (the gate before render): nothing goes on screen
without "approved".

For every photo and clip in a language's production script the auditor
asks one question with the picture in front of it: may THIS be shown
while THESE words are spoken? It sees the exact narration sentences
(English, the language-independent plan) — not a search query — so a
pet cannot pass for "the police dog", a cheerful stock photo cannot sit
under a disappearance, a different person cannot pass for a named one.
A clip is judged as video by the video auditor (video_auditor.py): all
its frames in order, every frame must pass.

Rules, in order:
  1. The asset itself must be verified (an unverified one — e.g. a fresh
     upload — is vision-checked first; needs_review or rejected never
     goes on screen).
  2. The placement must be approved by the auditor (role visual_auditor,
     independent of the visual director that chose it).
  3. A rejected shot is replaced by the next unused, verified picture or
     clip of that moment — a clip before a photo of the same tier — and
     the replacement is audited again; at most visual_audit.max_redos
     replacements per shot.
  4. Still nothing approved: the previous picture holds (if it may), or
     the next one comes early, or a short black pause — the wrong
     picture is never shown. Each such shot is listed as left out.
  5. Approved as "symbolic" → the shot carries the "symbolic image"
     label (role illustration).

Verdicts are stored (VisualAudit) per asset file + exact sentences, so
the other languages reuse them and a changed picture or changed words
are audited again.
"""

from __future__ import annotations


from app.core.prompts import prompt

from typing import TYPE_CHECKING
import hashlib
import json
import logging

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import (
    Case,
    EditorialBlueprint,
    ProductionScript,
    VisualAsset,
    VisualPlan,
)
from app.documentary.visuals import rights as R
from app.documentary.visuals import spoilers as SP
from app.documentary.visuals.usage import (
    UsageTracker,
    annotate,
    asset_facts,
    named_between,
)

log = logging.getLogger(__name__)

AUDITOR_SYSTEM = prompt("documentary/visuals/auditor/auditor_system")

AUDITED_KINDS = ("image", "video")


if TYPE_CHECKING:
    from app.agents.visual_auditor import VisualAuditor

def _loads(text, default):
    try:
        return json.loads(text) if text else default
    except (TypeError, ValueError):
        return default


def plan_sentences(plan: dict) -> dict[tuple[str, int], str]:
    """(beat_id, n) -> English sentence of the language-independent plan."""
    out = {}
    for b in plan.get("beats") or []:
        for k, sn in enumerate(b.get("sentences") or []):
            out[(b.get("beat_id"), sn.get("n", k))] = sn.get("text") or ""
    return out


def shot_sentences(shot: dict, spans: list[dict], texts: dict) -> list[str]:
    """The plan sentences spoken while the shot is on screen (in order)."""
    out = []
    for sp in spans or []:
        if sp["start"] < shot["end"] - 0.05 and sp.get("end", sp["start"]) > shot["start"] + 0.05:
            t = texts.get((sp.get("beat_id"), sp.get("n")))
            if t and t not in out:
                out.append(t)
    return out


def audit_key(asset: VisualAsset, sentences: list[str], story: dict | None = None) -> str:
    """One verdict per picture (its exact file, for a piece its window),
    words and point in the story (what is still to be revealed)."""
    window = [asset.clip_start, asset.clip_end] if asset.asset_type == "video" else None
    payload = json.dumps([asset.asset_code, asset.sha256 or "", sentences,
                          (story or {}).get("told_later") or [], window], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def decide(v: dict) -> tuple[str, str | None, list[str]]:
    """(verdict, shown_as, reasons) from the auditor's JSON — strict:
    anything but an explicit, consistent approval is a rejection."""
    reasons = [str(r)[:300] for r in (v.get("reasons") or []) if r][:6]
    shown_as = v.get("as") if v.get("as") in ("evidence", "context", "symbolic") else None
    try:
        fit = float(v.get("fits_words"))
    except (TypeError, ValueError):
        fit = 0.0
    problems = []
    if v.get("verdict") != "approved":
        problems.append("auditor rejected it")
    for flag, why in (("specific_kind_ok", "not the specific kind of thing named"),
                      ("tone_ok", "tone clashes with the narration"),
                      ("person_ok", "not clearly the person named"),
                      ("spoiler_free", "gives away what the story tells only later")):
        if v.get(flag) is False:
            problems.append(why)
    if fit < ai_config.visual_audit.min_fit:
        problems.append(f"fits the words only {fit:.2f}")
    if shown_as is None:
        problems.append("no approved role")
    if problems:
        # the auditor's own words first (what it saw), then the failed checks
        return "rejected", shown_as, reasons + problems
    return "approved", shown_as, reasons


def _picture(asset: VisualAsset):
    from app.documentary.visuals.verification import image_for_check

    return image_for_check(asset)


# ---------------------------------------------------------------------------
# the script pass
# ---------------------------------------------------------------------------


def _replacement_pool(script: dict, shot: dict, assets: dict[str, VisualAsset],
                      sentence_entities: list[dict], exclude: set[str]) -> list[str]:
    """What may replace a rejected shot: the beat's checked candidates
    first, then verified case assets showing an entity named while the
    shot is on screen — photos and clips only, rights allowed, never
    beyond the reveal firewall."""
    fwj = script.get("firewall") or {}
    fw = SP.Firewall.from_json(fwj)
    beat = shot.get("beat_id")
    names = named_between(sentence_entities, shot["start"], shot["end"])
    pool = list((script.get("candidates") or {}).get(beat) or [])
    pool += [a.get("asset_id") for a in shot.get("alternatives") or [] if a.get("asset_id")]
    if names:
        pool += sorted(c for c, a in assets.items()
                       if set(_loads(a.entities_json, [])) & names)

    need = shot["end"] - shot["start"]

    def long_enough(a):
        if a.asset_type != "video":
            return True
        lo = float(a.clip_start or 0.0)
        hi = a.clip_end if a.clip_end is not None else a.duration_seconds
        return hi is not None and float(hi) - lo >= need - 0.5

    def ok(c):
        a = assets.get(c)
        return (a is not None and c not in exclude and a.asset_type in ("photo", "video")
                and a.verification_status == "verified" and R.allowed(a.rights_status)
                and not fw.blocks(beat, a) and long_enough(a))

    return [c for c in dict.fromkeys(pool) if ok(c)]


def _use(shot: dict, a: VisualAsset, reason: str) -> None:
    from app.documentary.production.script import _asset_info, _clip_info

    for k in ("reframe", "clip_start", "clip_end", "map_paths", "place", "marker",
              "repeat_reason", "repeat_justified", "fill_reason", "black_filled",
              "highlight", "audit"):
        shot.pop(k, None)
    shot.update(_asset_info(a))
    if a.asset_type == "video":
        shot.update(_clip_info(a))
    else:
        shot.update({"kind": "image", "command": "NEW_IMAGE"})
        if shot.get("motion") in ("CROP_FOCUS", "CONTINUE", "NONE", "MAP_ZOOM",
                                  "DOCUMENT_HIGHLIGHT", None):
            shot["motion"] = "SLOW_PUSH"
    shot["fill_reason"] = reason


def _leave_out_options(shots: list[dict], i: int) -> list[tuple[str, dict, float, float]]:
    """Ways to cover shot i without a new picture: (what, neighbour,
    new start, new end) — the previous picture holds longer, or the next
    one comes early (within the hold limit; a clip only while it runs)."""
    s = shots[i]
    limit = ai_config.motion.max_hold_seconds
    out = []
    prev = shots[i - 1] if i > 0 else None
    nxt = shots[i + 1] if i + 1 < len(shots) else None

    def fits(sh, start, end):
        if (sh.get("audit") or {}).get("verdict") == "rejected":
            return False
        if sh.get("kind") not in ("image", "map", "document", "video"):
            return False
        if end - start > limit:
            return False
        if sh.get("kind") == "video" and sh.get("clip_end") is not None:
            return end - start <= float(sh["clip_end"]) - float(sh.get("clip_start") or 0) + 0.5
        return True

    if prev is not None and fits(prev, prev["start"], s["end"]):
        out.append(("held the previous picture", prev, prev["start"], s["end"]))
    if nxt is not None and nxt.get("kind") != "video" and fits(nxt, s["start"], nxt["end"]):
        out.append(("the next picture comes early", nxt, s["start"], nxt["end"]))
    return out


def _black(s: dict) -> None:
    for k in ("asset_id", "path", "clip_start", "clip_end", "focus", "credit", "role"):
        s.pop(k, None)
    s.update({"kind": "black", "command": "BLACK_SCREEN", "motion": "NONE"})


async def audit_script(db: Session, row: ProductionScript, auditor: "VisualAuditor | None" = None
                       ) -> dict:
    """Audit (and repair) one language's production script. Writes the
    approved script back and the report to row.audit_json."""
    case = db.get(Case, row.case_id)
    script = _loads(row.script_json, {})
    bp_row = db.get(EditorialBlueprint, row.blueprint_id) if row.blueprint_id else None
    blueprint = _loads(bp_row.blueprint_json, {}) if bp_row else {}
    firewall = SP.Firewall.from_json(script.get("firewall"))
    if not firewall.reveals:  # scripts composed before the claim firewall
        firewall.reveals = {b: set(v) for b, v in SP.reveal_blocks(blueprint).items()}
        script["firewall"] = firewall.as_json()
    plan_row = db.get(VisualPlan, row.visual_plan_id) if row.visual_plan_id else None
    plan = _loads(plan_row.plan_json, {}) if plan_row else {}
    texts = plan_sentences(plan)
    spans = script.get("sentence_entities") or []
    assets = {a.asset_code: a for a in db.query(VisualAsset).filter(
        VisualAsset.case_id == row.case_id).all()}
    from app.agents.visual_auditor import VisualAuditor

    auditor = auditor or VisualAuditor(verify=_default_verify(plan_row))
    shots = script.get("shots") or []
    redos = ai_config.visual_audit.max_redos
    report = {"status": "approved", "audited": 0, "approved": 0, "replaced": [],
              "left_out": [], "symbolic": [], "max_redos": redos}
    rejected_for: dict[int, set[str]] = {}

    i = 0
    while i < len(shots):
        s = shots[i]
        if s.get("kind") not in AUDITED_KINDS or not s.get("asset_id"):
            i += 1
            continue
        sentences = shot_sentences(s, spans, texts)
        story = SP.story_point(blueprint, s.get("beat_id"))
        tries = 0
        approved = False
        while True:
            a = assets.get(s.get("asset_id"))
            if a is None:
                verdict, shown_as, reasons = "rejected", None, ["unknown picture"]
            else:
                verdict, shown_as, reasons = await auditor.check(db, case, a, sentences,
                                                                 story, firewall)
            report["audited"] += 1
            s["audit"] = {"verdict": verdict, "as": shown_as, "reasons": reasons[:4],
                          "sentences": sentences, "try": tries}
            if verdict == "approved":
                approved = True
                break
            key = id(s)
            rejected_for.setdefault(key, set()).add(s.get("asset_id"))
            if tries >= redos:
                break
            # the next approved-to-be candidate: unused, a clip first
            tr = UsageTracker.from_shots(shots, {c: asset_facts(x) for c, x in assets.items()},
                                         skip={i})
            pool = _replacement_pool(script, s, assets, spans, rejected_for[key])
            near = {x.get("asset_id") for x in shots[max(0, i - 2):i + 3]} - {s.get("asset_id")}
            choice = tr.pick(pool, s["start"], s["end"],
                             named_between(spans, s["start"], s["end"]), avoid=near,
                             allow_repeat=False,
                             prefer=lambda c: 0 if assets[c].asset_type == "video" else 1)
            if choice is None:
                break
            before = s.get("asset_id")
            _use(s, assets[choice["asset_id"]], f"auditor: replaced {before} ({reasons[0] if reasons else 'rejected'})")
            report["replaced"].append({"at": round(s["start"], 2), "from": before,
                                       "to": choice["asset_id"], "why": reasons[:3]})
            tries += 1
        if approved:
            report["approved"] += 1
            if shown_as == "symbolic":
                s["role"] = "illustration"
                report["symbolic"].append(s["asset_id"])
            i += 1
            continue
        # no approved picture: a neighbour covers the moment — audited
        # again for the words it now runs under — or a black pause; the
        # rejected picture is never shown
        what = None
        for label, nb, start, end in _leave_out_options(shots, i):
            if nb.get("kind") in AUDITED_KINDS and nb.get("asset_id") in assets:
                # the neighbour now also runs at this point of the story
                v2, _as, _why = await auditor.check(
                    db, case, assets[nb["asset_id"]],
                    shot_sentences({"start": start, "end": end}, spans, texts),
                    SP.story_point(blueprint, s.get("beat_id")), firewall)
                report["audited"] += 1
                if v2 != "approved":
                    continue
            nb["start"], nb["end"] = start, end
            shots.pop(i)
            what = label
            break
        if what is None:
            _black(s)
            what = "black pause (no approved picture for these words)"
            i += 1
        report["left_out"].append({"at": round(s["start"], 2),
                                   "rejected": sorted(rejected_for.get(id(s), set())),
                                   "why": (s.get("audit") or {}).get("reasons", [])[:3],
                                   "done": what, "sentences": sentences})

    for n, sh in enumerate(shots):
        sh["index"] = n
    script["shots"] = shots
    from app.documentary.production.script import credit_overlays, label_overlays

    dur = float(script.get("duration") or (shots[-1]["end"] if shots else 0))
    script["overlays"] = sorted(
        [o for o in script.get("overlays") or [] if o["kind"] not in ("credit", "label")]
        + credit_overlays(shots, dur) + label_overlays(shots, script.get("language") or "en", dur),
        key=lambda o: o["start"])
    annotate(shots, {c: asset_facts(a) for c, a in assets.items()}, spans)
    row.script_json = json.dumps(script, ensure_ascii=False)
    row.audit_json = json.dumps(report, ensure_ascii=False)
    row.status = "audited"
    from app.documentary.visuals.usage import record_media_usage

    record_media_usage(db, row, script)
    db.commit()
    return report


def _default_verify(plan_row: VisualPlan | None):
    """Vision-check unverified assets with the case's entity list."""
    async def verify(db: Session, case: Case, assets: list[VisualAsset]) -> None:
        from app.documentary.jobs import verify_assets

        ents = (_loads(plan_row.requirements_json, {}) if plan_row else {}).get("entities") or []
        await verify_assets(db, case, assets, ents)

    return verify


def audit_summary(report: dict) -> dict:
    """The stage detail: counts, plus a degraded note when something had
    to be left out."""
    out = {"audited": report.get("audited"), "approved": report.get("approved"),
           "replaced": len(report.get("replaced") or []),
           "left_out": len(report.get("left_out") or []),
           "symbolic": len(report.get("symbolic") or [])}
    if report.get("left_out"):
        out["degraded"] = (f"{len(report['left_out'])} moment(s) without an approved picture "
                           "(held, moved or black)")
    return out


def __getattr__(name: str):
    # the agent class lives in app/agents/visual_auditor.py (imported when first asked for)
    if name == "VisualAuditor":
        from app.agents.visual_auditor import VisualAuditor

        return VisualAuditor
    raise AttributeError(name)
