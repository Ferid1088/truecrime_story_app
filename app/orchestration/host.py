"""What the own machine has to give right now: free memory and the load per core."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass

import psutil


@dataclass(frozen=True)
class HostSample:
    free_gb: float
    total_gb: float
    load_per_core: float
    cores: int

    def as_dict(self) -> dict:
        return {k: round(v, 2) if isinstance(v, float) else v for k, v in asdict(self).items()}


def sample_host() -> HostSample:
    memory = psutil.virtual_memory()
    cores = os.cpu_count() or 1
    return HostSample(free_gb=memory.available / 2**30, total_gb=memory.total / 2**30,
                      load_per_core=os.getloadavg()[0] / cores, cores=cores)
