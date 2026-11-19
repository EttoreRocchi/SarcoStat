"""Monte Carlo runner: orchestrates kappa x n cells over a chain simulator.

The runner is intentionally a pure function (plus tiny private helpers)
rather than a class. The simulator instance is injected, results are
returned as a plain dict, and persistence is delegated to a
:class:`~sarcostat.simulation._artifacts.RunArtifacts` instance. This
keeps the simulator unit-testable without the multiprocessing harness
and makes it trivial to drop in a different parallelism strategy.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Any

import numpy as np

from .._seeding import per_cell_seed
from .config import Config
from .simulator import ChainSimulator

_METRIC_KEYS: tuple[str, ...] = (
    "linearity_ratio",
    "mean_vector_length",
    "rayleigh_z",
    "rayleigh_p",
    "v_statistic",
    "v_p_value",
    "angles",
)


ProgressCallback = Callable[[int, int], None]


def _run_cell(
    simulator: ChainSimulator,
    kappa: float,
    n: int,
    sim_count: int,
    base_seed: int,
    angle_buffer: int = 500,
) -> dict[str, np.ndarray]:
    """Run ``sim_count`` trials for one (kappa, n) cell."""
    rng = np.random.default_rng(per_cell_seed(base_seed, kappa, n))
    lr, mvl, rz, rp, vs, vp = [], [], [], [], [], []
    angle_pool: list[float] = []
    for _ in range(sim_count):
        outcome = simulator.simulate_trial(n, kappa, rng)
        lr.append(outcome.linearity_ratio)
        mvl.append(outcome.mean_vector_length)
        rz.append(outcome.rayleigh_z)
        rp.append(outcome.rayleigh_p)
        vs.append(outcome.v_statistic)
        vp.append(outcome.v_p_value)
        if len(angle_pool) < angle_buffer:
            angle_pool.extend(outcome.angles.tolist())
    return {
        "linearity_ratio": np.asarray(lr),
        "mean_vector_length": np.asarray(mvl),
        "rayleigh_z": np.asarray(rz),
        "rayleigh_p": np.asarray(rp),
        "v_statistic": np.asarray(vs),
        "v_p_value": np.asarray(vp),
        "angles": np.asarray(angle_pool),
    }


def _new_results_dict(cfg: Config) -> dict[str, dict[float, dict[int, np.ndarray]]]:
    """Allocate the nested dict consumed by plotters and tests (filled per cell)."""
    return {
        m: {k: {n: np.empty(0) for n in cfg.n_sarcomeres} for k in cfg.kappa_values}
        for m in _METRIC_KEYS
    }


def _run_serial(
    simulator: ChainSimulator,
    cfg: Config,
    progress: ProgressCallback | None,
    logger: logging.Logger,
) -> dict[str, dict[float, dict[int, np.ndarray]]]:
    """Single-process loop over all (kappa, n) cells."""
    results = _new_results_dict(cfg)
    combos = [(k, n) for k in cfg.kappa_values for n in cfg.n_sarcomeres]
    total = len(combos)
    for i, (k, n) in enumerate(combos, start=1):
        out = _run_cell(simulator, k, n, cfg.n_simulations, cfg.seed)
        for m in _METRIC_KEYS:
            results[m][k][n] = out[m]
        logger.info("Cell %d/%d - kappa=%s, N=%d", i, total, k, n)
        if progress is not None:
            progress(i, total)
    return results


def _run_parallel(
    simulator: ChainSimulator,
    cfg: Config,
    progress: ProgressCallback | None,
    logger: logging.Logger,
) -> dict[str, dict[float, dict[int, np.ndarray]]]:
    """Parallel loop using a process pool."""
    results = _new_results_dict(cfg)
    combos = [(k, n) for k in cfg.kappa_values for n in cfg.n_sarcomeres]
    total = len(combos)
    with ProcessPoolExecutor(max_workers=cfg.n_processes) as ex:
        futures = {
            ex.submit(_run_cell, simulator, k, n, cfg.n_simulations, cfg.seed): (k, n)
            for (k, n) in combos
        }
        done = 0
        for fut in as_completed(futures):
            k, n = futures[fut]
            done += 1
            out = fut.result()
            for m in _METRIC_KEYS:
                results[m][k][n] = out[m]
            logger.info("Cell %d/%d - kappa=%s, N=%d", done, total, k, n)
            if progress is not None:
                progress(done, total)
    return results


def run_monte_carlo(
    simulator: ChainSimulator,
    cfg: Config,
    *,
    progress: ProgressCallback | None = None,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """Sweep ``simulator`` over the kappa x n grid defined by ``cfg``.

    Parameters
    ----------
    simulator : ChainSimulator
        Trial-level simulator (injected so that tests can mock it).
    cfg : Config
        Sweep parameters and parallelism setting.
    progress : callable, optional
        ``progress(done, total)`` callback fired after each cell. Used
        by the typer CLI to drive a rich progress bar.
    logger : logging.Logger, optional
        Logger used for per-cell info messages. Defaults to a module
        logger.

    Returns
    -------
    dict
        Nested dict ``results[metric][kappa][n] -> ndarray``.
    """
    log = logger or logging.getLogger("sarcostat.simulation.chain")
    log.info("Starting Monte Carlo sweep over kappa x N grid")
    if cfg.n_processes > 1:
        return _run_parallel(simulator, cfg, progress, log)
    return _run_serial(simulator, cfg, progress, log)


def summarise_results(
    results: dict[str, Any],
    kappa_values: list[float],
    n_sarcomeres: list[int],
) -> dict[str, Any]:
    """Compact mean/std/quantile reduction for JSON persistence.

    Parameters
    ----------
    results : dict
        Output of :func:`run_monte_carlo`.
    kappa_values, n_sarcomeres : list
        Axis values to reduce over (must match those in ``results``).

    Returns
    -------
    dict
        ``summary[metric][kappa][n]`` -> dict of mean/std/median/min/max/q25/q75.
        The raw ``angles`` series is excluded.
    """
    summary: dict[str, Any] = {}
    for metric, by_kappa in results.items():
        if metric == "angles":
            continue
        summary[metric] = {}
        for k, by_n in by_kappa.items():
            summary[metric][k] = {}
            for n, values in by_n.items():
                arr = np.asarray(values)
                summary[metric][k][n] = {
                    "mean": float(np.mean(arr)),
                    "std": float(np.std(arr, ddof=1)) if arr.size > 1 else 0.0,
                    "median": float(np.median(arr)),
                    "min": float(np.min(arr)),
                    "max": float(np.max(arr)),
                    "q25": float(np.percentile(arr, 25)),
                    "q75": float(np.percentile(arr, 75)),
                }
    return summary


__all__ = ["run_monte_carlo", "summarise_results", "ProgressCallback"]
