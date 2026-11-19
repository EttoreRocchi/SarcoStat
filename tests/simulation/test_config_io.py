"""Strict configuration loading for the four simulation pipelines."""

from __future__ import annotations

import textwrap
import warnings
from pathlib import Path

import pytest

from sarcostat.simulation.chain import load_config
from sarcostat.simulation.rigor.coverage import load_coverage_config
from sarcostat.simulation.rigor.mde import load_mde_config
from sarcostat.simulation.sarc import load_sarcomere_config


def _write(tmp_path: Path, body: str) -> Path:
    target = tmp_path / "cfg.yaml"
    target.write_text(textwrap.dedent(body).strip() + "\n")
    return target


class TestUnknownKeys:
    def test_misspelled_chain_key_suggests_fix(self, tmp_path: Path) -> None:
        path = _write(tmp_path, "n_simulation: 10\nlenght_std: 0.3\n")
        with pytest.raises(ValueError) as exc:
            load_config(path)
        msg = str(exc.value)
        assert "'n_simulation' (did you mean 'n_simulations'?)" in msg
        assert "'lenght_std' (did you mean 'length_std'?)" in msg

    def test_unknown_nested_sarc_key(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            """
            noise_sensitivity:
              noise_level: [0, 5]
            """,
        )
        with pytest.raises(ValueError, match="noise_sensitivity.*did you mean 'noise_levels'"):
            load_sarcomere_config(path)

    @pytest.mark.parametrize("loader", [load_coverage_config, load_mde_config])
    def test_unknown_rigor_key(self, tmp_path: Path, loader) -> None:
        with pytest.raises(ValueError, match="Unknown key"):
            loader(_write(tmp_path, "seeds: 3\n"))


class TestStrictTypes:
    def test_quoted_false_is_not_true(self, tmp_path: Path) -> None:
        path = _write(tmp_path, 'enable_persistence: "false"\n')
        with pytest.raises(ValueError, match="enable_persistence must be true or false"):
            load_config(path)

    def test_non_integral_count_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="n_simulations must be an integer"):
            load_config(_write(tmp_path, "n_simulations: 10.5\n"))

    def test_kappa_integers_are_kept(self, tmp_path: Path) -> None:
        cfg = load_config(_write(tmp_path, "kappa_values: [0, 0.5, 2]\n"))
        assert cfg.kappa_values == [0, 0.5, 2]
        assert isinstance(cfg.kappa_values[0], int)


class TestRanges:
    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ("n_simulations: 0", "n_simulations"),
            ("kappa_values: [1, 1.0]", "duplicate"),
            ("n_sarcomeres: [5, 5]", "duplicate"),
            ("length_min: 2.0\nlength_max: 1.0", "length_min < length_max"),
            ("length_distribution: weibull", "not registered"),
        ],
    )
    def test_chain(self, tmp_path: Path, body: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            load_config(_write(tmp_path, body))

    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ("confidence: 95", "confidence"),
            ("n_bootstrap: 0", "n_bootstrap"),
            ("n_values: [1]", "n_values"),
        ],
    )
    def test_coverage(self, tmp_path: Path, body: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            load_coverage_config(_write(tmp_path, body))

    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ("power: 80", "power"),
            ("alpha: 0", "alpha"),
            ("sarcomere_conditions: [custom]", "sarcomere_conditions"),
        ],
    )
    def test_mde(self, tmp_path: Path, body: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            load_mde_config(_write(tmp_path, body))

    @pytest.mark.parametrize(
        ("body", "match"),
        [
            ("target_condition: custom", "target_condition"),
            ("boundary_analysis:\n  offset_range: [0.3, -0.3]", "boundary_offset_range"),
        ],
    )
    def test_sarc(self, tmp_path: Path, body: str, match: str) -> None:
        with pytest.raises(ValueError, match=match):
            load_sarcomere_config(_write(tmp_path, body))


class TestRetiredSarcKeys:
    def test_n_processes_warns_but_loads(self, tmp_path: Path) -> None:
        with pytest.warns(FutureWarning, match="n_processes is ignored"):
            load_sarcomere_config(_write(tmp_path, "n_processes: 4\n"))

    def test_legacy_anisotropic_sigmas_become_weights(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            """
            noise_sensitivity:
              noise_model: anisotropic
              anisotropic:
                sigma_along_line: 10
                sigma_perpendicular: 20
            """,
        )
        with pytest.warns(FutureWarning, match="relative weight"):
            cfg = load_sarcomere_config(path)
        assert (cfg.anisotropic_along_weight, cfg.anisotropic_perpendicular_weight) == (10, 20)

    def test_enabled_flags_are_read(self, tmp_path: Path) -> None:
        path = _write(
            tmp_path,
            """
            noise_sensitivity:
              enabled: false
            boundary_analysis:
              enabled: false
            """,
        )
        cfg = load_sarcomere_config(path)
        assert cfg.run_noise_sensitivity is False
        assert cfg.generate_boundary_cases is False


class TestPackagedConfigs:
    """The YAML files shipped in the wheel load without warnings."""

    def test_packaged_defaults_load_cleanly(self) -> None:
        import sarcostat.simulation as sim

        root = Path(sim.__file__).parent
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            load_config(root / "config.yaml")
            load_sarcomere_config(root / "sarcomere_config.yaml")

    @pytest.mark.parametrize(
        ("name", "loader"),
        [
            ("config_minimal.yaml", load_config),
            ("config_sarc_minimal.yaml", load_sarcomere_config),
            ("config_coverage_smoke.yaml", load_coverage_config),
            ("config_mde_smoke.yaml", load_mde_config),
        ],
    )
    def test_examples_load_cleanly(self, name: str, loader) -> None:
        path = Path(__file__).resolve().parents[2] / "examples" / "configs" / name
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            loader(path)
