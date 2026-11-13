"""The numbers of the cost dashboard, all from the one ledger (provider_calls). "Real" money is what is counted: no test data, no row that only repeats another
row of the same call, and only rows with a price. Every figure also says how much of it is only estimated."""

from __future__ import annotations

import re
from datetime import timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import String, func
from sqlalchemy import case as sql_case
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import FunctionElement

from app.costs import registry as R
from app.db.models import Case, CostInvoice, ShortClip, Video
from app.db.models import ProviderCall as P
from app.services.budget import BudgetService
from app.utils import utc_now

PRICE = func.coalesce(P.cost_eur, 0.0)
ESTIMATED = func.coalesce(func.sum(sql_case((P.accuracy == "estimated", PRICE), else_=0.0)), 0.0)
FAILED = func.coalesce(func.sum(sql_case((P.ok.is_(False), PRICE), else_=0.0)), 0.0)
STEP = func.coalesce(P.step, "other")
LANGUAGE = sql_case((P.language.isnot(None), P.language), (STEP.in_(R.shared_steps()), R.SHARED), else_=R.UNKNOWN_LANGUAGE)
VENDOR = func.coalesce(P.vendor, "other")


class LocalText(FunctionElement):
    """The call's time as text in the display time zone ("2026-11-13 08:15:00"), so days and hours are the owner's, not the database's."""

    type = String()
    name = "local_text"
    inherit_cache = True


@compiles(LocalText)
def _local_text(element, compiler, **kw):
    return f"CAST(timezone('{R.registry()['display_timezone']}', {compiler.process(element.clauses, **kw)}) AS TEXT)"


@compiles(LocalText, "sqlite")
def _local_text_sqlite(element, compiler, **kw):
    return f"CAST({compiler.process(element.clauses, **kw)} AS TEXT)"


DATE_TEXT = LocalText(P.created_at)


class PartText(FunctionElement):
    """What a stage is called before its first colon, in lower case ("Reading: write fa_b01" is "reading"); a language in brackets is taken off afterwards."""

    type = String()
    name = "part_text"
    inherit_cache = True


@compiles(PartText)
def _part_text(element, compiler, **kw):
    return f"lower(btrim(split_part(coalesce({compiler.process(element.clauses, **kw)}, '-'), ':', 1)))"


@compiles(PartText, "sqlite")
def _part_text_sqlite(element, compiler, **kw):
    text = f"coalesce({compiler.process(element.clauses, **kw)}, '-')"
    return f"lower(trim(CASE WHEN instr({text}, ':') > 0 THEN substr({text}, 1, instr({text}, ':') - 1) ELSE {text} END))"


PART = PartText(P.stage)
MONTH = func.substr(DATE_TEXT, 1, 7)
DIMENSIONS = {"step": STEP, "language": LANGUAGE, "vendor": VENDOR, "model": func.coalesce(P.model, "-"), "subject": func.coalesce(P.subject, "-"),
              "accuracy": func.coalesce(P.accuracy, "none"), "day": func.substr(DATE_TEXT, 1, 10), "case": P.case_id}


def local_now():
    return utc_now().astimezone(ZoneInfo(R.registry()["display_timezone"]))


def counted(q):
    return q.filter(P.is_test_data.is_(False), P.duplicate_of.is_(None), P.cost_eur.isnot(None))


def scoped(q, case_id: int | None, filters: dict | None = None):
    if case_id is not None:
        q = q.filter(P.case_id == case_id)
    for name, value in (filters or {}).items():
        if value in (None, ""):
            continue
        expression = DIMENSIONS[name]
        q = q.filter(expression == (int(value) if name == "case" else value))
    return q


def _round(x) -> float:
    return round(float(x or 0.0), 4)


