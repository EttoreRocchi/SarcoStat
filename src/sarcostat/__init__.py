"""SarcoStat: sarcomere and myofibril analysis with circular statistics and morphometry.

Subpackages
-----------
core
    Pure-numerical primitives - circular statistics and single-sarcomere
    morphometry. No I/O, no plotting, no GUI dependencies.
simulation
    Monte Carlo validation tools for circular metrics and morphometry,
    including coverage validation and minimum-detectable-effect analysis.
gui
    Interactive Tk-based annotation and analysis window.
cli
    Typer/rich command-line interface (entry point ``sarcostat``).

Public API
----------
The recommended import path is the qualified subpackage form, for example
``from sarcostat.core import analyze_segment_sequence``. Top-level imports
are intentionally minimal so that importing :mod:`sarcostat` does not pull
in matplotlib, scipy, or Tk.
"""

__version__ = "0.2.0rc1.dev0"

__all__ = ["__version__"]
