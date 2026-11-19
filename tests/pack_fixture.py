"""A scripted model for the creative pack, shared by the tests: it answers the ONE request of the whole pack with a valid pack for every language (`damage` changes its first answer) and
`make_clips` stores such a pack the way the application does."""

import asyncio
import json

from app.shortform import pack as PK

FILL = {"fa": "در خانه شب سایه نامه همسایه تلفن آشپزخانه گزارش پنجره خیابان خانواده ساعت لحظه دفتر راه باغ".split(),
        "ar": "في البيت ليلا ظل رسالة جار هاتف مطبخ تقرير نافذة شارع عائلة ساعة لحظة دفتر طريق حديقة".split()}
LATIN = "door morning house letter neighbour phone kitchen statement detail question street evening window answer silence family hour moment report note road garden".split()


def filler(code: str, n: int, seed: int = 0) -> str:
    words = FILL.get(code, LATIN)
    return " ".join(words[(seed + i) % len(words)] for i in range(n))


def good_language(code: str, limits: dict, slots: list[dict], fact_ids: list[str]) -> dict:
    words = limits["words"]
    mid = lambda part: (words[part][0] + words[part][1]) // 2          # noqa: E731
    title_n, cover_n = limits["title_words"][0] + 1, limits["cover_words"][0] + 1
    question = "؟" if code in ("fa", "ar") else "?"
    shorts = []
    for i, slot in enumerate(slots):
        shorts.append({"clip_key": slot["clip_key"], "angle": slot["angle"], "idea": f"idea {i}", "hook": filler(code, mid("hook"), i), "context": filler(code, mid("context"), i + 1),
                       "detail": filler(code, mid("detail"), i + 2), "reaction": filler(code, mid("reaction") - 3, i + 3) + question, "cta": filler(code, 2, i),
                       "title_candidates": [filler(code, title_n, k) for k in range(3)], "title": filler(code, title_n, i), "title_backup": filler(code, title_n, i + 5),
                       "cover_candidates": [filler(code, cover_n, k) for k in range(3)], "cover_text": filler(code, cover_n, i), "cover_backup": filler(code, cover_n, i + 4), "cover_alternative": filler(code, cover_n, i + 6),
                       "evidence_ids": [fact_ids[i % len(fact_ids)]], "tease_question": "why?"})
    film = {"title_candidates": [filler(code, title_n, k) for k in range(3)], "title": filler(code, title_n), "title_backup": filler(code, title_n, 3), "cover_candidates": [filler(code, cover_n, k) for k in range(3)],
            "cover_text": filler(code, cover_n), "cover_backup": filler(code, cover_n, 2), "hook": filler(code, 8), "hook_justification": "F: the call"}
    return {"film": film, "shorts": shorts}


class PackGen:
    """A scripted model that answers the one request of the whole pack; `damage` changes its first answer, `second` is what it answers to the repair."""

    def __init__(self, damage=None, junk=False):
        self.calls, self.damage, self.junk = [], damage, junk

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        if self.junk:
            return {"languages": "nonsense", "scouting": [1, 2, 3]}, None
        fact_ids = [f["id"] for f in data["facts"]] or ["F001"]
        concepts = [{"concept": f"concept {i}", "category": "Desire, affairs and murder", "angle": "question", "act_ids": ["act1"], "evidence_ids": [fact_ids[i % len(fact_ids)]], "proof": "quote",
                     "status": "testimony", "risk": "none", "scores": {"emotional_intensity": 9 - i * 0.3, "specificity_originality": 8, "question_strength": 8, "case_connection": 8, "reliability": 8}}
                    for i in range(data["minimum_concepts"])]
        pack = {"scouting": {"concepts": concepts, "chosen": [{"clip_key": s["clip_key"], "index": i, "why": "strongest"} for i, s in enumerate(data["slots"])], "runner_ups": [5, 6]},
                "languages": {code: good_language(code, limits, data["slots"], fact_ids) for code, limits in data["languages"].items()},
                "checks": {"first": "facts and rules checked", "second": "strength and consistency checked"}, "flags": []}
        if self.damage and "problems_to_fix" not in data:
            self.damage(pack)
        return pack, None


def make_clips(db, case, version, languages=None, keep_titles=False, **gen_kw):
    """The pack of the case stored as Short clips, titles and covers: the English master clips are returned, with the model used. The title candidates and cover texts it stored are removed again
    unless `keep_titles` (a test of the covers or of the titles makes its own)."""
    from app.db.models import CaseTitleCandidate, CoverProposal, EpisodeIdentity, ShortClip

    gen = PackGen(**gen_kw)
    story = PK.story_of(db, case, version)
    result = asyncio.run(PK.generate(gen, story, languages))
    PK.store(db, case, version, result)
    if not keep_titles:
        db.query(CoverProposal).filter(CoverProposal.case_id == case.id).delete()
        db.query(EpisodeIdentity).filter(EpisodeIdentity.case_id == case.id).delete()
        db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id).delete()
        db.commit()
    return gen, [r for r in db.query(ShortClip).filter(ShortClip.case_id == case.id, ShortClip.language == "en").order_by(ShortClip.id)]