def grouped(db: Session, case_id: int | None, by: str, filters: dict | None = None, limit: int = 200) -> list[dict]:
    """The real money by one dimension (step, language, vendor, model, subject, accuracy, day, case), most expensive first (a day: oldest first)."""
    key = DIMENSIONS[by]
    rows = (scoped(counted(db.query(key, func.sum(PRICE), ESTIMATED, FAILED, func.count(P.id), func.sum(P.tokens_in), func.sum(P.tokens_out))), case_id, filters)
            .group_by(key).order_by(key if by == "day" else func.sum(PRICE).desc()).limit(limit).all())
    return [{"key": k, "label": _label(by, k), "eur": _round(eur), "estimated_eur": _round(est), "failed_eur": _round(failed), "calls": n, "tokens_in": tin, "tokens_out": tout}
            for k, eur, est, failed, n, tin, tout in rows]


def _label(by: str, key) -> str:
    if by == "step":
        return R.step_labels().get(key, str(key))
    if by == "vendor":
        return R.vendor_labels().get(key, "Other")
    if by == "language":
        return {R.SHARED: "Shared (all languages)", R.UNKNOWN_LANGUAGE: "Language unknown"}.get(key, str(key))
    return str(key)


def totals(db: Session, case_id: int | None = None) -> dict:
    """Real money, what is only estimated, what was left out as a repeat, and the ledger's gross (everything the budget counter adds up)."""
    real, est, n = counted(db.query(func.sum(PRICE), ESTIMATED, func.count(P.id))).filter(*([P.case_id == case_id] if case_id is not None else [])).one()
    base = db.query(func.sum(PRICE)).filter(P.is_test_data.is_(False), *([P.case_id == case_id] if case_id is not None else []))
    gross = base.scalar()
    repeats = base.filter(P.duplicate_of.isnot(None)).scalar()
    unlabelled = db.query(func.count(P.id)).filter(P.attribution.is_(None), *([P.case_id == case_id] if case_id is not None else [])).scalar()
    return {"real_eur": _round(real), "estimated_eur": _round(est), "gross_eur": _round(gross), "repeats_eur": _round(repeats), "priced_calls": n, "unlabelled_rows": unlabelled}


def budget_of(db: Session, case_id: int) -> dict | None:
    case = db.get(Case, case_id)
    if case is None:
        return None
    snap = BudgetService(db).snapshot(case_id)
    return {"cap_eur": snap.cap_eur, "counter_eur": snap.spent_eur, "remaining_eur": snap.remaining_eur, "baseline_eur": _round(case.budget_spend_baseline_eur), "status": snap.status}


def overview(db: Session) -> dict:
    cases = {c.id: c for c in db.query(Case).all()}
    per_case = []
    for row in grouped(db, None, "case", limit=500):
        case = cases.get(row["key"])
        per_case.append({**row, "case_id": row["key"], "title": case.canonical_title if case else ("(no case)" if row["key"] is None else f"Case {row['key']} (deleted)"), "exists": case is not None})
    since = local_now() - timedelta(days=30)
    daily = [r for r in grouped(db, None, "day", limit=400) if r["key"] >= since.strftime("%Y-%m-%d")]
    return {"totals": totals(db), "by_vendor": grouped(db, None, "vendor"), "by_step": grouped(db, None, "step"), "by_language": grouped(db, None, "language"),
            "cases": per_case, "daily": daily, "accuracy": grouped(db, None, "accuracy")}


def languages_of(db: Session, case_id: int, rows: list[dict]) -> list[str]:
    configured = R.registry()["languages"]
    present = {r["key"] for r in rows if r["key"] not in (R.SHARED, R.UNKNOWN_LANGUAGE)}
    present |= {v for (v,) in db.query(Video.language).filter(Video.case_id == case_id).distinct()}
    return [x for x in configured if x in present] + sorted(present - set(configured))


