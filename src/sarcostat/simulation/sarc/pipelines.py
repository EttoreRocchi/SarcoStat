"""Validation pipelines for the single-sarcomere study.

Two pipelines are provided:

- :class:`NoiseSensitivityPipeline` sweeps a noise-level grid against a
  population of ground-truth sarcomeres and returns RMSE, bias, and
  classification-accuracy summaries.
- :class:`BoundaryClassificationPipeline` sweeps a Cur-AsR offset grid
  to characterise misclassification at the AsR ~ Cur boundary.

The boundary pipeline uses the Cisterna et al. (2021) shape classifier
(:func:`~sarcostat.core.sarcomere_morphometry.classify_sarcomere_shape`)
with ``cfg.boundary_tolerance`` for both the ground truth and the noisy
prediction, so that at zero noise every sample is classified correctly.
Its class edges in offset space sit at ``Cur - AsR = +/- tolerance * AsR``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np

from sarcostat.core.sarcomere_morphometry import (
    Point,
    SarcomereGeometry,
    ShapeClassification,
    classify_sarcomere_shape,
)

from .config import Condition, SingleSarcomereConfig
from .generator import SarcomereGenerator
from .metrics import compute_metrics_from_points, compute_metrics_with_noise
from .noise import make_noise_model
from .results import BoundaryClassificationResults, NoiseSensitivityResults


def _condition_geometry_defaults(
    cfg: SingleSarcomereConfig,
) -> tuple[float, float]:
    """Return ``(z_mean, sarc_mean)`` for the configured target condition."""
    if cfg.target_condition == "trisomic":
        return cfg.trisomic_z_length[0], cfg.trisomic_sarcomere_length[0]
    return cfg.euploid_z_length[0], cfg.euploid_sarcomere_length[0]


@dataclass
class NoiseSensitivityPipeline:
    """Run the per-noise-level RMSE and classification-accuracy sweep.

    Parameters
    ----------
    cfg : SingleSarcomereConfig
        Drives sweep sizes, the Cisterna parameters, and the noise
        model (isotropic or anisotropic).
    generator : SarcomereGenerator
        Source of ground-truth sarcomeres.
    """

    cfg: SingleSarcomereConfig
    generator: SarcomereGenerator
    logger: logging.Logger = logging.getLogger("sarcostat.simulation.sarc")

    def _generate_ground_truth_population(
        self, rng: np.random.Generator
    ) -> list[tuple[dict[str, Point], SarcomereGeometry]]:
        """Sample ``cfg.n_simulations`` ground-truth sarcomeres."""
        condition = Condition(self.cfg.target_condition)
        return [self.generator.cisterna(condition, rng) for _ in range(self.cfg.n_simulations)]

    def _accumulate_errors(
        self,
        ground_truths: list[tuple[dict[str, Point], SarcomereGeometry]],
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Run the (noise, sarcomere, replicate) tensor of errors."""
        n_noise = len(self.cfg.noise_levels)
        n_sarc = self.cfg.n_simulations
        n_rep = self.cfg.n_replicates
        asr_e = np.zeros((n_noise, n_sarc, n_rep))
        cur_e = np.zeros((n_noise, n_sarc, n_rep))
        si_e = np.zeros((n_noise, n_sarc, n_rep))
        cls_ok = np.zeros((n_noise, n_sarc, n_rep), dtype=bool)
        for ni, sigma in enumerate(self.cfg.noise_levels):
            noise_model = make_noise_model(self.cfg, sigma)
            for si, (gt_points, gt_geom) in enumerate(ground_truths):
                rep_rng = np.random.default_rng(self.cfg.seed + ni * n_sarc + si)
                metrics = compute_metrics_with_noise(gt_points, noise_model, n_rep, rep_rng)
                asr_e[ni, si, :] = metrics["asr"] - gt_geom.aspect_ratio
                cur_e[ni, si, :] = metrics["cur"] - gt_geom.curviness
                si_e[ni, si, :] = metrics["shape_index"] - gt_geom.shape_index
                cls_ok[ni, si, :] = metrics["shape_class"] == gt_geom.shape_classification.value
        return asr_e, cur_e, si_e, cls_ok

    def run(self) -> NoiseSensitivityResults:
        """Execute the noise-sensitivity sweep."""
        self.logger.info("Starting noise sensitivity analysis")
        rng = np.random.default_rng(self.cfg.seed)
        ground_truths = self._generate_ground_truth_population(rng)
        asr_e, cur_e, si_e, cls_ok = self._accumulate_errors(ground_truths)

        asr_rmse = np.sqrt(np.nanmean(asr_e**2, axis=(1, 2)))
        cur_rmse = np.sqrt(np.nanmean(cur_e**2, axis=(1, 2)))
        si_rmse = np.sqrt(np.nanmean(si_e**2, axis=(1, 2)))
        asr_bias = np.nanmean(asr_e, axis=(1, 2))
        cur_bias = np.nanmean(cur_e, axis=(1, 2))
        cls_agree = np.mean(cls_ok, axis=(1, 2))
        return NoiseSensitivityResults(
            noise_levels=self.cfg.noise_levels,
            n_sarcomeres=self.cfg.n_simulations,
            n_replicates=self.cfg.n_replicates,
            asr_errors=asr_e,
            cur_errors=cur_e,
            shape_index_errors=si_e,
            asr_rmse=asr_rmse,
            cur_rmse=cur_rmse,
            shape_index_rmse=si_rmse,
            asr_bias=asr_bias,
            cur_bias=cur_bias,
            classification_agreement=cls_agree,
        )


