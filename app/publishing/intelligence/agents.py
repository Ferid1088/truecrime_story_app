"""The two model calls of the AI publishing plan: the planner (judgment, order, calendar, routing; one call) and the metadata writer (one call per platform). Each call is tracked
(cost ledger, the case's cost cap, the Emergency Stop) by `track_run`; a checked answer that has errors is sent back once with what was wrong, and what is still wrong afterwards is
kept with the result for the owner to see, not hidden."""

from __future__ import annotations

import asyncio
import difflib
import json
from collections.abc import Awaitable, Callable

from sqlalchemy.orm import Session

from app.agents.runner import run_agent
from app.core.ai_config import ai_config
from app.core.prompts import prompt
from app.db.base import SessionLocal
from app.documentary.pipeline.errors import JobPaused
from app.providers.generation import get_generation_provider
from app.publishing.config import GenerationSettings
from app.publishing.intelligence import validate as V
from app.publishing.intelligence.examples import examples_for
from app.publishing.intelligence.inputs import Inputs, critic_view, metadata_view, planner_view
from app.publishing.registry import PublicationRegistry
from app.services.budget import BudgetStopped
from app.services.tracking import stamp_run, track_run

PLANNER, WRITER, CRITIC, FIXER, PROOFREADER = "publishing_planner", "publishing_metadata_writer", "publishing_hook_critic", "publishing_orthography_fixer", "publishing_proofreader"
ZWNJ = "\u200c"
HOOK_SCORES = ("stakes", "gap", "concrete")


def _json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False)


def judge_for(registry: PublicationRegistry, inputs: Inputs, platform: str):
    """The checker of a platform's texts: `judge(kind, item)` lists what is wrong with an item against that platform's limits and the case record."""
    facts = {f["id"] for f in inputs.dossier["facts"]} | set(inputs.dossier["evidence_refs"])

    def judge(kind: str, item: dict) -> list[dict]:
        return V.metadata_findings(item, kind, registry.cfg.intelligence.metadata_limits[f"{platform}/{kind}"], fact_ids=facts, clip_keys=set(inputs.clip_keys),
                                   known_words=inputs.dossier["known_words"], chapter_times=inputs.chapter_times, story=inputs.story, cutoff=inputs.cutoff)

    return judge


PROOF_FIELDS = ("title", "text", "hashtags", "keywords", "cta")
PROOF_TASK = prompt("publishing/proofread_task")


class PlanError(RuntimeError):
    """The plan could not be made (no usable answer after the repair round)."""


