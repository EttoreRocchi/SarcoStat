"""Stateless helpers that bridge 6-point annotations to morphometry.

The two functions here are free functions with no instance state. The
noise model is injected explicitly so that callers can mix any
``(generator, noise_model)`` pair without subclassing.
"""

from __future__ import annotations

import numpy as np

from sarcostat.core.sarcomere_morphometry import (
    Point,
    SarcomereGeometry,
    calculate_sarcomere_geometry,
)

from .noise import NoiseModel


def compute_metrics_from_points(points: dict[str, Point]) -> SarcomereGeometry | None:
    """Wrap :func:`calculate_sarcomere_geometry` and trap zero-length errors.

    Parameters
    ----------
    points : dict
        Six-point annotation (keys ``A``-``F``).

    Returns
    -------
    SarcomereGeometry or None
        ``None`` is returned when the underlying geometry is degenerate
        (any zero-length line raises ``ValueError`` in the core module).
    """
    try:
        return calculate_sarcomere_geometry(
            point_a=points["A"],
            point_b=points["B"],
            point_c=points["C"],
            point_d=points["D"],
            point_e=points["E"],
            point_f=points["F"],
            unit="nm",
        )
    except ValueError:
        return None


def compute_metrics_with_noise(
    ground_truth_points: dict[str, Point],
    noise_model: NoiseModel,
    n_replicates: int,
    rng: np.random.Generator,
) -> dict[str, np.ndarray]:
    """Compute morphometry metrics over ``n_replicates`` noisy replicas.

    Parameters
    ----------
    ground_truth_points : dict
        Noise-free 6-point annotation.
    noise_model : NoiseModel
        Instance whose :meth:`apply` is called once per replicate.
    n_replicates : int
        Number of replicates to draw.
    rng : numpy.random.Generator
        Shared RNG (a single Generator is sufficient because the noise
        model is responsible for all randomness).

    Returns
    -------
    dict
        Keys ``asr``, ``cur``, ``shape_index``, ``shape_class``. The
        first three are float arrays of length ``n_replicates``; the
        last is an object array of strings (``"invalid"`` for replicates
        whose perturbed geometry was degenerate).
    """
    asr_values: list[float] = []
    cur_values: list[float] = []
    si_values: list[float] = []
    shape_values: list[str] = []
    for _ in range(n_replicates):
        noisy = noise_model.apply(ground_truth_points, rng)
        geom = compute_metrics_from_points(noisy)
        if geom is None:
            asr_values.append(np.nan)
            cur_values.append(np.nan)
            si_values.append(np.nan)
            shape_values.append("invalid")
            continue
        asr_values.append(geom.aspect_ratio)
        cur_values.append(geom.curviness)
        si_values.append(geom.shape_index)
        shape_values.append(geom.shape_classification.value)
    return {
        "asr": np.asarray(asr_values),
        "cur": np.asarray(cur_values),
        "shape_index": np.asarray(si_values),
        "shape_class": np.asarray(shape_values),
    }


__all__ = ["compute_metrics_from_points", "compute_metrics_with_noise"]
