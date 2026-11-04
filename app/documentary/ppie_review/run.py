"""One review of a Persian narration: its records live in `<narration folder>/review/<run id>/` and nothing in them is read by another run.

A review is free unless the owner authorizes more: the alignment (local embeddings), the analysis copies and the recognizer (local) cost nothing.
The verifier, reading and corrector agents and every new take are billable; they run only under the run's authorization (`authorization.json`:
what the owner allowed and the most it may cost) and the case's budget cap, checked before each call. A new take goes to the run's `candidates`
folder; the narration is changed only when the owner accepts a take, and only by assembling it again from takes already made (a provider that
refuses every request stands in for the voice, so the assembly cannot pay for anything); the narration as it was is kept next to it first."""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import StoryVersion
from app.documentary import audio as A
from app.documentary import storage
from app.documentary.performance import performance_for_version
from app.documentary.ppie_review import acoustic as AC
from app.documentary.ppie_review import alignment as AL
from app.documentary.ppie_review import candidates as CA
from app.documentary.ppie_review import correction as CO
from app.documentary.ppie_review import expectation as EX
from app.documentary.ppie_review import sources as SRC
from app.documentary.ppie_review import validation as VA
from app.documentary.ppie_review.audio_prep import analysis_copies
from app.documentary.reading import numbers as NUM
from app.documentary.reading.words import key_of, tokenize
from app.documentary.voice_experiment import eur_per_character
from app.providers.voice import VoiceRequest
from app.services.budget import BudgetService
from app.utils import utc_now


class NotAuthorized(RuntimeError):
    """A billable step the owner has not authorized, or one that would pass the run's or the case's limit."""


@dataclass
class Authorization:
    agents: bool = False              # the verifier, reading and corrector agents
    tts: bool = False                 # new takes of words to repair
    max_eur: float = 0.0              # the most this run may spend on both
    by: str = ""
    at: str = ""


class NoVoice:
    """The voice of an assembly: every take must already exist."""

    name = "no_voice"

    def is_configured(self) -> bool:
        return True

    async def synthesize(self, req: VoiceRequest):
        raise NotAuthorized("the assembly of a reviewed narration asked for a new take: nothing was voiced")


