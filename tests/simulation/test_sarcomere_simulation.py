"""Tests for the single-sarcomere validation suite (Phase 2D).

Coverage:

- :class:`SingleSarcomereConfig` validators.
- :class:`SarcomereGenerator` (from_morphometry, cisterna, at_boundary).
- :class:`GaussianNoise` and :class:`AnisotropicNoise`.
- :func:`compute_metrics_from_points` and
  :func:`compute_metrics_with_noise`.
- :class:`NoiseSensitivityPipeline` and
  :class:`BoundaryClassificationPipeline` smoke tests.
- :class:`NoiseSensitivityResults` minimum-precision helper.
"""

from __future__ import annotations

import numpy as np
import pytest

from sarcostat.core.sarcomere_morphometry import (
    ShapeClassification,
    calculate_sarcomere_geometry,
)
from sarcostat.simulation.sarc import (
    AnisotropicNoise,
    BoundaryClassificationPipeline,
    BoundaryClassificationResults,
    Condition,
    GaussianNoise,
    NoiseSensitivityPipeline,
    NoiseSensitivityResults,
    SarcomereGenerator,
    SingleSarcomereConfig,
    compute_metrics_from_points,
    compute_metrics_with_noise,
)


def _geom(points: dict) -> object:
    """Helper: build SarcomereGeometry from the standard 6-key dict."""
    return calculate_sarcomere_geometry(
        point_a=points["A"],
        point_b=points["B"],
        point_c=points["C"],
        point_d=points["D"],
        point_e=points["E"],
        point_f=points["F"],
    )


class TestSingleSarcomereConfig:
    """Validator coverage for :class:`SingleSarcomereConfig`."""

    def test_default_config(self) -> None:
        """The default configuration is valid."""
        cfg = SingleSarcomereConfig()
        assert cfg.n_simulations == 1000
        assert cfg.seed == 42
        assert cfg.target_condition == "euploid"

    def test_invalid_n_simulations(self) -> None:
        """``n_simulations < 1`` is rejected."""
        with pytest.raises(ValueError, match="n_simulations must be positive"):
            SingleSarcomereConfig(n_simulations=0)

    def test_invalid_target_condition(self) -> None:
        """An unknown ``target_condition`` is rejected."""
        with pytest.raises(ValueError, match="target_condition"):
            SingleSarcomereConfig(target_condition="invalid")

    def test_invalid_noise_model(self) -> None:
        """An unknown ``noise_model`` is rejected."""
        with pytest.raises(ValueError, match="noise_model"):
            SingleSarcomereConfig(noise_model="invalid")

    def test_negative_noise_level(self) -> None:
        """Negative entries in ``noise_levels`` are rejected."""
        with pytest.raises(ValueError, match="noise_levels must be non-negative"):
            SingleSarcomereConfig(noise_levels=[-1, 0, 10])


