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


_KEYS = ("you are", "json", "return", "task", "output", "do not", "never", "must", "narrat",
         "write ", "update research", "targeted follow-up")


def _is_prompt(text: str) -> bool:
    low = text.lower()
    return (len(text) >= 200 and any(k in low for k in _KEYS) and "provider calls=" not in low
            and not low.lstrip().startswith(("select ", "create ", "alter ")))


def inline_texts() -> set[str]:
    """Long instruction-like text constants and f-strings that are not docstrings:
    prompts still in code."""
    found = set()
    for f in APP.rglob("*.py"):
        if f.name == "prompts.py":
            continue
        src = f.read_text(encoding="utf-8")
        tree = ast.parse(src)
        docs = {id(n.body[0].value) for n in ast.walk(tree)
                if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
                and n.body and isinstance(n.body[0], ast.Expr)
                and isinstance(n.body[0].value, ast.Constant)}
        parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        for n in ast.walk(tree):
            text = None
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs \
                    and not isinstance(parent.get(n), (ast.JoinedStr, ast.FormattedValue)):
                text = n.value
            elif isinstance(n, ast.JoinedStr):
                text = "".join(v.value for v in n.values if isinstance(v, ast.Constant))
            if text and _is_prompt(text):
                found.add(f"{f.relative_to(ROOT)}:{re.sub(r'[^A-Za-z]+', '_', text.strip()[:32])}")
    return found


def test_no_new_prompt_text_in_code():
    """Long inline text is a prompt: it belongs in prompts/. The known remaining
    ones are listed in prompts/INLINE_DEBT.txt; the list may only shrink."""
    debt = set((ROOT / "prompts" / "INLINE_DEBT.txt").read_text().split())
    new = inline_texts() - debt
    assert not new, f"prompt text in code (move it to prompts/): {sorted(new)}"


def test_every_agent_is_defined_routed_and_has_its_prompt():
    from app.core.ai_config import REQUIRED_ROLES

    routing = ai_config.generation_provider().routing
    for name, d in ai_config.agents.items():
        assert d.role in REQUIRED_ROLES and d.role in routing, f"{name}: role {d.role} not routed"
        if d.prompt:
            assert d.prompt in prompts.available(), f"{name}: prompt {d.prompt} missing"
    for name in registry.CLASSES:
        agent = registry.get_agent(name, gen=object())
        assert isinstance(agent, Agent) and agent.name == name and name in ai_config.agents


def test_agent_names_in_code_are_defined_and_every_agent_is_used():
    used = set()
    for f in APP.rglob("*.py"):
        used |= set(re.findall(r'run_agent\(\s*"([\w.]+)"', f.read_text(encoding="utf-8")))
        used |= set(re.findall(r'^\s*name = "([\w.]+)"', f.read_text(encoding="utf-8"), re.M)
                    if f.parent.name == "agents" else [])
    dynamic = {n for n, d in ai_config.agents.items() if d.prompt is None and n == d.role}
    assert used <= set(ai_config.agents), sorted(used - set(ai_config.agents))
    unused = set(ai_config.agents) - used - dynamic
    assert not unused, f"defined but never run: {sorted(unused)}"


def test_every_model_call_goes_through_run_agent():
    """Stages never call the provider with a role themselves."""
    offenders = []
    for f in APP.rglob("*.py"):
        if f.parts[-3:-1] == ("providers", "generation") or f.name == "runner.py":
            continue
        if re.search(r"\.generate_(structured|text)\(", f.read_text(encoding="utf-8")):
            offenders.append(str(f.relative_to(ROOT)))
    assert not offenders, offenders


def test_run_agent_uses_definition_and_role_override():
    import asyncio
    from app.agents.runner import run_agent

    seen = {}

    class Gen:
        async def generate_structured(self, role, system, user, images=None):
            seen.update(role=role, system=system, user=user)
            return {"ok": 1}, "res"

        async def generate_text(self, role, system, user, images=None):
            seen.update(role=role, system=system)
            return "text-res"

    d, _ = asyncio.run(run_agent("story.consistency", Gen(), "u", system="SYS"))
    assert seen["role"] == "consistency_checker" and seen["system"] == "SYS" and d == {"ok": 1}
    asyncio.run(run_agent("story.final_edit", Gen(), "u", system="S2", role="master_final_editor"))
    assert seen["role"] == "master_final_editor"
    out = asyncio.run(run_agent("localization.final_edit", Gen(), "u", language="German", marker_instruction=""))
    assert out == "text-res" and "German" in seen["system"]


def test_unknown_agent_and_class_less_agent_say_so():
    with pytest.raises(registry.AgentError, match="unknown"):
        registry.get_agent("nope")
    with pytest.raises(registry.AgentError, match="run_agent"):
        registry.get_agent("story.consistency")


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
    ready = {r["name"] for r in rows}
    assert {"case_naming_agent", "story.consistency", "visuals.audit", "documentary.blueprint"} <= ready
    assert all(r["model_alias"] for r in rows)


def test_stage_agents_resolve_without_building():
    from app.agents.stages import STAGE_AGENTS, stage_agent_class

    for name in STAGE_AGENTS:
        cls = stage_agent_class(name)
        assert isinstance(cls, type), name
