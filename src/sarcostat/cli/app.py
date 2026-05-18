"""Top-level Typer application for SarcoStat.

The CLI exposes one subcommand group (``sim``) and a ``version``
command. Each ``sim`` subcommand is a thin wrapper around the
corresponding pipeline / runner: it loads the YAML config, builds the
appropriate pipeline objects, runs them, persists the artifacts via
:class:`~sarcostat.simulation._artifacts.RunArtifacts`, and renders
formatted summaries with rich tables.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from sarcostat import __version__

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


def _ConfigOption() -> typer.Option:
    """Reusable ``--config`` option for the simulation subcommands."""
    return typer.Option(
        None,
        "--config",
        "-c",
        help="Path to a YAML configuration file.",
        exists=True,
        dir_okay=False,
        readable=True,
    )


@app.command("version")
def version_cmd() -> None:
    """Print the installed SarcoStat version."""
    _CONSOLE.print(f"sarcostat [bold]{__version__}[/bold]")


@app.command("gui")
def gui_cmd() -> None:
    """Launch the interactive Tk annotation window."""
    from sarcostat.gui.gui import main as gui_main

    gui_main()


@sim_app.command("chain")
def sim_chain(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="YAML config path.", exists=True, dir_okay=False, readable=True
    ),
) -> None:
    """Run the chain Monte Carlo (LR vs MVL across kappa x N)."""
    import matplotlib.pyplot as plt

    from sarcostat.simulation._artifacts import RunArtifacts
    from sarcostat.simulation.chain import (
        ChainSimulator,
        Config,
        default_plotters,
        load_config,
        render_all,
        run_monte_carlo,
        summarise_results,
    )

    cfg: Config = load_config(config) if config else _packaged_chain_default()
    artifacts = RunArtifacts(cfg.output_dir, logger_name="sarcostat.cli.sim.chain")
    artifacts.save_parameters(_chain_param_summary(cfg))

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
def sim_sarc(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="YAML config path.", exists=True, dir_okay=False, readable=True
    ),
) -> None:
    """Run the single-sarcomere noise sensitivity and boundary pipelines."""
    from dataclasses import asdict

    import matplotlib.pyplot as plt

    from sarcostat.simulation._artifacts import RunArtifacts
    from sarcostat.simulation.sarc import (
        BoundaryAnalysisPlot,
        BoundaryClassificationPipeline,
        ExampleSarcomeresPlot,
        NoiseSensitivityPipeline,
        NoiseSensitivityPlot,
        SarcomereGenerator,
        SingleSarcomereConfig,
        load_sarcomere_config,
    )

    cfg: SingleSarcomereConfig = (
        load_sarcomere_config(config) if config else SingleSarcomereConfig()
    )
    artifacts = RunArtifacts(cfg.output_dir, timestamped=True, logger_name="sarcostat.cli.sim.sarc")
    artifacts.save_parameters(asdict(cfg))

    generator = SarcomereGenerator(cfg)
    with _CONSOLE.status("Running noise sensitivity pipeline"):
        noise_results = NoiseSensitivityPipeline(
            cfg=cfg, generator=generator, logger=artifacts.logger
        ).run()
    artifacts.save_json("noise_sensitivity_summary.json", noise_results.to_dict())

    boundary_results = None
    if cfg.generate_boundary_cases:
        with _CONSOLE.status("Running boundary classification pipeline"):
            boundary_results = BoundaryClassificationPipeline(
                cfg=cfg, generator=generator, logger=artifacts.logger
            ).run()
        artifacts.save_json("boundary_analysis_summary.json", boundary_results.to_dict())

    if cfg.save_figures:
        fig_noise = NoiseSensitivityPlot().render(noise_results)
        artifacts.save_figure(fig_noise, "noise_sensitivity_combined", dpi=cfg.figure_dpi)
        plt.close(fig_noise)
        if boundary_results is not None:
            fig_bound = BoundaryAnalysisPlot().render(boundary_results)
            artifacts.save_figure(fig_bound, "boundary_misclassification", dpi=cfg.figure_dpi)
            plt.close(fig_bound)
        fig_examples = ExampleSarcomeresPlot().render(cfg, generator)
        artifacts.save_figure(fig_examples, "example_sarcomeres", dpi=cfg.figure_dpi)
        plt.close(fig_examples)

    _print_sarc_summary_table(noise_results, boundary_results)
    _CONSOLE.print(f"\n[green]Done[/green]. Output: {artifacts.output_dir}")


@sim_app.command("coverage")
def sim_coverage(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="YAML config path.", exists=True, dir_okay=False, readable=True
    ),
) -> None:
    """Empirical CI coverage for circular mean and MVL bootstrap intervals."""
    from sarcostat.simulation.rigor.coverage import (
        CoverageConfig,
        load_coverage_config,
    )
    from sarcostat.simulation.rigor.coverage import (
        run as run_coverage,
    )

    cfg = load_coverage_config(config) if config else CoverageConfig()
    with _CONSOLE.status("Running CI coverage validation"):
        out = run_coverage(cfg)
    _CONSOLE.print(f"\n[green]Done[/green]. Output: {out}")


@sim_app.command("mde")
def sim_mde(
    config: Path | None = typer.Option(
        None, "--config", "-c", help="YAML config path.", exists=True, dir_okay=False, readable=True
    ),
) -> None:
    """Minimum detectable effect size for MVL, AsR, and Cur."""
    from sarcostat.simulation.rigor.mde import (
        MDEConfig,
        load_mde_config,
    )
    from sarcostat.simulation.rigor.mde import (
        run as run_mde,
    )

    cfg = load_mde_config(config) if config else MDEConfig()
    with _CONSOLE.status("Running MDE analysis"):
        out = run_mde(cfg)
    _CONSOLE.print(f"\n[green]Done[/green]. Output: {out}")


def _packaged_chain_default():
    """Return the chain ``Config`` from the packaged ``config.yaml``.

    The packaged YAML lives next to this module's grandparent (the
    ``simulation/`` directory). If it is missing, raise a clear error.
    """
    from sarcostat.simulation.chain import load_config

    candidate = Path(__file__).parent.parent / "simulation" / "config.yaml"
    if not candidate.exists():
        raise typer.BadParameter(
            "No --config supplied and the packaged default "
            f"{candidate} is missing. Pass --config explicitly."
        )
    _CONSOLE.print(f"Using packaged default config: [cyan]{candidate}[/cyan]")
    return load_config(candidate)


def _save_chain_raw_npz(artifacts, results: dict, kappa_values, n_sarcomeres) -> None:
    """Persist per-cell raw arrays to ``data/raw_k{k}_N{n}.npz``.

    The figure scripts under ``paper/latex_ejtm/figures`` consume these
    files directly; the per-cell layout matches the original
    ``ChainSimulator`` output.
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


def _chain_param_summary(cfg) -> dict:
    """Subset of the chain config that gets persisted alongside the run."""
    return {
        "n_simulations": cfg.n_simulations,
        "seed": cfg.seed,
        "n_sarcomeres": cfg.n_sarcomeres,
        "kappa_values": cfg.kappa_values,
        "length_distribution": cfg.length_distribution,
        "angular_distribution": cfg.angular_distribution,
    }


def _print_chain_summary_table(summary: dict, cfg) -> None:
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


def _print_sarc_summary_table(noise_results, boundary_results) -> None:
    """Render the sarc-summary table covering RMSE per noise level."""
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
        bt = Table(title="Critical boundary width per noise level")
        bt.add_column("noise sigma (nm)", justify="right")
        bt.add_column("|Cur - AsR| at <10% misclassification", justify="right")
        for sigma in sorted(boundary_results.critical_boundary_width):
            width = boundary_results.critical_boundary_width[sigma]
            bt.add_row(f"{sigma:g}", f"{width:.3f}")
        _CONSOLE.print(bt)


__all__ = ["app"]
