"""The punctuation stage of a Persian narration run: for every block its layer, ezafe boundaries, syntax (when the agents run) and review.

Without the agents (config semantic_punctuation.use_agents false, the default: free) only the deterministic rules apply and every boundary they
cannot settle stays EZAFE_CONTEXT_DEPENDENT; nothing is inserted on a guess. With them (billable, each call checked against the case's budget):
the syntax agent, then the punctuation agent's proposals filtered by code, then the independent checker in a bounded loop: PASS keeps the
proposals; REVISE drops the edits it names and is checked again; UNCERTAIN, REJECT or no PASS within `max_rounds` keeps only the rule changes (the
safest version that keeps the meaning) and flags the block. A layer whose words or tags would differ from the approved text is never used.

Identical blocks are analysed once per run; nothing is kept after it."""

from __future__ import annotations

import hashlib
import json
import pickle
import time
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import Session

from app.core.ai_config import ai_config
from app.documentary.punctuation import boundary as BD
from app.documentary.punctuation import check as CK
from app.documentary.punctuation import punctuate as PU
from app.documentary.punctuation import syntax as SY
from app.documentary.punctuation.layer import Edit, Layer, check
from app.documentary.voice_performance import scrub_forbidden


@dataclass
class BlockResult:
    layer: Layer
    boundaries: list[BD.BoundaryDecision]
    syntax: SY.Syntax | None = None
    review: str = "NOT_REVIEWED"         # NOT_REVIEWED (rules only) | PASS | UNCERTAIN | REJECT | UNRESOLVED
    rounds: int = 0
    refused: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)

    def describe(self) -> dict:
        words = self.layer.words
        return {**self.layer.describe(), "word_map": self.layer.word_map(), "boundaries": [b.describe(words) for b in self.boundaries],
                "syntax": self.syntax.describe() if self.syntax else None, "review": self.review, "rounds": self.rounds, "refused": self.refused,
                "flags": self.flags, "checks": self.checks}


class PunctuationStage:
    def __init__(self, db: Session | None, case_id: int | None, use_agents: bool | None = None, syntax_runner=None, punctuate_runner=None,
                 check_runner=None, memo_path: Path | None = None):
        """memo_path: this run's own file for the finished blocks, so a run stopped by a provider error continues without paying again (a local
        file written and read by this code only; another run does not use it)."""
        self.db, self.case_id = db, case_id
        self.memo_path = memo_path
        self.use_agents = ai_config.semantic_punctuation.use_agents if use_agents is None else use_agents
        self.runners = (syntax_runner, punctuate_runner, check_runner)
        self.memo: dict[str, BlockResult] = {}
        if memo_path is not None and memo_path.exists():
            self.memo = pickle.loads(memo_path.read_bytes())

    def remember(self, key: str, out: BlockResult) -> None:
        self.memo[key] = out
        if self.memo_path is not None:
            self.memo_path.parent.mkdir(parents=True, exist_ok=True)
            self.memo_path.write_bytes(pickle.dumps(self.memo))

    def paid(self) -> None:
        if self.db is not None and self.case_id is not None:
            from app.services.budget import BudgetService

            BudgetService(self.db).check(self.case_id, "punctuation")

    async def block(self, b: dict, previous: str = "", following: str = "", english: str = "", alignment_status: str = "MISSING_REFERENCE") -> BlockResult:
        base = scrub_forbidden(b.get("tts_text") or b["text"])
        key = hashlib.sha256(json.dumps([b["text"], base, previous, following, english, self.use_agents], ensure_ascii=False).encode()).hexdigest()
        if key in self.memo:
            found = self.memo[key]
            return BlockResult(Layer(b["block_id"], b["text"], base, list(found.layer.edits)), found.boundaries, found.syntax, found.review,
                               found.rounds, found.refused, found.flags, found.checks)
        layer = Layer(b["block_id"], b["text"], base)
        if check(layer):
            out = BlockResult(layer, [], flags=[f"source integrity: {p}" for p in check(layer)], review="REJECT")
            self.remember(key, out)
            return out
        syntax = None
        if self.use_agents:
            self.paid()
            syntax = await SY.analyze(self.db, self.case_id, b["block_id"], layer.words, previous, following, english, alignment_status, self.runners[0])
        boundaries = BD.analyze(layer.words, layer.gaps(), syntax.boundaries if syntax else None, syntax.ambiguous if syntax else None)
        rules = PU.normalized(layer)
        out = BlockResult(layer, boundaries, syntax)
        out.flags += [f"ambiguous boundary after word {q['n']}: {q['question']}" for q in (syntax.questions if syntax else [])]
        layer.edits = list(rules)
        if self.use_agents:
            await self.agents(out, rules, english, previous, following)
        if check(layer):
            out.flags += [f"source integrity: {p}" for p in check(layer)]
            layer.edits, out.review = [], "REJECT"
        self.remember(key, out)
        return out

    async def agents(self, out: BlockResult, rules: list[Edit], english: str, previous: str, following: str) -> None:
        layer, feedback = out.layer, []
        kept: list[Edit] | None = None
        for round_no in range(1, ai_config.semantic_punctuation.max_rounds + 1):
            out.rounds = round_no
            if kept is None:
                self.paid()
                proposed = await PU.propose(self.db, self.case_id, Layer(layer.block_id, layer.canonical, layer.base, list(rules)), out.boundaries,
                                            out.syntax.describe() if out.syntax else {}, english, feedback, self.runners[1])
                kept = []
                for e in proposed:
                    why = PU.safe(e, layer, out.boundaries)
                    (out.refused.append({**e.describe(), "why": why}) if why else kept.append(e))
            trial = Layer(layer.block_id, layer.canonical, layer.base, rules + kept)
            if check(trial):
                out.refused += [{**e.describe(), "why": "the layer would change more than punctuation"} for e in kept]
                kept = []
                trial = Layer(layer.block_id, layer.canonical, layer.base, list(rules))
            if not kept:
                out.review = "PASS" if not out.refused else "UNCERTAIN"
                break
            self.paid()
            verdict = await CK.check(self.db, self.case_id, layer.block_id, layer.canonical, trial.text, [e.describe() for e in kept],
                                     {"boundaries": [b.describe(layer.words) for b in out.boundaries], "syntax": out.syntax.describe() if out.syntax else None},
                                     english, previous, following, self.runners[2])
            out.checks.append({"round": round_no, **verdict.describe()})
            if verdict.decision == "PASS":
                layer.edits, out.review = rules + kept, "PASS"
                return
            if verdict.decision == "REVISE":
                kept = [e for i, e in enumerate(kept) if i not in verdict.drop]
                feedback = verdict.issues
                continue
            out.review = verdict.decision
            out.flags.append(f"the checker answered {verdict.decision}: only the rule changes are kept ({'; '.join(verdict.issues)})")
            return
        else:
            out.review = "UNRESOLVED"
            out.flags.append(f"no PASS within {ai_config.semantic_punctuation.max_rounds} rounds: only the rule changes are kept")
        layer.edits = list(rules)

    async def run(self, blocks: list[dict], english: dict[str, tuple[str, str]] | None = None) -> tuple[dict[str, BlockResult], dict]:
        """Every block of the plan, in order; `english`: block -> (English passage, alignment status)."""
        started, out = time.monotonic(), {}
        english = english or {}
        for i, b in enumerate(blocks):
            passage, status = english.get(b["block_id"], ("", "MISSING_REFERENCE"))
            out[b["block_id"]] = await self.block(b, blocks[i - 1]["text"] if i else "", blocks[i + 1]["text"] if i + 1 < len(blocks) else "", passage, status)
        return out, statistics(out, time.monotonic() - started, self.use_agents)


