"""The controlled voice experiment: the same blocks voiced three ways with the same voice, model, settings and seed, so that the effect of the pronunciation
risk engine and planner can be measured instead of assumed.

  A  original text, no dictionary, no intervention
  B  what the pipeline did before the planner: the owner-approved words respelled, and the film's dictionary
  C  the risk engine and the adaptive planner (the same dictionary, plus the planner's decisions)

The selection is made from the planner's own decisions so that every kind of decision is tested, and control blocks (no decision, no dictionary rule) are
voiced in every arm with the same request: their differences are the run-to-run noise of the voice, which any real effect has to exceed. Nothing here calls a
provider; `estimate` is what a run would cost and `Selection` is what it would voice."""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path

from app.core.ai_config import ai_config
from app.documentary.pronunciation_planner import Planner
from app.documentary.reading.voicing import Voicing
from app.documentary.reading.words import key_of
from app.documentary.voice_performance import scrub_forbidden

ARMS = ("A", "B", "C")
GROUPS = {"control": 3, "dictionary": 3, "approved": 2, "candidate": 4, "escalated": 2}


@dataclass
class Candidate:
    block_id: str
    text: str
    previous_text: str
    next_text: str
    group: str
    targets: list[str]
    block: dict                                  # the block of the version's performance plan, as the renderer takes it
    texts: dict[str, str]                        # arm -> the text the voice is given
    dictionary: dict[str, bool]                  # arm -> whether the block gets the film dictionary

    @property
    def chars(self) -> dict[str, int]:
        return {arm: len(t) for arm, t in self.texts.items()}


@dataclass
class Selection:
    blocks: list[Candidate] = field(default_factory=list)

    def characters(self, context: bool = False) -> int:
        """The characters the voice provider is sent over the three arms (`context`: also the previous and next text of every request, the worst case if a
        provider counted them)."""
        return sum(len(t) + (len(c.previous_text) + len(c.next_text) if context else 0) for c in self.blocks for t in c.texts.values())

    def describe(self) -> dict:
        return {"blocks": [{"block_id": c.block_id, "group": c.group, "targets": c.targets, "chars_per_arm": c.chars, "dictionary": c.dictionary,
                            "texts": c.texts if c.texts["A"] != c.texts["C"] or c.texts["A"] != c.texts["B"] else {"all arms": c.texts["A"]}} for c in self.blocks]}


def eur_per_character(model_id: str = "eleven_v4") -> float:
    import json
    from pathlib import Path

    pricing = json.loads((Path(__file__).resolve().parents[2] / "config" / "parameters" / "pricing.json").read_text(encoding="utf-8"))
    return pricing["tts"][model_id]["eur_per_1m_characters"] / 1_000_000


def estimate(selection: Selection, model_id: str, buffer: float = 1.5) -> dict:
    """The exact characters, the cost at the configured price, and a hard cap (the estimate with the worst case of counted context, times the buffer)."""
    price = eur_per_character(model_id)
    chars, worst = selection.characters(), selection.characters(context=True)
    return {"takes": len(selection.blocks) * len(ARMS), "characters": chars, "characters_if_context_counted": worst, "eur_per_1m_characters": round(price * 1e6, 3),
            "estimated_eur": round(chars * price, 4), "worst_case_eur": round(worst * price, 4), "cap_eur": round(worst * price * buffer, 2)}


def classify(plan, covered: frozenset[str]) -> tuple[str, list[str]] | None:
    """The group of a planned block and the words it is about."""
    by = {s: [d.surface for d in plan.decisions if d.strategy == s] for s in ("respelling", "selective_marks", "escalate")}
    hit = [d.surface for d in plan.decisions if key_of(d.surface) in covered]
    risky = any(r.level in ("HIGH", "CRITICAL") for r in plan.risks)
    if by["selective_marks"]:
        return "candidate", by["selective_marks"]
    if by["respelling"]:
        return "approved", by["respelling"]
    if by["escalate"]:
        return "escalated", by["escalate"]
    if hit:
        return "dictionary", hit
    if not risky:
        return "control", []
    return None