_MISCLASS_TYPE_BARREL_TO_HG = (ShapeClassification.BARREL, ShapeClassification.HOURGLASS)
_MISCLASS_TYPE_HG_TO_BARREL = (ShapeClassification.HOURGLASS, ShapeClassification.BARREL)


def _per_sample_outcomes(
    generator: SarcomereGenerator,
    cfg: SingleSarcomereConfig,
    base_asr: float,
    sarc_mean: float,
    offset: float,
    sigma: float,
    n_samples: int,
    seed_base: int,
    gt_class: ShapeClassification,
) -> tuple[int, int, int, int]:
    """Tally classification outcomes for one (offset, sigma) cell.

    Returns
    -------
    tuple of int
        ``(n_misclass, n_barrel_to_hg, n_hg_to_barrel, n_valid)``.
    """
    n_misclass = 0
    n_barrel_to_hg = 0
    n_hg_to_barrel = 0
    n_valid = 0
    noise_model = make_noise_model(cfg, sigma)
    for sample_idx in range(n_samples):
        rep_rng = np.random.default_rng(seed_base + sample_idx)
        points, _gt_geom = generator.at_boundary(
            boundary_offset=offset,
            rng=rep_rng,
            base_asr=base_asr,
            sarcomere_length=sarc_mean,
        )
        noisy = noise_model.apply(points, rep_rng)
        geom = compute_metrics_from_points(noisy)
        if geom is None:
            continue
        n_valid += 1
        pred = classify_sarcomere_shape(geom.aspect_ratio, geom.curviness, cfg.boundary_tolerance)
        if pred == gt_class:
            continue
        n_misclass += 1
        if (gt_class, pred) == _MISCLASS_TYPE_BARREL_TO_HG:
            n_barrel_to_hg += 1
        elif (gt_class, pred) == _MISCLASS_TYPE_HG_TO_BARREL:
            n_hg_to_barrel += 1
    return n_misclass, n_barrel_to_hg, n_hg_to_barrel, n_valid


