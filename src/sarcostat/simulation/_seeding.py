"""Deterministic per-cell seeding for the simulation grids.

Each ``(kappa, n)`` cell of a sweep gets its own
:class:`numpy.random.SeedSequence` derived from the master seed and the
cell coordinates, so a cell's random stream does not depend on which
other cells are in the grid, their order, or whether cells run serially
or in a process pool.
"""

from __future__ import annotations

import struct

import numpy as np


def per_cell_seed(base_seed: int, kappa: float, n: int) -> np.random.SeedSequence:
    """Deterministic seed sequence for a ``(kappa, n)`` cell.

    Encodes ``kappa`` via its IEEE-754 byte pattern so every distinct
    float maps to a distinct seed, avoiding the silent collisions of an
    ``int(kappa * 1e6)`` scheme. ``kappa`` is always converted to float
    first, so ``0`` and ``0.0`` give the same stream.
    """
    kappa_lo, kappa_hi = struct.unpack("<II", struct.pack("<d", float(kappa)))
    return np.random.SeedSequence([int(base_seed), int(kappa_lo), int(kappa_hi), int(n)])


__all__ = ["per_cell_seed"]