def takes(folder: Path, plan_blocks: list[dict]) -> dict[str, dict]:
    """The take each block of a narration was assembled from: {block: {"meta", "mp3", "text", "seconds"}}. The manifest keeps a block's voice text
    only when it differs from the block's own, so the plan's text (as the renderer sent it) stands for the others; the seed must match too."""
    from app.documentary.voice_performance import scrub_forbidden

    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    own = {b["block_id"]: scrub_forbidden(b.get("tts_text") or b["text"]) for b in plan_blocks}
    metas: dict[str, list[tuple[dict, Path]]] = {}
    for path in (folder / "blocks").glob("*__*.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        metas.setdefault(meta.get("block_id", ""), []).append((meta, path))
    out = {}
    for b in manifest.get("blocks", []):
        text = b.get("tts_text") or own.get(b["block_id"])
        for meta, path in metas.get(b["block_id"], []):
            if meta.get("text") == text and meta.get("seed") == b.get("seed") and path.with_suffix(".mp3").exists():
                out[b["block_id"]] = {"meta": meta, "mp3": path.with_suffix(".mp3"), "text": text, "seconds": b.get("actual_seconds")}
                break
    return out


def differing_words(text: str, voice_text: str) -> list[str]:
    """Words of the approved text the voice was not given as they are (marks, half-spaces and the words of a number aside): each is reported
    with what the voice was given instead, or as missing."""
    out = []
    for w, piece in zip(tokenize(text), AC.matched(text, voice_text), strict=True):
        said = " ".join(p.surface for p in piece)
        if not piece:
            out.append(f"{w.surface} → (another word, or none)")
        elif not NUM.has_digit(w) and key_of(w.surface) != key_of(said):
            out.append(f"{w.surface} → {said}")
    return out


class ReviewRun:
    def __init__(self, db: Session, version: StoryVersion, folder: Path, run_id: str | None = None):
        self.db, self.version, self.narration = db, version, folder
        self.sources = SRC.approved_sources(db, version)
        self.run_id = run_id or utc_now().strftime("%Y%m%dT%H%M%S")
        self.folder = folder / ai_config.ppie_review.folder / self.run_id
        self.folder.mkdir(parents=True, exist_ok=True)
        self.started = BudgetService(db).snapshot(version.case_id).spent_eur if not (self.folder / "summary.json").exists() else \
            self.read("summary.json").get("spent_before_eur", 0.0)

    @property
    def voiced_version(self) -> StoryVersion:
        """The version the narration was voiced from (a preview may be of another version than the approved one)."""
        manifest = json.loads((self.narration / "manifest.json").read_text(encoding="utf-8"))
        return self.db.get(StoryVersion, manifest.get("story_version_id")) or self.version

    def recorded(self) -> dict[str, dict]:
        return takes(self.narration, performance_for_version(self.db, self.voiced_version)["blocks"])

    # --- records ---------------------------------------------------------------------------------------------------------------------

    def write(self, name: str, data) -> None:
        storage.write_atomic(self.folder / name, json.dumps(data, ensure_ascii=False, indent=1, default=str))

    def read(self, name: str, default=None):
        try:
            return json.loads((self.folder / name).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return default

    @property
    def authorization(self) -> Authorization:
        return Authorization(**(self.read("authorization.json") or {}))

    def authorize(self, auth: Authorization) -> None:
        self.write("authorization.json", asdict(auth))

    def allow(self, kind: str, estimate_eur: float = 0.0) -> None:
        """Raises NotAuthorized unless the owner allowed this kind of paid step and it fits the run's and the case's limits."""
        auth = self.authorization
        if not getattr(auth, kind) or auth.max_eur <= 0:
            raise NotAuthorized(f"the owner has not authorized {kind} for this review (with a limit above 0 EUR)")
        snap = BudgetService(self.db).snapshot(self.version.case_id)
        if snap.spent_eur >= snap.cap_eur or snap.spent_eur + estimate_eur > snap.cap_eur:
            raise NotAuthorized(f"the case has spent {snap.spent_eur} of {snap.cap_eur} EUR")
        if snap.spent_eur - self.started + estimate_eur > auth.max_eur:
            raise NotAuthorized(f"this review may spend {auth.max_eur} EUR; it has spent {round(snap.spent_eur - self.started, 4)}")

    # --- the review ------------------------------------------------------------------------------------------------------------------

    async def review(self, verify_runner=None, expect_runner=None, rank_runner=None, listener=None, encoder=None,
                     given: dict[str, dict[int, EX.Expectation]] | None = None, only: set[str] | None = None) -> dict:
        """`given`: readings a person decided, used instead of the reading agent's (no agent is called for them). `only`: these blocks alone."""
        from app.documentary.ppie_review.embeddings import get_encoder
        from app.documentary.pronunciation import get_listener

        plan = performance_for_version(self.db, self.version)
        if only:
            plan = {**plan, "blocks": [b for b in plan["blocks"] if b["block_id"] in only]}
        story, english = self.sources.narration.story_text or "", self.sources.english.story_text or ""
        persian, english_units, matches = AL.prepare(story, english, encoder or get_encoder())
        if ai_config.ppie_review.alignment.verify_with_agent and self.authorization.agents:
            self.allow("agents")
            matches = await AL.verify(matches, persian, english_units, verify_runner or self.verify_runner())
        aligned = {b.block_id: b for b in AL.blocks(plan["blocks"], story, english, persian, english_units, matches)}
        self.write("alignment.json", {"summary": AL.summary(persian, english_units, matches, list(aligned.values())),
                                      "blocks": [b.describe() for b in aligned.values()]})
        expected = given if given is not None else await self.expectations(plan, aligned, expect_runner) if self.authorization.agents else {}
        listener = listener or get_listener()
        validation = VA.current()
        recorded = self.recorded()
        ppie = self.ppie_words()
        reviews, proposals, compat = {}, [], {}
        for b in plan["blocks"]:
            take = recorded.get(b["block_id"])
            if take is None:
                continue
            compat[b["block_id"]] = differing_words(b["text"], take["text"])
            words = await asyncio.to_thread(self.review_take, b["text"], take, expected.get(b["block_id"], {}), listener, validation)
            reviews[b["block_id"]] = [w.describe() for w in words]
            for w in words:
                if w.suggested == "FAIL":
                    proposals.append(self.proposal(b, take, w, expected.get(b["block_id"], {}).get(w.n), ppie.get((b["block_id"], w.n)), aligned))
        if proposals and self.authorization.agents and ai_config.ppie_review.correction.use_agent:
            for p in proposals:
                self.allow("agents")
                await CO.rank(self.db, self.version.case_id, p, {"block": p.block_id, "word": p.word, "target": p.target}, rank_runner)
        self.write("acoustic.json", reviews)
        self.write("proposals.json", [p.describe() for p in proposals])
        summary = self.summary(reviews, proposals, compat, validation, recorded)
        self.write("summary.json", summary)
        return summary

    def verify_runner(self):
        from app.agents.runner import run_agent
        from app.documentary.reading.ask import ask

        async def runner(payload: dict) -> dict:
            self.allow("agents")
            return await ask(self.db, self.version.case_id, "PPIE review: alignment check", payload, "ppie_alignment_verifier",
                             lambda gen, body: run_agent("documentary.ppie_review.align_verify", gen, body))
        return runner

    async def expectations(self, plan: dict, aligned: dict, runner) -> dict[str, dict[int, EX.Expectation]]:
        out: dict[str, dict[int, EX.Expectation]] = {}
        blocks = plan["blocks"]
        for i, b in enumerate(blocks):
            self.allow("agents")
            a = aligned.get(b["block_id"])
            found = await EX.expect(self.db, self.version.case_id, b["block_id"], b["text"], blocks[i - 1]["text"] if i else "",
                                    blocks[i + 1]["text"] if i + 1 < len(blocks) else "", a.english_text if a else "",
                                    a.status if a else "MISSING_REFERENCE", runner)
            out[b["block_id"]] = {e.n: e for e in found}
        self.write("expectations.json", {b: [e.describe() for e in found.values()] for b, found in out.items()})
        return out

    def ppie_words(self) -> dict[tuple[str, int], dict]:
        """PPIE's reading and voice form of the words its run recorded (risky, changed or escalated ones), for the diagnosis only."""
        try:
            record = json.loads((self.narration / ai_config.ppie.run_file).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return {(w["block_id"], w["n"]): {"reading": w["finglish"], "tts_input": w["tts_input"]} for w in record.get("words", [])}

    def review_take(self, text: str, take: dict, expected: dict[int, EX.Expectation], listener, validation: dict) -> list[AC.WordReview]:
        spans = AC.windows(text, take["text"], take["meta"].get("alignment") or {})
        with analysis_copies(take["mp3"], self.folder) as copies:
            signals = {speed: listener.log_probs(str(c.path)) for speed, c in copies.items() if c.usable}
            frames = listener.frames(str(copies[1.0].path))
        out = []
        words = tokenize(text)
        for w in words:
            e = expected.get(w.n)
            reading = e.reading if e and e.reading else None
            cands = CA.for_word(reading, e.alternatives) if reading and " " not in reading else (
                [CA.made(reading, "expected")] if reading else [])
            span = spans.get(w.n)
            out.append(AC.review_word(w, span.window if span else None, reading, cands, signals, listener, frames, bool(validation.get("validated"))))
        return out

    def proposal(self, block: dict, take: dict, review: AC.WordReview, expected: EX.Expectation | None, ppie: dict | None, aligned: dict) -> CO.Proposal:
        words = tokenize(block["text"])
        span = AC.windows(block["text"], take["text"], take["meta"].get("alignment") or {}).get(review.n)
        a = aligned.get(block["block_id"])
        cause, evidence = CO.diagnose(review.describe(), review.expected or "", ppie, a.status if a else "MISSING_REFERENCE", NUM.has_digit(words[review.n]))
        tried = [x for p in self.read("proposals.json", []) if p["block_id"] == block["block_id"] and p["word_id"] == review.n for x in p["attempts"]]
        return CO.propose(block["block_id"], review.n, review.surface, review.expected or "", cause, evidence, take["text"],
                          (span.char_start, span.char_end) if span else (0, 0), tried)

    def summary(self, reviews: dict, proposals: list, compat: dict, validation: dict, recorded: dict) -> dict:
        words = [w for block in reviews.values() for w in block]
        statuses = {s: sum(1 for w in words if w["status"] == s) for s in ("PASS", "FAIL", "UNCERTAIN", "NOT_EVALUABLE")}
        suggested = {s: sum(1 for w in words if w["suggested"] == s) for s in ("PASS", "FAIL", "UNCERTAIN", "NOT_EVALUABLE")}
        snap = BudgetService(self.db).snapshot(self.version.case_id)
        return {"run_id": self.run_id, "created_at": utc_now().isoformat(), "sources": self.sources.describe(), "narration_folder": str(self.narration),
                "blocks_reviewed": len(reviews), "blocks_without_take": sum(1 for b in performance_for_version(self.db, self.version)["blocks"]
                                                                          if b["block_id"] not in recorded),
                "words": len(words), "statuses": statuses, "suggested": suggested, "proposals": len(proposals),
                "model_validation": {"validated": bool(validation.get("validated")), "labelled_clips": validation.get("labelled_clips", 0)},
                "advisory": not validation.get("validated"),
                "voice_text_differs_from_approved": {b: d for b, d in compat.items() if d},
                "authorization": asdict(self.authorization), "spent_before_eur": self.started,
                "spent_by_run_eur": round(snap.spent_eur - self.started, 4)}

    # --- repairs ---------------------------------------------------------------------------------------------------------------------

    def proposals(self) -> list[dict]:
        return self.read("proposals.json", [])

    def save_proposal(self, updated: dict) -> None:
        self.write("proposals.json", [updated if (p["block_id"], p["word_id"]) == (updated["block_id"], updated["word_id"]) else p
                                      for p in self.proposals()])

    def find(self, block_id: str, n: int) -> dict:
        found = next((p for p in self.proposals() if p["block_id"] == block_id and p["word_id"] == n), None)
        if found is None:
            raise KeyError(f"no proposal for {block_id} word {n} in run {self.run_id}")
        return found

    async def generate(self, block_id: str, n: int, strategy: int, provider=None, listener=None) -> dict:
        """A new take of the block with one candidate (billable: checked against the run's authorization and the case's cap), reviewed and
        compared with the original by code. The original take and the narration are not touched."""
        from app.documentary.pronunciation import get_listener
        from app.documentary.voice_experiment import eur_per_character
        from app.documentary.voice_render import VoiceRenderer
        from app.providers.voice import get_voice_provider

        proposal = self.find(block_id, n)
        candidate = proposal["strategies"][strategy]
        if any(a["form"] == candidate["form"] for a in proposal["attempts"]):
            raise NotAuthorized("this form was already tried in this run")
        original = self.recorded()[block_id]
        meta = original["meta"]
        self.allow("tts", len(candidate["tts_text"]) * eur_per_character(meta["model_id"]))
        renderer = VoiceRenderer(provider=provider or get_voice_provider(), use_asr=False, use_listener=False, db=self.db,
                                 budget_case_id=self.version.case_id, budget_stage=f"ppie_review:{self.run_id}")
        req = VoiceRequest(text=candidate["tts_text"], voice_id=meta["voice_id"], model_id=meta["model_id"], settings=meta["settings"],
                           previous_text=meta.get("previous_text", ""), next_text=meta.get("next_text", ""), seed=meta.get("seed"),
                           language="fa", language_code=meta.get("language_code"))
        folder = self.folder / "candidates"
        folder.mkdir(exist_ok=True)
        take = await renderer._take({"block_id": block_id}, req, folder, "fa")
        raw = Path(take["raw_path"])
        new = {"meta": json.loads(raw.with_suffix(".json").read_text(encoding="utf-8")), "mp3": raw, "text": candidate["tts_text"]}
        before = (self.read("acoustic.json") or {}).get(block_id, [])
        expected = {w["n"]: EX.Expectation(w["n"], w["word"], w["expected"]) for w in before if w.get("expected")}
        block = next(b for b in performance_for_version(self.db, self.version)["blocks"] if b["block_id"] == block_id)
        after = [w.describe() for w in await asyncio.to_thread(self.review_take, block["text"], new, expected, listener or get_listener(), VA.current())]
        verdict = CO.verify(before, after, n, await asyncio.to_thread(A.probe_duration, original["mp3"]), float(take["raw_duration"]))
        attempt = {"strategy": candidate["type"], "form": candidate["form"], "take": str(new["mp3"].relative_to(self.folder)), "review": after,
                   "verification": verdict, "outcome": "failed" if verdict["final_decision"] == "REJECTED" or verdict["target_error_resolved"] is False
                   else "owner_decides", "at": utc_now().isoformat()}
        proposal["attempts"].append(attempt)
        proposal["status"] = "GENERATED"
        self.save_proposal(proposal)
        return attempt

    async def accept(self, block_id: str, n: int, attempt: int, by: str) -> dict:
        """The owner keeps a new take: it joins the narration's takes and the narration is assembled again from takes already made (no voice
        request); the narration as it was is kept first, under a name with this run's id."""
        from app.documentary.production.audio import render_documentary_audio
        from app.documentary.voice_render import VoiceRenderer

        if self.voiced_version.id != self.version.id:
            raise NotAuthorized("this narration was voiced from another version than the approved one: it is reviewed, never changed")
        proposal = self.find(block_id, n)
        chosen = proposal["attempts"][attempt]
        take_mp3 = self.folder / chosen["take"]
        for path in (take_mp3, take_mp3.with_suffix(".json")):
            shutil.copy2(path, self.narration / "blocks" / path.name)
        kept = self.narration / f"before-{self.run_id}"
        kept.mkdir(exist_ok=True)
        for name in ("narration.mp3", "narration.wav", "narration_original.mp3", "narration_enhanced.wav", "manifest.json"):
            if (self.narration / name).exists() and not (kept / name).exists():
                shutil.copy2(self.narration / name, kept / name)
        manifest = json.loads((self.narration / "manifest.json").read_text(encoding="utf-8"))
        plan = performance_for_version(self.db, self.version)
        texts = {block: take["text"] for block, take in self.recorded().items()}
        texts[block_id] = json.loads(take_mp3.with_suffix(".json").read_text(encoding="utf-8"))["text"]
        renderer = VoiceRenderer(provider=NoVoice(), use_asr=False, use_listener=False)
        renderer.text_overrides = texts
        renderer.seed_bumps = {b["block_id"]: b["seed"] - ai_config.voice.seed for b in manifest["blocks"]}
        try:
            await render_documentary_audio(plan, case_id=self.version.case_id, story_version_id=self.version.id, db=self.db, renderer=renderer,
                                           with_music=False)
        except Exception:
            for name in kept.iterdir():
                shutil.copy2(name, self.narration / name.name)
            raise
        proposal["status"], proposal["accepted"] = "ACCEPTED", {"attempt": attempt, "by": by, "at": utc_now().isoformat()}
        self.save_proposal(proposal)
        return proposal

    def reject(self, block_id: str, n: int, by: str) -> dict:
        proposal = self.find(block_id, n)
        proposal["status"], proposal["rejected"] = "REJECTED", {"by": by, "at": utc_now().isoformat()}
        self.save_proposal(proposal)
        return proposal


def runs(folder: Path) -> list[str]:
    base = folder / ai_config.ppie_review.folder
    return sorted((p.name for p in base.iterdir() if p.is_dir() and (p / "summary.json").exists()), reverse=True) if base.exists() else []


def narrations(version: StoryVersion) -> dict[str, Path]:
    """The narrations of a version that can be reviewed, by label: its PPIE narration, and the previews of its case (whose takes are compared with
    the approved text; a preview voiced from another text is reported as not of the approved text)."""
    from app.documentary import narration_preview as NP
    from app.documentary.voice_render import VoiceRenderer

    out = {"ppie": VoiceRenderer.out_dir_for(version.case_id, "fa", version.id)}
    for p in NP.listing():
        if p["case_id"] == version.case_id and p["language"] == "fa":
            out[f"preview:{p['name']}"] = NP.audio_path(p["name"]).parent
    return out


def overview(run: ReviewRun) -> dict:
    """What the owner sees of a run: its summary, and for each block its English passage, alignment, differences from the approved text, the words
    the recognizer did not pass (with evidence) and the repair proposals."""
    alignment = {b["block_id"]: b for b in (run.read("alignment.json") or {}).get("blocks", [])}
    acoustic = run.read("acoustic.json") or {}
    proposals = run.proposals()
    summary = run.read("summary.json") or {}
    differs = summary.get("voice_text_differs_from_approved", {})
    texts = {b["block_id"]: b["text"] for b in performance_for_version(run.db, run.version)["blocks"]}
    recorded = run.recorded() if (run.narration / "manifest.json").exists() else {}
    price = eur_per_character()
    for p in proposals:
        p["estimated_eur"] = round(p["estimated_characters"] * price, 4)
    blocks = []
    for block_id, a in alignment.items():
        words = acoustic.get(block_id, [])
        blocks.append({"block_id": block_id, "text": texts.get(block_id, ""), "voice_text": recorded.get(block_id, {}).get("text"),
                       "has_take": block_id in recorded, "alignment": a, "voice_text_differs": differs.get(block_id, []),
                       "counts": {s: sum(1 for w in words if w["suggested"] == s) for s in ("PASS", "FAIL", "UNCERTAIN", "NOT_EVALUABLE")},
                       "flagged": [w for w in words if w["suggested"] in ("FAIL", "UNCERTAIN")],
                       "proposals": [p for p in proposals if p["block_id"] == block_id]})
    return {"summary": summary, "alignment_summary": (run.read("alignment.json") or {}).get("summary"), "blocks": blocks}
