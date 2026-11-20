"""Top-level Typer application for SarcoStat.

The CLI exposes one subcommand group (``sim``) plus ``version``,
``analyze`` and ``gui``. ``analyze`` reads an annotation file of format v1
and prints its results through :mod:`sarcostat.analysis`,
:mod:`sarcostat.export` and :mod:`sarcostat.engine.reports`, the path the
web app uses. ``gui`` prints the address of the web app (the console entry
answers it before Typer is loaded). Each ``sim`` subcommand is a thin wrapper around the
corresponding pipeline / runner: it loads the YAML config (an invalid
config exits with status 2 and a one-line message), builds the pipeline
objects, runs them, persists the artifacts via
:class:`~sarcostat.simulation._artifacts.RunArtifacts`, and renders
formatted summaries with rich tables.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any, TypeVar

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from sarcostat import __version__
from sarcostat.cli._entry import GUI_URL

app = typer.Typer(
    name="sarcostat",
    help="SarcoStat: sarcomere/myofibril analysis with circular statistics.",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    add_completion=False,
)
sim_app = typer.Typer(
    name="sim",
    help="Monte Carlo simulation suite (chain, sarc, coverage, mde).",
    no_args_is_help=True,
    pretty_exceptions_show_locals=False,
    add_completion=False,
)
app.add_typer(sim_app, name="sim")


_CONSOLE = Console()
_PACKAGED_DIR = Path(__file__).resolve().parent.parent / "simulation"

ConfigOption = Annotated[
    Path | None,
    typer.Option(
        "--config",
        "-c",
        help="Path to a YAML configuration file.",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
]

C = TypeVar("C")


def _console_handler() -> logging.Handler:
    """Log handler that prints through the shared rich console.

    Routing log records through the same :class:`~rich.console.Console`
    as the progress bar keeps log lines above the live display instead of
    interleaving with it.
    """
    return RichHandler(console=_CONSOLE, show_path=False, markup=False)


def _load_config(loader: Callable[[Path], C], path: Path) -> C:
    """Run a config loader, turning validation errors into a clean ``--config`` error."""
    import yaml

    try:
        return loader(path)
    except (ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise typer.BadParameter(f"{path}: {exc}", param_hint="'--config'") from exc


def _resolve_config(
    config: Path | None, packaged_name: str, loader: Callable[[Path], C]
) -> tuple[C, Path]:
    """Load ``config`` or, when it is ``None``, the packaged default YAML."""
    if config is None:
        config = _PACKAGED_DIR / packaged_name
        if not config.exists():
            raise typer.BadParameter(
                f"No --config supplied and the packaged default {config} is missing.",
                param_hint="'--config'",
            )
        _CONSOLE.print(f"Using packaged default config: [cyan]{config}[/cyan]")
    return _load_config(loader, config), config


@app.command("version")
def version_cmd() -> None:
    """Print the installed SarcoStat version."""
    _CONSOLE.print(f"sarcostat [bold]{__version__}[/bold]")


def _one_line(exc: BaseException | str) -> str:
    """The message of ``exc`` on one line."""
    return " ".join(str(exc).split()) or type(exc).__name__


def _analyze_line(path: Path, message: str) -> str:
    """``sarcostat analyze: <file name>: <message>`` (the name is not repeated)."""
    if message.startswith(f"{path.name}: "):
        message = message[len(path.name) + 2 :]
    return f"sarcostat analyze: {path.name}: {message}"


def _analyze_error(path: Path, message: str) -> typer.Exit:
    """Print one line on standard error and return the exit with status 2."""
    typer.echo(_analyze_line(path, message), err=True)
    return typer.Exit(code=2)


@app.command("analyze")
def analyze_cmd(
    path: Annotated[Path, typer.Argument(help="Annotation file of format v1 (.json).")],
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the results JSON (sarcostat-results).")
    ] = False,
    as_csv: Annotated[bool, typer.Option("--csv", help="Print the results table (CSV).")] = False,
    out: Annotated[
        Path | None,
        typer.Option(
            "--out",
            help="Write the results JSON and/or CSV and the provenance sidecar into this folder.",
        ),
    ] = None,
    image_dir: Annotated[
        Path | None,
        typer.Option(
            "--image-dir",
            help="Folder of the images, to check their SHA-256 against the file.",
        ),
    ] = None,
) -> None:
    """Analyse an annotation file: sentences by default, or the results as JSON or CSV."""
    import warnings

    from sarcostat.analysis import analyze_file
    from sarcostat.export import results_csv, results_json, write_exports
    from sarcostat.io.annotations import sha256_file

    if as_json and as_csv and out is None:
        raise _analyze_error(path, "choose --json or --csv, or write both with --out")
    if not path.is_file():
        raise _analyze_error(path, "no such file")
    if image_dir is not None and not image_dir.is_dir():
        raise _analyze_error(path, f"the image folder {image_dir.name} does not exist")
    if out is not None and out.exists() and not out.is_dir():
        raise _analyze_error(path, f"the output folder {out.name} is a file")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            results = analyze_file(path, image_dir=image_dir)
        except (OSError, ValueError) as exc:
            raise _analyze_error(path, _one_line(exc)) from None
    for warning in caught:
        typer.echo(_analyze_line(path, _one_line(warning.message)), err=True)
    if out is not None:
        kinds = [k for k, on in (("results_json", as_json), ("results_csv", as_csv)) if on]
        try:
            names = write_exports(
                results,
                out,
                path.stem,
                kinds or ("results_json", "results_csv"),
                inputs=[(path.name, sha256_file(path))],
            )
        except (OSError, ValueError) as exc:
            raise _analyze_error(path, _one_line(exc)) from None
        for name in names:
            typer.echo(name)
        return
    if as_json:
        typer.echo(results_json(results), nl=False)
        return
    if as_csv:
        typer.echo(results_csv(results), nl=False)
        return
    from sarcostat.engine.reports import report_lines

    for line in report_lines(results):
        typer.echo(line)


@app.command(
    "gui",
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def gui_cmd(ctx: typer.Context) -> None:
    """Print the address of the SarcoStat web app, which replaces the desktop window."""
    typer.echo(GUI_URL)


@sim_app.command("chain")
def sim_chain(config: ConfigOption = None) -> None:
    """Run the chain Monte Carlo (LR vs MVL across kappa x N)."""
    import matplotlib.pyplot as plt

    from sarcostat.simulation._artifacts import RunArtifacts, run_metadata
    from sarcostat.simulation._plotting import simulation_style
    from sarcostat.simulation.chain import (
        ChainSimulator,
        default_plotters,
        load_config,
        render_all,
        run_monte_carlo,
        summarise_results,
    )

    cfg, config_path = _resolve_config(config, "config.yaml", load_config)
    artifacts = RunArtifacts(
        cfg.output_dir,
        logger_name="sarcostat.cli.sim.chain",
        console_handler=_console_handler(),
    )
    artifacts.save_parameters({**asdict(cfg), **run_metadata(config_path)})

    simulator = ChainSimulator.from_config(cfg)
    n_cells = len(cfg.kappa_values) * len(cfg.n_sarcomeres)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=_CONSOLE,
    ) as progress:
        task = progress.add_task("Running cells", total=n_cells)
        results = run_monte_carlo(
            simulator,
            cfg,
            progress=lambda done, total: progress.update(task, completed=done),
            logger=artifacts.logger,
        )

    summary = summarise_results(results, cfg.kappa_values, cfg.n_sarcomeres)
    artifacts.save_json("results_summary.json", summary)
    _print_chain_summary_table(summary, cfg)

    if cfg.save_raw_data:
        _save_chain_raw_npz(artifacts, results, cfg.kappa_values, cfg.n_sarcomeres)

    if cfg.save_figures:
        with simulation_style():
            figures = render_all(
                default_plotters(),
                results,
                cfg.kappa_values,
                cfg.n_sarcomeres,
                dpi=cfg.figure_dpi,
            )
            for name, fig in figures.items():
                artifacts.save_figure(fig, f"chain_{name}", dpi=cfg.figure_dpi)
                plt.close(fig)

    _CONSOLE.print(f"\n[green]Done[/green]. Output: {artifacts.output_dir}")


@sim_app.command("sarc")
def sim_sarc(config: ConfigOption = None) -> None:
    """Run the single-sarcomere noise sensitivity and boundary pipelines."""
    import matplotlib.pyplot as plt
    import numpy as np

    from sarcostat.simulation._artifacts import RunArtifacts, run_metadata
    from sarcostat.simulation._plotting import simulation_style
    from sarcostat.simulation.sarc import (
        BoundaryAnalysisPlot,
        BoundaryClassificationPipeline,
        ExampleSarcomeresPlot,
        NoiseSensitivityPipeline,
        NoiseSensitivityPlot,
        SarcomereGenerator,
        load_sarcomere_config,
    )

    cfg, config_path = _resolve_config(config, "sarcomere_config.yaml", load_sarcomere_config)
    artifacts = RunArtifacts(
        cfg.output_dir,
        timestamped=True,
        logger_name="sarcostat.cli.sim.sarc",
        console_handler=_console_handler(),
    )
    artifacts.save_parameters({**asdict(cfg), **run_metadata(config_path)})

    generator = SarcomereGenerator(cfg)
    noise_results = None
    if cfg.run_noise_sensitivity:
        with _CONSOLE.status("Running noise sensitivity pipeline"):
            noise_results = NoiseSensitivityPipeline(
                cfg=cfg, generator=generator, logger=artifacts.logger
            ).run()
        artifacts.save_json("noise_sensitivity_summary.json", noise_results.to_dict())
        if cfg.save_raw_data:
            artifacts.save_npz(
                "noise_sensitivity_raw",
                noise_levels=np.asarray(noise_results.noise_levels, dtype=float),
                asr_errors=noise_results.asr_errors,
                cur_errors=noise_results.cur_errors,
                shape_index_errors=noise_results.shape_index_errors,
            )

    boundary_results = None
    if cfg.generate_boundary_cases:
        with _CONSOLE.status("Running boundary classification pipeline"):
            boundary_results = BoundaryClassificationPipeline(
                cfg=cfg, generator=generator, logger=artifacts.logger
            ).run()
        artifacts.save_json("boundary_analysis_summary.json", boundary_results.to_dict())

    if cfg.save_figures:
        with simulation_style():
            figures = {"example_sarcomeres": ExampleSarcomeresPlot().render(cfg, generator)}
            if noise_results is not None:
                figures["noise_sensitivity_combined"] = NoiseSensitivityPlot().render(noise_results)
            if boundary_results is not None:
                figures["boundary_misclassification"] = BoundaryAnalysisPlot().render(
                    boundary_results
                )
            for name, fig in figures.items():
                artifacts.save_figure(fig, name, dpi=cfg.figure_dpi)
                plt.close(fig)

    _print_sarc_summary_table(noise_results, boundary_results)
    _CONSOLE.print(f"\n[green]Done[/green]. Output: {artifacts.output_dir}")


@sim_app.command("coverage")
def sim_coverage(config: ConfigOption = None) -> None:
    """Empirical CI coverage for circular mean and MVL bootstrap intervals."""
    from sarcostat.simulation.rigor.coverage import CoverageConfig, load_coverage_config, run

    cfg = _load_config(load_coverage_config, config) if config else CoverageConfig()
    with _CONSOLE.status("Running CI coverage validation"):
        out = run(cfg, config_path=config, console_handler=_console_handler())
    _CONSOLE.print(f"\n[green]Done[/green]. Output: {out}")


@sim_app.command("mde")
def sim_mde(config: ConfigOption = None) -> None:
    """Minimum detectable effect size for MVL, AsR, and Cur."""
    from sarcostat.simulation.rigor.mde import MDEConfig, load_mde_config, run

    cfg = _load_config(load_mde_config, config) if config else MDEConfig()
    with _CONSOLE.status("Running MDE analysis"):
        out = run(cfg, config_path=config, console_handler=_console_handler())
    _CONSOLE.print(f"\n[green]Done[/green]. Output: {out}")


def _save_chain_raw_npz(artifacts: Any, results: dict, kappa_values, n_sarcomeres) -> None:
    """Persist per-cell raw arrays to ``data/raw_k{k}_N{n}.npz``.

    Downstream figure scripts read these files directly, so the file
    naming and array keys are kept stable.
    """
    metric_keys = (
        "linearity_ratio",
        "mean_vector_length",
        "rayleigh_z",
        "rayleigh_p",
        "v_statistic",
        "v_p_value",
        "angles",
    )
    for k in kappa_values:
        k_str = str(int(k)) if float(k).is_integer() else str(k)
        for n in n_sarcomeres:
            payload = {m: results[m][k][n] for m in metric_keys if m in results}
            artifacts.save_npz(f"raw_k{k_str}_N{n}", **payload)


def _print_chain_summary_table(summary: dict, cfg: Any) -> None:
    """Render the chain-summary table for a single representative metric."""
    table = Table(title="Linearity ratio (mean across MC replicates)")
    table.add_column("kappa", justify="right")
    for n in cfg.n_sarcomeres:
        table.add_column(f"N={n}", justify="right")
    for k in cfg.kappa_values:
        row = [f"{k}"]
        for n in cfg.n_sarcomeres:
            cell = summary.get("linearity_ratio", {}).get(k, {}).get(n, {})
            mean = cell.get("mean", float("nan"))
            row.append(f"{mean:.4f}")
        table.add_row(*row)
    _CONSOLE.print(table)


def _print_sarc_summary_table(noise_results: Any, boundary_results: Any) -> None:
    """Render the sarc-summary tables (noise RMSE and boundary band widths)."""
    if noise_results is not None:
        table = Table(title="Single-sarcomere noise sensitivity")
        table.add_column("noise sigma (nm)", justify="right")
        table.add_column("AsR RMSE", justify="right")
        table.add_column("Cur RMSE", justify="right")
        table.add_column("class. accuracy", justify="right")
        for i, sigma in enumerate(noise_results.noise_levels):
            table.add_row(
                f"{sigma:g}",
                f"{noise_results.asr_rmse[i]:.4f}",
                f"{noise_results.cur_rmse[i]:.4f}",
                f"{noise_results.classification_agreement[i] * 100:.1f}%",
            )
        _CONSOLE.print(table)
    if boundary_results is not None:
        threshold = 100 * boundary_results.misclass_threshold
        bt = Table(title="Unreliable band around the class edges per noise level")
        bt.add_column("noise sigma (nm)", justify="right")
        bt.add_column(f"half-width with >= {threshold:g}% misclassification", justify="right")
        for sigma in sorted(boundary_results.critical_boundary_width):
            width = boundary_results.critical_boundary_width[sigma]
            bt.add_row(f"{sigma:g}", f"{width:.3f}")
        _CONSOLE.print(bt)


__all__ = ["app"]
