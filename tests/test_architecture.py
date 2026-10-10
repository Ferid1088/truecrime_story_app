"""Guards for the layered structure: prompts, agents, configuration."""

import ast
import json
import re
from pathlib import Path

import pytest

from app.agents import registry
from app.agents.base import Agent
from app.core import prompts
from app.core.ai_config import ai_config, config_files, load_raw_config

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def test_every_prompt_referenced_in_code_exists():
    used = set()
    for f in APP.rglob("*.py"):
        if f.name == "prompts.py":
            continue
        used |= set(re.findall(r'prompt\("([\w/\-]+)"\)', f.read_text(encoding="utf-8")))
    assert used, "no prompts referenced?"
    missing = [u for u in used if u not in prompts.available()]
    assert not missing, missing


def inline_texts() -> set[str]:
    """Long text constants and f-strings that are not docstrings: prompts still in code."""
    found = set()
    for f in APP.rglob("*.py"):
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        for n in ast.walk(tree):
            text = None
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
                text = n.value
            elif isinstance(n, ast.JoinedStr):
                text = ast.get_source_segment(src, n) or ""
            if text and len(text) > 600 and "role" not in f.name:
                found.add(f"{f.relative_to(ROOT)}:{re.sub(r'[^A-Za-z]+', '_', text.strip()[:32])}")
    return found


def test_no_new_prompt_text_in_code():
    """Long inline text is a prompt: it belongs in prompts/. The known remaining
    ones are listed in prompts/INLINE_DEBT.txt; the list may only shrink."""
    debt = set((ROOT / "prompts" / "INLINE_DEBT.txt").read_text().split())
    new = inline_texts() - debt
    assert not new, f"prompt text in code (move it to prompts/): {sorted(new)}"


def test_ready_agents_have_prompt_role_and_class():
    routing = ai_config.generation_provider().routing
    for spec in registry._SPECS.values():
        if spec.status != "ready":
            continue
        agent = registry.get_agent(spec.name, gen=object())
        assert isinstance(agent, Agent) and agent.name == spec.name
        assert spec.role in routing, f"{spec.role} is not routed in models.json"
        assert f"agents/{spec.name}" in prompts.available()


def test_legacy_agents_say_so():
    with pytest.raises(registry.AgentError, match="legacy"):
        registry.get_agent("story_director")
    with pytest.raises(registry.AgentError, match="unknown"):
        registry.get_agent("nope")


def test_agent_ask_uses_its_prompt_role_and_no_model_name():
    from app.agents.naming import NativeTitleCritic
    import asyncio

    seen = {}

    class Gen:
        async def generate_structured(self, role, system, user, images=None):
            seen.update(role=role, system=system, user=user)
            return {"scores": [{"title": "x", "native_quality": 0.9}]}, None

    out = asyncio.run(NativeTitleCritic(Gen()).run(language="de", titles=["x"]))
    assert out and seen["role"] == "native_title_critic" and "German" in seen["system"]


def test_config_layers_are_disjoint_and_complete():
    raw = load_raw_config()
    names = [f.name for f in config_files()]
    assert names[:2] == ["connections.json", "models.json"] and len(names) >= 8
    conn = json.loads(config_files()[0].read_text())
    models = json.loads(config_files()[1].read_text())
    # connections hold endpoints and secret NAMES, models hold aliases and routing
    prov = conn["generation_providers"]["apimaster"]
    assert set(prov) <= {"enabled", "base_url", "secret_env"}
    assert {"models", "routing"} <= set(models["generation_providers"]["apimaster"])
    assert "routing" not in prov and "base_url" not in models["generation_providers"]["apimaster"]
    assert raw["case_naming"] and raw["voice"] and raw["channels"]


def test_agents_endpoint(client):
    rows = client.get("/api/agents").json()
    ready = {r["name"] for r in rows if r["status"] == "ready"}
    assert {"case_naming_agent", "case_title_critic", "native_title_critic", "thumbnail_critic",
            "case_status_verifier"} <= ready
    assert all(r["model_alias"] for r in rows if r["status"] == "ready")
