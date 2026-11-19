"""Unified run-artifact management for SarcoStat simulations.

The :class:`RunArtifacts` class owns the three standard subdirectories
(``data/``, ``plots/``, ``logs/``), configures a named logger with
file + console + warning handlers, and offers small typed helpers for
the four output formats used across the package: JSON, YAML, CSV, and
matplotlib figures.

The class is decoupled from any specific simulator: callers pass it
into the chain runner (:func:`sarcostat.simulation.chain.run_monte_carlo`),
the single-sarcomere pipelines
(:class:`sarcostat.simulation.sarc.NoiseSensitivityPipeline`,
:class:`sarcostat.simulation.sarc.BoundaryClassificationPipeline`), or
the rigor entry points; tests inject one with ``tmp_path``.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from sarcostat import __version__

_DETAILED_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(funcName)s:%(lineno)d - %(message)s"
_SIMPLE_FORMAT = "%(asctime)s - %(levelname)s - %(message)s"
_DETAILED_DATEFMT = "%Y-%m-%d %H:%M:%S"
_SIMPLE_DATEFMT = "%H:%M:%S"


class RunArtifacts:
    """Bundle of output directories and a configured logger for one run.

    The constructor creates ``data/``, ``plots/``, and ``logs/`` under
    the resolved base directory, attaches a file handler on
    ``logs/simulation.log``, a console handler at ``INFO`` level, and a
    warnings-only handler on ``logs/errors.log``. The same logger is
    returned for every consumer of the same instance.

    Parameters
    ----------
    base_dir : str or pathlib.Path
        Base output directory. Created if missing.
    timestamped : bool, optional
        When True the actual run directory is
        ``{base_dir}/run_{YYYYMMDD_HHMMSS}``. Default False keeps
        compatibility with consumers that use a stable directory name.
    logger_name : str, optional
        Logger name. Defaults to ``"sarcostat.simulation"``. Pass a
        unique name per run if you want to keep handlers distinct
        across multiple :class:`RunArtifacts` instances.
    console_handler : logging.Handler, optional
        Handler used for console output instead of the default
        ``INFO``-level stdout stream handler; the CLI passes a rich
        handler so log lines do not break its progress display.
    """

    def __init__(
        self,
        base_dir: str | Path,
        *,
        timestamped: bool = False,
        logger_name: str = "sarcostat.simulation",
        console_handler: logging.Handler | None = None,
    ) -> None:
        base = Path(base_dir)
        if timestamped:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            self.output_dir = base / f"run_{stamp}"
        else:
            self.output_dir = base
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.data_dir = self.output_dir / "data"
        self.plots_dir = self.output_dir / "plots"
        self.logs_dir = self.output_dir / "logs"
        for d in (self.data_dir, self.plots_dir, self.logs_dir):
            d.mkdir(exist_ok=True)

        self._logger_name = logger_name
        self._console_handler = console_handler
        self.logger = self._setup_logging()
        self.logger.info("Output directory: %s", self.output_dir)

    def _setup_logging(self) -> logging.Logger:
        """Return a freshly-configured logger for this run."""
        logger = logging.getLogger(self._logger_name)
        logger.setLevel(logging.DEBUG)
        logger.handlers = []

        detailed = logging.Formatter(_DETAILED_FORMAT, datefmt=_DETAILED_DATEFMT)
        simple = logging.Formatter(_SIMPLE_FORMAT, datefmt=_SIMPLE_DATEFMT)

        file_handler = logging.FileHandler(self.logs_dir / "simulation.log")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(detailed)
        logger.addHandler(file_handler)

        console_handler = self._console_handler
        if console_handler is None:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setFormatter(simple)
        console_handler.setLevel(logging.INFO)
        logger.addHandler(console_handler)

        err_handler = logging.FileHandler(self.logs_dir / "errors.log")
        err_handler.setLevel(logging.WARNING)
        err_handler.setFormatter(detailed)
        logger.addHandler(err_handler)

        return logger

    def data_path(self, name: str) -> Path:
        """Return ``data/{name}`` as a Path."""
        return self.data_dir / name

    def plots_path(self, name: str) -> Path:
        """Return ``plots/{name}`` as a Path."""
        return self.plots_dir / name

    def logs_path(self, name: str) -> Path:
        """Return ``logs/{name}`` as a Path."""
        return self.logs_dir / name

    def save_json(
        self,
        name: str,
        data: Any,
        *,
        indent: int = 4,
        default: Any | None = str,
    ) -> Path:
        """Write ``data`` to ``data/{name}`` as JSON.

        Parameters
        ----------
        name : str
            Filename relative to the data directory.
        data : Any
            JSON-encodable payload (or anything ``default`` can stringify).
        indent : int, optional
            Indentation forwarded to :func:`json.dumps` (default 4).
        default : callable, optional
            Fallback for non-serializable values, forwarded to
            :func:`json.dumps`. Defaults to :class:`str` so that numpy
            scalars and Path objects survive a stringification.

        Returns
        -------
        pathlib.Path
            Resolved path that was written.
        """
        target = self.data_path(name)
        with target.open("w") as f:
            json.dump(data, f, indent=indent, default=default)
        self.logger.info("Saved JSON: %s", target)
        return target

    def save_yaml(self, name: str, data: Any) -> Path:
        """Write ``data`` to ``data/{name}`` via :func:`yaml.safe_dump`.

        Parameters
        ----------
        name : str
            Filename relative to the data directory.
        data : Any
            YAML-encodable payload.

        Returns
        -------
        pathlib.Path
            Resolved path that was written.
        """
        target = self.data_path(name)
        with target.open("w") as f:
            yaml.safe_dump(data, f, sort_keys=False)
        self.logger.info("Saved YAML: %s", target)
        return target

    def save_csv(self, name: str, df: Any, *, index: bool = False) -> Path:
        """Write a pandas DataFrame to ``data/{name}`` as CSV.

        Parameters
        ----------
        name : str
            Filename relative to the data directory.
        df : pandas.DataFrame
            Frame to persist. Pandas is not imported at module load
            time; the type is duck-typed via the ``to_csv`` method.
        index : bool, optional
            Whether to write the index column (default False).

        Returns
        -------
        pathlib.Path
            Resolved path that was written.
        """
        target = self.data_path(name)
        df.to_csv(target, index=index)
        self.logger.info("Saved CSV: %s", target)
        return target

    def save_figure(
        self,
        fig: Any,
        basename: str,
        *,
        dpi: int = 300,
        formats: Iterable[str] = ("pdf", "png"),
    ) -> list[Path]:
        """Save a matplotlib figure under ``plots/`` in one or more formats.

        Parameters
        ----------
        fig : matplotlib.figure.Figure
            Figure to persist (duck-typed via ``savefig``).
        basename : str
            Filename stem; extensions are derived from ``formats``.
        dpi : int, optional
            Dots-per-inch (default 300).
        formats : iterable of str, optional
            Extensions to write; defaults to ``("pdf", "png")``.

        Returns
        -------
        list of pathlib.Path
            One path per format that was saved.
        """
        paths: list[Path] = []
        for ext in formats:
            target = self.plots_path(f"{basename}.{ext}")
            fig.savefig(target, dpi=dpi, bbox_inches="tight")
            paths.append(target)
            self.logger.info("Saved figure: %s", target)
        return paths

    def save_parameters(self, params: dict[str, Any]) -> Path:
        """Persist a flat parameter dict to ``data/simulation_parameters.json``."""
        return self.save_json("simulation_parameters.json", params)

    def save_npz(self, name: str, **arrays: np.ndarray) -> Path:
        """Write keyword arrays to ``data/{name}`` via :func:`numpy.savez`.

        Parameters
        ----------
        name : str
            Filename (with or without ``.npz`` extension).
        **arrays
            Keyword arrays forwarded to :func:`numpy.savez`.

        Returns
        -------
        pathlib.Path
            Resolved path that was written.
        """
        if not name.endswith(".npz"):
            name = f"{name}.npz"
        target = self.data_path(name)
        np.savez(target, **arrays)
        self.logger.info("Saved NPZ: %s", target)
        return target


def run_metadata(config_path: str | Path | None = None) -> dict[str, Any]:
    """Provenance stored next to a run's parameters.

    Parameters
    ----------
    config_path : str or pathlib.Path, optional
        YAML file the run was configured from; ``None`` for runs built
        from in-code defaults.

    Returns
    -------
    dict
        ``sarcostat_version``, ``config_file`` (absolute path or None) and
        ``created`` (ISO-8601 timestamp).
    """
    return {
        "sarcostat_version": __version__,
        "config_file": None if config_path is None else str(Path(config_path).resolve()),
        "created": datetime.now().isoformat(timespec="seconds"),
    }


__all__ = ["RunArtifacts", "run_metadata"]
