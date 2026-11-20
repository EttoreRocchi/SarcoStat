"""One analysis path from an annotation document of format v1.

Every number SarcoStat reports for an annotation file is computed here,
from a validated document (:mod:`sarcostat.io.annotations`), for one image
or for all images of the document. The command line and the web app's
engine call these functions on the same document, so a file gives the same
numbers everywhere. The results are raw numbers and codes (no sentences);
they hold no wall-clock time and no folder, so they are a pure function of
the document.

Rules
-----
Settings (:func:`settings_for_image`)
    The ``gui`` block of a free save sets, for every image, the scale
    (``pixel_size``, ``unit``), the reference direction of the V-test
    (``expected_direction_deg``) and the click error (``click_sigma_z``,
    ``click_sigma_m``, ``click_perp_ratio``). Without it the scale is the
    image's ``scale.nm_per_px`` with unit ``"nm"``, or 1 with unit ``"px"``
    when that is null; there is then no reference direction and no click
    error.
Sarcomere numbers (:func:`sarcomere_ids`)
    Within each image the shape items are numbered 1, 2, 3, ... in the
    order of ``items``. Every shape item counts, also a skipped item and an
    item whose M-line was skipped: they keep their number but have no shape
    indices. Chain and pair items are not numbered. The number is the
    ``sarcomere_id`` of the measured geometry and the seed of its
    class-stability Monte Carlo. Deleting a shape item renumbers the shape
    items after it in the same image; a shape item appended to an image
    gets the number of shape items of that image plus one.
Shape items
    Each sarcomere with all six landmarks is measured (AsR, Cur, SI and
    class); with a click error, also the standard uncertainties of AsR, Cur
    and SI and the class stability. With two or more measured sarcomeres in
    an image, the chain parameters alpha and H are added
    (:class:`SarcomereChainSummary`).
Chain items
    The Z-line midpoints of each chain item, in click order, give one
    linearity analysis (LR, MVL, circular tests, persistence), with the
    V-test against the reference direction when one is set.
Pair items
    The Z-line register comes from pair items only, between
    ``points.myofibrils[0]`` (A) and ``points.myofibrils[1]`` (B). Pairs are
    labelled 1, 2, 3, ... in the order of the image's pair items, and their
    myofibrils 1, 2, 3, ... two per pair item (pair k holds myofibrils
    2k - 1 and 2k). A pair that cannot be computed, for example because no
    Z-line of B lies alongside A, is reported as skipped with a reason code
    and keeps its label, so it never shifts the labels of the other pairs.
    An image with two or more chain items and no pair item gets the notice
    ``register_needs_pair_items`` and no register output.

Reason codes (:data:`SKIP_REASONS`): ``item_skipped`` (the whole item was
skipped), ``m_line_skipped``, ``degenerate_geometry`` (a line of zero
length, or Z-lines that coincide), ``coincident_z_lines`` (two consecutive
Z-lines of a chain share their midpoint) and ``no_overlap`` (no Z-line of
myofibril B lies alongside myofibril A).

Nothing from the GUI, the engine, the command line, pandas or matplotlib is
imported; scipy is loaded only by the functions that need it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from sarcostat.core.chain_statistics import DEFAULT_N_PERMUTATIONS
from sarcostat.core.circular_stats import SequenceMetrics, analyze_segment_sequence
from sarcostat.core.register import RegisterMetrics, z_line_register
from sarcostat.core.sarcomere_morphometry import (
    DEFAULT_SHAPE_TOLERANCE,
    SarcomereChainMetrics,
    SarcomereGeometry,
    ZLineAlignment,
    analyze_sarcomere_chain,
    calculate_sarcomere_geometry,
    calculate_z_staircase_alignment,
)
from sarcostat.core.shape_uncertainty import (
    DEFAULT_N_DRAWS,
    AnnotationError,
    ClassStability,
    ShapeUncertainty,
    class_stability,
    estimate_annotation_sigma,
    shape_uncertainty,
)
from sarcostat.io.annotations import PathInput, read_annotations, validate_annotations

Point = tuple[float, float]
Line = tuple[Point, Point]

SKIP_REASONS = (
    "item_skipped",
    "m_line_skipped",
    "degenerate_geometry",
    "coincident_z_lines",
    "no_overlap",
)
NOTICE_CODES = ("register_needs_pair_items",)
SCALE_SOURCES = ("gui", "image", "none")
N_PERMUTATIONS = DEFAULT_N_PERMUTATIONS
PERMUTATION_SEED = 0
CLASS_STABILITY_DRAWS = DEFAULT_N_DRAWS
SHAPE_TOLERANCE = DEFAULT_SHAPE_TOLERANCE

_NO_OVERLAP_MESSAGE = "No Z-line of myofibril B lies alongside myofibril A"


class AnalysisError(ValueError):
    """A request the analysis cannot answer.

    Attributes
    ----------
    code : str
        ``unknown_image`` (no image with that ``image_id``),
        ``too_few_repeats`` (fewer than two measured sarcomeres to estimate
        a click error from) or ``not_estimable`` (the repeats do not give
        an estimate).
    params : dict
        Values that complete the code, for example the ``image_id``.
    """

    def __init__(self, code: str, message: str, **params: str | int | bool) -> None:
        super().__init__(message)
        self.code = code
        self.params = dict(params)


@dataclass(frozen=True)
class Settings:
    """Settings the metrics of one image depend on.

    Attributes
    ----------
    pixel_size : float
        Length of one pixel, in ``unit``.
    unit : str
        Unit of every length.
    scale_source : str
        Where the scale comes from: ``"gui"`` (the ``gui`` block),
        ``"image"`` (the image's ``scale.nm_per_px``) or ``"none"`` (1 px).
    reference_direction_deg : float or None
        Reference direction of the V-test of chain headings, in degrees in
        the image frame (0 = right, 90 = down); ``None`` skips the V-test.
    click_sigma_z, click_sigma_m : float or None
        Click error along the Z-lines and along the M-line, in ``unit``;
        ``click_sigma_z`` ``None`` means no click error, so no uncertainty
        and no class stability. ``click_sigma_m`` ``None`` means the same
        as ``click_sigma_z``.
    click_perp_ratio : float
        Ratio of the click error across a line to the error along it.
    """

    pixel_size: float
    unit: str
    scale_source: str
    reference_direction_deg: float | None
    click_sigma_z: float | None
    click_sigma_m: float | None
    click_perp_ratio: float

    @property
    def has_click_error(self) -> bool:
        """Whether a click error is set (uncertainty and class stability are reported)."""
        return self.click_sigma_z is not None


@dataclass(frozen=True)
class ShapeMeasurement:
    """One measured sarcomere of an annotation document.

    Attributes
    ----------
    item_id : str
        The item of the document.
    target_id : str or None
        Its target (``None`` outside a study).
    image_id : str
        Its image.
    geometry : SarcomereGeometry
        Lengths, AsR, Cur, SI and shape class; ``geometry.sarcomere_id``
        follows :func:`sarcomere_ids`.
    """

    item_id: str
    target_id: str | None
    image_id: str
    geometry: SarcomereGeometry


@dataclass(frozen=True)
class SarcomereUncertainty:
    """Click-error uncertainty of one measured sarcomere.

    Attributes
    ----------
    sarcomere_id : int
        Number of the sarcomere (:func:`sarcomere_ids`), also the seed of
        the class-stability Monte Carlo.
    uncertainty : ShapeUncertainty
        First-order standard uncertainties of AsR, Cur and SI.
    stability : ClassStability
        Probability that a re-annotation keeps the same shape class.
    """

    sarcomere_id: int
    uncertainty: ShapeUncertainty
    stability: ClassStability


@dataclass(frozen=True)
class SarcomereChainSummary:
    """Chain-level parameters of the measured sarcomeres of one image (two or more).

    Attributes
    ----------
    chain : SarcomereChainMetrics
        Alpha angles between consecutive sarcomere axes (meaningful for
        consecutive sarcomeres of one myofibril) and AsR, Cur and SI
        summaries. Its ``timestamp`` is the empty string: the results hold
        no wall-clock time.
    alignment : ZLineAlignment
        H parameter between the first Z-lines (A-B) of consecutive
        sarcomeres (meaningful for a Z-staircase across adjacent
        myofibrils).
    """

    chain: SarcomereChainMetrics
    alignment: ZLineAlignment


@dataclass(frozen=True)
class ShapeItem:
    """One shape item of an image and its measurement.

    Attributes
    ----------
    item_id : str
        The item of the document.
    target_id : str or None
        Its target (``None`` outside a study).
    sarcomere_id : int
        Its number (:func:`sarcomere_ids`).
    geometry : SarcomereGeometry or None
        The measurement, or ``None`` when the item has no shape indices.
    skip_reason : str or None
        ``None`` when measured; otherwise ``item_skipped``,
        ``m_line_skipped`` or ``degenerate_geometry``.
    """

    item_id: str
    target_id: str | None
    sarcomere_id: int
    geometry: SarcomereGeometry | None
    skip_reason: str | None


@dataclass(frozen=True)
class ChainResult:
    """Linearity analysis of one chain item.

    Attributes
    ----------
    item_id : str
        The item of the document.
    target_id : str or None
        Its target (``None`` outside a study).
    chain : int
        Label of the chain: 1, 2, 3, ... in the order of the image's chain
        items (skipped items included).
    n_z_lines : int
        Number of Z-lines (0 for a skipped item).
    branch_end : bool
        The chain ends before a branch point of the myofibril.
    metrics : SequenceMetrics or None
        The analysis of the Z-line midpoints, or ``None`` when skipped. It
        carries no timestamp.
    skip_reason : str or None
        ``None`` when analysed; otherwise ``item_skipped`` or
        ``coincident_z_lines``.
    """

    item_id: str
    target_id: str | None
    chain: int
    n_z_lines: int
    branch_end: bool
    metrics: SequenceMetrics | None
    skip_reason: str | None


@dataclass(frozen=True)
class PairResult:
    """Z-line register of one pair item, or why it was skipped.

    Attributes
    ----------
    item_id : str
        The item of the document.
    target_id : str or None
        Its target (``None`` outside a study).
    pair : int
        Label of the pair: 1, 2, 3, ... in the order of the image's pair
        items, skipped pairs included.
    myofibril_a, myofibril_b : int
        Numbers of its two myofibrils, ``2 * pair - 1`` (A,
        ``points.myofibrils[0]``) and ``2 * pair`` (B).
    n_z_lines_a, n_z_lines_b : int
        Number of Z-lines of A and B (0 for a skipped item).
    metrics : RegisterMetrics or None
        Register of B relative to A, or ``None`` when skipped.
    skip_reason : str or None
        ``None`` when computed; otherwise ``item_skipped``, ``no_overlap``
        or ``degenerate_geometry``.
    """

    item_id: str
    target_id: str | None
    pair: int
    myofibril_a: int
    myofibril_b: int
    n_z_lines_a: int
    n_z_lines_b: int
    metrics: RegisterMetrics | None
    skip_reason: str | None


@dataclass(frozen=True)
class Notice:
    """A remark about the results of an image, as a code with parameters.

    Attributes
    ----------
    code : str
        One of :data:`NOTICE_CODES`. ``register_needs_pair_items``: the
        image has two or more chain items and no pair item, so no register
        is computed (parameter ``n_chain_items``).
    params : dict
        Values that complete the code (strings, integers, booleans).
    """

    code: str
    params: dict[str, str | int | bool]


@dataclass(frozen=True)
class ImageResults:
    """Everything measured on one image of a document.

    Attributes
    ----------
    image_id, file_name, sha256 : str
        The image as recorded in the document (a file name, never a path).
    width_px, height_px : int
        Its size.
    settings : Settings
        The settings the metrics were computed with.
    shapes : tuple of ShapeItem
        Every shape item of the image, in item order.
    uncertainties : tuple of SarcomereUncertainty or None
        One per measured sarcomere, in item order; ``None`` without a
        click error.
    shape_summary : SarcomereChainSummary or None
        Alpha and H, with two or more measured sarcomeres.
    chains : tuple of ChainResult
        Every chain item of the image, in item order.
    register : tuple of PairResult
        Every pair item of the image, in item order.
    notices : tuple of Notice
        Remarks, in a fixed order.
    """

    image_id: str
    file_name: str
    sha256: str
    width_px: int
    height_px: int
    settings: Settings
    shapes: tuple[ShapeItem, ...]
    uncertainties: tuple[SarcomereUncertainty, ...] | None
    shape_summary: SarcomereChainSummary | None
    chains: tuple[ChainResult, ...]
    register: tuple[PairResult, ...]
    notices: tuple[Notice, ...]

    @property
    def geometries(self) -> tuple[SarcomereGeometry, ...]:
        """The measured sarcomeres, in item order."""
        return tuple(s.geometry for s in self.shapes if s.geometry is not None)


@dataclass(frozen=True)
class AnalysisResults:
    """The results of a document.

    Attributes
    ----------
    images : tuple of ImageResults
        One per analysed image, in the order of the document's ``images``.
    """

    images: tuple[ImageResults, ...]


@dataclass(frozen=True)
class ClickErrorEstimate:
    """Click error estimated from repeated annotations of one sarcomere.

    Attributes
    ----------
    estimate : AnnotationError
        Pooled standard deviations along and across the Z-lines and the
        M-line, the number of repeats and the unit.
    click_sigma_z, click_sigma_m, click_perp_ratio : float
        The settings that follow from it: the errors along the Z-lines and
        along the M-line, and the ratio across/along of the Z-lines (1 when
        the error along them is 0).
    """

    estimate: AnnotationError
    click_sigma_z: float
    click_sigma_m: float
    click_perp_ratio: float


def _point(value: Any) -> Point:
    return (float(value[0]), float(value[1]))


def _line(value: Any) -> Line:
    return (_point(value[0]), _point(value[1]))


def _image_entry(doc: dict[str, Any], image_id: str) -> dict[str, Any]:
    for image in doc["images"]:
        if image["image_id"] == image_id:
            entry: dict[str, Any] = image
            return entry
    raise AnalysisError(
        "unknown_image", f"the document has no image {image_id!r}", image_id=image_id
    )


def _settings(doc: dict[str, Any], image: dict[str, Any]) -> Settings:
    gui = doc.get("gui")
    if gui is not None:
        direction = gui["expected_direction_deg"]
        sigma_z, sigma_m = gui["click_sigma_z"], gui["click_sigma_m"]
        return Settings(
            pixel_size=float(gui["pixel_size"]),
            unit=str(gui["unit"]),
            scale_source="gui",
            reference_direction_deg=None if direction is None else float(direction),
            click_sigma_z=None if sigma_z is None else float(sigma_z),
            click_sigma_m=None if sigma_m is None else float(sigma_m),
            click_perp_ratio=float(gui["click_perp_ratio"]),
        )
    nm_per_px = image["scale"]["nm_per_px"]
    if nm_per_px is None:
        pixel_size, unit, source = 1.0, "px", "none"
    else:
        pixel_size, unit, source = float(nm_per_px), "nm", "image"
    return Settings(pixel_size, unit, source, None, None, None, 1.0)


def settings_for_image(doc: dict[str, Any], image_id: str) -> Settings:
    """Settings of image ``image_id`` of a validated document (see the module rules).

    Raises
    ------
    AnalysisError
        ``unknown_image`` if the document has no such image.
    """
    return _settings(doc, _image_entry(doc, image_id))


def sarcomere_ids(doc: dict[str, Any]) -> dict[str, int]:
    """Number every shape item of a document: ``{item_id: sarcomere_id}``.

    Within each image the shape items are numbered 1, 2, 3, ... in the
    order of ``items``; skipped items and items whose M-line was skipped
    count too (they keep a number but have no shape indices). Chain and
    pair items are left out. The number is the ``sarcomere_id`` of the
    measured geometry and the seed of its class stability, so both depend
    only on the item's position among the shape items of its image.

    Parameters
    ----------
    doc : dict
        A validated annotation document.

    Returns
    -------
    dict
        ``item_id`` to ``sarcomere_id``, in item order.
    """
    counts: dict[str, int] = {}
    ids: dict[str, int] = {}
    for item in doc["items"]:
        if item["type"] != "shape":
            continue
        image_id = item["image_id"]
        counts[image_id] = counts.get(image_id, 0) + 1
        ids[item["item_id"]] = counts[image_id]
    return ids


def _measure(
    item: dict[str, Any], sarcomere_id: int, settings: Settings
) -> tuple[SarcomereGeometry | None, str | None]:
    if item["skipped"]:
        return None, "item_skipped"
    points = item["points"]
    if "m" not in points:
        return None, "m_line_skipped"
    (a, b), (c, d), (e, f) = (_line(points[key]) for key in ("z1", "m", "z2"))
    try:
        geometry = calculate_sarcomere_geometry(
            a,
            b,
            c,
            d,
            e,
            f,
            sarcomere_id=sarcomere_id,
            pixel_to_unit=settings.pixel_size,
            unit=settings.unit,
        )
    except ValueError:
        return None, "degenerate_geometry"
    return geometry, None


def _shape_items(
    items: list[dict[str, Any]], settings: Settings, ids: dict[str, int]
) -> tuple[ShapeItem, ...]:
    out = []
    for item in items:
        if item["type"] == "shape":
            sarcomere_id = ids[item["item_id"]]
            geometry, reason = _measure(item, sarcomere_id, settings)
            out.append(
                ShapeItem(item["item_id"], item["target_id"], sarcomere_id, geometry, reason)
            )
    return tuple(out)


def _uncertainties(
    geometries: tuple[SarcomereGeometry, ...], settings: Settings
) -> tuple[SarcomereUncertainty, ...] | None:
    sigma_z = settings.click_sigma_z
    if sigma_z is None:
        return None
    sigma_m, ratio = settings.click_sigma_m, settings.click_perp_ratio
    return tuple(
        SarcomereUncertainty(
            sarcomere_id=g.sarcomere_id,
            uncertainty=shape_uncertainty(g, sigma_z, sigma_m, ratio),
            stability=class_stability(
                g,
                sigma_z,
                sigma_m,
                ratio,
                n_draws=CLASS_STABILITY_DRAWS,
                tolerance=SHAPE_TOLERANCE,
                seed=g.sarcomere_id,
            ),
        )
        for g in geometries
    )


def _shape_summary(
    geometries: tuple[SarcomereGeometry, ...], settings: Settings
) -> SarcomereChainSummary | None:
    if len(geometries) < 2:
        return None
    alignment = calculate_z_staircase_alignment(
        [g.z_line_1_points for g in geometries],
        pixel_to_unit=settings.pixel_size,
        unit=settings.unit,
    )
    chain = replace(analyze_sarcomere_chain(list(geometries)), timestamp="")
    return SarcomereChainSummary(chain=chain, alignment=alignment)


def _chain_result(item: dict[str, Any], label: int, settings: Settings) -> ChainResult:
    item_id, target_id = item["item_id"], item["target_id"]
    branch_end = bool(item.get("branch_end", False))
    if item["skipped"]:
        return ChainResult(item_id, target_id, label, 0, branch_end, None, "item_skipped")
    lines = [_line(z) for z in item["points"]["z_lines"]]
    midpoints = [((p[0] + q[0]) / 2.0, (p[1] + q[1]) / 2.0) for p, q in lines]
    direction = settings.reference_direction_deg
    try:
        metrics = analyze_segment_sequence(
            np.array(midpoints, dtype=float),
            pixel_to_unit=settings.pixel_size,
            unit_name=settings.unit,
            expected_direction=None if direction is None else math.radians(direction),
            n_permutations=N_PERMUTATIONS,
            seed=PERMUTATION_SEED,
        )
    except ValueError:
        return ChainResult(
            item_id, target_id, label, len(lines), branch_end, None, "coincident_z_lines"
        )
    return ChainResult(item_id, target_id, label, len(lines), branch_end, metrics, None)


def _pair_result(item: dict[str, Any], label: int, settings: Settings) -> PairResult:
    item_id, target_id = item["item_id"], item["target_id"]
    a, b = 2 * label - 1, 2 * label
    if item["skipped"]:
        return PairResult(item_id, target_id, label, a, b, 0, 0, None, "item_skipped")
    zlines_a, zlines_b = ([_line(z) for z in fibril] for fibril in item["points"]["myofibrils"])
    n_a, n_b = len(zlines_a), len(zlines_b)
    try:
        metrics = z_line_register(
            zlines_a,
            zlines_b,
            pixel_to_unit=settings.pixel_size,
            unit=settings.unit,
            n_permutations=N_PERMUTATIONS,
            seed=PERMUTATION_SEED,
        )
    except ValueError as exc:
        reason = "no_overlap" if str(exc).startswith(_NO_OVERLAP_MESSAGE) else "degenerate_geometry"
        return PairResult(item_id, target_id, label, a, b, n_a, n_b, None, reason)
    return PairResult(item_id, target_id, label, a, b, n_a, n_b, metrics, None)


def _analyze_image(doc: dict[str, Any], image: dict[str, Any], ids: dict[str, int]) -> ImageResults:
    settings = _settings(doc, image)
    items = [item for item in doc["items"] if item["image_id"] == image["image_id"]]
    shapes = _shape_items(items, settings, ids)
    geometries = tuple(s.geometry for s in shapes if s.geometry is not None)
    chain_items = [item for item in items if item["type"] == "chain"]
    pair_items = [item for item in items if item["type"] == "pair"]
    notices: tuple[Notice, ...] = ()
    if len(chain_items) >= 2 and not pair_items:
        notices = (Notice("register_needs_pair_items", {"n_chain_items": len(chain_items)}),)
    return ImageResults(
        image_id=image["image_id"],
        file_name=image["file_name"],
        sha256=image["sha256"],
        width_px=int(image["width_px"]),
        height_px=int(image["height_px"]),
        settings=settings,
        shapes=tuple(shapes),
        uncertainties=_uncertainties(geometries, settings),
        shape_summary=_shape_summary(geometries, settings),
        chains=tuple(
            _chain_result(item, k, settings) for k, item in enumerate(chain_items, start=1)
        ),
        register=tuple(
            _pair_result(item, k, settings) for k, item in enumerate(pair_items, start=1)
        ),
        notices=notices,
    )


def analyze_document(doc: dict[str, Any], image_id: str | None = None) -> AnalysisResults:
    """Analyse an annotation document, for one image or for all of them.

    Parameters
    ----------
    doc : dict
        An annotation document of format 1.x. It is validated first and
        not modified.
    image_id : str, optional
        The image to analyse. Default: every image, in document order.

    Returns
    -------
    AnalysisResults

    Raises
    ------
    AnnotationFormatError
        If ``doc`` is not a valid document.
    AnalysisError
        ``unknown_image`` if ``image_id`` is not in the document.
    """
    validate_annotations(doc)
    images = doc["images"] if image_id is None else [_image_entry(doc, image_id)]
    ids = sarcomere_ids(doc)
    return AnalysisResults(images=tuple(_analyze_image(doc, image, ids) for image in images))


def analyze_file(path: PathInput, image_dir: PathInput | None = None) -> AnalysisResults:
    """Read an annotation file and analyse all of its images.

    Parameters
    ----------
    path : str or os.PathLike
        The annotation file.
    image_dir : str or os.PathLike, optional
        Folder of the images, to check their SHA-256 as in
        :func:`~sarcostat.io.annotations.read_annotations`.

    Returns
    -------
    AnalysisResults
    """
    return analyze_document(read_annotations(path, image_dir=image_dir))


def shape_geometries(doc: dict[str, Any]) -> list[ShapeMeasurement]:
    """Measure the shape items of a validated annotation document.

    Skipped items, sarcomeres whose M-line was skipped and sarcomeres with
    a line of zero length have no shape indices and are left out; the
    others are measured with the settings of their image and numbered by
    :func:`sarcomere_ids`.

    Parameters
    ----------
    doc : dict
        A document returned by :func:`~sarcostat.io.annotations.read_annotations`
        or checked with :func:`~sarcostat.io.annotations.validate_annotations`.

    Returns
    -------
    list of ShapeMeasurement
        In item order across the document.
    """
    images = {image["image_id"]: image for image in doc["images"]}
    settings = {image_id: _settings(doc, image) for image_id, image in images.items()}
    ids = sarcomere_ids(doc)
    out = []
    for item in doc["items"]:
        if item["type"] != "shape":
            continue
        image_id = item["image_id"]
        geometry, _reason = _measure(item, ids[item["item_id"]], settings[image_id])
        if geometry is not None:
            out.append(ShapeMeasurement(item["item_id"], item["target_id"], image_id, geometry))
    return out


def load_shape_geometries(
    path: PathInput, image_dir: PathInput | None = None
) -> list[ShapeMeasurement]:
    """Read an annotation file and measure its shape items (:func:`shape_geometries`).

    Parameters
    ----------
    path : str or os.PathLike
        The annotation file.
    image_dir : str or os.PathLike, optional
        Folder of the images, to check their SHA-256 as in
        :func:`~sarcostat.io.annotations.read_annotations`.

    Returns
    -------
    list of ShapeMeasurement
    """
    return shape_geometries(read_annotations(path, image_dir=image_dir))


def estimate_click_error(doc: dict[str, Any], image_id: str) -> ClickErrorEstimate:
    """Estimate the click error from the measured sarcomeres of one image, taken as repeats.

    The annotator measures the same sarcomere several times (three or
    more recommended); the scatter of each landmark gives the click error
    along and across the Z-lines and the M-line
    (:func:`~sarcostat.core.shape_uncertainty.estimate_annotation_sigma`).

    Parameters
    ----------
    doc : dict
        An annotation document of format 1.x (validated first).
    image_id : str
        The image whose measured sarcomeres are the repeats.

    Returns
    -------
    ClickErrorEstimate

    Raises
    ------
    AnalysisError
        ``unknown_image``; ``too_few_repeats`` with fewer than two measured
        sarcomeres; ``not_estimable`` if the repeats give no estimate.
    """
    validate_annotations(doc)
    image = _image_entry(doc, image_id)
    items = [item for item in doc["items"] if item["image_id"] == image_id]
    shapes = _shape_items(items, _settings(doc, image), sarcomere_ids(doc))
    geometries = [s.geometry for s in shapes if s.geometry is not None]
    if len(geometries) < 2:
        raise AnalysisError(
            "too_few_repeats",
            "annotate the same sarcomere at least twice (three or more recommended)",
            n_repeats=len(geometries),
        )
    try:
        est = estimate_annotation_sigma(geometries)
    except ValueError as exc:
        raise AnalysisError("not_estimable", str(exc)) from None
    ratio = est.sigma_z_perp / est.sigma_z_along if est.sigma_z_along > 0 else 1.0
    return ClickErrorEstimate(
        estimate=est,
        click_sigma_z=est.sigma_z_along,
        click_sigma_m=est.sigma_m_along,
        click_perp_ratio=ratio,
    )


__all__ = [
    "CLASS_STABILITY_DRAWS",
    "NOTICE_CODES",
    "N_PERMUTATIONS",
    "PERMUTATION_SEED",
    "SCALE_SOURCES",
    "SHAPE_TOLERANCE",
    "SKIP_REASONS",
    "AnalysisError",
    "AnalysisResults",
    "ChainResult",
    "ClickErrorEstimate",
    "ImageResults",
    "Notice",
    "PairResult",
    "SarcomereChainSummary",
    "SarcomereUncertainty",
    "Settings",
    "ShapeItem",
    "ShapeMeasurement",
    "analyze_document",
    "analyze_file",
    "estimate_click_error",
    "load_shape_geometries",
    "sarcomere_ids",
    "settings_for_image",
    "shape_geometries",
]
