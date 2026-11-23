"""SarcoStat: sarcomere and myofibril analysis with circular statistics and morphometry.

Subpackages
-----------
core
    Pure-numerical primitives - circular statistics and single-sarcomere
    morphometry. No I/O, no plotting, no GUI dependencies.
io
    Annotation format v1, session kits of the observer study and provenance.
analysis, export
    The one analysis path from an annotation file, and its result files.
engine
    The interaction rules of the web app (annotation and study modes) and
    the JSON bridge its worker calls; the app itself is at
    https://ettorerocchi.github.io/SarcoStat/.
simulation
    Monte Carlo validation tools for circular metrics and morphometry,
    including coverage validation and minimum-detectable-effect analysis.
cli
    Command-line interface (entry point ``sarcostat``; ``analyze`` and ``plan``
    need the ``[cli]`` extra, ``sim`` the ``[sim]`` extra).

Public API
----------
The recommended import path is the qualified subpackage form, for example
``from sarcostat.core import analyze_segment_sequence``. Top-level imports
are intentionally minimal so that importing :mod:`sarcostat` does not pull
in matplotlib or scipy.
"""

__version__ = "0.2.0b1"

__all__ = ["__version__"]
