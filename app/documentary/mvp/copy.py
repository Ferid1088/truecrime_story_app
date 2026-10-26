"""The two small text jobs of the alternative graphics, both from stored evidence only and both checked by rules
before anything is shown: one disputed point structured into its two sourced sides, and stored facts shortened into
one faithful line each. Results are kept on the needs plan so every language and every rerun reuses them."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.db.models import Case, VisualPlan
from app.documentary.mvp.needs import fold
from app.providers.generation import get_generation_provider
from app.providers.generation.base import GenerationError
from app.services.tracking import stamp_run, track_run

log = logging.getLogger(__name__)
NUMBER = re.compile(r"\d[\d,.:]*")
STOP = {"the", "and", "was", "were", "that", "his", "her", "with", "for", "from", "this", "had", "has", "not"}


def numbers(text: str) -> set[str]:
    return {m.rstrip(".,:").replace(",", "") for m in NUMBER.findall(text or "")}


def words(text: str) -> set[str]:
    return {w for w in fold(text or "").split() if len(w) > 2 and w not in STOP}


def valid_line(claim: str, line: str, limit: int) -> bool:
    """A shortened line stays within its limit and keeps every figure of the claim it was made from."""
    line = " ".join((line or "").split())
    return bool(line) and len(line) <= limit and numbers(claim) <= numbers(line) and bool(words(claim) & words(line))


def valid_conflict(raw: dict, conflict: dict, sources: dict[int, dict]) -> dict | None:
    """The structured conflict when it follows the rules (two sides taken from the description, each with a source
    from the listed ones, within the graphic's limits), else None."""
    cfg = ai_config.graphics_settings().conflict
    sides = raw.get("sides") or []
    title = " ".join(str(raw.get("title", "")).split())
    summary = " ".join(str(raw.get("summary", "")).split())
    if raw.get("skip") or len(sides) != 2 or raw.get("kind") not in cfg["kinds"]:
        return None
    if not title or len(title) > cfg["title_max_chars"] or not summary or len(summary) > cfg["summary_max_chars"]:
        return None
    known = {int(s) for s in conflict["source_ids"]}
    out = []
    for side in sides:
        claim = " ".join(str(side.get("claim", "")).split())
        who, source = str(side.get("who", "")).strip(), " ".join(str(side.get("source", "")).split())
        ids = [int(i) for i in side.get("source_ids") or [] if str(i).isdigit()]
        if not (who and source and ids) or not claim or len(claim) > cfg["claim_max_chars"] or len(source) > 60:
            return None
        if not set(ids) <= known or len(words(claim) & words(conflict["description"])) < 2:
            return None
        out.append({"who": who[:60], "claim": claim, "source": source, "source_ids": ids, "fact_id": conflict["id"]})
    return {"id": conflict["id"], "title": title, "kind": raw["kind"], "summary": summary, "sides": out,
            "topic": conflict["topic"], "description": conflict["description"]}


INSTRUCTION = {
    "mvp.conflict": "Structure this disputed point as instructed. Reply with the JSON object only.",
    "mvp.copy": "Shorten these facts as instructed. Reply with the JSON object only.",
    "mvp.headline": "Decide where this headline may be shown, as instructed. Reply with the JSON object only.",
}


class Copywriter:
    """Calls the agents; `cache` is the dict kept on the needs plan (read, filled, saved by the caller)."""

    def __init__(self, db: Session, case: Case, cache: dict, generation=None):
        self.db, self.case, self.cache = db, case, cache
        self.generation = generation or get_generation_provider()

    async def _ask(self, agent: str, label: str, payload: dict) -> dict | None:
        """The agent's JSON answer; None when the call failed (a failure is never remembered as an answer)."""
        task = json.dumps(payload, ensure_ascii=False)
        try:
            with track_run(self.db, self.case.id, f"Alternative graphics: {label}", input_summary=label) as run:
                data, res = await asyncio.wait_for(
                    run_agent(agent, self.generation, f"{INSTRUCTION[agent]}\n\n{task}"),
                    ai_config.mvp().call_timeout_s * 2)
                stamp_run(run, res, ai_config.agents[agent].role)
        except (GenerationError, TimeoutError) as e:
            log.warning("%s failed: %s", agent, e)
            return None
        return data or None

    async def lines(self, items: list[dict]) -> dict[str, str]:
        """{id: short line} for [{id, claim, max_chars}]; an id the agent could not shorten faithfully is left out."""
        todo = [i for i in items if f"line|{i['id']}|{i['max_chars']}|{hashlib.sha256(i['claim'].encode()).hexdigest()[:12]}"
                not in self.cache]
        for start in range(0, len(todo), 60):
            batch = todo[start:start + 60]
            answer = await self._ask("mvp.copy", f"{len(batch)} short lines", {"items": batch})
            if answer is None:
                continue
            got = answer.get("lines") or {}
            for item in batch:
                key = f"line|{item['id']}|{item['max_chars']}|{hashlib.sha256(item['claim'].encode()).hexdigest()[:12]}"
                line = " ".join(str(got.get(item["id"], "")).split())
                self.cache[key] = line if valid_line(item["claim"], line, item["max_chars"]) else ""
        return {i["id"]: self.cache[k] for i in items
                if self.cache.get(k := f"line|{i['id']}|{i['max_chars']}|{hashlib.sha256(i['claim'].encode()).hexdigest()[:12]}")}

    async def headline_where(self, data: dict, key: str, beats: list[dict], order: dict[str, int]) -> dict:
        """{"earliest": beat id, "best": beat id} for a real headline, or {} when it should not be shown."""
        if key not in self.cache:
            raw = await self._ask("mvp.headline", data["fact_id"], {
                "headline": data["headline"], "newspaper": data["source"], "published": data["published"],
                "beats": beats})
            if raw is None:
                return {}
            earliest, best = raw.get("earliest_beat"), raw.get("best_beat")
            ok = raw.get("decision") == "use" and earliest in order and best in order and order[best] >= order[earliest]
            self.cache[key] = {"earliest": earliest, "best": best} if ok else {}
        return self.cache[key]

    async def conflicts(self, contradictions: list[dict], sources: dict[int, dict]) -> list[dict]:
        out = []
        for c in contradictions:
            key = f"conflict|{c['id']}|{hashlib.sha256((c['topic'] + c['description']).encode()).hexdigest()[:12]}"
            if key not in self.cache:
                listed = [{"id": i, "publisher": sources[i]["publisher"], "title": sources[i]["title"]}
                          for i in c["source_ids"] if i in sources]
                raw = await self._ask("mvp.conflict", c["id"], {
                    "topic": c["topic"], "description": c["description"], "sources": listed})
                if raw is None:
                    continue
                self.cache[key] = valid_conflict(raw, c, sources) or {}
            if self.cache[key]:
                out.append(self.cache[key])
        return out


def cache_of(plan: VisualPlan) -> dict:
    return json.loads(plan.validation_json or "{}").get("mvp", {}).get("alternatives_cache", {})


def save_cache(db: Session, plan: VisualPlan, cache: dict) -> None:
    validation = json.loads(plan.validation_json or "{}")
    validation.setdefault("mvp", {})["alternatives_cache"] = cache
    plan.validation_json = json.dumps(validation, ensure_ascii=False)
    db.commit()