def matrix(db: Session, case_id: int, allocation: str = "none") -> dict:
    """Step by language: money by cell, a column for the shared work and one for rows without a known language; with allocation "even" the shared money is also spread
    evenly over the languages (a share, not a fact)."""
    cells = (counted(db.query(STEP, LANGUAGE, func.sum(PRICE), ESTIMATED, func.count(P.id))).filter(P.case_id == case_id).group_by(STEP, LANGUAGE).all())
    languages = languages_of(db, case_id, [{"key": lang} for _s, lang, *_ in cells])
    columns = languages + [R.SHARED, R.UNKNOWN_LANGUAGE]
    by_step: dict[str, dict] = {}
    for step, lang, eur, est, n in cells:
        row = by_step.setdefault(step, {"step": step, "label": R.step_labels().get(step, step), "cells": {}})
        row["cells"][lang] = {"eur": _round(eur), "estimated_eur": _round(est), "calls": n}
    order = [s["key"] for s in R.registry()["steps"]]
    rows = sorted(by_step.values(), key=lambda r: order.index(r["step"]) if r["step"] in order else 99)
    share = 1 / len(languages) if allocation == "even" and languages else 0.0
    for row in rows:
        shared = row["cells"].get(R.SHARED, {"eur": 0.0, "estimated_eur": 0.0, "calls": 0})
        if share:
            for lang in languages:
                cell = row["cells"].setdefault(lang, {"eur": 0.0, "estimated_eur": 0.0, "calls": 0})
                cell["allocated_eur"] = _round(shared["eur"] * share)
        row["total_eur"] = _round(sum(c["eur"] for c in row["cells"].values()))
        row["total_estimated_eur"] = _round(sum(c["estimated_eur"] for c in row["cells"].values()))
    column_totals = {}
    for col in columns:
        column_totals[col] = {"eur": _round(sum(r["cells"].get(col, {}).get("eur", 0.0) for r in rows)),
                              "allocated_eur": _round(sum(r["cells"].get(col, {}).get("allocated_eur", 0.0) for r in rows)),
                              "estimated_eur": _round(sum(r["cells"].get(col, {}).get("estimated_eur", 0.0) for r in rows))}
    return {"allocation": allocation, "languages": languages, "columns": columns, "labels": {c: _label("language", c) for c in columns}, "rows": rows, "column_totals": column_totals,
            "total_eur": _round(sum(r["total_eur"] for r in rows)), "total_estimated_eur": _round(sum(r["total_estimated_eur"] for r in rows))}


def vendor_matrix(db: Session, case_id: int) -> dict:
    cells = (counted(db.query(VENDOR, LANGUAGE, func.sum(PRICE), ESTIMATED, func.count(P.id))).filter(P.case_id == case_id)
             .group_by(VENDOR, LANGUAGE).all())
    languages = languages_of(db, case_id, [{"key": lang} for _v, lang, *_ in cells])
    columns = languages + [R.SHARED, R.UNKNOWN_LANGUAGE]
    rows: dict[str, dict] = {}
    for vendor, lang, eur, est, n in cells:
        row = rows.setdefault(vendor, {"vendor": vendor, "label": R.vendor_labels().get(vendor, "Other"), "cells": {}})
        row["cells"][lang] = {"eur": _round(eur), "estimated_eur": _round(est), "calls": n}
    out = sorted(rows.values(), key=lambda r: -sum(c["eur"] for c in r["cells"].values()))
    for row in out:
        row["total_eur"] = _round(sum(c["eur"] for c in row["cells"].values()))
        row["total_estimated_eur"] = _round(sum(c["estimated_eur"] for c in row["cells"].values()))
    return {"languages": languages, "columns": columns, "labels": {c: _label("language", c) for c in columns}, "rows": out}


def call_row(p: P) -> dict:
    return {"id": p.id, "created_at": p.created_at, "step": p.step, "language": p.language, "vendor": p.vendor, "host": p.host, "model": p.model, "stage": p.stage, "subject": p.subject,
            "units": p.units, "tokens_in": p.tokens_in, "tokens_out": p.tokens_out, "tokens_cached": p.tokens_cached, "cost_eur": p.cost_eur, "accuracy": p.accuracy,
            "cost_source": p.cost_source, "ok": p.ok, "error": p.error, "request_id": p.request_id, "duplicate_of": p.duplicate_of, "attribution": p.attribution, "duration_ms": p.duration_ms}


