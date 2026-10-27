"""The planner's alternatives for a moment with no approved picture: the graphics of docs/alternative_graphics.md
built only from stored evidence (facts, contradictions, sources, the chapter plan, the story's open questions),
each offered with the beats it may appear in, and the placement rules that put them into bare stretches.

Every offer names the evidence it comes from (fact ids), appears at most once in the film (graphics that are not
pictures are never repeated), and never shows anything before the story has told it."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.documentary.graphics import dates
from app.documentary.graphics import shots as GS
from app.documentary.graphics import theme as T
from app.documentary.graphics.base import GraphicUnavailable
from app.documentary.graphics.document import verify_verbatim
from app.documentary.graphics.engines import kit
from app.documentary.graphics.keyfacts import extract
from app.documentary.graphics.registry import graphic
from app.documentary.mvp import headlines as HL
from app.documentary.mvp.copy import words


@dataclass
class Offer:
    kind: str
    key: str
    first: int                       # earliest beat index it may appear in
    last: int                        # latest beat index (it drifts at most this far from where it belongs)
    best: int                        # the beat it belongs to
    data: dict
    seconds: float
    min_seconds: float
    items: frozenset[str] = frozenset()
    credit: str | None = None
    placed: bool = False
    meta: dict = field(default_factory=dict)      # what the planner's inspector shows (engines)


@dataclass
class Evidence:
    """Everything the offers are built from, gathered once per film."""

    order: dict[str, int]
    beats: list[dict]
    questions: list[dict]
    facts: dict[str, dict]
    contradictions: list[dict]
    sources: dict[int, dict]
    events: list[dict]
    quotes: list[dict]
    narration: dict[str, str]
    starts: dict[str, float]
    total_seconds: float
    official: dict[int, dict] = field(default_factory=dict)   # source id -> official source with its stored text
    notes: list[str] = field(default_factory=list)
    timeline: list[dict] = field(default_factory=list)       # dated timeline entries of the case (id, event_date, claim)


def seconds_for(kind: str, count: int = 0) -> tuple[float, float]:
    cfg = T.config()
    if kind == "timeline":
        p = cfg.planner["timeline"]
        return (p["base_seconds"] + p["seconds_per_event"] * count,
                p["min_base_seconds"] + p["min_seconds_per_event"] * count)
    return cfg.hold_seconds[kind], cfg.planner["min_seconds"][kind]


def drift(kind: str) -> int:
    return int(T.config().planner["drift_beats"][kind])


def reveal_index(ev: Evidence, fact_id: str) -> int | None:
    idx = [ev.order[b["id"]] for b in ev.beats if fact_id in (b.get("reveals") or []) and b["id"] in ev.order]
    return min(idx) if idx else None


def offer(kind: str, key: str, data: dict, best: int, first: int | None = None, items: tuple[str, ...] = (),
          count: int = 0, credit: str | None = None, seconds: float | None = None) -> Offer | None:
    """An offer if the graphic accepts its data (with the cues the planner would give it at its normal length)."""
    default, minimum = seconds_for(kind, count)
    seconds = seconds or default
    try:                                   # drawn once at its full length: a page that cannot be set is never offered
        GS.render_for({"type": kind, "data": data, "cue_fractions": GS.cue_fractions(kind, graphic(kind).required_cues(data))},
                      seconds, "en")
    except GraphicUnavailable:
        return None
    return Offer(kind, key, first if first is not None else best, best + drift(kind), best, data, seconds, minimum,
                 frozenset(items), credit)


# -- builders ---------------------------------------------------------------


def timeline_offers(ev: Evidence, lines: dict[str, str]) -> list[Offer]:
    p = T.config().planner
    events = []
    for e in ev.events:
        label = (e.get("label") or {}).get("en") or ""
        label = label if label and len(label) <= p["copy"]["event_label_chars"] else lines.get(e["id"], "")
        parsed = _event_date(e.get("date"))
        if label and parsed and e.get("first_beat") in ev.order:
            events.append({"date": parsed, "label": label, "fact_id": e["id"],
                           "beat": ev.order[e["first_beat"]]})
    events.sort(key=lambda x: (x["beat"], x["date"]["year"], x["date"].get("month") or 0, x["date"].get("day") or 0))
    out: list[Offer] = []
    size = p["timeline"]["max_events_per_card"]
    for start in range(0, len(events), size):
        group = events[start:start + size]
        data = {"events": [{k: v for k, v in e.items() if k != "beat"} for e in group]}
        best = max(e["beat"] for e in group)
        made = offer("timeline", f"timeline|{group[0]['fact_id']}", data, best,
                     items=tuple(e["fact_id"] for e in group), count=len(group))
        if made:
            out.append(made)
    return out


def _event_date(value) -> dict | None:
    m = re.match(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$", str(value or ""))
    if not m:
        return None
    parsed = {"year": int(m.group(1))}
    if m.group(2):
        parsed["month"] = int(m.group(2))
    if m.group(3):
        parsed["day"] = int(m.group(3))
    return parsed if dates.valid(parsed) else None


def best_beat(ev: Evidence, text: str, minimum: int = 3) -> int | None:
    """The beat whose narration shares the most words with `text` (None if no beat shares enough)."""
    target = words(text)
    scored = [(len(target & words(ev.narration.get(b["id"], "") + " " + (b.get("summary") or ""))), -ev.order[b["id"]])
              for b in ev.beats if b["id"] in ev.order]
    if not scored:
        return None
    top = max(scored)
    return -top[1] if top[0] >= minimum else None


def conflict_offers(ev: Evidence, structured: list[dict]) -> list[Offer]:
    out = []
    for n, c in enumerate(structured, 1):
        best = best_beat(ev, c["topic"] + " " + c["description"])
        if best is None:
            ev.notes.append(f"conflict {c['id']}: no beat tells it, not offered")
            continue
        data = {"conflict": {"id": c["id"], "number": n, "title": c["title"], "kind": c["kind"], "status": "open",
                             "summary": c["summary"]},
                "sides": [{k: s[k] for k in ("who", "claim", "source", "fact_id")} for s in c["sides"]]}
        made = offer("conflict", f"conflict|{c['id']}", data, best, items=(c["id"],))
        if made:
            out.append(made)
    return out


def quote_offers(ev: Evidence) -> list[Offer]:
    """Verified quotations, complete ones only (a cut-off quotation is never shown), the fullest wording per fact."""
    fullest: dict[str, dict] = {}
    for q in ev.quotes:
        text = " ".join(q["text"].split())
        if text[-1:] not in ".?!\u201d\"'" and not q["fact_id"].startswith("S"):
            continue
        if q["fact_id"] not in fullest or len(text) > len(fullest[q["fact_id"]]["text"]):
            fullest[q["fact_id"]] = {**q, "text": text}
    out, seen = [], set()
    for q in fullest.values():
        text = q["text"]
        beat = reveal_index(ev, q["fact_id"]) if q.get("beat") is None else ev.order.get(q["beat"])
        if beat is None or text.lower() in seen:
            continue
        seen.add(text.lower())
        made = document_offer(ev, q, beat) or offer(
            "quote", f"quote|{q['fact_id']}",
            {"text": text, "fact_id": q["fact_id"], **({"speaker": q["speaker"]} if q.get("speaker") else {})},
            beat, items=(q["fact_id"],))
        if made:
            out.append(made)
    return out


def document_offer(ev: Evidence, quote: dict, beat: int) -> Offer | None:
    """A quotation that stands word for word in an official source (a court record) is shown as that document's
    page: its type, issuer and date from the stored source, the quote checked against the stored text."""
    for sid, src in ev.official.items():
        if verify_verbatim(src["text"], quote["text"]):
            parsed = HL.parse_date(src.get("published_at"))
            data = {"document": {"id": sid, "type": src["type"], "issuer": src["issuer"], "language": src["language"],
                                 **({"date": " ".join(dates.display(parsed, "en")).strip()} if parsed else {})},
                    "quote": {"text": quote["text"], "fact_id": quote["fact_id"], "verified": True},
                    "context_before": [], "context_after": [], "redactions": []}
            return offer("document", f"document|{quote['fact_id']}", data, beat, items=(quote["fact_id"],))
    return None


def key_fact_offers(ev: Evidence, lines: dict[str, str]) -> list[Offer]:
    cfg = T.config().planner["key_facts"]
    per = cfg["per_card"]
    by_beat: dict[int, list[dict]] = {}
    for fid, fact in ev.facts.items():
        if fact.get("disputed") or fact.get("uncertain") or (fact.get("confidence") or 0) < cfg["min_confidence"]:
            continue
        figure = extract(fact["claim"])
        beat = reveal_index(ev, fid)
        line = lines.get(fid)
        if not figure or beat is None or not line or figure["value"].lower() not in line.lower():
            continue
        by_beat.setdefault(beat, []).append({"value": figure["value"], "caption": line, "fact_id": fid})
    out = []
    for beat, group in sorted(by_beat.items()):
        for start in range(0, len(group), per):
            chunk = group[start:start + per]
            made = offer("key_facts", f"key_facts|{chunk[0]['fact_id']}", {"facts": chunk}, beat,
                         items=tuple(c["fact_id"] for c in chunk), count=len(chunk))
            if made:
                out.append(made)
    return out


def headline_offers(ev: Evidence, placed: dict[str, dict]) -> list[Offer]:
    """`placed`: headline key -> {"earliest": beat id, "best": beat id} from the placement agent."""
    out = []
    for data in HL.candidates(ev.sources):
        where = placed.get(HL.placement_key(data))
        if not where or where.get("earliest") not in ev.order or where.get("best") not in ev.order:
            continue
        best = max(ev.order[where["best"]], ev.order[where["earliest"]])
        made = offer("headline", f"headline|{data['fact_id']}", data, best,
                     items=(data["fact_id"],), credit=f"{data['source']}, {data['url'].split('/')[2]}")
        if made:
            out.append(made)
    return out


def similar(a: str, b: str, share: float = 0.6) -> bool:
    wa, wb = words(a), words(b)
    return bool(wa and wb) and len(wa & wb) / min(len(wa), len(wb)) >= share


def recap_offers(ev: Evidence, lines: dict[str, str], conflict_best: dict[str, int],
                 exclude: set[str]) -> list[Offer]:
    """A recap every few beats: only facts, disputes and questions the story has told by then, each item once."""
    p = T.config().planner
    every = int(p["recap"]["every_beats"])
    cfg = T.config().recap["max_items"]
    ranked = {"strong_primary": 0, "strong_secondary": 1, "documented": 1}
    anchors = list(range(every - 1, len(ev.beats) - 1, every))
    used_facts: set[str] = set(exclude)
    used_conflicts: set[str] = set()
    used_questions: set[str] = set()
    out = []
    previous = -1
    for anchor in anchors:
        beat = ev.beats[anchor]
        known = [f for f in beat.get("viewer_knows") or [] if f in ev.facts and f not in used_facts
                 and not ev.facts[f].get("disputed") and not ev.facts[f].get("uncertain")
                 and ev.facts[f].get("evidence_strength") in p["confirmed_strengths"] and lines.get(f)]
        recent = [f for f in known if (reveal_index(ev, f) or 0) > previous]
        pool = sorted(recent or known, key=lambda f: (ranked.get(ev.facts[f]["evidence_strength"], 2),
                                                      -(ev.facts[f].get("confidence") or 0)))
        confirmed: list[str] = []
        for f in pool:
            if len(confirmed) >= min(cfg["confirmed"], p["recap"]["max_facts"]):
                break
            if not any(similar(lines[f], lines[c]) for c in confirmed):      # two facts saying the same count once
                confirmed.append(f)
        disputed = [c for c in ev.contradictions if c["id"] not in used_conflicts and c["id"] in conflict_best
                    and conflict_best[c["id"]] <= anchor and lines.get(c["id"])][:cfg["disputed"]]
        unknown = [q for q in ev.questions if q["id"] not in used_questions and lines.get(q["id"])
                   and ev.order.get(q.get("opened_in"), 99) <= anchor < ev.order.get(q.get("resolved_in"), 999)
                   ][:cfg["unknown"]]
        data = {"confirmed": [{"text": lines[f], "fact_id": f} for f in confirmed],
                "disputed": [{"text": lines[c["id"]], "fact_id": c["id"]} for c in disputed],
                "unknown": [{"text": lines[q["id"]], "fact_id": q["id"]} for q in unknown],
                "progress": {"elapsed_s": int(ev.starts[beat["id"]]), "total_s": int(ev.total_seconds)}}
        previous = anchor
        if sum(len(data[s]) for s in ("confirmed", "disputed", "unknown")) < 2:
            continue
        made = offer("recap", f"recap|{beat['id']}", data, anchor,
                     items=tuple(x["fact_id"] for s in ("confirmed", "disputed", "unknown") for x in data[s]),
                     count=0)
        if made:
            used_facts |= set(confirmed)
            used_conflicts |= {c["id"] for c in disputed}
            used_questions |= {q["id"] for q in unknown}
            out.append(made)
    return out


# -- placement ---------------------------------------------------------------


@dataclass
class State:
    """What has been shown so far in the film."""

    engine_last: dict[str, int] = field(default_factory=dict)      # engine kind -> beat index of its last showing
    engine_kind: str | None = None                                 # the engine shown last
    engine_texts: list[str] = field(default_factory=list)
    engine_count: int = 0
    used_keys: set[str] = field(default_factory=set)
    used_items: set[str] = field(default_factory=set)
    pages: list[dict] = field(default_factory=list)
    seconds: dict[str, float] = field(default_factory=dict)
    placed: int = 0


def offer_text(o: Offer) -> str:
    return " ".join(h[k] for h, k in kit.texts_of(o.data))


def repeats_engine(o: Offer, idx: int, state: State) -> bool:
    """An engine is not shown again too soon, nor with the same story as one already shown."""
    if o.kind not in GS.ENGINE_KINDS:
        return False
    cfg = T.config().engines["selection"]
    if (idx - state.engine_last.get(o.kind, -10**6) < cfg["min_beats_between_same_engine"]
            or state.engine_count >= cfg["max_per_film"]):
        return True
    text = offer_text(o)
    return any(similar(text, other) for other in state.engine_texts)


def usable(o: Offer, idx: int, remaining: float, state: State) -> bool:
    return (not o.placed and o.key not in state.used_keys and o.first <= idx <= o.last
            and o.min_seconds <= remaining + 1e-6 and not (o.items & state.used_items)
            and not repeats_engine(o, idx, state))


def priority(o: Offer, idx: int, state: State | None = None) -> tuple:
    chain = T.config().planner["chain"]
    again = 1 if state is not None and o.kind == state.engine_kind else 0       # another engine than the last one
    return (0 if o.best == idx else 1, again, chain.index(o.kind) if o.kind in chain else len(chain), o.best)


def gap_runs(shots: list[dict], window_len: float) -> list[tuple[int, int, float]]:
    """Consecutive bare stretches of a beat: (first index, last index, seconds)."""
    runs, start = [], None
    for i, s in enumerate(shots + [{"gap": False}]):
        if s.get("gap") and start is None:
            start = i
        elif not s.get("gap") and start is not None:
            runs.append((start, i - 1, sum(x["share"] for x in shots[start:i]) * window_len))
            start = None
    return runs


def graphic_shot(o: Offer, seconds: float, window_len: float, why: str) -> dict:
    shot = {"command": "SHOW_GRAPHIC", "share": seconds / window_len, "motion": "NONE", "speed": 1.0,
            "transition_in": "CROSSFADE", "why": why, **GS.plan_fields(o.kind, o.data)}
    if o.credit:
        shot["credit"] = o.credit
    if o.meta:
        shot["engine"] = o.meta
    return shot


def fill_beat(shots: list[dict], window_len: float, idx: int, offers: list[Offer], state: State) -> None:
    """Put offers into the beat's bare stretches (longest first); what no offer fits stays bare for the next step."""
    minimum_run = T.config().planner["min_run_seconds"]
    for first, last, seconds in sorted(gap_runs(shots, window_len), key=lambda r: -r[2]):
        remaining, taken = seconds, []
        said = " ".join(s.get("said", "") for s in shots[first:last + 1]).strip()
        while remaining >= minimum_run:
            options = sorted((o for o in offers if usable(o, idx, remaining, state)), key=lambda o: priority(o, idx, state))
            if not options:
                break
            chosen = options[0]
            length = min(chosen.seconds, remaining)
            taken.append((chosen, length))
            chosen.placed = True
            state.used_keys.add(chosen.key)
            state.used_items |= chosen.items
            state.seconds[chosen.kind] = state.seconds.get(chosen.kind, 0.0) + length
            state.placed += 1
            if chosen.kind in GS.ENGINE_KINDS:
                state.engine_last[chosen.kind] = idx
                state.engine_kind = chosen.kind
                state.engine_count += 1
                state.engine_texts.append(offer_text(chosen))
            if chosen.kind == "conflict":
                state.pages.append({"order": state.placed, **chosen.data["conflict"], "a": chosen.data["sides"][0]["who"],
                                    "b": chosen.data["sides"][1]["who"]})
            remaining -= length
        if not taken:
            continue
        replacement = [graphic_shot(o, length, window_len, f"{o.kind} from stored evidence ({', '.join(sorted(o.items))[:60]})")
                       for o, length in taken]
        if remaining > 1e-6:
            replacement.append({"command": "BLACK_SCREEN", "share": remaining / window_len, "motion": "NONE",
                                "speed": 1.0, "transition_in": "CROSSFADE", "gap": True, "said": said,
                                "why": "no approved visual for this moment"})
        shots[first:last + 1] = replacement


def board_due(offers: list[Offer], state: State, idx: int) -> bool:
    """True once the last conflict page that can still be placed has been (or can no longer be) shown, and the
    board has not been offered yet."""
    pages = [o for o in offers if o.kind == "conflict"]
    boards = [o for o in offers if o.kind == "conflict_board"]
    return bool(pages) and not boards and len(state.pages) >= 2 and all(o.placed or o.last <= idx for o in pages)


def board_offer(state: State, idx: int) -> Offer | None:
    """The board of every conflict page shown so far (only pages already used, in the order they were used)."""
    if len(state.pages) < 2:
        return None
    cards = [{"id": p["id"], "number": n, "title": p["summary"], "a": p["a"], "b": p["b"], "status": p["status"]}
             for n, p in enumerate(state.pages, 1)][:12]
    data = {"cards": cards, "new_card": cards[-1]["id"]}
    return offer("conflict_board", f"board|{cards[-1]['id']}", data, idx, items=(f"board:{cards[-1]['id']}",))


def seconds_report(state: State) -> dict:
    return {k: round(v, 1) for k, v in sorted(state.seconds.items())}