def _critical_widths(
    offsets: np.ndarray,
    misclass_rate: np.ndarray,
    noise_levels: list[float],
    *,
    edge: float,
    threshold: float,
) -> dict[float, float]:
    """Half-width of the unreliable band around the class edges, per noise level.

    For each noise level, returns the largest distance from the nearest
    class edge (``|offset| = edge``) among the offsets whose
    misclassification rate reaches ``threshold``; ``0.0`` when every
    offset stays below it. Offsets farther than this from both edges are
    classified reliably at that noise level (to the grid resolution).
    """
    distance_to_edge = np.abs(np.abs(offsets) - edge)
    widths: dict[float, float] = {}
    for ni, sigma in enumerate(noise_levels):
        unreliable = misclass_rate[ni] >= threshold
        widths[sigma] = float(distance_to_edge[unreliable].max()) if unreliable.any() else 0.0
    return widths


@dataclass
class BoundaryClassificationPipeline:
    """Sweep Cur-AsR offsets against a noise-level grid and report misclass rates.

    Ground truth and prediction both use the shape classifier with
    ``cfg.boundary_tolerance``; a sample counts as misclassified when the
    noisy annotation lands in a different class than the noise-free one.
    """

    cfg: SingleSarcomereConfig
    generator: SarcomereGenerator
    logger: logging.Logger = logging.getLogger("sarcostat.simulation.sarc")
    misclass_threshold: float = 0.1

    def run(self) -> BoundaryClassificationResults:
        """Execute the boundary-classification sweep."""
        self.logger.info("Starting boundary classification analysis")
        z_mean, sarc_mean = _condition_geometry_defaults(self.cfg)
        base_asr = sarc_mean / z_mean
        offset_min, offset_max = self.cfg.boundary_offset_range
        offsets = np.linspace(offset_min, offset_max, 21)
        n_offsets = len(offsets)
        n_noise = len(self.cfg.noise_levels)

        misclass_rate = np.full((n_noise, n_offsets), np.nan)
        barrel_to_hg = np.full((n_noise, n_offsets), np.nan)
        hg_to_barrel = np.full((n_noise, n_offsets), np.nan)

        for oi, offset in enumerate(offsets):
            gt_class = classify_sarcomere_shape(
                base_asr, base_asr + float(offset), self.cfg.boundary_tolerance
            )
            for ni, sigma in enumerate(self.cfg.noise_levels):
                seed_base = (
                    self.cfg.seed
                    + oi * n_noise * self.cfg.n_boundary_samples
                    + ni * self.cfg.n_boundary_samples
                )
                n_mis, n_bh, n_hb, n_valid = _per_sample_outcomes(
                    self.generator,
                    self.cfg,
                    base_asr,
                    sarc_mean,
                    float(offset),
                    sigma,
                    self.cfg.n_boundary_samples,
                    seed_base,
                    gt_class,
                )
                if n_valid > 0:
                    misclass_rate[ni, oi] = n_mis / n_valid
                    barrel_to_hg[ni, oi] = n_bh / n_valid
                    hg_to_barrel[ni, oi] = n_hb / n_valid
                else:
                    self.logger.warning(
                        "Boundary cell (offset=%.3f, sigma=%.3f) has no valid replicates",
                        float(offset),
                        sigma,
                    )

        edge = self.cfg.boundary_tolerance * base_asr
        widths = _critical_widths(
            offsets,
            misclass_rate,
            self.cfg.noise_levels,
            edge=edge,
            threshold=self.misclass_threshold,
        )
        return BoundaryClassificationResults(
            noise_levels=self.cfg.noise_levels,
            boundary_offsets=offsets,
            misclassification_rate=misclass_rate,
            barrel_to_hourglass_rate=barrel_to_hg,
            hourglass_to_barrel_rate=hg_to_barrel,
            critical_boundary_width=widths,
            class_edges=(-edge, edge),
            misclass_threshold=self.misclass_threshold,
        )


__all__ = ["NoiseSensitivityPipeline", "BoundaryClassificationPipeline"]