def calls(db: Session, case_id: int | None, filters: dict, limit: int = 100, offset: int = 0) -> dict:
    """The single paid calls behind a figure (the repeats too, marked, so the sum can be checked by hand), the most expensive first."""
    q = scoped(db.query(P).filter(P.is_test_data.is_(False), P.cost_eur.isnot(None)), case_id, filters)
    total = q.count()
    rows = q.order_by(P.cost_eur.desc(), P.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "calls": [call_row(r) for r in rows]}


def live(db: Session, case_id: int | None, after_id: int | None = None, limit: int = 40) -> dict:
    q = db.query(P).filter(P.is_test_data.is_(False), P.cost_eur.isnot(None))
    if case_id is not None:
        q = q.filter(P.case_id == case_id)
    if after_id is not None:
        q = q.filter(P.id > after_id)
    rows = q.order_by(P.id.desc()).limit(limit).all()
    now = utc_now()

    def spent(minutes: int) -> float:
        return _round(counted(db.query(func.sum(PRICE))).filter(P.created_at >= now - timedelta(minutes=minutes), *([P.case_id == case_id] if case_id is not None else [])).scalar())

    last5, last60 = spent(5), spent(60)
    return {"calls": [call_row(r) for r in rows], "eur_per_minute": _round(last5 / 5), "last_5_min_eur": last5, "last_hour_eur": last60, "last_id": rows[0].id if rows else after_id}


def analysis(db: Session, case_id: int) -> dict:
    """What is worth looking at: the pace and the days left to the cap, the price of a finished film minute per language, what failed or was repeated, the dearest calls and
    models, the share of the prompt cache and the steps that suddenly cost much more than before."""
    now = utc_now()
    daily = grouped(db, case_id, "day", limit=60)
    recent = [d["eur"] for d in daily[-3:]]
    per_day = sum(recent) / len(recent) if recent else 0.0
    budget = budget_of(db, case_id)
    days_left = round(budget["remaining_eur"] / per_day, 1) if budget and per_day > 0 else None
    mat = matrix(db, case_id, "even")
    minutes = {}
    for video in db.query(Video).filter(Video.case_id == case_id, Video.duration_seconds.isnot(None)).order_by(Video.id).all():
        minutes[video.language] = round(video.duration_seconds / 60, 1)
    per_language = []
    for lang in mat["languages"]:
        col = mat["column_totals"][lang]
        own = col["eur"] + col["allocated_eur"]
        per_language.append({"language": lang, "own_eur": col["eur"], "with_shared_eur": _round(own), "film_minutes": minutes.get(lang), "eur_per_film_minute": _round(own / minutes[lang]) if minutes.get(lang) else None})
    clips = db.query(func.count(ShortClip.id)).filter(ShortClip.case_id == case_id, ShortClip.media_json.like('%"video"%')).scalar() or 0
    by_step = {r["key"]: r["eur"] for r in grouped(db, case_id, "step")}
    short_money = _round(by_step.get("shorts", 0) + by_step.get("covers", 0))
    repair = _round(counted(db.query(func.sum(PRICE))).filter(P.case_id == case_id, P.stage.ilike("%repair%")).scalar())
    cached = db.query(func.sum(P.tokens_cached), func.sum(P.tokens_in)).filter(P.case_id == case_id, P.is_test_data.is_(False), P.duplicate_of.is_(None), P.tokens_in.isnot(None)).one()
    recent_by_step = {r["key"]: r["eur"] for r in grouped(db, case_id, "step", {})}
    day_ago = {r[0]: float(r[1]) for r in counted(db.query(STEP, func.sum(PRICE))).filter(P.case_id == case_id, P.created_at >= now - timedelta(hours=24)).group_by(STEP).all()}
    first = db.query(func.min(P.created_at)).filter(P.case_id == case_id).scalar()
    span_days = max((now - first).total_seconds() / 86400, 1.0) if first else 1.0
    spikes = []
    for step, eur24 in day_ago.items():
        usual = max(recent_by_step.get(step, 0.0) - eur24, 0.0) / span_days
        if eur24 >= 1.0 and eur24 > 3 * usual:
            spikes.append({"step": step, "label": R.step_labels().get(step, step), "last_24h_eur": _round(eur24), "usual_per_day_eur": _round(usual)})
    return {"pace": {"eur_last_24h": _round(sum(day_ago.values())), "eur_per_day_recent": _round(per_day), "days_until_cap": days_left},
            "per_language": per_language, "shorts": {"clips_with_video": clips, "writing_and_covers_eur": short_money},
            "waste": {"failed_calls_eur": grouped_failed(db, case_id), "repair_calls_eur": repair},
            "top_calls": calls(db, case_id, {}, limit=20)["calls"], "models": grouped(db, case_id, "model", limit=15),
            "cache": {"tokens_cached": int(cached[0] or 0), "tokens_in": int(cached[1] or 0), "share": round(float(cached[0] or 0) / float(cached[1]), 3) if cached[1] else None},
            "spikes": spikes, "daily": daily}