def statistics(results: dict[str, BlockResult], seconds: float, agents: bool) -> dict:
    edits = [e for r in results.values() for e in r.layer.edits]
    statuses = [b.status for r in results.values() for b in r.boundaries]
    return {"agents": agents, "blocks": len(results), "blocks_unchanged": sum(1 for r in results.values() if not r.layer.edits),
            "commas_inserted": sum(1 for e in edits if not e.old and e.new == "،"), "marks_inserted": sum(1 for e in edits if not e.old),
            "marks_replaced_or_removed": sum(1 for e in edits if e.old), "ezafe_required": statuses.count(BD.REQUIRED),
            "ezafe_forbidden": statuses.count(BD.FORBIDDEN), "context_dependent": statuses.count(BD.CONTEXT),
            "no_analysis_needed": statuses.count(BD.NONE), "unresolved_ambiguities": sum(len(r.syntax.ambiguous) for r in results.values() if r.syntax),
            "reviewer_rejections": sum(1 for r in results.values() if r.review == "REJECT"),
            "reviews": {s: sum(1 for r in results.values() if r.review == s) for s in ("NOT_REVIEWED", "PASS", "UNCERTAIN", "REJECT", "UNRESOLVED")},
            "refused_proposals": sum(len(r.refused) for r in results.values()),
            "source_integrity_violations": sum(1 for r in results.values() if any(f.startswith("source integrity") for f in r.flags)),
            "seconds": round(seconds, 2)}


async def for_version(db: Session, case_id: int, version, plan: dict, stage: PunctuationStage | None = None) -> tuple[dict[str, BlockResult], dict]:
    """The punctuation of a version's narration blocks; the English passage of each block (local alignment, free) only when the agents run."""
    stage = stage or PunctuationStage(db, case_id)
    english = {}
    if stage.use_agents:
        from app.documentary.english_reference import alignment as AL
        from app.documentary.english_reference import sources as SRC
        from app.documentary.english_reference.embeddings import get_encoder

        sources = SRC.approved_sources(db, version)
        story, text = sources.narration.story_text or "", sources.english.story_text or ""
        persian, units, matches = AL.prepare(story, text, get_encoder())
        english = {b.block_id: (b.english_text, b.status) for b in AL.blocks(plan["blocks"], story, text, persian, units, matches)}
    return await stage.run(plan["blocks"], english)


def applied(plan: dict, results: dict[str, BlockResult]) -> dict:
    """The plan with each block's voice text replaced by its punctuation layer (the story and the block's own text are not touched)."""
    return {**plan, "blocks": [{**b, "tts_text": results[b["block_id"]].layer.text} if b["block_id"] in results else b for b in plan["blocks"]]}


def boundaries_of(results: dict[str, BlockResult]) -> dict[str, dict[int, dict]]:
    """For the planner: each block's settled boundaries (required or forbidden), with their reason and source."""
    return {block: {d.n: {"status": d.status, "reason": d.reason, "source": d.source} for d in r.boundaries if d.status in (BD.REQUIRED, BD.FORBIDDEN)}
            for block, r in results.items()}


def save(folder, results: dict[str, BlockResult], stats: dict) -> None:
    """The record of this run's punctuation next to its narration (read by the owner, never by a run)."""
    from app.documentary import storage

    storage.write_atomic(folder / ai_config.semantic_punctuation.run_file,
                         json.dumps({"statistics": stats, "blocks": [r.describe() for r in results.values()]}, ensure_ascii=False, indent=1))