class TestSarcomereGenerator:
    """Tests for :class:`SarcomereGenerator`."""

    @pytest.fixture
    def generator(self) -> SarcomereGenerator:
        """Build a generator with the default Cisterna parameters."""
        return SarcomereGenerator(SingleSarcomereConfig(n_simulations=10, n_replicates=10))

    def test_rectangular_target(self, generator: SarcomereGenerator) -> None:
        """``Cur == AsR`` produces a RECTANGULAR geometry."""
        points = generator.from_morphometry(1638.0, target_asr=4.0, target_cur=4.0)
        geom = _geom(points)
        assert abs(geom.aspect_ratio - 4.0) < 0.01
        assert geom.shape_classification == ShapeClassification.RECTANGULAR

    def test_barrel_target(self, generator: SarcomereGenerator) -> None:
        """``Cur < AsR`` produces a BARREL geometry."""
        points = generator.from_morphometry(1638.0, target_asr=4.0, target_cur=3.5)
        geom = _geom(points)
        assert geom.shape_classification == ShapeClassification.BARREL

    def test_hourglass_target(self, generator: SarcomereGenerator) -> None:
        """``Cur > AsR`` produces an HOURGLASS geometry."""
        points = generator.from_morphometry(1638.0, target_asr=4.0, target_cur=4.5)
        geom = _geom(points)
        assert geom.shape_classification == ShapeClassification.HOURGLASS

    def test_rotation_preserves_metrics(self, generator: SarcomereGenerator) -> None:
        """A rigid rotation does not change the morphometry summary."""
        a = generator.from_morphometry(1638.0, target_asr=4.0, target_cur=3.5, orientation=0.0)
        b = generator.from_morphometry(
            1638.0, target_asr=4.0, target_cur=3.5, orientation=np.pi / 4
        )
        geom_a, geom_b = _geom(a), _geom(b)
        assert abs(geom_a.aspect_ratio - geom_b.aspect_ratio) < 0.01
        assert abs(geom_a.curviness - geom_b.curviness) < 0.01

    def test_z_line_asymmetry(self, generator: SarcomereGenerator) -> None:
        """A 50 nm Z-asymmetry shows up as a 50 nm length difference."""
        points = generator.from_morphometry(
            1638.0, target_asr=4.0, target_cur=4.0, z_line_asymmetry=50.0
        )
        geom = _geom(points)
        assert abs(abs(geom.z_line_1_length - geom.z_line_2_length) - 50.0) < 0.01


class TestCisternaGeneration:
    """Cisterna parameter sets via :meth:`SarcomereGenerator.cisterna`."""

    @pytest.fixture
    def generator(self) -> SarcomereGenerator:
        """Generator with a small but realistic sample size."""
        return SarcomereGenerator(SingleSarcomereConfig(n_simulations=100, n_replicates=10))

    def test_euploid_asr_distribution(self, generator: SarcomereGenerator) -> None:
        """Euploid AsR mean falls inside the Cisterna-derived envelope."""
        rng = np.random.default_rng(0)
        asr = [generator.cisterna(Condition.EUPLOID, rng)[1].aspect_ratio for _ in range(500)]
        mean_asr = float(np.mean(asr))
        assert 3.5 < mean_asr < 4.8

    def test_trisomic_asr_distribution(self, generator: SarcomereGenerator) -> None:
        """Trisomic AsR mean falls inside the Cisterna-derived envelope."""
        rng = np.random.default_rng(0)
        asr = [generator.cisterna(Condition.TRISOMIC, rng)[1].aspect_ratio for _ in range(500)]
        mean_asr = float(np.mean(asr))
        assert 3.2 < mean_asr < 4.6

    def test_generated_sarcomere_has_valid_geometry(self, generator: SarcomereGenerator) -> None:
        """All sampled sarcomeres have positive lengths and a known shape class."""
        rng = np.random.default_rng(0)
        for _ in range(100):
            _, geom = generator.cisterna(Condition.EUPLOID, rng)
            assert geom.z_line_1_length > 0
            assert geom.z_line_2_length > 0
            assert geom.m_line_length > 0
            assert geom.sarcomere_length > 0
            assert geom.shape_classification in {
                ShapeClassification.RECTANGULAR,
                ShapeClassification.BARREL,
                ShapeClassification.HOURGLASS,
                ShapeClassification.SQUARE,
            }


