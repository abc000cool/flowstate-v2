"""Seeded randomness discipline (CLAUDE.md §0.5).

Every stochastic component takes an explicit integer seed. Replicate seeds are
spawned deterministically from a master seed via ``numpy.random.SeedSequence``
so that adding replicates never reshuffles existing ones.
"""

from __future__ import annotations

import numpy as np

SUMO_SEED_MOD = 2**31  # SUMO --seed accepts a 31-bit int


def make_rng(seed: int) -> np.random.Generator:
    """Create a PCG64 generator from an explicit seed."""
    return np.random.Generator(np.random.PCG64(seed))


def spawn_seeds(master_seed: int, n: int) -> list[int]:
    """Derive ``n`` independent replicate seeds from ``master_seed``.

    Deterministic: the first k seeds are identical for any n >= k.
    """
    if n < 0:
        raise ValueError(f"n must be >= 0, got {n}")
    children = np.random.SeedSequence(master_seed).spawn(n)
    return [int(c.generate_state(1, dtype=np.uint64)[0] % (2**63)) for c in children]


def sumo_seed(seed: int) -> int:
    """Map an arbitrary integer seed into SUMO's accepted 31-bit range."""
    return seed % SUMO_SEED_MOD


def truncated_normal(
    rng: np.random.Generator,
    mean: float,
    sigma: float,
    n_sigma: float = 3.0,
    low: float | None = None,
    high: float | None = None,
) -> float:
    """Draw from N(mean, sigma) truncated at ±n_sigma and optional hard bounds.

    Used for per-vehicle IDM parameter heterogeneity (CLAUDE.md §3.1).
    Rejection sampling; falls back to clipping after 1000 draws (practically
    unreachable for the ±3σ default).
    """
    if sigma < 0:
        raise ValueError(f"sigma must be >= 0, got {sigma}")
    lo = mean - n_sigma * sigma
    hi = mean + n_sigma * sigma
    if low is not None:
        lo = max(lo, low)
    if high is not None:
        hi = min(hi, high)
    if lo > hi:
        raise ValueError(f"empty truncation interval [{lo}, {hi}]")
    if sigma == 0.0:
        return float(min(max(mean, lo), hi))
    for _ in range(1000):
        x = float(rng.normal(mean, sigma))
        if lo <= x <= hi:
            return x
    return float(min(max(mean, lo), hi))
