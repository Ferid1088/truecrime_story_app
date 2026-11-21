"""A scripted model for the creative pack, shared by the tests: GPT-6 answers its ONE request with a pack for every language it is given, GPT-Luna answers its ONE request with the same pack
rewritten. `make_clips` stores such a pack the way the application does. No real model is ever called."""

import asyncio
import json

from app.providers.generation.base import GenerationError, GenerationResult
from app.shortform import pack as PK

FILL = {"fa": "در خانه شب سایه نامه همسایه تلفن آشپزخانه گزارش پنجره خیابان خانواده ساعت لحظه دفتر راه باغ".split(),
        "ar": "في البيت ليلا ظل رسالة جار هاتف مطبخ تقرير نافذة شارع عائلة ساعة لحظة دفتر طريق حديقة".split()}
LATIN = "door morning house letter neighbour phone kitchen statement detail question street evening window answer silence family hour moment report note road garden".split()


def filler(code: str, n: int, seed: int = 0) -> str:
    words = FILL.get(code, LATIN)
    return " ".join(words[(seed + i) % len(words)] for i in range(n))


def gpt6_answer(request: dict) -> dict:
    """What GPT-6 returns: five anchors, the situations, a summary, speculations and, for every language it was given, the film's title and cover and five Shorts (a hook, a story and an avatar line,
    ONE title and ONE cover text each)."""
    keys = request["clip_keys"]
    languages = {}
    for code in request["languages"]:
        languages[code] = {"film": {"title": filler(code, 9), "cover_text": filler(code, 8, 2)},
                           "shorts": [{"clip_key": k, "hook": filler(code, 5, i) + ".", "story": filler(code, 14 + i, i + 2) + ". " + filler(code, 6, i + 5) + ".", "avatar": filler(code, 5, i + 7) + "?",
                                       "title": filler(code, 9, i + 1), "cover_text": filler(code, 8 + i % 3, i + 3)} for i, k in enumerate(keys)]}
    return {"anchors": [{"clip_key": k, "anchor": f"plain summary {i} of what happened and who said it"} for i, k in enumerate(keys)],
            "situations": [{"theme": 1 + i % 7, "text": f"situation number {i} of the story, plainly told"} for i in range(request["situation_count"])],
            "speculations": [{"theme": 1 + i % 7, "text": f"a bold unproven what if number {i}"} for i in range(request["speculation_count"])], "summary": "a plain summary of the whole story", "languages": languages}


class PackGen:
    """The two creative models. `damage(pack)` changes GPT-6's answer, `polish(languages)` changes what Luna returns (default: every text gets one more lower-case word), `luna` can be "fail" (a
    GenerationError), "words" (an answer without JSON) or "off"."""

    def __init__(self, damage=None, polish=None, luna="ok", cost=0.02):
        self.calls, self.damage, self.polish, self.luna, self.cost = [], damage, polish, luna, cost

    def is_configured(self):
        return True

    def called(self, role: str) -> list:
        return [data for r, data in self.calls if r == role]

    def _result(self, role: str, text: str = "{}") -> GenerationResult:
        return GenerationResult(text=text, model=f"fake/{role}", provider="fake", input_tokens=1000, output_tokens=500, total_tokens=1500, cost_usd=self.cost)

    async def generate_structured(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        assert role == "creative_pack", role
        pack = gpt6_answer(data)
        if self.damage:
            self.damage(pack)
        return pack, self._result(role)

    async def generate_text(self, role, system, user, images=None):
        data = json.loads(user)
        self.calls.append((role, data))
        assert role == "creative_polisher", role
        if self.luna == "fail":
            raise GenerationError("provider", "the gateway gave an empty completion", provider="fake")
        if self.luna == "words":
            return self._result(role, "I rewrote everything, here it is: it was lovely.")
        languages = {code: {"film": {k: f"{v} polished" for k, v in body["film"].items()},
                            "shorts": [{**s, "hook": f"{s['hook'][:-1]} polished{s['hook'][-1]}", "story": f"{s['story']}", "avatar": f"{s['avatar'][:-1]} polished{s['avatar'][-1]}", "title": f"{s['title']} polished",
                                        "cover_text": f"{s['cover_text']} polished"} for s in body["shorts"]]}
                     for code, body in data["languages"].items()}
        if self.polish:
            self.polish(languages)
        picks = [{"theme": 1 + i % 7, "text": f"an anonymous pick number {i}"} for i in range(data["pick_count"])]
        speculations = [{"theme": 1 + i % 7, "text": f"a paranoid unproven theory number {i}"} for i in range(data["speculation_count"])]
        return self._result(role, "Here is the final version:\n```json\n" + json.dumps({"picks": picks, "speculations": speculations, "languages": languages}, ensure_ascii=False) + "\n```")


def make_clips(db, case, version, languages=None, keep_titles=False, **gen_kw):
    """The pack of the case stored as Short clips, titles and covers: the English master clips are returned, with the model used. The title candidates and cover texts it stored are removed again
    unless `keep_titles` (a test of the covers or of the titles makes its own)."""
    from app.db.models import CaseTitleCandidate, CoverProposal, EpisodeIdentity, ShortClip

    gen = PackGen(**gen_kw)
    story = PK.story_of(db, case, version)
    result = asyncio.run(PK.generate(gen, story, languages, case_key=f"test-{case.id}-{id(gen)}"))
    PK.store(db, case, version, result)
    if not keep_titles:
        db.query(CoverProposal).filter(CoverProposal.case_id == case.id).delete()
        db.query(EpisodeIdentity).filter(EpisodeIdentity.case_id == case.id).delete()
        db.query(CaseTitleCandidate).filter(CaseTitleCandidate.case_id == case.id).delete()
        db.commit()
    return gen, [r for r in db.query(ShortClip).filter(ShortClip.case_id == case.id, ShortClip.language == "en").order_by(ShortClip.id)]
