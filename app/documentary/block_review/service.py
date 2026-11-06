"""The hand review of one voiced Persian narration. A review is opened from the narration the voice made (its blocks and takes are registered, nothing is
paid); a person then corrects blocks (yellow for good, the corrected words red for good), has them voiced again (the old take stays, ticked off), ticks the take
that counts, and assembles the narration from the ticked takes into a separate folder. The assembled narration replaces the film's narration only when
the person approves it, and the film waits for that approval (documentary/voice_review.py)."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.db.models import StoryVersion, VoiceBlock, VoiceBlockTake, VoiceBlockWord, VoiceReview
from app.documentary import voice_review as VRV
from app.documentary.block_review import edits as E
from app.documentary.performance import performance_for_version
from app.documentary.voice_render import VoiceRenderer
from app.providers.voice.base import VoiceRequest
from app.utils import utc_now

REQUEST_FIELDS = ("text", "previous_text", "next_text", "voice_id", "model_id", "settings", "seed", "language_code", "language", "pronunciation_dictionaries")


class ReviewError(RuntimeError):
    """A request the review cannot do (no narration, an unknown block, nothing to send)."""


def default_provider():
    """The voice a correction is sent to (None: the film's own voice provider). One place, so a test can put a fake voice in."""
    return None


def required_settings(language: str, settings: dict) -> dict:
    """The voice settings a block voiced from the review must have (config voice.review_required): the provider's speed and stability, and the post speed after
    it. Refuses (nothing is sent, nothing is paid) when the request or the film's post speed differs; a language without required values is not checked."""
    want = ai_config.voice.review_required.get(language)
    if want is None:
        return {"speed": settings.get("speed"), "stability": settings.get("stability"), "post_speed": ai_config.voice.post_speed.get(language, 1.0)}
    have = {"speed": settings.get("speed"), "stability": settings.get("stability"), "post_speed": ai_config.voice.post_speed.get(language, 1.0)}
    wrong = {k: (have[k], getattr(want, k)) for k in have if have[k] != getattr(want, k)}
    if wrong:
        raise ReviewError("the block is not voiced: its settings are not the required ones (" + ", ".join(f"{k} {a} instead of {b}" for k, (a, b) in wrong.items()) + ")")
    return have


def request_of(record: dict) -> VoiceRequest:
    return VoiceRequest(**{k: record[k] for k in REQUEST_FIELDS if k in record})


def record_of(req: VoiceRequest) -> dict:
    return {k: getattr(req, k) for k in REQUEST_FIELDS}


def narration_folder(version: StoryVersion) -> Path:
    return VoiceRenderer.out_dir_for(version.case_id, version.language or "fa", version.id)


def reviewed_folder(version: StoryVersion) -> Path:
    return narration_folder(version) / "reviewed"


def get_review(db: Session, version: StoryVersion) -> VoiceReview | None:
    return db.query(VoiceReview).filter_by(story_version_id=version.id).one_or_none()


def blocks_of(db: Session, review: VoiceReview) -> list[VoiceBlock]:
    return db.query(VoiceBlock).filter_by(review_id=review.id).order_by(VoiceBlock.position).all()


def block_of(db: Session, review: VoiceReview, block_id: str) -> VoiceBlock:
    row = db.query(VoiceBlock).filter_by(review_id=review.id, block_id=block_id).one_or_none()
    if row is None:
        raise ReviewError(f"the narration has no block {block_id!r}")
    return row


def takes_of(db: Session, block: VoiceBlock) -> list[VoiceBlockTake]:
    return db.query(VoiceBlockTake).filter_by(block_pk=block.id).order_by(VoiceBlockTake.take_no).all()


def active_take(db: Session, block: VoiceBlock) -> VoiceBlockTake:
    return next(t for t in takes_of(db, block) if t.active)


def words_of(db: Session, block: VoiceBlock) -> dict[int, VoiceBlockWord]:
    return {w.word_n: w for w in db.query(VoiceBlockWord).filter_by(block_pk=block.id).all()}


# --- opening -----------------------------------------------------------------------------------------------------------------------------

def original_requests(folder: Path) -> dict[str, tuple[dict, Path]]:
    """The take each block of the narration was assembled from: block -> (its request, its take's file stem). Found by the manifest's text and seed;
    the request is checked against the cache key of the take (a take that does not match is not registered)."""
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    metas: dict[str, list[tuple[dict, Path]]] = {}
    for path in (folder / "blocks").glob("*__*.json"):
        meta = json.loads(path.read_text(encoding="utf-8"))
        metas.setdefault(meta.get("block_id", ""), []).append((meta, path))
    fmt = ai_config.voice.elevenlabs.output_format
    out: dict[str, tuple[dict, Path]] = {}
    for b in manifest["blocks"]:
        for meta, path in metas.get(b["block_id"], []):
            if meta.get("seed") != b.get("seed") or (b.get("tts_text") and meta.get("text") != b["tts_text"]):
                continue
            req = VoiceRequest(text=meta["text"], voice_id=meta["voice_id"], model_id=meta["model_id"], settings=meta["settings"],
                               previous_text=meta.get("previous_text") or "", next_text=meta.get("next_text") or "", seed=meta["seed"],
                               language=meta.get("language") or manifest["language"], language_code=meta.get("language_code"),
                               pronunciation_dictionaries=meta.get("dictionaries") or [])
            if VoiceRenderer.cache_key(req, fmt) == meta["cache_key"]:
                out[b["block_id"]] = (record_of(req), path)
                break
    return out


def open_review(db: Session, version: StoryVersion) -> VoiceReview:
    """The review of this narration (made once; opening it again changes nothing). Nothing is voiced or paid."""
    review = get_review(db, version)
    if review is not None:
        return review
    folder = narration_folder(version)
    if not (folder / "manifest.json").exists():
        raise ReviewError("the narration of this version is not made yet")
    plan = {b["block_id"]: b for b in performance_for_version(db, version)["blocks"]}
    found = original_requests(folder)
    review = VoiceReview(case_id=version.case_id, story_version_id=version.id, language=version.language or "fa")
    db.add(review)
    db.flush()
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    for position, b in enumerate(manifest["blocks"]):
        if b["block_id"] not in found:
            raise ReviewError(f"the take of block {b['block_id']} is not among the narration's files")
        record, path = found[b["block_id"]]
        block = VoiceBlock(review_id=review.id, block_id=b["block_id"], position=position, canonical_text=plan[b["block_id"]]["text"], voice_text=record["text"])
        db.add(block)
        db.flush()
        db.add(VoiceBlockTake(block_pk=block.id, take_no=1, source="original", text_sent=record["text"], request_json=json.dumps(record, ensure_ascii=False),
                              duration_s=b.get("actual_seconds"), active=True))
    db.commit()
    return review


# --- corrections -------------------------------------------------------------------------------------------------------------------------

def save_edit(db: Session, review: VoiceReview, block_id: str, text: str | None, words: list[E.WordEdit]) -> VoiceBlock:
    """A correction of a block, kept as a draft until the block is sent to the voice: the block turns yellow for good, the words changed red for good.
    `text` replaces the whole text of the draft; `words` correct single words of it (and give a finglish alias)."""
    block = block_of(db, review, block_id)
    current = block.draft_text if block.draft_text is not None else block.voice_text
    marks = {n: E.Mark(n, r.was, r.surface, r.finglish, r.pending) for n, r in words_of(db, block).items()}
    base = current if text is None else text
    marks, _ = E.remap(current, base, marks)
    new = E.apply_words(base, words)
    marks, _ = E.remap(base, new, marks)
    surface = E.surfaces(new)
    for e in words:
        if e.finglish is not None and e.n < len(surface):
            before = marks.get(e.n) or E.Mark(e.n, surface[e.n], surface[e.n])
            marks[e.n] = E.Mark(e.n, before.was, surface[e.n], e.finglish.strip(), True)
    db.query(VoiceBlockWord).filter_by(block_pk=block.id).delete()
    db.flush()
    for n, m in sorted(marks.items()):
        db.add(VoiceBlockWord(block_pk=block.id, word_n=n, was=m.was, surface=m.surface, finglish=m.finglish, pending=m.pending))
    block.draft_text = new
    block.edited = True
    db.commit()
    return block


def replace_everywhere(db: Session, review: VoiceReview, find: str, replacement: str, apply: bool) -> list[dict]:
    """The blocks in which the words `find` stand, and with `apply` the correction of each (a draft: the block yellow, the new words red). Without
    `apply` nothing is changed: it is the preview of what would be."""
    if not find.strip() or not replacement.strip() or E.surfaces(find) == E.surfaces(replacement):
        raise ReviewError("give the words to find and the different words to put in their place")
    found = []
    for block in blocks_of(db, review):
        text = block.draft_text if block.draft_text is not None else block.voice_text
        new, count = E.replace_text(text, find, replacement)
        if not count:
            continue
        start = E.find_spans(text, find)[0][0]
        found.append({"block_id": block.block_id, "position": block.position, "count": count, "before": text[max(0, start - 30):start + len(find) + 30],
                      "after": new[max(0, start - 30):start + len(replacement) + 30]})
        if apply:
            save_edit(db, review, block.block_id, new, [])
    return found


def needs_sending(db: Session, block: VoiceBlock) -> bool:
    return block.draft_text is not None and (block.draft_text != block.voice_text or any(w.pending and w.finglish for w in words_of(db, block).values()))


# --- voicing a corrected block -----------------------------------------------------------------------------------------------------------

def alias_rules(db: Session, block: VoiceBlock, text: str) -> list[dict]:
    """The alias rules of a block: each corrected word that has a finglish is replaced by it (ElevenLabs reads the alias instead of the word)."""
    return [{"string_to_replace": w.surface, "type": "alias", "alias": w.finglish}
            for w in sorted(words_of(db, block).values(), key=lambda w: w.word_n) if w.finglish and w.surface in text]


async def send_block(db: Session, review: VoiceReview, block_id: str, provider=None, again: bool = False) -> VoiceBlockTake:
    """Voices a block again (a paid take with the film's voice settings; the finglish of its corrected words goes along as aliases): its corrected text, or
    with `again` the text it has now (a new take of the same words, another seed). The new take is ticked; the earlier takes stay, ticked off."""
    from app.services.cost_model import load_pricing

    block = block_of(db, review, block_id)
    if not again and not needs_sending(db, block):
        raise ReviewError(f"block {block_id} has no correction to send")
    takes = takes_of(db, block)
    base = json.loads(takes[0].request_json)
    text = block.draft_text if block.draft_text is not None else block.voice_text
    rules = alias_rules(db, block, text)
    renderer = VoiceRenderer(provider=provider or default_provider(), db=db, budget_case_id=review.case_id, budget_stage=f"tts:{review.language}",
                             use_asr=False, use_listener=False)
    renderer.narration = True            # a take of the film's narration: it gets the language's post speed (1.1 for Persian) like every other block
    locators = list(base.get("pronunciation_dictionaries") or [])
    if rules:
        made = await renderer.provider.create_pronunciation_dictionary(f"review-{review.story_version_id}-{block_id}-t{len(takes) + 1}", rules)
        locators.append({"pronunciation_dictionary_id": made["id"], "version_id": made["version_id"]})
    seed = max(json.loads(t.request_json)["seed"] for t in takes) + 1
    req = request_of({**base, "text": text, "seed": seed, "pronunciation_dictionaries": locators})
    required_settings(review.language, req.settings)          # before anything is created or paid: speed, stability and post speed are the required ones
    version = db.get(StoryVersion, review.story_version_id)
    take = await renderer._take({"block_id": block_id, "text": text, "beats": [], "sentences": []}, req, narration_folder(version) / "blocks", review.language)
    characters = take.get("character_cost")
    price = float(load_pricing()["tts"][req.model_id]["eur_per_1m_characters"])
    for t in takes:
        t.active = False
    row = VoiceBlockTake(block_pk=block.id, take_no=len(takes) + 1, source="correction", text_sent=text, request_json=json.dumps(record_of(req), ensure_ascii=False),
                         aliases_json=json.dumps(rules, ensure_ascii=False), request_id=take.get("request_id"), characters=characters,
                         cost_eur=(characters or 0) / 1_000_000 * price, duration_s=take.get("duration"), active=True)
    db.add(row)
    block.voice_text, block.draft_text = text, None
    for w in words_of(db, block).values():
        w.pending = False
    review.status = "open"
    db.commit()
    return row


def activate(db: Session, review: VoiceReview, block_id: str, take_no: int) -> VoiceBlockTake:
    """Ticks the take that counts for the block (the others are ticked off); the narration is assembled from the ticked takes."""
    block = block_of(db, review, block_id)
    takes = takes_of(db, block)
    chosen = next((t for t in takes if t.take_no == take_no), None)
    if chosen is None:
        raise ReviewError(f"block {block_id} has no take {take_no}")
    for t in takes:
        t.active = t.id == chosen.id
    block.voice_text = chosen.text_sent
    review.status = "open"
    db.commit()
    return chosen


# --- assembling and approving ----------------------------------------------------------------------------------------------------------

class NoVoice:
    """The voice of an assembly: every take must already exist, nothing is paid."""

    name = "no_voice"

    def is_configured(self) -> bool:
        return True

    async def synthesize(self, req: VoiceRequest):
        raise ReviewError("the assembly asked for a take that does not exist: nothing was voiced")


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def active_takes(db: Session, review: VoiceReview) -> dict[str, int]:
    return {b.block_id: active_take(db, b).take_no for b in blocks_of(db, review)}


async def assemble(db: Session, review: VoiceReview) -> dict:
    """The narration of the ticked takes, in story order with the planned pauses, loudness-matched, into the review's own folder (the film's narration
    is not touched). No take is made or paid."""
    version = db.get(StoryVersion, review.story_version_id)
    fixed = {b.block_id: request_of(json.loads(active_take(db, b).request_json)) for b in blocks_of(db, review)}
    plan = performance_for_version(db, version)
    renderer = VoiceRenderer(provider=NoVoice(), db=db, budget_case_id=review.case_id, budget_stage=f"tts:{review.language}", use_asr=False, use_listener=False)
    manifest = await renderer.render(plan, case_id=review.case_id, story_version_id=version.id, fixed_requests=fixed, output_dir=reviewed_folder(version))
    review.assembled_sha256 = sha256_of(reviewed_folder(version) / "narration.mp3")
    review.assembled_takes_json = json.dumps(active_takes(db, review))
    review.assembled_at = utc_now()
    review.status = "assembled"
    db.commit()
    return {"duration_seconds": manifest["duration_seconds"], "blocks": manifest["blocks_rendered"], "sha256": review.assembled_sha256}


def assembled_is_current(db: Session, review: VoiceReview) -> bool:
    return review.status in ("assembled", "approved") and json.loads(review.assembled_takes_json or "{}") == active_takes(db, review)


async def approve(db: Session, review: VoiceReview, note: str | None = None) -> dict:
    """The one way to the film's narration: the narration is assembled from the ticked takes (when the assembled one is not exactly of them), becomes the
    film's narration (the earlier one is kept next to it) and is approved, so the film may go on. Refused while a corrected block has not been voiced:
    its correction would be missing."""
    waiting = [b.block_id for b in blocks_of(db, review) if needs_sending(db, b)]
    if waiting:
        raise ReviewError(f"{len(waiting)} corrected block(s) are not voiced yet ({', '.join(waiting[:4])}{'…' if len(waiting) > 4 else ''}): send them first")
    if not assembled_is_current(db, review):
        await assemble(db, review)
    version = db.get(StoryVersion, review.story_version_id)
    main, source = narration_folder(version), reviewed_folder(version)
    keep = main / f"before_review_{utc_now().strftime('%Y%m%dT%H%M%S')}"
    keep.mkdir()
    for name in ("narration.wav", "narration.mp3", "narration_enhanced.wav", "narration_original.mp3", "manifest.json", "dynamics.json"):
        if (main / name).exists():
            shutil.move(main / name, keep / name)
        if (source / name).exists():
            shutil.copy2(source / name, main / name)
    manifest = json.loads((main / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {k: v.replace("/reviewed/", "/") for k, v in manifest["files"].items()}
    (main / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    state = VRV.approve(version, note or "approved after the hand review")
    review.status = "approved"
    db.commit()
    return state


def asdict_take(t: VoiceBlockTake) -> dict:
    return {k: v for k, v in asdict_row(t).items() if k != "request_json"}


def asdict_row(row) -> dict:
    return {c.name: getattr(row, c.name) for c in row.__table__.columns}
