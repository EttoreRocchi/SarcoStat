"""Scoped matplotlib styling for the simulation figures.

Until 0.2.0 the rigor modules called ``plt.style.use`` and
``sns.set_palette`` at import time, which restyled every figure in the
importing process. :func:`simulation_style` applies the same look only
inside a ``with`` block, around both figure creation and saving.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import matplotlib.pyplot as plt
import seaborn as sns

SIMULATION_STYLE = "seaborn-v0_8-darkgrid"
SIMULATION_PALETTE = "husl"


@contextmanager
def simulation_style() -> Iterator[None]:
    """Apply the simulation figure style for the duration of the block."""
    with plt.style.context(SIMULATION_STYLE), sns.color_palette(SIMULATION_PALETTE):
        yield


__all__ = ["simulation_style"]
