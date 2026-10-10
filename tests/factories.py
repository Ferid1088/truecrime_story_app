"""Parent rows that other tables point to. Postgres enforces foreign keys (SQLite here does not),
so a test that needs a blueprint or host plan must create a real one, not invent an id."""
from app.db.models import EditorialBlueprint, HostPlan, StoryVersion


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
