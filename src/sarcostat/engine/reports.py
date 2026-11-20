"""Sentences, tables and plot data of analysis results: the one formatter of displayed numbers.

Entry point of the bridge's ``results.get``::

    results_view(image: sarcostat.analysis.ImageResults | Mapping) -> dict

It returns a JSON-safe dictionary with exactly three keys:

- ``sentences``: list of str, one line each, plain language;
- ``tables``: ``{name: {"title": str, "columns": [str], "rows": [[str]], "notes": [str]}}``
  with the names ``sarcomeres``, ``shape_summary``, ``chains`` and ``register``,
  each present only when the image has such results; every cell is a string
  (``""`` when a value does not apply), numbers already formatted with their units;
  ``notes`` are the plain-language lines of the table's metrics;
- ``plot_data``: raw numbers (``null`` when not finite), always with the three names
  ``shape`` (list of ``{"sarcomere_id", "shape_index", "u_shape_index", "shape_class"}``),
  ``chains`` (list of ``{"chain", "headings_deg": [..], "turning_deg": [..],
  "mean_heading_deg", "mean_vector_length", "reference_direction_deg"}``) and
  ``register`` (list of ``{"pair", "phases_deg": [..], "correlation_lags": [..],
  "correlation_values": [..]}``).

Every number SarcoStat shows to a person, in the web app or on the command
line, is formatted here, by :func:`format_number`, from the results of
:mod:`sarcostat.analysis` (or their JSON document, :func:`sarcostat.export.results_document`).
Besides :func:`results_view`, :func:`results_report` gives the same for every
image of the results and :func:`report_lines` the sentences of every image,
as ``sarcostat analyze`` prints them.

Sentences are plain language and each fits on one line; every length
carries its unit and every angle is in degrees. Reason and notice codes of
:mod:`sarcostat.analysis` become words here. Pairs are named by the order
of the image's pair items, so a pair without overlapping Z-lines is reported
as skipped and never shifts the labels of the others.

Nothing from the GUI, the command line, matplotlib or pandas is imported;
:mod:`sarcostat.analysis`, :mod:`sarcostat.core` and :mod:`sarcostat.export`
are imported only inside the functions that need them.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

UNKNOWN = "n/a"
FIGURES = {
    "length": 2,
    "ratio": 3,
    "index": 4,
    "statistic": 3,
    "angle": 1,
    "percent": 0,
}
"""Decimals of each kind of number in :func:`format_number`."""
P_FLOOR = 1e-4
CAVEAT_SEGMENTS = 10
SIGNIFICANCE = 0.05

REASON_WORDS = {
    "item_skipped": "not annotated",
    "m_line_skipped": "M-line not identifiable",
    "degenerate_geometry": "a line of zero length or Z-lines that coincide",
    "coincident_z_lines": "two consecutive Z-lines share their midpoint",
    "no_overlap": "no overlapping Z-lines",
}
"""Words of the reason codes of :data:`sarcostat.analysis.SKIP_REASONS`."""

PIXEL_UNITS = frozenset({"px", "pixel", "pixels"})
"""Units that mean pixels: with a pixel size of 1 they are no scale at all."""
SCALE_SENTENCES = {
    "gui": "Scale: 1 px = {size}, as saved in the annotation file.",
    "image": "Scale: 1 px = {size}, as recorded with the image.",
    "none": "No scale is set: lengths are in pixels.",
}

NOTICE_SENTENCES = {
    "register_needs_pair_items": (
        "The Z-line register needs pair items: this image has {n_chain_items} chain items "
        "and no pair item, so no register is computed."
    ),
}
"""Sentences of the notice codes of :data:`sarcostat.analysis.NOTICE_CODES`."""

SHAPE_HINT = (
    "No sarcomere is measured yet. 6-point annotation: A and B on the first Z-line, C and D "
    "on the M-line, E and F on the second Z-line. With two or more sarcomeres the chain "
    "parameters alpha and H are added; with a click error, AsR, Cur and SI get their "
    "uncertainty and the shape class its stability."
)
CHAIN_HINT = (
    "No chain is traced yet. Click both ends of each Z-line of one myofibril, in order; the "
    "headings between consecutive Z-line midpoints give LR, MVL and the circular tests."
)
REGISTER_HINT = (
    "No pair is traced yet. Trace the Z-lines of one myofibril and then those of the adjacent "
    "myofibril as one pair item; each Z-line of the second is paired with the nearest Z-line "
    "of the first, and the offset divided by the local sarcomere length is a phase (0° in "
    "register, 180° half a sarcomere apart)."
)
EMPTY_IMAGE = "This image has no annotated items."

SHAPE_NOTES = (
    "SI ± u(SI): Cur ÷ AsR. Above 1 the M-line is shorter than the Z-lines, "
    "so the sides curve inwards.",
    "AsR, Cur: sarcomere length ÷ mean Z-line length; sarcomere length ÷ M-line length.",
    "Stability: share of simulated re-annotations with your click error that keep the shape class.",
)
SUMMARY_NOTES = (
    "Alpha is the angle between consecutive sarcomere axes; it assumes consecutive "
    "sarcomeres of the same myofibril.",
    "H is the offset between the first Z-lines (A-B) of consecutive sarcomeres; it assumes "
    "one sarcomere per adjacent myofibril (a Z-staircase).",
)
CHAIN_NOTES = (
    "LR: end-to-end distance ÷ path length; 1 is straight. The noise floor is the LR "
    "that 95% of chains without alignment stay below.",
    "MVL [95% CI]: agreement of headings; 0 random, 1 identical.",
    "Rayleigh, V-test: headings are not uniform; headings cluster around the reference "
    "direction you set.",
    "Turning angles, persistence: local regularity, independent of tract length; persistence "
    "not estimable when headings stay correlated over the whole tract.",
    "MVL, Rayleigh and V-test are computed on the headings between consecutive Z-line "
    "midpoints (image frame: 0° = right, 90° = down).",
    "LR and the heading MVL describe global straightness: when headings are serially "
    "dependent they decrease as more sarcomeres are traced.",
)
REGISTER_NOTES = (
    "Register index R, phase: 1 when paired Z-lines keep the same offset, 0 when it is "
    "unrelated; 0° in register, 180° half a sarcomere apart.",
    "Spread, mean |offset| H: typical scatter of the offsets; distance between paired "
    "Z-lines along the myofibril, with the out-of-register reference.",
    "Drift test: a significant result means the offset drifts along the myofibril; with few "
    "Z-line pairs a non-significant result has little power.",
)

SARCOMERE_COLUMNS = (
    "Sarcomere",
    "Class",
    "SI",
    "AsR",
    "Cur",
    "Stability",
    "Sarcomere length",
    "Z-line 1",
    "Z-line 2",
    "M-line",
    "Status",
)
SUMMARY_COLUMNS = ("Parameter", "Value")
CHAIN_COLUMNS = (
    "Chain",
    "Z-lines",
    "Path length",
    "LR",
    "LR noise floor",
    "MVL [95% CI]",
    "Mean heading",
    "Rayleigh",
    "V-test",
    "Turning MVL",
    "Persistence length",
    "Status",
)
REGISTER_COLUMNS = (
    "Pair",
    "Myofibrils",
    "Z-line pairs",
    "Register index R",
    "Phase",
    "Spread",
    "Mean |offset| H",
    "H out of register",
    "V-test vs 0°",
    "Rayleigh",
    "Drift test",
    "Status",
)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def format_number(value: Any, kind: str, unit: str | None = None) -> str:
    """Format one number for display; the only number formatter of displayed results.

    Parameters
    ----------
    value : float, int or None
        The value; ``None``, NaN and infinities give ``"n/a"``.
    kind : str
        ``"length"`` (2 decimals, with ``unit``), ``"ratio"`` (AsR, Cur, SI:
        3 decimals), ``"index"`` (LR, MVL, R: 4 decimals), ``"statistic"``
        (Z, V, D: 3 decimals), ``"angle"`` (degrees, 1 decimal, with
        ``°``), ``"percent"`` (a fraction shown as a whole percentage),
        ``"count"`` (an integer) or ``"p"`` (``"p = 0.0123"``, or
        ``"p < 0.0001"``).
    unit : str, optional
        Unit written after a length.

    Returns
    -------
    str
        The text; a value that rounds to zero is never written with a minus sign.
    """
    number = _finite(value)
    if number is None:
        return UNKNOWN
    if kind == "count":
        return str(int(round(number)))
    if kind == "p":
        return f"p < {P_FLOOR:g}" if number < P_FLOOR else f"p = {_fixed(number, 4)}"
    if kind == "percent":
        return f"{_fixed(100.0 * number, FIGURES['percent'])}%"
    if kind not in FIGURES:
        raise ValueError(f"unknown kind of number {kind!r}")
    text = _fixed(number, FIGURES[kind])
    if kind == "angle":
        return f"{text}°"
    if kind == "length" and unit:
        return f"{text} {unit}"
    return text


def _fixed(number: float, decimals: int) -> str:
    text = f"{number:.{decimals}f}"
    return text[1:] if text.startswith("-") and float(text) == 0.0 else text


def _degrees(radians: Any) -> float | None:
    number = _finite(radians)
    return None if number is None else math.degrees(number)


def _plus_minus(value: Any, spread: Any, kind: str, unit: str | None = None) -> str:
    """``value ± spread`` with one unit at the end (only ``value`` when ``spread`` is unknown)."""
    number, deviation = _finite(value), _finite(spread)
    if number is None or deviation is None:
        return format_number(value, kind, unit)
    digits = FIGURES[kind]
    text = f"{_fixed(number, digits)} \u00b1 {_fixed(deviation, digits)}"
    if kind == "angle":
        return f"{text}\u00b0"
    if kind == "length" and unit:
        return f"{text} {unit}"
    return text


def _skip_text(label: str, code: str | None) -> str:
    return f"{label}: {REASON_WORDS.get(code or '', code or 'skipped')} (skipped)"


def _plain(value: Any) -> Any:
    """``value`` as JSON data: numpy values made Python, non-finite numbers ``None``."""
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return _finite(value)
    if hasattr(value, "tolist"):
        return _plain(value.tolist())
    return None


def results_document(results: Any) -> dict[str, Any]:
    """The JSON content of the results (``None`` for non-finite numbers).

    Parameters
    ----------
    results : AnalysisResults or mapping
        Results of :func:`sarcostat.analysis.analyze_document`, or their
        document (:func:`sarcostat.export.results_document`, or the parsed
        text of :func:`sarcostat.export.results_json`).
    """
    if isinstance(results, Mapping):
        doc: dict[str, Any] = _plain(results)
        return doc
    from sarcostat.export import results_json

    parsed: dict[str, Any] = json.loads(results_json(results))
    return parsed


def _scale_sentence(settings: Mapping[str, Any]) -> str:
    """The scale; 1 px of 1 px is no scale, wherever the setting comes from."""
    source = str(settings.get("scale_source"))
    unit = str(settings.get("unit"))
    if source == "none" or (unit in PIXEL_UNITS and _finite(settings.get("pixel_size")) == 1.0):
        return SCALE_SENTENCES["none"]
    size = format_number(settings.get("pixel_size"), "length", unit)
    return SCALE_SENTENCES.get(source, SCALE_SENTENCES["gui"]).format(size=size)


def _sigma_text(settings: Mapping[str, Any]) -> str:
    unit = str(settings.get("unit"))
    sigma_z, sigma_m = settings.get("click_sigma_z"), settings.get("click_sigma_m")
    if _finite(sigma_m) is None or sigma_m == sigma_z:
        return f"σ = {format_number(sigma_z, 'length', unit)}"
    return (
        f"σ = {format_number(sigma_z, 'length', unit)} on the Z-lines and "
        f"{format_number(sigma_m, 'length', unit)} on the M-line"
    )


def _click_sentence(settings: Mapping[str, Any]) -> str:
    if _finite(settings.get("click_sigma_z")) is None:
        return (
            "No click error is set, so AsR, Cur and SI are shown without their uncertainty "
            "and the stability of the shape class is not computed."
        )
    ratio = format_number(settings.get("click_perp_ratio"), "ratio")
    return (
        f"Click error: {_sigma_text(settings)} along the lines; across them it is {ratio} "
        "times as large."
    )


def _shape_lines(image: Mapping[str, Any]) -> tuple[list[str], list[list[str]], list[Any]]:
    settings = image["settings"]
    unit = str(settings["unit"])
    sentences: list[str] = []
    rows: list[list[str]] = []
    points: list[Any] = []
    for shape in image["sarcomeres"]:
        label = f"Sarcomere {shape['sarcomere_id']}"
        g = shape["geometry"]
        if g is None:
            sentences.append(_skip_text(label, shape["skip_reason"]))
            rows.append(
                [str(shape["sarcomere_id"]), *[""] * 9, _skip_text(label, shape["skip_reason"])]
            )
            continue
        u = shape["uncertainty"] or {}
        st = shape["class_stability"]
        shape_class = str(g["shape_classification"])
        si = _plus_minus(g["shape_index"], u.get("u_shape_index"), "ratio")
        asr = _plus_minus(g["aspect_ratio"], u.get("u_aspect_ratio"), "ratio")
        cur = _plus_minus(g["curviness"], u.get("u_curviness"), "ratio")
        length = format_number(g["sarcomere_length"], "length", unit)
        z1 = format_number(g["z_line_1_length"], "length", unit)
        z2 = format_number(g["z_line_2_length"], "length", unit)
        m = format_number(g["m_line_length"], "length", unit)
        sentences.append(
            f"{label}: {shape_class}, SI {si}; AsR {asr}, Cur {cur}; sarcomere length {length}, "
            f"Z-lines {z1} and {z2}, M-line {m}."
        )
        stability = ""
        if st is not None:
            stability = format_number(st["stability"], "percent")
            sentences.append(
                f"{label}: kept as {st['reference_class']} in {stability} of simulated "
                f"re-annotations with your click error, {_sigma_text(settings)}."
            )
        rows.append(
            [
                str(shape["sarcomere_id"]),
                shape_class,
                si,
                asr,
                cur,
                stability,
                length,
                z1,
                z2,
                m,
                "measured",
            ]
        )
        points.append(
            {
                "sarcomere_id": shape["sarcomere_id"],
                "shape_index": g["shape_index"],
                "u_shape_index": u.get("u_shape_index"),
                "shape_class": shape_class,
            }
        )
    return sentences, rows, points


def _summary_lines(summary: Mapping[str, Any], unit: str) -> tuple[list[str], list[list[str]]]:
    chain, align = summary["chain"], summary["z_line_alignment"]
    n = format_number(chain["n_sarcomeres"], "count")
    asr = _plus_minus(chain["mean_aspect_ratio"], chain["std_aspect_ratio"], "ratio")
    cur = _plus_minus(chain["mean_curviness"], chain["std_curviness"], "ratio")
    si = _plus_minus(chain["mean_shape_index"], chain["std_shape_index"], "ratio")
    dominant = str(chain["dominant_shape"])
    alpha = _plus_minus(_degrees(chain["alpha_mean"]), _degrees(chain["alpha_std"]), "angle")
    h = _plus_minus(align["h_mean"], align["h_std"], "length", unit)
    sentences = [
        f"{n} measured sarcomeres (mean ± sd): AsR {asr}, Cur {cur}, SI {si}; "
        f"dominant shape {dominant}.",
        f"Alpha between consecutive sarcomere axes: {alpha} (mean ± sd); it assumes "
        "consecutive sarcomeres of the same myofibril.",
        f"H between the first Z-lines (A-B) of consecutive sarcomeres: {h} (mean ± sd); "
        "it assumes one sarcomere per adjacent myofibril (a Z-staircase).",
    ]
    rows = [
        ["Sarcomeres", n],
        ["AsR (mean ± sd)", asr],
        ["Cur (mean ± sd)", cur],
        ["SI (mean ± sd)", si],
        ["Dominant shape", dominant],
        ["Alpha (mean ± sd)", alpha],
        ["H (mean ± sd)", h],
    ]
    return sentences, rows


def _persistence(metrics: Mapping[str, Any], unit: str) -> str:
    length = _finite(metrics.get("persistence_length"))
    if length is None:
        if _finite(metrics.get("persistence_amplitude")) is not None:
            return "not estimable: headings stay correlated over the whole tract"
        return "not estimable"
    from sarcostat.core.circular_stats import _LP_RESOLVED_MAX_TRACES

    total = _finite(metrics.get("total_length"))
    if total is not None and length > _LP_RESOLVED_MAX_TRACES * total:
        return f"not resolved, above {_LP_RESOLVED_MAX_TRACES:g} times the traced length"
    return format_number(length, "length", unit)


def _rayleigh_sentence(label: str, z: Any, p: Any, n: Any) -> str:
    from sarcostat.core import interpret_rayleigh_test

    test = f"Z = {format_number(z, 'statistic')}, {format_number(p, 'p')}"
    if _finite(z) is None or _finite(p) is None or _finite(n) is None:
        return f"{label}: Rayleigh test not available ({test})."
    bundle = interpret_rayleigh_test(float(z), float(p), int(n))
    if bundle["significant"]:
        return (
            f"{label}: headings are not uniform (Rayleigh {test}), with "
            f"{bundle['effect_description']} concentration."
        )
    return f"{label}: the Rayleigh test does not reject uniform headings ({test})."


def _v_sentence(label: str, metrics: Mapping[str, Any], reference: Any) -> str:
    from sarcostat.core import interpret_v_test

    v, p = metrics.get("v_statistic"), _finite(metrics.get("v_p_value"))
    degrees = _finite(reference)
    if p is None or degrees is None:
        return f"{label}: V-test not run, no reference direction is set."
    direction = format_number(degrees, "angle")
    test = f"V = {format_number(v, 'statistic')}, {format_number(p, 'p')}"
    bundle = interpret_v_test(_finite(v) or 0.0, p, math.radians(degrees))
    if bundle["significant"]:
        return f"{label}: headings cluster around the reference direction of {direction} ({test})."
    return (
        f"{label}: no evidence that headings cluster around the reference direction of "
        f"{direction} ({test})."
    )


def _chain_lines(image: Mapping[str, Any]) -> tuple[list[str], list[list[str]], list[Any]]:
    settings = image["settings"]
    unit = str(settings["unit"])
    reference = settings.get("reference_direction_deg")
    sentences: list[str] = []
    rows: list[list[str]] = []
    points: list[Any] = []
    for chain in image["chains"]:
        label = f"Chain {chain['chain']}"
        n_z = format_number(chain["n_z_lines"], "count")
        m = chain["metrics"]
        if m is None:
            sentences.append(_skip_text(label, chain["skip_reason"]))
            rows.append(
                [str(chain["chain"]), n_z, *[""] * 9, _skip_text(label, chain["skip_reason"])]
            )
            continue
        path = format_number(m["total_length"], "length", unit)
        lr = format_number(m["linearity_ratio"], "index")
        floor = format_number(m["lr_noise_floor"], "index")
        ci = m.get("mvl_ci")
        mvl = format_number(m["mean_vector_length"], "index")
        if ci and len(ci) == 2:
            mvl_ci = f"{mvl} [{format_number(ci[0], 'index')}, {format_number(ci[1], 'index')}]"
        else:
            mvl_ci = mvl
        heading = format_number(_degrees(m["circular_mean"]), "angle")
        spread = format_number(_degrees(m["circular_std"]), "angle")
        branch = "; it ends at a branch" if chain["branch_end"] else ""
        persistence = _persistence(m, unit)
        n_segments = m.get("n_segments")
        sentences += [
            f"{label}: {n_z} Z-lines, path length {path}, end-to-end distance "
            f"{format_number(m['head_to_tail_distance'], 'length', unit)}{branch}.",
            f"{label}: LR {lr} (1 is straight); without alignment LR stays below {floor} in 95% "
            f"of chains of this length ({format_number(m.get('lr_uniformity_p'), 'p')}).",
            f"{label}: MVL of headings {mvl_ci} (95% CI); mean heading {heading}, circular "
            f"standard deviation {spread}.",
            _rayleigh_sentence(label, m["rayleigh_z"], m["rayleigh_p"], n_segments),
            _v_sentence(label, m, reference),
            f"{label}: turning angles have MVL {format_number(m.get('turning_mvl'), 'index')}, "
            f"mean {format_number(_degrees(m.get('turning_mean')), 'angle')} and circular "
            f"standard deviation {format_number(_degrees(m.get('turning_circular_std')), 'angle')} "
            "per sarcomere; they describe local regularity, independent of tract length.",
            f"{label}: persistence length {persistence}.",
        ]
        count = _finite(n_segments)
        if count is not None and count < CAVEAT_SEGMENTS:
            sentences.append(
                f"{label}: with {format_number(count, 'count')} sarcomeres, LR and MVL are weakly "
                "determined; compare intervals, not point values."
            )
        v_cell = (
            f"V = {format_number(m.get('v_statistic'), 'statistic')}, "
            f"{format_number(m.get('v_p_value'), 'p')}"
            if _finite(m.get("v_p_value")) is not None
            else "not run"
        )
        rows.append(
            [
                str(chain["chain"]),
                n_z,
                path,
                lr,
                floor,
                mvl_ci,
                heading,
                f"Z = {format_number(m['rayleigh_z'], 'statistic')}, "
                f"{format_number(m['rayleigh_p'], 'p')}",
                v_cell,
                format_number(m.get("turning_mvl"), "index"),
                persistence,
                "analysed; ends at a branch" if chain["branch_end"] else "analysed",
            ]
        )
        segments = m.get("segments") or []
        points.append(
            {
                "chain": chain["chain"],
                "headings_deg": [_degrees(s.get("angle_absolute")) for s in segments],
                "turning_deg": [_degrees(s.get("angle_relative")) for s in segments[1:]],
                "mean_heading_deg": _degrees(m["circular_mean"]),
                "mean_vector_length": _finite(m["mean_vector_length"]),
                "reference_direction_deg": _finite(reference),
            }
        )
    return sentences, rows, points


def _drift_sentence(label: str, metrics: Mapping[str, Any]) -> str:
    p = _finite(metrics.get("serial_dependence_p"))
    if p is None:
        return f"{label}: drift test not run (fewer than 3 Z-line pairs)."
    test = (
        f"D = {format_number(metrics.get('serial_dependence_statistic'), 'statistic')}, "
        f"{format_number(p, 'p')}"
    )
    if p < SIGNIFICANCE:
        return (
            f"{label}: the offset drifts along the myofibril ({test}): the register is not "
            "stationary over this tract."
        )
    return (
        f"{label}: drift not significant over this tract ({test}); with few Z-line pairs the "
        "test has little power, so this does not show a stationary register."
    )


def _register_lines(image: Mapping[str, Any]) -> tuple[list[str], list[list[str]], list[Any]]:
    unit = str(image["settings"]["unit"])
    sentences: list[str] = []
    rows: list[list[str]] = []
    points: list[Any] = []
    for pair in image["register"]:
        label = f"Pair {pair['pair']}"
        fibrils = f"{pair['myofibril_a']} and {pair['myofibril_b']}"
        r = pair["metrics"]
        if r is None:
            sentences.append(_skip_text(label, pair["skip_reason"]))
            rows.append(
                [str(pair["pair"]), fibrils, *[""] * 9, _skip_text(label, pair["skip_reason"])]
            )
            continue
        n_pairs = format_number(r["n_pairs"], "count")
        index = format_number(r["register_index"], "index")
        phase = format_number(_degrees(r["mean_phase"]), "angle")
        spread = format_number(r["register_spread"], "length", unit)
        h = format_number(r["h_mean"], "length", unit)
        h_out = format_number(r["h_unregistered"], "length", unit)
        v_test = f"V = {format_number(r['v_statistic'], 'statistic')}, {format_number(r['v_p_value'], 'p')}"
        rayleigh = (
            f"Z = {format_number(r['rayleigh_z'], 'statistic')}, "
            f"{format_number(r['rayleigh_p'], 'p')}"
        )
        drift_p = _finite(r.get("serial_dependence_p"))
        drift = (
            "not run"
            if drift_p is None
            else f"D = {format_number(r.get('serial_dependence_statistic'), 'statistic')}, "
            f"{format_number(drift_p, 'p')}"
        )
        sentences += [
            f"{label} (myofibrils {fibrils}): {n_pairs} paired Z-lines; register index R {index}, "
            f"mean phase {phase}.",
            f"{label}: spread {spread}; mean |offset| H {h} (out of register: {h_out}); lateral "
            f"distance {format_number(r['lateral_distance'], 'length', unit)}.",
            f"{label}: V-test against perfect register {v_test}; Rayleigh test {rayleigh}.",
            _drift_sentence(label, r),
        ]
        rows.append(
            [
                str(pair["pair"]),
                fibrils,
                n_pairs,
                index,
                phase,
                spread,
                h,
                h_out,
                v_test,
                rayleigh,
                drift,
                "computed",
            ]
        )
        points.append(
            {
                "pair": pair["pair"],
                "phases_deg": [_degrees(v) for v in r.get("phases") or []],
                "correlation_lags": list(r.get("correlation_lags") or []),
                "correlation_values": [_finite(v) for v in r.get("correlation_values") or []],
            }
        )
    return sentences, rows, points


def _table(
    title: str, columns: Sequence[str], rows: list[list[str]], notes: Sequence[str]
) -> dict[str, Any]:
    return {"title": title, "columns": list(columns), "rows": rows, "notes": list(notes)}


def results_view(image: Any) -> dict[str, Any]:
    """Sentences, tables and plot data of one image (the bridge's ``results.get``).

    Parameters
    ----------
    image : ImageResults or mapping
        One image of :func:`sarcostat.analysis.analyze_document`, or one entry
        of ``images`` of its results document.

    Returns
    -------
    dict
        ``{"sentences", "tables", "plot_data"}`` as described in the module
        docstring. JSON-safe.
    """
    if not isinstance(image, Mapping):
        from sarcostat.analysis import AnalysisResults

        image = results_document(AnalysisResults(images=(image,)))["images"][0]
    image = _plain(image)
    settings = image["settings"]
    unit = str(settings["unit"])
    sentences = [_scale_sentence(settings)]
    tables: dict[str, Any] = {}
    plot_data: dict[str, Any] = {"shape": [], "chains": [], "register": []}
    if image["sarcomeres"]:
        sentences.append(_click_sentence(settings))
    if image["chains"]:
        reference = settings.get("reference_direction_deg")
        sentences.append(
            "Reference direction of the V-test: "
            + (
                f"{format_number(reference, 'angle')} (image frame: 0° = right, 90° = down)."
                if _finite(reference) is not None
                else "none set."
            )
        )
    for notice in image["notices"]:
        template = NOTICE_SENTENCES.get(notice["code"])
        if template is not None:
            sentences.append(template.format(**notice["params"]))
    if image["sarcomeres"]:
        lines, rows, points = _shape_lines(image)
        sentences += lines
        notes = SHAPE_NOTES if any(r[5] for r in rows) else SHAPE_NOTES[:2]
        tables["sarcomeres"] = _table("Sarcomeres", SARCOMERE_COLUMNS, rows, notes)
        plot_data["shape"] = points
    if image["shape_summary"] is not None:
        lines, rows = _summary_lines(image["shape_summary"], unit)
        sentences += lines
        tables["shape_summary"] = _table(
            "Sarcomere chain parameters", SUMMARY_COLUMNS, rows, SUMMARY_NOTES
        )
    if image["chains"]:
        lines, rows, points = _chain_lines(image)
        sentences += lines
        tables["chains"] = _table("Myofibril straightness", CHAIN_COLUMNS, rows, CHAIN_NOTES)
        plot_data["chains"] = points
    if image["register"]:
        lines, rows, points = _register_lines(image)
        sentences += lines
        tables["register"] = _table("Z-line register", REGISTER_COLUMNS, rows, REGISTER_NOTES)
        plot_data["register"] = points
    if not (image["sarcomeres"] or image["chains"] or image["register"]):
        sentences.append(EMPTY_IMAGE)
    return {"sentences": sentences, "tables": tables, "plot_data": plot_data}


def results_report(results: Any) -> dict[str, Any]:
    """:func:`results_view` of every image: ``{"images": [{"image_id", "file_name", ...}]}``.

    Parameters
    ----------
    results : AnalysisResults or mapping
        See :func:`results_document`.
    """
    doc = results_document(results)
    return {
        "images": [
            {"image_id": image["image_id"], "file_name": image["file_name"], **results_view(image)}
            for image in doc["images"]
        ]
    }


def report_lines(results: Any) -> list[str]:
    """The sentences of every image, each image headed by its file name and ID.

    Images are separated by an empty line; this is what ``sarcostat analyze``
    prints without ``--json`` or ``--csv``.
    """
    lines: list[str] = []
    for k, image in enumerate(results_report(results)["images"]):
        if k:
            lines.append("")
        lines.append(f"{image['file_name']} (image {image['image_id']})")
        lines += image["sentences"]
    return lines


__all__ = [
    "CHAIN_COLUMNS",
    "CHAIN_HINT",
    "CHAIN_NOTES",
    "EMPTY_IMAGE",
    "FIGURES",
    "NOTICE_SENTENCES",
    "PIXEL_UNITS",
    "REASON_WORDS",
    "REGISTER_COLUMNS",
    "REGISTER_HINT",
    "REGISTER_NOTES",
    "SARCOMERE_COLUMNS",
    "SCALE_SENTENCES",
    "SHAPE_HINT",
    "SHAPE_NOTES",
    "SUMMARY_COLUMNS",
    "SUMMARY_NOTES",
    "UNKNOWN",
    "format_number",
    "report_lines",
    "results_document",
    "results_report",
    "results_view",
]
