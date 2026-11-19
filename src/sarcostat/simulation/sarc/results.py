"""Result containers for the single-sarcomere validation pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass
class NoiseSensitivityResults:
    """Outputs of :class:`NoiseSensitivityPipeline`.

    Attributes
    ----------
    noise_levels : list of float
        Noise standard deviations swept (nm).
    n_sarcomeres, n_replicates : int
        Sweep sizes.
    asr_errors, cur_errors, shape_index_errors : numpy.ndarray
        Per-replicate signed errors with shape
        ``(n_noise_levels, n_sarcomeres, n_replicates)``.
    asr_rmse, cur_rmse, shape_index_rmse : numpy.ndarray
        Per-noise-level RMSE.
    asr_bias, cur_bias : numpy.ndarray
        Per-noise-level mean bias.
    classification_agreement : numpy.ndarray
        Per-noise-level fraction of correct shape labels.
    """

    noise_levels: list[float]
    n_sarcomeres: int
    n_replicates: int
    asr_errors: np.ndarray
    cur_errors: np.ndarray
    shape_index_errors: np.ndarray
    asr_rmse: np.ndarray
    cur_rmse: np.ndarray
    shape_index_rmse: np.ndarray
    asr_bias: np.ndarray
    cur_bias: np.ndarray
    classification_agreement: np.ndarray

    def minimum_precision_requirement(
        self, max_asr_error: float = 0.1, max_cur_error: float = 0.1
    ) -> float:
        """Highest noise level whose AsR and Cur RMSE stay within the bounds.

        Scans every level in ``noise_levels`` and returns the maximum of
        those that satisfy both error bounds. Robust to non-monotonic
        noise-level ordering or non-monotonic stochastic RMSE curves.

        Parameters
        ----------
        max_asr_error, max_cur_error : float, optional
            Maximum acceptable RMSE for AsR and Cur (default 0.1 each).

        Returns
        -------
        float
            Maximum sigma satisfying the constraints, or ``-1`` if no
            sweep level qualifies.
        """
        qualifying = [
            float(sigma)
            for sigma, asr, cur in zip(
                self.noise_levels, self.asr_rmse, self.cur_rmse, strict=False
            )
            if asr <= max_asr_error and cur <= max_cur_error
        ]
        return max(qualifying) if qualifying else -1.0

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly summary (per-replicate arrays excluded)."""
        return {
            "noise_levels": self.noise_levels,
            "n_sarcomeres": self.n_sarcomeres,
            "n_replicates": self.n_replicates,
            "asr_rmse": self.asr_rmse.tolist(),
            "cur_rmse": self.cur_rmse.tolist(),
            "shape_index_rmse": self.shape_index_rmse.tolist(),
            "asr_bias": self.asr_bias.tolist(),
            "cur_bias": self.cur_bias.tolist(),
            "classification_agreement": self.classification_agreement.tolist(),
            "minimum_precision_asr_01": self.minimum_precision_requirement(0.1, float("inf")),
            "minimum_precision_cur_01": self.minimum_precision_requirement(float("inf"), 0.1),
            "minimum_precision_combined_01": self.minimum_precision_requirement(0.1, 0.1),
        }


@dataclass
class BoundaryClassificationResults:
    """Outputs of :class:`BoundaryClassificationPipeline`.

    Attributes
    ----------
    noise_levels : list of float
        Noise standard deviations swept.
    boundary_offsets : numpy.ndarray
        Cur - AsR offsets evaluated.
    misclassification_rate : numpy.ndarray
        Per (noise, offset) misclassification rate.
    barrel_to_hourglass_rate, hourglass_to_barrel_rate : numpy.ndarray
        Direction-specific misclassification rates.
    critical_boundary_width : dict of float to float
        For each noise level, the half-width (in ``Cur - AsR`` units) of
        the band around the class edges where the misclassification rate
        reaches ``misclass_threshold``; 0 when no offset does.
    class_edges : tuple of float
        Offsets ``(-tolerance * AsR, +tolerance * AsR)`` separating
        RECTANGULAR from BARREL and HOURGLASS.
    misclass_threshold : float
        Rate used to define ``critical_boundary_width``.
    """

    noise_levels: list[float]
    boundary_offsets: np.ndarray
    misclassification_rate: np.ndarray
    barrel_to_hourglass_rate: np.ndarray
    hourglass_to_barrel_rate: np.ndarray
    critical_boundary_width: dict[float, float]
    class_edges: tuple[float, float] = (float("nan"), float("nan"))
    misclass_threshold: float = 0.1

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly dictionary view of the boundary results."""
        return {
            "noise_levels": self.noise_levels,
            "boundary_offsets": self.boundary_offsets.tolist(),
            "misclassification_rate": self.misclassification_rate.tolist(),
            "barrel_to_hourglass_rate": self.barrel_to_hourglass_rate.tolist(),
            "hourglass_to_barrel_rate": self.hourglass_to_barrel_rate.tolist(),
            "critical_boundary_width": self.critical_boundary_width,
            "class_edges": list(self.class_edges),
            "misclass_threshold": self.misclass_threshold,
        }


__all__ = ["NoiseSensitivityResults", "BoundaryClassificationResults"]
