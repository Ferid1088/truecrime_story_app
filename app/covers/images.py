"""The scene picture of a cover, made once by the image model from the planner's scene description in the language's theme.

The model paints only the scene: no text, no logo, no person, and an empty area where the title and the avatar go (on the side the language reads from). Every attempt
is recorded in `cover_images` (model, tokens of text and image in and out, runtime, retry number, cost) and every successful one in the ledger as an estimate: the
gateway reports tokens but no price, and the only rate the app knows (output image tokens) is the owner's, not yet confirmed by a balance measurement."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.covers import compose as C
from app.covers import service as SV
from app.db.models import Case, CoverImage, CoverProposal
from app.documentary import storage
from app.providers.image.apimaster import APIMasterImageProvider, ImageProviderError
from app.services.budget import BudgetService
from app.services.cost_model import load_pricing

RTL = ("fa", "ar")


def prompt_for(language: str, kind: str, scene: str) -> str:
    """The text sent to the image model: the language's look, the scene, what the picture never shows and where it stays empty."""
    cfg = ai_config.covers_settings()
    theme = cfg.themes[language]
    empty = round(cfg.scene_image["empty_fraction"][kind] * 100)
    where = ("Tall 2:3 frame. Keep the top {n}% of the frame dark and empty (calm negative space)." if kind == "short"
             else "Wide 3:2 frame. Keep the " + ("right" if language in RTL else "left") + " {n}% of the frame dark and empty (calm negative space).").format(n=empty)
    return (f"Photorealistic, restrained, cinematic documentary still for a true-crime film cover. The look of the channel: {theme.look}. The scene: {scene.strip().rstrip('.')}. "
            f"Absolutely no text, no letters, no logo, no watermark, no people, no faces, no gore, no weapons. Also avoid: {', '.join(theme.forbidden)}. {where}")


def scene_issues(scene: str) -> list[str]:
    """What the scene description may not contain (a person, a victim, a weapon ...): the picture is symbolic, never made-up evidence."""
    words = f" {scene.lower()} "
    found = [t.strip() for t in ai_config.covers_settings().scene["forbidden_terms"] if f" {t.strip()} " in words or f" {t.strip()}s " in words]
    return [f"the scene mentions {', '.join(sorted(set(found)))}: a cover scene is symbolic (a door, a lock, a house, an object), never a person, a victim or a weapon"] if found else []


def priced(usage: dict, model: str) -> tuple[float | None, dict]:
    """(USD of the priced part, the token counts). Only the output image tokens have a rate; the input tokens are recorded, not priced."""
    out_details, in_details = usage.get("output_tokens_details") or {}, usage.get("input_tokens_details") or {}
    tokens = {"text_tokens_in": in_details.get("text_tokens"), "image_tokens_in": in_details.get("image_tokens"), "text_tokens_out": out_details.get("text_tokens"),
              "image_tokens_out": out_details.get("image_tokens")}
    rate = (load_pricing().get("image_models") or {}).get(model, {}).get("output_image_usd_per_1m_tokens")
    return (round(tokens["image_tokens_out"] * rate / 1_000_000, 6) if rate and tokens["image_tokens_out"] is not None else None), tokens


def image_dir(case_id: int, language: str):
    d = SV.covers_dir(case_id, language) / "scenes"
    d.mkdir(parents=True, exist_ok=True)
    return d


async def make_image(db: Session, case: Case, language: str, row: CoverProposal, provider: APIMasterImageProvider | None = None, *, again: bool = False) -> CoverImage:
    """The scene picture of a cover row: the one it has (unless `again`), else a new one. Stops before the call when the case's budget is spent; a failed call is
    tried again up to `scene_image.attempts` times, each attempt recorded. Raises ImageProviderError when every attempt failed."""
    cfg = ai_config.covers_settings().scene_image
    if not cfg["enabled"]:
        raise ImageProviderError("the image model is switched off for covers")
    if row.image_id and not again:
        return db.get(CoverImage, row.image_id)
    if not row.scene_prompt.strip():
        raise C.CoverError("the cover has no scene description")
    if problems := scene_issues(row.scene_prompt):
        raise C.CoverError(problems[0])
    provider = provider or APIMasterImageProvider()
    prompt = prompt_for(language, row.kind, row.scene_prompt)
    size, model = cfg["sizes"][row.kind], cfg["model"]
    budget, last_error = BudgetService(db), "no attempt was made"
    for attempt in range(1, cfg["attempts"] + 1):
        budget.check(case.id, cfg["ledger_stage"])
        result = await provider.generate(model, prompt, size, cfg["timeout_s"])
        usd, tokens = priced(result.usage, model)
        record = CoverImage(case_id=case.id, language=language, cover_key=row.cover_key, proposal_id=row.id, attempt=attempt, model=model, size=size, prompt=prompt,
                            ok=result.ok, error=result.error, duration_ms=result.duration_ms, request_id=result.request_id, **tokens)
        db.add(record)
        db.flush()
        if result.ok:
            path = image_dir(case.id, language) / f"{row.cover_key}_{row.id}_{record.id}.png"
            storage.write_atomic(path, result.content)
            record.file_path = storage.rel(path)
            call = budget.record(case_id=case.id, stage=cfg["ledger_stage"], model=model, units=1, cost_usd=usd, tokens_in=result.usage.get("input_tokens"),
                                 tokens_out=result.usage.get("output_tokens"), estimated=True, duration_ms=result.duration_ms, request_id=f"cover_image:{record.id}",
                                 cost_source="assumed-unverified")
            record.cost_usd, record.cost_eur, record.cost_source, record.provider_call_id = usd, call.cost_eur, "assumed-unverified", call.id
            row.image_id = record.id
            db.commit()
            return record
        last_error = result.error or "the image model returned no image"
        db.commit()
    raise ImageProviderError(f"{cfg['attempts']} attempts failed: {last_error}")
