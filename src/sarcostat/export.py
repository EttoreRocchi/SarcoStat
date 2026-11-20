"""Exports of analysis results: results JSON, results CSV and the provenance sidecar.

Every writer takes the :class:`~sarcostat.analysis.AnalysisResults` of
:mod:`sarcostat.analysis` and returns text; :func:`write_exports` writes the
texts into a folder and returns the file names it wrote. The command line
and the web app return the same texts for the same document.

- :func:`results_json`: canonical JSON (sorted keys, two-space indentation,
  ASCII only, one trailing newline); NaN and infinities are written as
  ``null``, numbers in radians and in the unit of the settings. The
  structure is :func:`results_document`.
- :func:`results_csv`: one row per item, with the columns of the item
  types present (see :func:`results_csv`); empty cells for values that do
  not apply or are not finite; angles in degrees.
- :func:`provenance_sidecar`: the outputs it describes and the block of
  :func:`~sarcostat.io.provenance.provenance_from_digests` (version, source
  digest, input file names and SHA-256, settings).

No export records a folder, a time or a file size. pandas and matplotlib
are not used.
"""

from __future__ import annotations

import csv
import io
import json
import math
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

import numpy as np

from sarcostat.analysis import (
    CLASS_STABILITY_DRAWS,
    N_PERMUTATIONS,
    PERMUTATION_SEED,
    SHAPE_TOLERANCE,
    AnalysisResults,
    ChainResult,
    ImageResults,
    PairResult,
    SarcomereUncertainty,
    ShapeItem,
)
from sarcostat.io.annotations import PathInput
from sarcostat.io.provenance import provenance_from_digests

RESULTS_FORMAT = "sarcostat-results"
RESULTS_FORMAT_VERSION = "1.0"
EXPORT_KINDS = ("results_json", "results_csv", "provenance")

COMMON_COLUMNS = (
    "image_id",
    "file_name",
    "item_id",
    "target_id",
    "item_type",
    "label",
    "skipped",
    "skip_reason",
    "unit",
)
SHAPE_COLUMNS = (
    "z_line_1_length",
    "z_line_2_length",
    "m_line_length",
    "sarcomere_length",
    "aspect_ratio",
    "curviness",
    "shape_index",
    "shape_classification",
)
UNCERTAINTY_COLUMNS = (
    "click_sigma_z",
    "click_sigma_m",
    "u_aspect_ratio",
    "u_curviness",
    "u_shape_index",
    "class_stability",
)
LANDMARK_COLUMNS = tuple(
    f"{line}_{axis}" for line in ("z1", "m", "z2") for axis in ("x1", "y1", "x2", "y2")
)
CHAIN_COLUMNS = (
    "n_z_lines",
    "branch_end",
    "n_segments",
    "total_length",
    "head_to_tail",
    "linearity_ratio",
    "mean_vector_length",
    "mvl_ci_low",
    "mvl_ci_high",
    "circular_mean_heading_degrees",
    "circular_std_degrees",
    "rayleigh_z",
    "rayleigh_p",
    "v_test_direction_degrees",
    "v_statistic",
    "v_p_value",
    "n_eff",
    "rho_squared_corrected",
    "lr_noise_floor",
    "lr_uniformity_p",
    "turning_mvl",
    "turning_mean_degrees",
    "turning_circular_std_degrees",
    "curvature_per_unit",
    "serial_dependence_statistic",
    "serial_dependence_p",
    "persistence_length",
    "persistence_amplitude",
)
REGISTER_COLUMNS = (
    "myofibril_a",
    "myofibril_b",
    "n_z_lines_a",
    "n_z_lines_b",
    "n_pairs",
    "register_index",
    "mean_phase_degrees",
    "register_spread",
    "h_mean",
    "h_unregistered",
    "lateral_distance",
    "register_v_statistic",
    "register_v_p_value",
    "register_rayleigh_z",
    "register_rayleigh_p",
    "register_serial_dependence_statistic",
    "register_serial_dependence_p",
)