def select(blocks: list[dict], voicing: Voicing, planner: Planner, covered: frozenset[str], quotas: dict[str, int] | None = None) -> Selection:
    """The shortest blocks of each group (short blocks cost least), each with the text of every arm."""
    found: dict[str, list[Candidate]] = {g: [] for g in GROUPS}
    for b in blocks:
        text = scrub_forbidden(b.get("tts_text") or b["text"])
        plan = planner.plan(b["block_id"], text, voicing, covered=covered)
        group = classify(plan, covered) if plan.decisions else None
        if group is None:
            continue
        before = voicing.text(b["block_id"], text)
        has_dictionary = group[0] != "control"
        found[group[0]].append(Candidate(b["block_id"], text, b.get("previous_text") or "", b.get("next_text") or "", group[0], group[1], b,
                                         {"A": text, "B": before, "C": plan.text}, {"A": False, "B": has_dictionary, "C": has_dictionary}))
    chosen: list[Candidate] = []
    for group, quota in (quotas or GROUPS).items():
        pool = sorted(found[group], key=lambda c: len(c.text))
        seen: set[str] = set()
        for c in pool:
            marker = key_of(c.targets[0]) if c.targets else c.block_id
            if marker in seen:
                continue                                  # different words first
            seen.add(marker)
            chosen.append(c)
            if sum(1 for x in chosen if x.group == group) >= quota:
                break
    return Selection(chosen)


# --- running it ------------------------------------------------------------------------------------------------------------------------

class CapReached(RuntimeError):
    """A request would pass the character cap of the experiment: nothing more is sent."""


class CountingProvider:
    """A voice provider that counts the characters of every request (what the provider bills) and refuses the request that would pass the cap."""

    def __init__(self, inner, max_chars: int):
        self.inner, self.max_chars, self.used, self.requests = inner, max_chars, 0, 0
        self.name = inner.name

    def is_configured(self) -> bool:
        return self.inner.is_configured()

    async def synthesize(self, request):
        if self.used + len(request.text) > self.max_chars:
            raise CapReached(f"{self.used} characters sent, the next request has {len(request.text)}, the cap is {self.max_chars}")
        self.used += len(request.text)
        self.requests += 1
        return await self.inner.synthesize(request)


def guard(est: dict, cap_eur: float, spent_eur: float, case_cap_eur: float, confirmed: bool) -> None:
    """Refuses to start unless the owner confirmed, the cap covers the estimate and the case still has the room for the cap."""
    if not confirmed:
        raise PermissionError("the experiment is billable: it needs the explicit confirmation of the owner")
    if cap_eur < est["worst_case_eur"]:
        raise ValueError(f"the cap {cap_eur} EUR is below the worst-case cost {est['worst_case_eur']} EUR of the selection")
    if spent_eur >= case_cap_eur or spent_eur + cap_eur > case_cap_eur:
        raise ValueError(f"the case has spent {spent_eur} of {case_cap_eur} EUR: no room for a cap of {cap_eur} EUR")


def arm_plan(plan: dict, selection: Selection, arm: str, locators: list[dict], rules: list[dict]) -> dict:
    """The plan one arm is voiced from: the chosen blocks only, and the film's dictionary in the arms that have one."""
    return {**plan, "blocks": [c.block for c in selection.blocks], "pronunciation_dictionary": locators if arm != "A" else [], "pronunciation_rules": rules if arm != "A" else []}


async def run_arm(arm: str, *, db, case_id: int, version_id: int, language: str, plan: dict, selection: Selection, voicing: Voicing, planner_for,
                  provider, work_dir: Path, locators: list[dict], rules: list[dict]) -> dict:
    """Voices the chosen blocks in one arm, into its own folder (the configured work folder is only swapped inside this call: production audio is not touched)."""
    from app.documentary.voice_render import VoiceRenderer

    work_dir.mkdir(parents=True, exist_ok=True)
    kept = ai_config.voice.work_dir
    ai_config.voice.work_dir = str(work_dir)
    try:
        renderer = VoiceRenderer(db=db, budget_case_id=case_id, budget_stage=f"experiment:{arm}", provider=provider, use_asr=False, use_listener=False)
        if arm in ("B", "C"):
            renderer.voicing = voicing
        if arm == "C":
            renderer.planner = planner_for()
        manifest = await renderer.render(arm_plan(plan, selection, arm, locators, rules), case_id=case_id, story_version_id=version_id)
        return {"arm": arm, "folder": str(renderer.out_dir(case_id, language, version_id)), "blocks": len(selection.blocks), "provider_requests": getattr(provider, "requests", None),
                "characters_sent": getattr(provider, "used", None), "plan_file": manifest.get("pronunciation_plan")}
    finally:
        ai_config.voice.work_dir = kept


