"""The orchestrator's admission control: whether one more film job may start now.

Two things limit how much runs at once. a) The own machine: a job is admitted only while the memory that stays free after it (its
expected need counted in advance) and the load per core stay within the configured limits, and two jobs never start closer than
`stagger_seconds` (the memory of the first must show before the second is judged). b) The providers: each has its own concurrent-call
limit (app.core.concurrency) that shrinks when it answers "too many requests"; while the language model's or the voice's limit is
pulled down, no new job is admitted, because it would only add load to a provider that is asking for less. The first job always starts (holding it would stop everything); a job that is not
admitted stays queued, with the reason; it is never dropped."""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from app.core import concurrency
from app.core.ai_config import OrchestratorConfig, ai_config
from app.orchestration.host import HostSample, sample_host

# The limits a film job depends on most: a job is held while one of these is throttled.
PROVIDER_LIMITS = ("llm", "vision", "elevenlabs_tts")


@dataclass(frozen=True)
class Decision:
    allowed: bool
    code: str        # admit | cap | stagger | memory | cpu | provider
    reason: str


class Governor:
    def __init__(self, cfg: OrchestratorConfig | None = None, sampler: Callable[[], HostSample] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.cfg = cfg or ai_config.orchestrator_settings()
        self.sampler, self.clock = sampler or (lambda: sample_host()), clock
        self.last_admitted: float | None = None
        self.last: Decision = Decision(True, "admit", "no job has been judged yet")
        self.events: deque[dict] = deque(maxlen=self.cfg.event_log_size)

    def decide(self, running: int, cap: int) -> Decision:
        """May one more job start, given `running` jobs and the worker's cap?"""
        decision = self._judge(running, cap)
        if decision.code != self.last.code:
            self.events.append({"at": time.time(), "code": decision.code, "reason": decision.reason})
        self.last = decision
        return decision

    def _judge(self, running: int, cap: int) -> Decision:
        if running >= cap:
            return Decision(False, "cap", f"{running} of {cap} film jobs are running")
        now = self.clock()
        if self.last_admitted is not None and now - self.last_admitted < self.cfg.stagger_seconds:
            wait = self.cfg.stagger_seconds - (now - self.last_admitted)
            return Decision(False, "stagger", f"the last film started {int(now - self.last_admitted)} s ago; the next one starts in {int(wait) + 1} s")
        if running == 0:
            return Decision(True, "admit", "no film is running: the first one always starts")      # holding it would stop everything
        host, limits = self.sampler(), self.cfg.host
        after = host.free_gb - limits.job_memory_gb
        if after < limits.min_free_memory_gb:
            return Decision(False, "memory", f"{host.free_gb:.1f} GB memory is free; one more film needs {limits.job_memory_gb:.1f} GB "
                                             f"and {limits.min_free_memory_gb:.1f} GB must stay free")
        if host.load_per_core > limits.max_load_per_core:
            return Decision(False, "cpu", f"the machine's load is {host.load_per_core:.2f} per core (limit {limits.max_load_per_core:.2f})")
        held = [name for name in PROVIDER_LIMITS if concurrency.throttled(name, now=None)]
        if held:
            return Decision(False, "provider", "a provider is asking for less: " + ", ".join(
                f"{name} limit {concurrency.effective_limit(name)} of {concurrency.limit_size(name)}" for name in held))
        return Decision(True, "admit", "the machine and the providers have room")

    def decide_short(self, running: int, cap: int) -> Decision:
        """May one more Shorts job start? Shorts are light, so only the cap, the machine's load and the providers count; the first
        one always starts."""
        if running >= cap:
            return Decision(False, "cap", f"{running} of {cap} jobs are running")
        if running == 0:
            return Decision(True, "admit", "no Shorts job is running: the first one always starts")
        host = self.sampler()
        if host.load_per_core > self.cfg.host.max_load_per_core:
            return Decision(False, "cpu", f"the machine's load is {host.load_per_core:.2f} per core (limit {self.cfg.host.max_load_per_core:.2f})")
        held = [name for name in PROVIDER_LIMITS if concurrency.throttled(name, now=None)]
        if held:
            return Decision(False, "provider", "a provider is asking for less: " + ", ".join(held))
        return Decision(True, "admit", "the machine and the providers have room")

    def admitted(self) -> None:
        """A job was taken: the stagger clock starts."""
        self.last_admitted = self.clock()

    def snapshot(self, running: int, cap: int) -> dict:
        host, active = self.sampler(), concurrency.usage()
        return {"host": host.as_dict(), "limits": self.cfg.host.model_dump(), "running": running, "cap": cap,
                "decision": {"allowed": self.last.allowed, "code": self.last.code, "reason": self.last.reason},
                "providers": {name: {"active": int(active.get(name, {}).get("active", 0)), "configured": concurrency.limit_size(name), "limit": concurrency.effective_limit(name),
                                     "throttled": concurrency.throttled(name)} for name in sorted(ai_config.concurrency.model_dump())},
                "events": list(self.events)}
