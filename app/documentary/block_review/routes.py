"""The routes of a hand review of voice blocks that belong to a language's film but not to its narration: the host's scenes and the film's opening block. The
same review as the narration's, without the two things that belong to the narration alone: there is no route that assembles and none that approves. One factory
makes the routes of each kind (documentary > Voice review)."""

from __future__ import annotations

from types import ModuleType

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.files import serve as serve_file
from app.db.base import get_db
from app.db.models import StoryVersion, VoiceReview
from app.documentary.block_review import api as A
from app.documentary.block_review import edits as E
from app.documentary.block_review import service as S


def build_router(base: str, kind: str, adapter: ModuleType, parts, finishable: bool = False) -> APIRouter:
    """`base` is the route segment (`host-voice-review`), `adapter` the module with the review's functions (current_review, open_review, allowed, audio_path),
    `parts(db, version)` the blocks the version has (no review can be opened without them). A `finishable` review (the short clips') has two more routes: the owner says
    he is finished (the voices are final and the next steps may go on) and takes it back; any change of a block, a take or a text reopens it."""
    router = APIRouter()

    def speed_of(version: StoryVersion) -> float | None:
        """The local speed the blocks of this kind have, when it is not the language's own (the opening's is faster)."""
        return adapter.post_speed(version.language or "") if hasattr(adapter, "post_speed") else None

    def _review(db: Session, version: StoryVersion) -> VoiceReview:
        review = adapter.current_review(db, version)
        if review is None:
            raise HTTPException(status_code=409, detail=f"Open the review of the {kind} voice first")
        return review

    def overview(db: Session, version: StoryVersion, review: VoiceReview) -> dict:
        blocks = S.blocks_of(db, review)
        finished = finishable and review.status == "approved"
        return {"exists": True, "kind": kind, "version_id": version.id, "language": review.language, "status": review.status if finishable else "open", "assembled_current": False,
                "assembled_at": None, "approved": finished, "assembled_audio_url": None, "corrected_blocks": sum(1 for b in blocks if b.edited),
                "to_send": sum(1 for b in blocks if S.needs_sending(db, b)), "blocks": [A.block_view(db, version, b, base, speed_of(version)) for b in blocks]}

    def _reopen(db: Session, review: VoiceReview) -> None:
        """A finished review is open again as soon as anything in it changes."""
        if finishable and review.status == "approved":
            review.status = "open"
            db.commit()

    root = f"/api/documentary/versions/{{version_id}}/{base}"

    @router.get(root)
    def get_review(version_id: int, db: Session = Depends(get_db)):
        version = A._version(db, version_id)
        review = adapter.current_review(db, version)
        if review is None:
            return {"exists": False, "kind": kind, "version_id": version.id, "can_open": adapter.allowed(version.language or "") and bool(parts(db, version))}
        return overview(db, version, review)

    @router.post(f"{root}/open")
    def open_review(version_id: int, db: Session = Depends(get_db)):
        version = A._version(db, version_id)
        return overview(db, version, A._guard(lambda: adapter.open_review(db, version)))

    @router.put(f"{root}/blocks/{{block_id}}")
    def save_edit(version_id: int, block_id: str, body: A.EditIn, db: Session = Depends(get_db)):
        version = A._version(db, version_id)
        review = _review(db, version)
        try:
            block = A._guard(lambda: S.save_edit(db, review, block_id, body.text, [E.WordEdit(w.n, w.surface, w.finglish) for w in body.words]))
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e)) from e
        _reopen(db, review)
        return A.block_view(db, version, block, base, speed_of(version))

    @router.post(f"{root}/blocks/{{block_id}}/send")
    async def send_block(version_id: int, block_id: str, body: A.SendIn | None = None, db: Session = Depends(get_db)):
        version = A._version(db, version_id)
        review = _review(db, version)
        try:
            await S.send_block(db, review, block_id, again=bool(body and body.again))
        except S.ReviewError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        _reopen(db, review)
        return A.block_view(db, version, S.block_of(db, review, block_id), base, speed_of(version))

    @router.post(f"{root}/replace")
    def replace_everywhere(version_id: int, body: A.ReplaceIn, db: Session = Depends(get_db)):
        version = A._version(db, version_id)
        review = _review(db, version)
        matches = A._guard(lambda: S.replace_everywhere(db, review, body.find, body.replace, body.apply))
        if body.apply and matches:
            _reopen(db, review)
        return {"applied": body.apply, "blocks_found": len(matches), "places_found": sum(m["count"] for m in matches), "matches": matches,
                **(overview(db, version, review) if body.apply else {})}

    @router.post(f"{root}/send-all")
    async def send_all(version_id: int, db: Session = Depends(get_db)):
        """Voices every corrected block that has not been sent yet, one after the other (a block that fails stays a draft and is reported)."""
        version = A._version(db, version_id)
        review = _review(db, version)
        sent, failed = [], []
        for block in S.blocks_of(db, review):
            if not S.needs_sending(db, block):
                continue
            try:
                await S.send_block(db, review, block.block_id)
                sent.append(block.block_id)
            except Exception as e:  # noqa: BLE001 -- one block failing must not stop the others; it is reported
                db.rollback()
                failed.append({"block_id": block.block_id, "error": str(e)[:300]})
        if sent:
            _reopen(db, review)
        return {"sent": sent, "failed": failed, **overview(db, version, review)}

    @router.post(f"{root}/blocks/{{block_id}}/takes/{{take_no}}/activate")
    def activate(version_id: int, block_id: str, take_no: int, db: Session = Depends(get_db)):
        version = A._version(db, version_id)
        review = _review(db, version)
        _ = A._guard(lambda: S.activate(db, review, block_id, take_no))
        _reopen(db, review)
        return A.block_view(db, version, S.block_of(db, review, block_id), base, speed_of(version))

    @router.get(f"{root}/blocks/{{block_id}}/takes/{{take_no}}/audio")
    def take_audio(version_id: int, block_id: str, take_no: int, db: Session = Depends(get_db)):
        version = A._version(db, version_id)
        block = S.block_of(db, _review(db, version), block_id)
        take = next((t for t in S.takes_of(db, block) if t.take_no == take_no), None)
        path = adapter.audio_path(db, block, take) if take else None
        if path is None or not path.exists():
            raise HTTPException(status_code=404, detail="Take not found")
        return serve_file(path, "audio/mpeg")

    if finishable:
        @router.post(f"{root}/finish")
        def finish(version_id: int, db: Session = Depends(get_db)):
            """The owner has listened to everything and is finished: the voices are final. Refused while a corrected block has not been voiced."""
            version = A._version(db, version_id)
            review = _review(db, version)
            waiting = [b.block_id for b in S.blocks_of(db, review) if S.needs_sending(db, b)]
            if waiting:
                raise HTTPException(status_code=409, detail=f"{len(waiting)} corrected block(s) are not voiced yet ({', '.join(waiting[:4])}): send them first")
            review.status = "approved"
            db.commit()
            return overview(db, version, review)

        @router.post(f"{root}/reopen")
        def reopen(version_id: int, db: Session = Depends(get_db)):
            version = A._version(db, version_id)
            review = _review(db, version)
            review.status = "open"
            db.commit()
            return overview(db, version, review)

    return router
