"""Parent rows that other tables point to. Postgres enforces foreign keys (SQLite here does not),
so a test that needs a blueprint or host plan must create a real one, not invent an id."""
import json
import uuid

from app.db.models import EditorialBlueprint, HostPlan, StoryVersion
from app.providers.generation.base import GenerationResult


def make_story_version(db, case, **kw):
    sv = StoryVersion(case_id=case.id, version=kw.pop("version", 1), kind=kw.pop("kind", "master"),
                      language=kw.pop("language", "en"), narrative_angle="test", story_text="x",
                      engagement_score=0.0, **kw)
    db.add(sv)
    db.commit()
    return sv


def make_blueprint(db, case, *, id=None, story_version=None, status="valid"):
    sv = story_version or make_story_version(db, case)
    bp = EditorialBlueprint(case_id=case.id, story_version_id=sv.id, status=status, blueprint_json="{}")
    if id is not None:
        bp.id = id
    db.add(bp)
    db.commit()
    return bp


def make_host_plan(db, case, blueprint=None):
    bp = blueprint or make_blueprint(db, case)
    hp = HostPlan(case_id=case.id, blueprint_id=bp.id)
    db.add(hp)
    db.commit()
    return hp


class DistinctEmbedder:
    """Fake embedding client: every text gets its own direction, so no two
    facts are ever judged duplicates and nothing leaves the process."""

    model = "fake-embed"
    total_tokens = 0
    total_cost_usd = 0.0

    async def embed(self, texts):
        import numpy as np

        return np.eye(len(texts), dtype=np.float32)


def _mk_case(db):
    from app.db.models import Case
    from app.utils import slugify

    title = f"MP Case {uuid.uuid4().hex[:8]}"
    c = Case(canonical_title=title, slug=slugify(title), language="en")
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _add_fact(db, case, claim="F", **kw):
    from app.db.models import Fact

    f = Fact(
        case_id=case.id, claim=claim,
        category=kw.pop("category", "context"),
        confidence=kw.pop("confidence", 0.9), **kw,
    )
    db.add(f)
    db.commit()
    return f


class ScriptGen:
    """Scripted generation provider: deterministic, token/cost reporting."""

    def __init__(self, tokens=0, cost=0.0):
        self.structured = {}   # role -> dict | callable(system, user) -> dict
        self.texts = {}        # role -> str | callable(system, user) -> str
        self.calls = []        # ("text"|"json", role, user)
        self.tokens = tokens
        self.cost = cost

    def is_configured(self):
        return True

    def _result(self, text):
        return GenerationResult(
            text=text, model="m/fake", provider="fake",
            total_tokens=self.tokens or None,
            cost_usd=self.cost or None,
        )

    async def generate_text(self, role, system, user):
        self.calls.append(("text", role, user))
        v = self.texts.get(role, _sized_text)
        out = v(system, user) if callable(v) else v
        return self._result(out)

    async def generate_structured(self, role, system, user):
        self.calls.append(("json", role, user))
        v = self.structured.get(role)
        out = v(system, user) if callable(v) else v
        if out is None:
            out = _default_structured(role)
        return out, self._result("{}")

    def count(self, kind, role):
        return sum(1 for c in self.calls if c[0] == kind and c[1] == role)


def _sized_text(system, user):
    """Default text generator: produce ~the requested word budget."""
    u = json.loads(user)
    n = (
        (u.get("act") or {}).get("target_words")
        or len((u.get("section_text") or "").split())
        or len((u.get("paragraph") or "").split())
        or 400
    )
    n = max(int(n), 5)
    return "narration " + " ".join(f"w{i}" for i in range(n))


def _default_structured(role):
    table = {
        "story_director": {
            "title": "t", "central_question": "q",
            "acts": [
                {"id": "act1", "purpose": "p1", "evidence_ids": []},
                {"id": "act2", "purpose": "p2", "evidence_ids": []},
            ],
        },
        "grounding_validator": {
            "grounding_score": 0.95, "supported_claims": [],
            "unsupported_claims": [], "uncertainty_errors": [],
        },
        "consistency_checker": {"violations": []},
    }
    return table.get(role, {})