class PublishingAI:
    def __init__(self, gen=None):
        self.gen = gen or get_generation_provider()

    async def _ask(self, db: Session, case_id: int, agent: str, label: str, call: Callable[[], Awaitable], runs: list[int]):
        with track_run(db, case_id, label, input_summary=label) as run:
            runs.append(run.id)                                          # a call that fails is still a billed call: it belongs to the run's usage
            raw, res = await call()
            stamp_run(run, res, ai_config.agents[agent].role)
        return raw, res

    async def analyse(self, db: Session, registry: PublicationRegistry, inputs: Inputs, runs: list[int]) -> dict:
        """The planner's judgment, order, calendar and routing for the clips of the case (one call, and one repair call when the answer is malformed)."""
        view = planner_view(registry, inputs)
        platforms = {d.split("/")[0]: [] for d in inputs.destinations}
        for d in inputs.destinations:
            platforms[d.split("/")[0]].append(d.split("/")[1])
        methods = {p: v["routing_methods"] for p, v in view["platforms"].items()}
        extra: dict = {}
        for attempt in range(registry.cfg.intelligence.generation.repairs + 1):
            raw, res = await self._ask(db, inputs.case.id, PLANNER, f"Publishing planner ({inputs.language})",
                                       lambda payload={**view, **extra}: run_agent("publishing_planner", self.gen, _json(payload)), runs)
            problems = V.planner_problems(raw, inputs.clip_keys, platforms)
            if not problems or attempt == registry.cfg.intelligence.generation.repairs:
                break
            extra = {"previous": raw, "problems_to_fix": problems}
        if problems:
            raise PlanError("the planner's answer is unusable: " + "; ".join(problems)[:400])
        return {**V.clean_planner(raw, inputs.clip_keys, platforms, methods), "model": getattr(res, "model", None)}

    async def write_metadata(self, registry: PublicationRegistry, inputs: Inputs, platform: str, analysis: dict | None, runs: list[int]) -> dict:
        """The metadata of every destination of one platform: {"<kind>/<asset>": {"item": {...}, "findings": [...]}} and the model. Uses its own session, so that platforms can run side by side."""
        view = metadata_view(registry, inputs, platform, analysis)
        wanted = [(i["kind"], i["asset"]) for i in view["items"]]
        limits = {kind: registry.cfg.intelligence.metadata_limits[f"{platform}/{kind}"] for kind, _ in wanted}
        attribution = ai_config.shorts_host_led.music_attribution_text.get(inputs.language)
        db = SessionLocal()
        try:
            extra: dict = {}
            repairs = registry.cfg.intelligence.generation.repairs

            judge = judge_for(registry, inputs, platform)

            for attempt in range(repairs + 1):
                raw, res = await self._ask(db, inputs.case.id, WRITER, f"Publishing metadata writer ({inputs.language}, {platform})",
                                           lambda payload={**view, **extra}: run_agent("publishing_metadata_writer", self.gen, _json(payload)), runs)
                # an item is found by its asset ("film" or the clip key): a model may leave out `kind`, which the asset already says
                answered = {str(i.get("asset") or i.get("clip_key")): i for i in (raw.get("items") if isinstance(raw, dict) else None) or [] if isinstance(i, dict)}
                results: dict[str, dict] = {}
                problems: list[str] = []
                for kind, asset in wanted:
                    if asset not in answered:
                        problems.append(f"{kind}/{asset}: no item written")
                        continue
                    item, fixes = V.tidy(answered[asset])
                    if kind == "short" and attribution and attribution not in item["text"]:
                        item["text"] = f"{item['text']}\n\n{attribution}".strip()      # the music credit is added by the app, never by the model
                    findings = judge(kind, item)
                    results[f"{kind}/{asset}"] = {"item": item, "findings": findings, "fixes": fixes}
                    problems += [f"{kind}/{asset}: {m}" for m in V.errors(findings)]
                if not problems and results:
                    for asset, why in (await self.weak_hooks(db, inputs, registry.cfg.intelligence.generation, platform, results, limits["short"].title_max, runs)).items():
                        problems.append(f"short/{asset}: the hook is weak ({why})")
                        results[f"short/{asset}"]["findings"].append(V.finding("warning", "weak_hook", f"The hook was judged weak by the critic: {why}"))
                if not problems or attempt == repairs:
                    break
                # a text that lost its half-spaces is rewritten, not patched: the model copies its own earlier spelling when it sees it again (live: Facebook failed the same way on every repair)
                lost = any(f["type"] == "no_half_space" for r in results.values() for f in r["findings"])
                extra = {"problems_to_fix": problems} if lost else {"previous": raw, "problems_to_fix": problems}
            lost = [key for key, r in results.items() if any(f["type"] == "no_half_space" for f in r["findings"])]
            if lost:
                for key in await self.restore_half_spaces(db, inputs, results, lost, judge, runs):
                    problems = [p for p in problems if not (p.startswith(f"{key}: ") and "half-space" in p)]
            if not results:
                raise PlanError(f"the {platform} writer returned nothing usable")
            return {"results": results, "model": getattr(res, "model", None), "unresolved": problems}
        finally:
            db.close()

    async def restore_half_spaces(self, db: Session, inputs: Inputs, results: dict[str, dict], keys: list[str], judge: Callable[[str, dict], list[dict]], runs: list[int]) -> list[str]:
        """Persian texts the writer spelled without half-spaces, even after its repairs, go through one small call that adds them and nothing else. An answer that changes anything but
        half-spaces is refused (the text stays as it was, with its finding). Returns the keys that were put right."""
        fields = {f"{key}#{name}": results[key]["item"][name] for key in keys for name in ("title", "text") if results[key]["item"][name]}
        raw, _ = await self._ask(db, inputs.case.id, FIXER, f"Publishing orthography fixer ({inputs.language})", lambda: run_agent("publishing_orthography_fixer", self.gen, _json({"language": inputs.language, "fields": fields})), runs)
        answer = raw.get("fields") if isinstance(raw, dict) and isinstance(raw.get("fields"), dict) else {}
        plain = lambda t: str(t).replace(ZWNJ, "")  # noqa: E731
        fixed: list[str] = []
        for key in keys:
            item = results[key]["item"]
            names = [n for n in ("title", "text") if item[n]]
            if not all(plain(answer.get(f"{key}#{n}", "")) == plain(item[n]) for n in names):
                continue
            for n in names:
                item[n] = str(answer[f"{key}#{n}"])
            findings = judge(key.split("/", 1)[0], item)
            if any(f["type"] == "no_half_space" for f in findings):      # the editor found nothing to add: a plain past-tense narrative can honestly have no half-space, so the check no longer blocks
                findings = [V.finding("warning", f["type"], "no half-space in the text; the orthography fixer found no place for one") if f["type"] == "no_half_space" else f for f in findings]
                note = "orthography fixer: no half-space needed"
            else:
                note = "half-spaces restored by the orthography fixer"
            results[key]["findings"], results[key]["fixes"] = findings, [*results[key]["fixes"], note]
            fixed.append(key)
        return fixed

    async def weak_hooks(self, db: Session, inputs: Inputs, settings: GenerationSettings, platform: str, results: dict[str, dict], title_max: int, runs: list[int]) -> dict[str, str]:
        """The Shorts of one platform whose hook the critic found weak: {asset: what to fix}. A hook is weak when stakes, open question or concrete detail scores below `hook_min_score`,
        or when it gives the answer away or accuses. The scores decide, never the critic's own verdict word; an unreadable answer judges nothing."""
        written = {key.split("/", 1)[1]: r["item"] for key, r in results.items() if key.startswith("short/")}
        if settings.hook_min_score == 0 or not written:
            return {}
        titles = [e["title"] for e in examples_for(inputs.language, settings)[: settings.hook_critic_examples]]
        view = critic_view(inputs, platform, written, title_max, titles)
        raw, _ = await self._ask(db, inputs.case.id, CRITIC, f"Publishing hook critic ({inputs.language}, {platform})", lambda: run_agent("publishing_hook_critic", self.gen, _json(view)), runs)
        weak: dict[str, str] = {}
        for entry in (raw.get("items") if isinstance(raw, dict) else None) or []:
            if not isinstance(entry, dict) or str(entry.get("asset")) not in written:
                continue
            scores = {k: entry.get(k) for k in HOOK_SCORES}
            if not all(isinstance(v, (int, float)) for v in scores.values()):
                continue
            low = [k for k, v in scores.items() if v < settings.hook_min_score]
            risk = str(entry.get("risk") or "none")
            if low or risk != "none":
                parts = [f"{', '.join(low)} too low ({', '.join(f'{k} {scores[k]}' for k in HOOK_SCORES)} of 10)"] if low else []
                parts += [f"risk: {risk}"] if risk != "none" else []
                weak[str(entry["asset"])] = "; ".join(parts) + (f". Direction: {entry['direction']}" if entry.get("direction") else "")
        return weak

    def _proof_accepted(self, original: dict, corrected: dict, judge, kind: str, attribution: str | None, min_similarity: float) -> str | None:
        """None when the corrected texts are a correction of the original ones; else why not: a rewrite, a lost credit line or hashtag, or an error the original did not have."""
        for name in ("title", "text", "cta"):
            if not isinstance(corrected.get(name), str):
                return f"{name} is not a text"
            if original.get(name) and difflib.SequenceMatcher(None, original[name], corrected[name], autojunk=False).ratio() < min_similarity:
                return f"{name} was rewritten, not corrected"
        for name in ("hashtags", "keywords"):
            if not isinstance(corrected.get(name), list) or len(corrected[name]) != len(original.get(name) or []) or not all(isinstance(x, str) for x in corrected[name]):
                return f"{name} lost or gained an entry"
        if any(not tag.startswith("#") for tag in corrected["hashtags"]):
            return "a hashtag lost its #"
        if attribution and attribution in original.get("text", "") and attribution not in corrected["text"]:
            return "the credit line was changed"
        before = set(V.errors(judge(kind, original)))
        new = [m for m in V.errors(judge(kind, {**original, **{k: corrected[k] for k in PROOF_FIELDS}})) if m not in before]
        return f"it would introduce: {new[0]}" if new else None

    async def proofread_texts(self, db: Session, registry: PublicationRegistry, inputs: Inputs, entries: list[dict], runs: list[int]) -> dict[str, dict]:
        """All the texts go to the proofreader in ONE call (so that one mistake gets one correction on every portal and in every film and Short) and come back as corrections. `entries` are
        {"key": "platform/kind/asset", "platform", "kind", "asset", "item": {title, text, hashtags, keywords, cta, ...}}. Returns {key: {"item": the corrected item, "changes": [...]}} for
        every entry whose corrected answer was accepted (an unchanged text has no changes), and {key: {"refused": why}} for the others. The answer is checked: a rewrite, a lost credit line
        or hashtag, or a new error means the original stays."""
        out: dict[str, dict] = {}
        if not entries:
            return out
        settings = registry.cfg.intelligence.generation
        attribution = ai_config.shorts_host_led.music_attribution_text.get(inputs.language)
        payload = {"task": PROOF_TASK, "language": inputs.language, "known_words": sorted(inputs.dossier["known_words"]),
                   "items": [{"key": e["key"], "asset": e["asset"], **{k: e["item"].get(k) or ([] if k in ("hashtags", "keywords") else "") for k in PROOF_FIELDS}} for e in entries]}
        raw, _ = await self._ask(db, inputs.case.id, PROOFREADER, f"Publishing proofreader ({inputs.language}, all {len(entries)} texts)", lambda: run_agent("publishing_proofreader", self.gen, _json(payload)), runs)
        answered = {str(i.get("key")): i for i in (raw.get("items") if isinstance(raw, dict) else None) or [] if isinstance(i, dict)}
        for entry in entries:
            got = answered.get(entry["key"])
            if got is None:
                out[entry["key"]] = {"refused": "the proofreader returned nothing for it"}
                continue
            judge = judge_for(registry, inputs, entry["platform"])
            why = self._proof_accepted(entry["item"], got, judge, entry["kind"], attribution, settings.proofread_min_similarity)
            if why:
                out[entry["key"]] = {"refused": why}
                continue
            item = {**entry["item"], **{k: got[k] for k in PROOF_FIELDS}}
            changes = [c for c in got.get("changes") or [] if isinstance(c, dict)] if any(item[k] != entry["item"].get(k) for k in PROOF_FIELDS) else []
            out[entry["key"]] = {"item": item, "changes": changes}
        return out

    async def proofread(self, registry: PublicationRegistry, inputs: Inputs, metadata: dict[str, dict], runs: list[int]) -> dict:
        """The texts the writers made, for a language the owner reads (config: intelligence.generation.proofread_languages), are proofread before anything is saved or shown. Corrected texts
        replace the written ones in `metadata` (with their findings checked again); a refused answer leaves the written text. Returns {"checked", "corrected", "refused": {key: why}}."""
        if inputs.language not in registry.cfg.intelligence.generation.proofread_languages:
            return {"checked": 0, "corrected": 0, "refused": {}}
        entries = [{"key": f"{platform}/{key}", "platform": platform, "kind": key.split("/", 1)[0], "asset": key.split("/", 1)[1], "item": result["item"]}
                   for platform, made in metadata.items() for key, result in made["results"].items()]
        db = SessionLocal()
        try:
            found = await self.proofread_texts(db, registry, inputs, entries, runs)
        finally:
            db.close()
        corrected, refused = 0, {}
        for entry in entries:
            answer = found.get(entry["key"], {})
            if "refused" in answer:
                refused[entry["key"]] = answer["refused"]
                continue
            result = metadata[entry["platform"]]["results"][entry["key"].split("/", 1)[1]]
            if answer["changes"]:
                corrected += 1
                result["item"] = answer["item"]
                result["findings"] = judge_for(registry, inputs, entry["platform"])(entry["kind"], answer["item"])
                result["fixes"] = [*result.get("fixes", []), f"proofread: {len(answer['changes'])} corrections"]
                result["proofread"] = answer["changes"]
        return {"checked": len(entries), "corrected": corrected, "refused": refused}

    async def write_all(self, registry: PublicationRegistry, inputs: Inputs, analysis: dict | None, runs: list[int], on_done=None) -> dict[str, dict]:
        gate = asyncio.Semaphore(registry.cfg.intelligence.generation.concurrency)
        platforms = list(dict.fromkeys(d.split("/")[0] for d in inputs.destinations))

        async def one(platform: str) -> tuple[str, dict]:
            async with gate:
                try:
                    made = await self.write_metadata(registry, inputs, platform, analysis, runs)
                except (BudgetStopped, JobPaused):
                    raise                                                  # the cost cap or the Emergency Stop ends the whole run, not one platform
                except Exception as exc:  # noqa: BLE001 - one platform failing does not stop the others; it is recorded
                    made = {"results": {}, "model": None, "unresolved": [], "error": f"{type(exc).__name__}: {exc}"[:600]}
                if on_done:
                    on_done(platform, made)
                return platform, made

        return dict(await asyncio.gather(*(one(p) for p in platforms)))