class TestNoiseModels:
    """Tests for :class:`GaussianNoise` and :class:`AnisotropicNoise`."""

    @pytest.fixture
    def points(self) -> dict:
        """A canonical, axis-aligned 6-point sarcomere."""
        gen = SarcomereGenerator(SingleSarcomereConfig())
        return gen.from_morphometry(1638.0, target_asr=4.0, target_cur=4.0)

    def test_zero_noise_preserves_points(self, points: dict) -> None:
        """``sigma <= 0`` returns the input unchanged."""
        out = GaussianNoise(sigma=0.0).apply(points, np.random.default_rng(0))
        for key in points:
            assert out[key] == points[key]

    def test_noise_has_zero_mean(self, points: dict) -> None:
        """Repeated draws have approximately zero mean."""
        rng = np.random.default_rng(42)
        noise = GaussianNoise(sigma=10.0)
        deltas = []
        for _ in range(1000):
            out = noise.apply(points, rng)
            for key in points:
                deltas.append(out[key][0] - points[key][0])
                deltas.append(out[key][1] - points[key][1])
        assert abs(float(np.mean(deltas))) < 1.0

    def test_noise_has_correct_std(self, points: dict) -> None:
        """Repeated draws have standard deviation within 15% of sigma."""
        rng = np.random.default_rng(42)
        noise = GaussianNoise(sigma=20.0)
        deltas = []
        for _ in range(1000):
            out = noise.apply(points, rng)
            for key in points:
                deltas.append(out[key][0] - points[key][0])
                deltas.append(out[key][1] - points[key][1])
        std = float(np.std(deltas))
        assert abs(std - 20.0) / 20.0 < 0.15

    def test_anisotropic_noise(self, points: dict) -> None:
        """Anisotropic noise modifies the points without raising."""
        out = AnisotropicNoise(sigma_along=5.0, sigma_perpendicular=15.0).apply(
            points, np.random.default_rng(0)
        )
        assert set(out.keys()) == set(points.keys())
        assert any(out[k] != points[k] for k in points)


class TestNoiseSensitivityPipeline:
    """Smoke tests for :class:`NoiseSensitivityPipeline`."""

    @pytest.fixture
    def pipeline(self) -> NoiseSensitivityPipeline:
        """Small but realistic pipeline (fast)."""
        cfg = SingleSarcomereConfig(
            n_simulations=20,
            n_replicates=20,
            noise_levels=[0.0, 10.0, 50.0],
        )
        return NoiseSensitivityPipeline(cfg=cfg, generator=SarcomereGenerator(cfg))

    def test_error_increases_with_noise(self, pipeline: NoiseSensitivityPipeline) -> None:
        """RMSE at the highest noise level exceeds RMSE at zero noise."""
        results = pipeline.run()
        assert results.asr_rmse[0] < results.asr_rmse[-1]
        assert results.cur_rmse[0] < results.cur_rmse[-1]

    def test_zero_noise_has_zero_error(self, pipeline: NoiseSensitivityPipeline) -> None:
        """Zero noise yields essentially zero RMSE on AsR and Cur."""
        results = pipeline.run()
        assert results.asr_rmse[0] < 0.01
        assert results.cur_rmse[0] < 0.01

    def test_classification_accuracy_decreases_with_noise(
        self, pipeline: NoiseSensitivityPipeline
    ) -> None:
        """Classification accuracy is high at zero noise and degrades at high noise."""
        results = pipeline.run()
        assert (
            results.classification_agreement[0] > results.classification_agreement[-1]
            or results.classification_agreement[0] >= 0.95
        )

    def test_results_serializable(self, pipeline: NoiseSensitivityPipeline) -> None:
        """``to_dict`` exposes the documented keys."""
        results = pipeline.run()
        result_dict = results.to_dict()
        assert "noise_levels" in result_dict
        assert "asr_rmse" in result_dict
        assert "cur_rmse" in result_dict
        assert "minimum_precision_combined_01" in result_dict