def grouped_failed(db: Session, case_id: int) -> float:
    return _round(counted(db.query(func.sum(PRICE))).filter(P.case_id == case_id, P.ok.is_(False)).scalar())


def reconciliation(db: Session) -> list[dict]:
    """The ledger against the bills the owner typed in, by vendor and month (only months that have a bill, and the months the ledger has money for)."""
    month = MONTH
    ledger = {(v, m): (float(e), float(est)) for v, m, e, est in counted(db.query(VENDOR, month, func.sum(PRICE), ESTIMATED)).group_by(VENDOR, month).all()}
    bills = {(i.vendor, i.period): i for i in db.query(CostInvoice).all()}
    out = []
    for key in sorted(set(ledger) | set(bills), key=lambda k: (k[1], k[0]), reverse=True):
        eur, est = ledger.get(key, (0.0, 0.0))
        bill = bills.get(key)
        out.append({"vendor": key[0], "label": R.vendor_labels().get(key[0], "Other"), "period": key[1], "ledger_eur": _round(eur), "ledger_estimated_eur": _round(est),
                    "invoice_id": bill.id if bill else None, "invoice_eur": _round(bill.amount_eur) if bill else None, "note": bill.note if bill else None,
                    "difference_eur": _round(eur - bill.amount_eur) if bill else None})
    return out