def _plain(value: Any) -> Any:
    """``value`` with numpy scalars, tuples and non-finite floats made JSON-safe."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [_plain(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        return number if math.isfinite(number) else None
    return value


def _dumps(payload: dict[str, Any]) -> str:
    text = json.dumps(
        _plain(payload),
        sort_keys=True,
        indent=2,
        separators=(",", ": "),
        ensure_ascii=True,
        allow_nan=False,
    )
    return text + "\n"


def _settings_dict(image: ImageResults) -> dict[str, Any]:
    s = image.settings
    return {
        "pixel_size": s.pixel_size,
        "unit": s.unit,
        "scale_source": s.scale_source,
        "reference_direction_deg": s.reference_direction_deg,
        "click_sigma_z": s.click_sigma_z,
        "click_sigma_m": s.click_sigma_m,
        "click_perp_ratio": s.click_perp_ratio,
    }


def _uncertainty_by_id(image: ImageResults) -> dict[int, SarcomereUncertainty]:
    return {u.sarcomere_id: u for u in image.uncertainties or ()}


def _shape_dict(shape: ShapeItem, unc: SarcomereUncertainty | None) -> dict[str, Any]:
    return {
        "item_id": shape.item_id,
        "target_id": shape.target_id,
        "sarcomere_id": shape.sarcomere_id,
        "skipped": shape.geometry is None,
        "skip_reason": shape.skip_reason,
        "geometry": None if shape.geometry is None else shape.geometry.to_dict(),
        "uncertainty": None if unc is None else unc.uncertainty.to_dict(),
        "class_stability": None if unc is None else unc.stability.to_dict(),
    }


def _chain_dict(chain: ChainResult) -> dict[str, Any]:
    return {
        "item_id": chain.item_id,
        "target_id": chain.target_id,
        "chain": chain.chain,
        "n_z_lines": chain.n_z_lines,
        "branch_end": chain.branch_end,
        "skipped": chain.metrics is None,
        "skip_reason": chain.skip_reason,
        "metrics": None if chain.metrics is None else chain.metrics.to_dict(),
    }


def _pair_dict(pair: PairResult) -> dict[str, Any]:
    return {
        "item_id": pair.item_id,
        "target_id": pair.target_id,
        "pair": pair.pair,
        "myofibril_a": pair.myofibril_a,
        "myofibril_b": pair.myofibril_b,
        "n_z_lines_a": pair.n_z_lines_a,
        "n_z_lines_b": pair.n_z_lines_b,
        "skipped": pair.metrics is None,
        "skip_reason": pair.skip_reason,
        "metrics": None if pair.metrics is None else pair.metrics.to_dict(),
    }


def _image_dict(image: ImageResults) -> dict[str, Any]:
    by_id = _uncertainty_by_id(image)
    summary = None
    if image.shape_summary is not None:
        chain = image.shape_summary.chain.to_dict()
        del chain["sarcomeres"], chain["timestamp"]
        summary = {"chain": chain, "z_line_alignment": image.shape_summary.alignment.to_dict()}
    return {
        "image_id": image.image_id,
        "file_name": image.file_name,
        "sha256": image.sha256,
        "width_px": image.width_px,
        "height_px": image.height_px,
        "settings": _settings_dict(image),
        "notices": [{"code": n.code, "params": dict(n.params)} for n in image.notices],
        "sarcomeres": [_shape_dict(s, by_id.get(s.sarcomere_id)) for s in image.shapes],
        "shape_summary": summary,
        "chains": [_chain_dict(c) for c in image.chains],
        "register": [_pair_dict(p) for p in image.register],
    }


def results_document(results: AnalysisResults) -> dict[str, Any]:
    """The results as a JSON-ready dictionary (the content of :func:`results_json`).

    Structure: ``format`` (``"sarcostat-results"``), ``format_version``
    and ``images``, one entry per analysed image with ``image_id``,
    ``file_name``, ``sha256``, ``width_px``, ``height_px``, ``settings``,
    ``notices`` (``{"code", "params"}``), ``sarcomeres`` (every shape item:
    ``item_id``, ``target_id``, ``sarcomere_id``, ``skipped``,
    ``skip_reason``, ``geometry``, ``uncertainty``, ``class_stability``),
    ``shape_summary`` (``null`` or ``{"chain", "z_line_alignment"}``),
    ``chains`` (every chain item: ``item_id``, ``target_id``, ``chain``,
    ``n_z_lines``, ``branch_end``, ``skipped``, ``skip_reason``,
    ``metrics``) and ``register`` (every pair item: ``item_id``,
    ``target_id``, ``pair``, ``myofibril_a``, ``myofibril_b``,
    ``n_z_lines_a``, ``n_z_lines_b``, ``skipped``, ``skip_reason``,
    ``metrics``). Reason and notice codes are those of
    :mod:`sarcostat.analysis`.
    """
    return {
        "format": RESULTS_FORMAT,
        "format_version": RESULTS_FORMAT_VERSION,
        "images": [_image_dict(image) for image in results.images],
    }


def results_json(results: AnalysisResults) -> str:
    """Canonical JSON text of :func:`results_document`.

    Keys are sorted, the indentation is two spaces with ``", "``/``": "``
    separators, non-ASCII characters are escaped, NaN and infinities are
    ``null`` and the text ends with one newline, so equal results give
    identical text.
    """
    return _dumps(results_document(results))


def _degrees(value: float | None) -> float | None:
    return None if value is None else float(np.degrees(value))


def _shape_cells(shape: ShapeItem, unc: SarcomereUncertainty | None) -> dict[str, Any]:
    g = shape.geometry
    if g is None:
        return {}
    cells: dict[str, Any] = {
        "z_line_1_length": g.z_line_1_length,
        "z_line_2_length": g.z_line_2_length,
        "m_line_length": g.m_line_length,
        "sarcomere_length": g.sarcomere_length,
        "aspect_ratio": g.aspect_ratio,
        "curviness": g.curviness,
        "shape_index": g.shape_index,
        "shape_classification": g.shape_classification.value,
    }
    if unc is not None:
        su, st = unc.uncertainty, unc.stability
        cells.update(
            click_sigma_z=su.sigma_z_along,
            click_sigma_m=su.sigma_m_along,
            u_aspect_ratio=su.u_aspect_ratio,
            u_curviness=su.u_curviness,
            u_shape_index=su.u_shape_index,
            class_stability=st.stability,
        )
    lines = (("z1", g.z_line_1_points), ("m", g.m_line_points), ("z2", g.z_line_2_points))
    for name, (p, q) in lines:
        cells.update(
            {f"{name}_x1": p[0], f"{name}_y1": p[1], f"{name}_x2": q[0], f"{name}_y2": q[1]}
        )
    return cells


def _chain_cells(chain: ChainResult) -> dict[str, Any]:
    cells: dict[str, Any] = {"n_z_lines": chain.n_z_lines, "branch_end": chain.branch_end}
    a = chain.metrics
    if a is None:
        return cells
    ci = a.mvl_ci
    cells.update(
        n_segments=a.n_segments,
        total_length=a.total_length,
        head_to_tail=a.head_to_tail_distance,
        linearity_ratio=a.linearity_ratio,
        mean_vector_length=a.mean_vector_length,
        mvl_ci_low=None if ci is None else ci[0],
        mvl_ci_high=None if ci is None else ci[1],
        circular_mean_heading_degrees=_degrees(a.circular_mean),
        circular_std_degrees=_degrees(a.circular_std),
        rayleigh_z=a.rayleigh_z,
        rayleigh_p=a.rayleigh_p,
        v_test_direction_degrees=_degrees(a.expected_direction),
        v_statistic=a.v_statistic,
        v_p_value=a.v_p_value,
        n_eff=a.n_eff,
        rho_squared_corrected=a.rho_squared_corrected,
        lr_noise_floor=a.lr_noise_floor,
        lr_uniformity_p=a.lr_uniformity_p,
        turning_mvl=a.turning_mvl,
        turning_mean_degrees=_degrees(a.turning_mean),
        turning_circular_std_degrees=_degrees(a.turning_circular_std),
        curvature_per_unit=a.curvature_per_unit,
        serial_dependence_statistic=a.serial_dependence_statistic,
        serial_dependence_p=a.serial_dependence_p,
        persistence_length=a.persistence_length,
        persistence_amplitude=a.persistence_amplitude,
    )
    return cells


def _pair_cells(pair: PairResult) -> dict[str, Any]:
    cells: dict[str, Any] = {
        "myofibril_a": pair.myofibril_a,
        "myofibril_b": pair.myofibril_b,
        "n_z_lines_a": pair.n_z_lines_a,
        "n_z_lines_b": pair.n_z_lines_b,
    }
    r = pair.metrics
    if r is None:
        return cells
    cells.update(
        n_pairs=r.n_pairs,
        register_index=r.register_index,
        mean_phase_degrees=_degrees(r.mean_phase),
        register_spread=r.register_spread,
        h_mean=r.h_mean,
        h_unregistered=r.h_unregistered,
        lateral_distance=r.lateral_distance,
        register_v_statistic=r.v_statistic,
        register_v_p_value=r.v_p_value,
        register_rayleigh_z=r.rayleigh_z,
        register_rayleigh_p=r.rayleigh_p,
        register_serial_dependence_statistic=r.serial_dependence_statistic,
        register_serial_dependence_p=r.serial_dependence_p,
    )
    return cells


def _rows(results: AnalysisResults) -> list[tuple[str, dict[str, Any]]]:
    rows = []
    for image in results.images:
        by_id = _uncertainty_by_id(image)
        entries = [
            *(
                (
                    "shape",
                    s.item_id,
                    s.target_id,
                    s.sarcomere_id,
                    s.skip_reason,
                    _shape_cells(s, by_id.get(s.sarcomere_id)),
                )
                for s in image.shapes
            ),
            *(
                ("chain", c.item_id, c.target_id, c.chain, c.skip_reason, _chain_cells(c))
                for c in image.chains
            ),
            *(
                ("pair", p.item_id, p.target_id, p.pair, p.skip_reason, _pair_cells(p))
                for p in image.register
            ),
        ]
        for kind, item_id, target_id, label, reason, cells in entries:
            row = {
                "image_id": image.image_id,
                "file_name": image.file_name,
                "item_id": item_id,
                "target_id": target_id,
                "item_type": kind,
                "label": label,
                "skipped": reason is not None,
                "skip_reason": reason,
                "unit": image.settings.unit,
                **cells,
            }
            rows.append((kind, row))
    return rows


def _cell(value: Any) -> str:
    value = _plain(value)
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return repr(value)
    return str(value)


def results_csv(results: AnalysisResults) -> str:
    """CSV text with one row per item of the results.

    Rows follow the images of the results; within an image come its shape
    items, then its chain items, then its pair items, each in item order.
    The columns are :data:`COMMON_COLUMNS` (``label`` is the
    ``sarcomere_id``, the chain number or the pair number), then the groups
    of the item types present in the results: :data:`SHAPE_COLUMNS`,
    :data:`UNCERTAINTY_COLUMNS` (when a click error is set),
    :data:`LANDMARK_COLUMNS` (landmarks in original pixels),
    :data:`CHAIN_COLUMNS` and :data:`REGISTER_COLUMNS`. Lengths are in the
    ``unit`` of the row and angles in degrees; a cell is empty when its
    value does not apply to the item or is not finite; booleans are
    ``true`` or ``false``; lines end with ``\\n``.
    """
    rows = _rows(results)
    kinds = {kind for kind, _row in rows}
    has_unc = any(image.uncertainties for image in results.images)
    columns = list(COMMON_COLUMNS)
    if "shape" in kinds:
        columns += SHAPE_COLUMNS
        if has_unc:
            columns += UNCERTAINTY_COLUMNS
        columns += LANDMARK_COLUMNS
    if "chain" in kinds:
        columns += CHAIN_COLUMNS
    if "pair" in kinds:
        columns += REGISTER_COLUMNS
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(columns)
    for _kind, row in rows:
        writer.writerow([_cell(row.get(column)) for column in columns])
    return buffer.getvalue()


def analysis_parameters(results: AnalysisResults) -> dict[str, Any]:
    """Settings and fixed constants the results depend on, for the provenance block."""
    return {
        "images": [{"image_id": im.image_id, **_settings_dict(im)} for im in results.images],
        "shape_tolerance": SHAPE_TOLERANCE,
        "class_stability_draws": CLASS_STABILITY_DRAWS,
        "class_stability_seed": "sarcomere_id",
        "permutations": N_PERMUTATIONS,
        "permutation_seed": PERMUTATION_SEED,
    }


def provenance_block(
    results: AnalysisResults, inputs: Iterable[tuple[str, str]] = ()
) -> dict[str, Any]:
    """Provenance block of the results.

    Parameters
    ----------
    results : AnalysisResults
        The results it describes.
    inputs : iterable of (str, str)
        ``(file name, SHA-256)`` of input files to list before the images,
        for example the annotation file.

    Returns
    -------
    dict
        The block of :func:`~sarcostat.io.provenance.provenance_from_digests`:
        the inputs, then each image's file name and recorded SHA-256, and
        :func:`analysis_parameters`.
    """
    files = [*inputs, *((image.file_name, image.sha256) for image in results.images)]
    return provenance_from_digests(files, parameters=_plain(analysis_parameters(results)))


def provenance_sidecar(
    results: AnalysisResults,
    outputs: Sequence[str],
    inputs: Iterable[tuple[str, str]] = (),
) -> str:
    """JSON text of the provenance sidecar: ``{"outputs": [...], "provenance": {...}}``.

    Parameters
    ----------
    results : AnalysisResults
        The results the outputs hold.
    outputs : sequence of str
        Names of the files the sidecar describes (names, not paths).
    inputs : iterable of (str, str)
        ``(file name, SHA-256)`` of further input files (see
        :func:`provenance_block`).
    """
    names = [_check_name(name, "output") for name in outputs]
    return _dumps({"outputs": names, "provenance": provenance_block(results, inputs)})


def _check_name(name: str, what: str) -> str:
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"{what} must be a file name without folders, got {name!r}")
    return name


def export_file_names(stem: str) -> dict[str, str]:
    """File names of the exports for ``stem``.

    Returns
    -------
    dict
        ``results_json``: ``<stem>_results.json``, ``results_csv``:
        ``<stem>_results.csv``, ``provenance``: ``<stem>_provenance.json``.
    """
    _check_name(stem, "stem")
    return {
        "results_json": f"{stem}_results.json",
        "results_csv": f"{stem}_results.csv",
        "provenance": f"{stem}_provenance.json",
    }


def write_exports(
    results: AnalysisResults,
    output_dir: PathInput,
    stem: str,
    kinds: Sequence[str] = ("results_json", "results_csv"),
    inputs: Iterable[tuple[str, str]] = (),
) -> list[str]:
    """Write the exports of ``results`` into ``output_dir`` with their provenance sidecar.

    Parameters
    ----------
    results : AnalysisResults
        The results to write.
    output_dir : str or os.PathLike
        Folder to write into (created if missing).
    stem : str
        Beginning of the file names (:func:`export_file_names`).
    kinds : sequence of str
        ``results_json`` and/or ``results_csv``; the sidecar is always
        written, last.
    inputs : iterable of (str, str)
        ``(file name, SHA-256)`` of further input files for the sidecar.

    Returns
    -------
    list of str
        Names of the files written, the sidecar last.
    """
    names = export_file_names(stem)
    writers = {"results_json": results_json, "results_csv": results_csv}
    unknown = [kind for kind in kinds if kind not in writers]
    if unknown:
        raise ValueError(f"unknown export kinds {unknown}; choose from {sorted(writers)}")
    folder = Path(output_dir)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for kind in dict.fromkeys(kinds):
        (folder / names[kind]).write_bytes(writers[kind](results).encode("utf-8"))
        written.append(names[kind])
    sidecar = provenance_sidecar(results, written, inputs)
    (folder / names["provenance"]).write_bytes(sidecar.encode("utf-8"))
    return [*written, names["provenance"]]


__all__ = [
    "CHAIN_COLUMNS",
    "COMMON_COLUMNS",
    "EXPORT_KINDS",
    "LANDMARK_COLUMNS",
    "REGISTER_COLUMNS",
    "RESULTS_FORMAT",
    "RESULTS_FORMAT_VERSION",
    "SHAPE_COLUMNS",
    "UNCERTAINTY_COLUMNS",
    "analysis_parameters",
    "export_file_names",
    "provenance_block",
    "provenance_sidecar",
    "results_csv",
    "results_document",
    "results_json",
    "write_exports",
]