# --- reading the result ------------------------------------------------------------------------------------------------------------------

def block_wavs(folder: Path) -> dict[str, Path]:
    manifest = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
    from app.documentary.voice_render import ROOT

    return {b["block_id"]: Path(ROOT) / b["wav"] if not Path(b["wav"]).is_absolute() else Path(b["wav"]) for b in manifest["timeline"]["blocks"]}


def prosody_report(folders: dict[str, Path], selection: Selection) -> dict:
    """Prosody features of every block in every arm; the noise floor from the control blocks (the same request in every arm); and where an arm differs from arm A
    beyond that noise. The features say how a take differs, not whether it sounds better."""
    from app.documentary import prosody

    wavs = {arm: block_wavs(f) for arm, f in folders.items()}
    feats = {arm: {b: prosody.of_file(p) for b, p in blocks.items()} for arm, blocks in wavs.items()}
    pairs = [(feats[x][c.block_id], feats[y][c.block_id]) for c in selection.blocks if c.group == "control" for x, y in (("A", "B"), ("A", "C"), ("B", "C"))
             if x in feats and y in feats]
    floor = prosody.noise_floor(pairs)
    verdicts = {arm: {c.block_id: prosody.judge(feats["A"][c.block_id], feats[arm][c.block_id], floor) for c in selection.blocks if c.group != "control"}
                for arm in feats if arm != "A"}
    return {"noise_floor": floor, "noise_from_control_blocks": len(pairs), "features": feats, "against_A": verdicts}


def register_clips(db, folders: dict[str, Path], selection: Selection, case_id: int, version_id: int, readings: dict[str, list[str]], key_file: Path, language: str = "fa") -> int:
    """The target words of each arm as unlabeled clips of the benchmark for a person to label without knowing the arm (the arm of each clip is kept in
    `key_file`, not in the clip)."""
    from app.documentary import phonetic_benchmark as PB
    from app.documentary.reading.words import tokenize

    key, made = {}, 0
    for arm, folder in folders.items():
        manifest = json.loads((Path(folder) / "manifest.json").read_text(encoding="utf-8"))
        wavs = block_wavs(folder)
        timeline = {}
        for w in manifest["timeline"]["words"]:
            timeline.setdefault(w["block_id"], []).append(w)
        starts = {b["block_id"]: b["start"] for b in manifest["timeline"]["blocks"]}
        for c in selection.blocks:
            tokens, spoken = tokenize(c.texts[arm]), timeline.get(c.block_id, [])
            reading = readings.get(c.block_id, [])
            if len(tokens) != len(spoken) or len(tokens) != len(reading):
                continue
            for token, word, latin in zip(tokens, spoken, reading, strict=True):
                if key_of(token.surface) not in {key_of(t) for t in c.targets}:
                    continue
                token_id = hashlib.sha256(f"{arm}{c.block_id}{token.n}{random.random()}".encode()).hexdigest()[:12]
                path = f"clips/exp_{token_id}.wav"
                PB.cut(wavs[c.block_id], word["start"] - starts[c.block_id], word["end"] - starts[c.block_id], PB.BASE / path)
                try:
                    candidates = PB.candidates_for(latin, "short_vowel")
                except ValueError:
                    continue
                if len(candidates) < 2:
                    continue
                sample, new = PB.add_sample(db, language=language, source="recording", case_id=case_id, story_version_id=version_id, block_id="", word_n=None, surface=c.targets[0] if len(c.targets) == 1 else token.surface,
                                            sentence="", audio_path=path, start_s=None, end_s=None, voice_id="", model_id="", speaker="", speed="film", category="short_vowel",
                                            candidates=json.dumps(candidates, ensure_ascii=False), expected_key=candidates[0]["key"])
                key[str(sample.id)] = {"arm": arm, "block": c.block_id, "word": token.surface}
                made += int(new)
    key_file.write_text(json.dumps(key, ensure_ascii=False, indent=1), encoding="utf-8")
    return made