def series(db: Session, case_id: int | None, by: str, bucket: str, span: int) -> dict:
    """The real money over the last `span` days or hours (zero where nothing was spent), one line per vendor or per step. A step outside the few the chart gives a colour
    to is added to "Other steps"; a vendor with no colour of its own to "Other"."""
    chart = R.registry()["chart"]
    now = local_now()
    if bucket == "hour":
        end = now.replace(minute=0, second=0, microsecond=0)
        labels = [(end - timedelta(hours=i)).strftime("%Y-%m-%d %H") for i in range(span - 1, -1, -1)]
        start, width = end - timedelta(hours=span - 1), 13
    else:
        end = now.replace(hour=0, minute=0, second=0, microsecond=0)
        labels = [(end - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(span - 1, -1, -1)]
        start, width = end - timedelta(days=span - 1), 10
    when = func.substr(DATE_TEXT, 1, width)
    key = STEP if by == "step" else VENDOR
    rows = (scoped(counted(db.query(when, key, func.sum(PRICE), ESTIMATED)), case_id).filter(P.created_at >= start.astimezone(ZoneInfo("UTC"))).group_by(when, key).all())
    keep = chart["step_series"] if by == "step" else chart["vendor_series"]
    other = "other_steps" if by == "step" else "other"
    folded: dict[str, dict[str, float]] = {}
    for label, k, eur, est in rows:
        into = k if k in keep else other
        cell = folded.setdefault(into, {}).setdefault(label, [0.0, 0.0])
        cell[0] += float(eur or 0.0)
        cell[1] += float(est or 0.0)
    names = R.step_labels() if by == "step" else R.vendor_labels()
    out = []
    for k in [*keep, other]:
        if k not in folded:
            continue
        out.append({"key": k, "label": "Other steps" if k == "other_steps" else names.get(k, "Other"), "values": [_round(folded[k].get(x, [0.0, 0.0])[0]) for x in labels],
                    "estimated": [_round(folded[k].get(x, [0.0, 0.0])[1]) for x in labels]})
    return {"by": by, "bucket": bucket, "timezone": R.registry()["display_timezone"], "labels": labels, "series": out}


def _part_name(part: str) -> str:
    """The name of a part without a language in brackets or the block it was about ("semantic review fa_b03_g02_01" is "semantic review")."""
    return re.sub(r"\s+\S*_b\d+\S*$", "", re.sub(r"\s*\(.*\)\s*$", "", part))


def _parts(found: dict[str, dict]) -> list[dict]:
    """The parts a phase is made of (the agent or stage group that paid), the dearest first; the many tiny ones are added up as "other parts"."""
    ranked = sorted(found.items(), key=lambda kv: -kv[1]["eur"])
    out = [{"key": k, "label": k.replace("_", " ").capitalize(), "eur": _round(v["eur"]), "vendors": {a: _round(b) for a, b in v["vendors"].items() if b},
            "languages": {a: _round(b) for a, b in v["languages"].items() if b}} for k, v in ranked[:12]]
    rest = ranked[12:]
    if rest:
        vendors: dict[str, float] = {}
        languages: dict[str, float] = {}
        for _k, v in rest:
            for a, b in v["vendors"].items():
                vendors[a] = vendors.get(a, 0.0) + b
            for a, b in v["languages"].items():
                languages[a] = languages.get(a, 0.0) + b
        out.append({"key": "_other_parts", "label": "Other parts", "eur": _round(sum(v["eur"] for _k, v in rest)), "vendors": {a: _round(b) for a, b in vendors.items()}, "languages": {a: _round(b) for a, b in languages.items()}})
    return out


def phases(db: Session, case_id: int | None) -> dict:
    """The money by pipeline phase (in the order of the pipeline), split by vendor and by language (and each language by vendor): the figures behind "in which phase did how much go to which vendor"
    and "how do the languages compare phase by phase"."""
    vendors = {(s, v): float(e) for s, v, e in scoped(counted(db.query(STEP, VENDOR, func.sum(PRICE))), case_id).group_by(STEP, VENDOR).all()}
    languages = {(s, lang, v): float(e) for s, lang, v, e in scoped(counted(db.query(STEP, LANGUAGE, VENDOR, func.sum(PRICE))), case_id).group_by(STEP, LANGUAGE, VENDOR).all()}
    estimated = {s: float(e) for s, e in scoped(counted(db.query(STEP, ESTIMATED)), case_id).group_by(STEP).all()}
    pieces: dict[str, dict[str, dict]] = {}
    for s, part, v, lang, e in scoped(counted(db.query(STEP, PART, VENDOR, LANGUAGE, func.sum(PRICE))), case_id).group_by(STEP, PART, VENDOR, LANGUAGE).all():
        cell = pieces.setdefault(s, {}).setdefault(_part_name(part), {"eur": 0.0, "vendors": {}, "languages": {}})
        cell["eur"] += float(e)
        cell["vendors"][v] = cell["vendors"].get(v, 0.0) + float(e)
        cell["languages"][lang] = cell["languages"].get(lang, 0.0) + float(e)
    steps = []
    for step in R.registry()["steps"]:
        key = step["key"]
        by_vendor = {v: _round(e) for (s, v), e in vendors.items() if s == key and e}
        by_language: dict[str, dict[str, float]] = {}
        for (s, lang, v), e in languages.items():
            if s == key and e:
                by_language.setdefault(lang, {})[v] = _round(e)
        if by_vendor:
            steps.append({"key": key, "label": step["label"], "shared": step["shared"], "total_eur": _round(sum(by_vendor.values())), "estimated_eur": _round(estimated.get(key)),
                          "vendors": by_vendor, "languages": by_language, "parts": _parts(pieces.get(key, {}))})
    return {"steps": steps, "languages": R.registry()["languages"], "vendors": [{"key": k, "label": v} for k, v in R.vendor_labels().items()] + [{"key": "other", "label": "Other"}]}