class TestBoundaryClassificationPipeline:
    """Smoke tests for :class:`BoundaryClassificationPipeline`."""

    @pytest.fixture
    def pipeline(self) -> BoundaryClassificationPipeline:
        """Small but realistic pipeline (fast)."""
        cfg = SingleSarcomereConfig(
            n_simulations=10,
            n_replicates=10,
            noise_levels=[0.0, 20.0],
            n_boundary_samples=20,
            generate_boundary_cases=True,
        )
        return BoundaryClassificationPipeline(cfg=cfg, generator=SarcomereGenerator(cfg))

    def test_at_boundary_generation(self) -> None:
        """``generator.at_boundary`` produces the expected shape class."""
        cfg = SingleSarcomereConfig()
        gen = SarcomereGenerator(cfg)
        rng = np.random.default_rng(0)
        _, barrel = gen.at_boundary(boundary_offset=-0.3, rng=rng)
        _, hg = gen.at_boundary(boundary_offset=0.3, rng=rng)
        _, rect = gen.at_boundary(boundary_offset=0.0, rng=rng)
        assert barrel.shape_classification == ShapeClassification.BARREL
        assert hg.shape_classification == ShapeClassification.HOURGLASS
        assert rect.shape_classification == ShapeClassification.RECTANGULAR

    def test_boundary_analysis_runs(self, pipeline: BoundaryClassificationPipeline) -> None:
        """The pipeline returns a populated :class:`BoundaryClassificationResults`."""
        results = pipeline.run()
        assert isinstance(results, BoundaryClassificationResults)
        assert len(results.noise_levels) == 2
        assert results.misclassification_rate.shape[0] == 2

    def test_misclassification_rate_in_range(
        self, pipeline: BoundaryClassificationPipeline
    ) -> None:
        """Misclassification rates lie in [0, 1]."""
        results = pipeline.run()
        assert (results.misclassification_rate >= 0).all()
        assert (results.misclassification_rate <= 1).all()


class TestMetricsHelpers:
    """Tests for :func:`compute_metrics_from_points` and ``_with_noise``."""

    @pytest.fixture
    def generator(self) -> SarcomereGenerator:
        return SarcomereGenerator(SingleSarcomereConfig())

    def test_compute_metrics_from_valid_points(self, generator: SarcomereGenerator) -> None:
        """The recovered metrics match the ground truth."""
        points, gt = generator.cisterna(Condition.EUPLOID, np.random.default_rng(0))
        recovered = compute_metrics_from_points(points)
        assert recovered is not None
        assert abs(recovered.aspect_ratio - gt.aspect_ratio) < 0.01
        assert abs(recovered.curviness - gt.curviness) < 0.01

    def test_compute_metrics_with_noise_returns_dict(self, generator: SarcomereGenerator) -> None:
        """The replicate sweep returns the documented keys."""
        points, _ = generator.cisterna(Condition.EUPLOID, np.random.default_rng(0))
        metrics = compute_metrics_with_noise(
            points, GaussianNoise(sigma=10.0), n_replicates=10, rng=np.random.default_rng(1)
        )
        assert {"asr", "cur", "shape_index", "shape_class"} <= metrics.keys()
        assert len(metrics["asr"]) == 10


class TestNoiseSensitivityResults:
    """Tests for :class:`NoiseSensitivityResults` itself."""

    def test_minimum_precision_requirement(self) -> None:
        """``minimum_precision_requirement`` returns the highest qualifying sigma."""
        results = NoiseSensitivityResults(
            noise_levels=[0.0, 10.0, 20.0, 30.0],
            n_sarcomeres=100,
            n_replicates=100,
            asr_errors=np.zeros((4, 100, 100)),
            cur_errors=np.zeros((4, 100, 100)),
            shape_index_errors=np.zeros((4, 100, 100)),
            asr_rmse=np.array([0.01, 0.05, 0.12, 0.20]),
            cur_rmse=np.array([0.01, 0.04, 0.10, 0.18]),
            shape_index_rmse=np.array([0.01, 0.05, 0.11, 0.19]),
            asr_bias=np.array([0.0, 0.0, 0.0, 0.0]),
            cur_bias=np.array([0.0, 0.0, 0.0, 0.0]),
            classification_agreement=np.array([1.0, 0.95, 0.85, 0.75]),
        )
        assert results.minimum_precision_requirement(0.1, 0.1) == 10.0
