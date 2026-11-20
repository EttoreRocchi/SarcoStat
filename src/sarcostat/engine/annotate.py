"""The annotation session of the web app and its shape flow.

An :class:`AnnotationSession` holds one image, the settings the metrics
depend on (scale, reference direction, click error) and the format v1
items of that image in document order: the shape items it creates and
every item of a file it opened, chain and pair items included, which are
kept unchanged and written back. :class:`ShapeFlow` holds the clicks of
the sarcomere being annotated, landmarks A to F, with the M-line skip of
study mode.

Each command method matches one bridge command (``doc.new`` is
:meth:`AnnotationSession.doc_new`, ``shape.click`` is
:meth:`AnnotationSession.shape_click`, and so on) and returns the
:class:`ModeState` the page draws. A refused action leaves the session as
it was and reports a ``notice``; a command that cannot run at all raises
:class:`~sarcostat.engine.bridge.Refused`, and opening a file made on
another image raises :class:`~sarcostat.engine.bridge.NeedsConfirmation`
until the user confirms.

Numbers in a :class:`ModeState` are image coordinates (pixel centres, the
pixel ``i`` spanning ``[i - 0.5, i + 0.5)``), counts and indexes, plus the
settings the user entered (``settings``); never a metric. The metrics come
from :mod:`sarcostat.analysis`, run on the session's document
(:meth:`AnnotationSession.analysis`), so the session and
``sarcostat analyze`` give the same numbers, sarcomere numbers and
class-stability seeds included.

Notices and prompts are code objects ``{"code", "params"}``;
:func:`describe` gives their English sentence (:data:`NOTICE_TEXT`,
:data:`STEP_TEXT`).
"""

from __future__ import annotations

import copy
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Any, TypedDict

from sarcostat.analysis import (
    AnalysisError,
    AnalysisResults,
    ImageResults,
    analyze_document,
    estimate_click_error,
    sarcomere_ids,
)
from sarcostat.core.sarcomere_morphometry import calculate_sarcomere_geometry
from sarcostat.engine.bridge import NeedsConfirmation, Refused
from sarcostat.engine.document import (
    DEFAULT_UNIT,
    ImageInfo,
    SessionSettings,
    build_document,
    check_image,
    dumps_document,
    export_file_name,
    image_id_for,
    read_document,
    shape_item,
)
from sarcostat.io.annotations import AnnotationFormatError

Point = tuple[float, float]
Params = dict[str, str | int | bool]

MODES = ("shape", "home")
LANDMARKS = ("A", "B", "C", "D", "E", "F")
M_SKIPPED_LANDMARKS = ("A", "B", "E", "F")
MIN_LINE_PX = 1.0
LANDMARK_NAMES = {
    "A": "A (Z-line 1 start)",
    "B": "B (Z-line 1 end)",
    "C": "C (M-line start)",
    "D": "D (M-line end)",
    "E": "E (Z-line 2 start)",
    "F": "F (Z-line 2 end)",
}
LINE_NAMES = {"z1": "Z-line 1 (A-B)", "m": "M-line (C-D)", "z2": "Z-line 2 (E-F)"}
ABSENT_MODES = {"chain": "straightness", "pair": "register"}

STEP_TEXT = {
    "shape_click": "Sarcomere {sarcomere}: click {landmark_name} ({number} of {of}).",
    "shape_ready": "Sarcomere {sarcomere}: all points placed; press Confirm to save it, or Undo.",
}
NOTICE_TEXT = {
    "click_outside_image": "Click inside the image.",
    "shape_complete": "All points of this sarcomere are placed: confirm it, or undo.",
    "shape_incomplete": "Place all {needed} points of the sarcomere first ({placed} placed).",
    "line_too_short": (
        "The ends of {line_name} are less than 1 pixel apart; undo and click them again."
    ),
    "shape_degenerate": (
        "This sarcomere cannot be measured: its Z-lines have the same midpoint; undo and "
        "click again."
    ),
    "m_line_already_skipped": "The M-line is already marked as not identifiable.",
    "m_line_started": "The M-line is already started: undo its points first.",
    "nothing_to_undo": "Nothing to undo.",
    "item_not_found": "There is no item {item_id}.",
    "item_not_shape": "Item {item_id} is a {type} item: it is kept as it is in this mode.",
    "pixel_size_invalid": "Pixel size must be a positive number.",
    "reference_direction_invalid": "The reference direction must be a finite number of degrees.",
    "click_error_invalid": "Click error must be a non-negative number.",
    "click_error_too_few_repeats": (
        "To estimate the click error, annotate the same sarcomere at least twice (three or "
        "more recommended); {n_repeats} measured."
    ),
    "click_error_not_estimable": "These repeats do not give a click error estimate.",
    "click_error_estimated": "Click error estimated from {n_repeats} repeats.",
    "document_loaded": (
        "Loaded {shape_items} sarcomere item(s), {chain_items} chain item(s) and "
        "{pair_items} pair item(s)."
    ),
}
STEP_CODES = tuple(STEP_TEXT)
NOTICE_CODES = tuple(NOTICE_TEXT)


class CodeObject(TypedDict):
    """A structured notice or prompt."""

    code: str
    params: Params


class MarkJSON(TypedDict):
    """A point to draw, in image coordinates, with its role (landmark or myofibril)."""

    x: float
    y: float
    role: str


class StrokeJSON(TypedDict):
    """A line to draw, in image coordinates, with its role (line kind or myofibril)."""

    p1: list[float]
    p2: list[float]
    role: str


class PendingJSON(TypedDict):
    """The clicks of the sarcomere being annotated."""

    marks: list[MarkJSON]
    strokes: list[StrokeJSON]


class ItemJSON(TypedDict):
    """One item of the image, as drawn."""

    item_id: str
    type: str
    sarcomere_id: int | None
    marks: list[MarkJSON]
    strokes: list[StrokeJSON]


class ControlsJSON(TypedDict):
    """Which actions the page offers."""

    can_confirm: bool
    can_undo: bool
    can_skip_m_line: bool
    can_delete: bool


class SettingsJSON(TypedDict):
    """The settings the user entered (inputs of the metrics, not metrics)."""

    pixel_size: float
    unit: str
    reference_direction_deg: float | None
    click_sigma_z: float | None
    click_sigma_m: float | None
    click_perp_ratio: float


class ModeState(TypedDict):
    """Everything the page draws after an annotation command (bridge result)."""

    mode: str
    image: dict[str, Any] | None
    settings: SettingsJSON
    pending: PendingJSON
    items: list[ItemJSON]
    controls: ControlsJSON
    step: CodeObject | None
    notice: CodeObject | None
    not_exported: bool


def settings_json(settings: SessionSettings) -> SettingsJSON:
    """The settings as the page shows them in its fields (``click_sigma_*`` null when off)."""
    direction = settings.reference_direction_deg
    sigma_z, sigma_m = settings.click_sigma_z, settings.click_sigma_m
    return {
        "pixel_size": float(settings.pixel_size),
        "unit": settings.unit,
        "reference_direction_deg": None if direction is None else float(direction),
        "click_sigma_z": None if sigma_z is None else float(sigma_z),
        "click_sigma_m": None if sigma_m is None else float(sigma_m),
        "click_perp_ratio": float(settings.click_perp_ratio),
    }


def code(name: str, **params: str | int | bool) -> CodeObject:
    """A code object ``{"code": name, "params": params}``."""
    return {"code": name, "params": dict(params)}


def describe(obj: CodeObject) -> str:
    """The English sentence of a notice or prompt, on one line."""
    name, params = obj["code"], obj["params"]
    values: dict[str, Any] = dict(params)
    if "landmark" in params:
        values["landmark_name"] = LANDMARK_NAMES[str(params["landmark"])]
    if "line" in params:
        values["line_name"] = LINE_NAMES[str(params["line"])]
    template = STEP_TEXT.get(name) or NOTICE_TEXT[name]
    text = template.format(**values)
    if name == "shape_click" and params.get("can_skip_m_line"):
        text += " If the M-line is not identifiable, press Skip M-line."
    if name == "document_loaded":
        if params.get("incomplete_items"):
            text += (
                f" {params['incomplete_items']} item(s) are skipped or lack the M-line"
                " and give no shape indices."
            )
        if params.get("missing_modes"):
            modes = str(params["missing_modes"]).replace(",", " and ")
            text += (
                f" Its chain and pair items need the {modes} mode, which this build does not"
                " have: they are kept and written back on export."
            )
        if not params.get("image_checked", True):
            text += " The image was not checked: open it to compare its SHA-256 with the file."
    return text


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _mark(point: Any, role: str) -> MarkJSON:
    return {"x": float(point[0]), "y": float(point[1]), "role": role}


def _stroke(p1: Any, p2: Any, role: str) -> StrokeJSON:
    return {
        "p1": [float(p1[0]), float(p1[1])],
        "p2": [float(p2[0]), float(p2[1])],
        "role": role,
    }


_SHAPE_LINES = (("z1", "A", "B"), ("m", "C", "D"), ("z2", "E", "F"))
_LINE_OF_FIRST = {"A": "z1", "C": "m", "E": "z2"}


@dataclass
class ShapeFlow:
    """The clicks of the sarcomere being annotated (landmarks A to F).

    The landmarks are clicked in the order A, B (Z-line 1), C, D (M-line),
    E, F (Z-line 2). Before the M-line is started (two clicks or fewer) it
    can be marked as not identifiable; the sarcomere then takes A, B, E, F
    and is written with ``skipped_parts = ["m_line"]``. Undo removes the
    last step, a click or the M-line skip.
    """

    actions: list[tuple[str, float, float]] = field(default_factory=list)

    @property
    def clicks(self) -> list[Point]:
        """The clicked points, in click order."""
        return [(x, y) for kind, x, y in self.actions if kind == "click"]

    @property
    def m_skipped(self) -> bool:
        """Whether the M-line is marked as not identifiable."""
        return any(kind == "m_skip" for kind, _x, _y in self.actions)

    @property
    def landmarks(self) -> tuple[str, ...]:
        """The landmarks this sarcomere takes, in click order."""
        return M_SKIPPED_LANDMARKS if self.m_skipped else LANDMARKS

    @property
    def complete(self) -> bool:
        """Whether every landmark is placed."""
        return len(self.clicks) == len(self.landmarks)

    @property
    def can_skip_m_line(self) -> bool:
        """Whether the M-line can still be marked as not identifiable."""
        return not self.m_skipped and len(self.clicks) <= 2

    def click(self, x: float, y: float) -> CodeObject | None:
        """Place the next landmark; a notice when every landmark is placed."""
        if self.complete:
            return code("shape_complete")
        self.actions.append(("click", float(x), float(y)))
        return None

    def skip_m_line(self) -> CodeObject | None:
        """Mark the M-line as not identifiable; a notice when that is not possible."""
        if self.m_skipped:
            return code("m_line_already_skipped")
        if len(self.clicks) > 2:
            return code("m_line_started")
        self.actions.append(("m_skip", 0.0, 0.0))
        return None

    def undo(self) -> bool:
        """Remove the last step; ``False`` when there is none."""
        if not self.actions:
            return False
        self.actions.pop()
        return True

    def clear(self) -> None:
        """Drop every pending step."""
        self.actions.clear()

    def lines(self) -> dict[str, list[list[float]]]:
        """The lines of a complete sarcomere: ``z1``, ``z2`` and, unless skipped, ``m``."""
        clicks = self.clicks
        out: dict[str, list[list[float]]] = {}
        for k in range(0, len(clicks), 2):
            role = _LINE_OF_FIRST[self.landmarks[k]]
            out[role] = [list(clicks[k]), list(clicks[k + 1])]
        return out

    def problem(self) -> CodeObject | None:
        """Why the pending sarcomere cannot be confirmed, or ``None``."""
        clicks = self.clicks
        needed = len(self.landmarks)
        if len(clicks) != needed:
            return code("shape_incomplete", placed=len(clicks), needed=needed)
        lines = self.lines()
        for role in ("z1", "m", "z2"):
            if role in lines:
                (x1, y1), (x2, y2) = lines[role]
                if math.hypot(x2 - x1, y2 - y1) < MIN_LINE_PX:
                    return code("line_too_short", line=role)
        if not self.m_skipped:
            a, b, c, d, e, f = clicks
            try:
                calculate_sarcomere_geometry(a, b, c, d, e, f)
            except ValueError:
                return code("shape_degenerate")
        return None

    def marks(self) -> list[MarkJSON]:
        """The pending clicks with their landmark roles."""
        return [_mark(p, role) for p, role in zip(self.clicks, self.landmarks, strict=False)]

    def strokes(self) -> list[StrokeJSON]:
        """The pending lines whose two ends are placed."""
        clicks = self.clicks
        return [
            _stroke(clicks[k], clicks[k + 1], _LINE_OF_FIRST[self.landmarks[k]])
            for k in range(0, len(clicks) - 1, 2)
        ]

    def step(self, sarcomere: int) -> CodeObject:
        """The prompt for the next click of sarcomere number ``sarcomere``."""
        n = len(self.clicks)
        seq = self.landmarks
        if n >= len(seq):
            return code("shape_ready", sarcomere=sarcomere)
        return code(
            "shape_click",
            sarcomere=sarcomere,
            landmark=seq[n],
            number=n + 1,
            of=len(seq),
            can_skip_m_line=self.can_skip_m_line,
        )


def _system_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _image_info(value: Mapping[str, Any]) -> ImageInfo:
    try:
        info = ImageInfo.from_json(value)
    except (KeyError, TypeError, ValueError) as exc:
        raise Refused(f"The image description is incomplete: {exc}") from None
    check_image(info)
    return info


def _item_view(item: Mapping[str, Any], ids: Mapping[str, int]) -> ItemJSON:
    kind = str(item["type"])
    points = item.get("points") or {}
    marks: list[MarkJSON] = []
    strokes: list[StrokeJSON] = []
    if not item["skipped"] and points:
        if kind == "shape":
            for role, first, second in _SHAPE_LINES:
                if role in points:
                    p, q = points[role]
                    marks += [_mark(p, first), _mark(q, second)]
                    strokes.append(_stroke(p, q, role))
        else:
            fibrils = [points["z_lines"]] if kind == "chain" else points["myofibrils"]
            for k, fibril in enumerate(fibrils, start=1):
                role = "chain" if kind == "chain" else f"myofibril_{k}"
                for p, q in fibril:
                    marks += [_mark(p, role), _mark(q, role)]
                    strokes.append(_stroke(p, q, role))
    return {
        "item_id": str(item["item_id"]),
        "type": kind,
        "sarcomere_id": ids.get(str(item["item_id"])) if kind == "shape" else None,
        "marks": marks,
        "strokes": strokes,
    }


def _no_controls() -> ControlsJSON:
    return {
        "can_confirm": False,
        "can_undo": False,
        "can_skip_m_line": False,
        "can_delete": False,
    }


class AnnotationSession:
    """One image, its settings, its items and the shape flow.

    Parameters
    ----------
    utc_now : callable, optional
        Source of the wall-clock time written as ``created_utc`` on export;
        default: the system clock.

    Notes
    -----
    The settings outlive the image: a new image keeps the scale, the
    reference direction and the click error, as in the desktop window.
    Opening an annotation file takes the file's settings
    (:func:`sarcostat.analysis.settings_for_image`).
    """

    def __init__(self, utc_now: Callable[[], datetime] | None = None) -> None:
        self._utc_now = utc_now or _system_utc_now
        self._settings = SessionSettings()
        self._flow = ShapeFlow()
        self._image: ImageInfo | None = None
        self._image_id: str | None = None
        self._items: list[dict[str, Any]] = []
        self._mode = "home"
        self._last_mode_shape = False
        self._open_myofibril = False
        self._base: dict[str, Any] | None = None
        self._base_text: str | None = None
        self._base_content: tuple[Any, ...] | None = None
        self._base_scale: tuple[float, str] | None = None
        self._exported_items: list[dict[str, Any]] | None = None
        self._notice: CodeObject | None = None

    @property
    def image(self) -> ImageInfo | None:
        """The annotated image, or ``None``."""
        return self._image

    @property
    def image_id(self) -> str | None:
        """The ``image_id`` of the image in the session's document."""
        return self._image_id

    @property
    def settings(self) -> SessionSettings:
        """Scale, reference direction and click error."""
        return self._settings

    @property
    def items(self) -> list[dict[str, Any]]:
        """Copies of the format v1 items of the image, in document order."""
        return copy.deepcopy(self._items)

    @property
    def mode(self) -> str:
        """``"shape"`` or ``"home"``."""
        return self._mode

    @property
    def flow(self) -> ShapeFlow:
        """The shape flow (pending clicks)."""
        return self._flow

    @property
    def notice(self) -> CodeObject | None:
        """The notice of the last command, or ``None``."""
        return None if self._notice is None else copy.deepcopy(self._notice)

    @property
    def not_exported(self) -> bool:
        """Whether items exist that differ from the last export or opened file.

        Pending clicks and settings do not count, as in the desktop window.
        """
        return bool(self._items) and self._items != self._exported_items

    def _require_image(self) -> ImageInfo:
        if self._image is None:
            raise Refused("Open an image first.")
        return self._image

    def _enter_shape(self) -> ImageInfo:
        image = self._require_image()
        self._mode = "shape"
        self._last_mode_shape = True
        return image

    def _done(self, notice: CodeObject | None = None) -> ModeState:
        self._notice = notice
        return self.state()

    def _content(self) -> tuple[Any, ...]:
        return (self._image, self._settings, copy.deepcopy(self._items), self._open_myofibril)

    def _stamp(self) -> str:
        now = self._utc_now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _shape_items(self) -> list[dict[str, Any]]:
        return [item for item in self._items if item["type"] == "shape"]

    def _new_item_id(self) -> str:
        taken = {str(item["item_id"]) for item in self._items}
        numbers = [
            int(ident.split("-", 1)[1])
            for ident in taken
            if ident.startswith("sarcomere-") and ident.split("-", 1)[1].isdigit()
        ]
        n = max(numbers, default=0) + 1
        while f"sarcomere-{n}" in taken:
            n += 1
        return f"sarcomere-{n}"

    def document(self) -> dict[str, Any]:
        """The format v1 document that an export would write now (a copy).

        The file that was opened, or last exported, is returned unchanged
        while its items, settings and image are unchanged.
        """
        image = self._require_image()
        assert self._image_id is not None
        if self._base is not None and self._content() == self._base_content:
            return copy.deepcopy(self._base)
        s = self._settings
        base_scale = None
        if self._base is not None and self._base_scale == (s.pixel_size, s.unit):
            for entry in self._base["images"]:
                if entry["image_id"] == self._image_id:
                    base_scale = entry["scale"]
        return build_document(
            image=image,
            image_id=self._image_id,
            settings=s,
            items=self._items,
            last_mode_shape=self._last_mode_shape,
            open_myofibril=self._open_myofibril,
            created_utc=self._stamp(),
            base=self._base,
            base_scale=base_scale,
        )

    def results(self) -> AnalysisResults | None:
        """The results of the session's document (:func:`sarcostat.analysis.analyze_document`).

        These are the results ``sarcostat analyze`` gives for the exported
        file, and the input of the results exports of :mod:`sarcostat.export`.
        """
        if self._image is None:
            return None
        return analyze_document(self.document())

    def analysis(self) -> ImageResults | None:
        """The results of the session's image (the only image of its document)."""
        results = self.results()
        return None if results is None else results.images[0]

    def state(self) -> ModeState:
        """What the page draws now."""
        image = self._image
        if image is None:
            return {
                "mode": "home",
                "image": None,
                "settings": settings_json(self._settings),
                "pending": {"marks": [], "strokes": []},
                "items": [],
                "controls": _no_controls(),
                "step": None,
                "notice": self.notice,
                "not_exported": False,
            }
        ids = sarcomere_ids(self.document())
        shapes = self._shape_items()
        shape_mode = self._mode == "shape"
        controls = _no_controls()
        if shape_mode:
            controls = {
                "can_confirm": self._flow.complete,
                "can_undo": bool(self._flow.actions) or bool(shapes),
                "can_skip_m_line": self._flow.can_skip_m_line,
                "can_delete": bool(shapes),
            }
        return {
            "mode": self._mode,
            "image": image.to_json(),
            "settings": settings_json(self._settings),
            "pending": {
                "marks": self._flow.marks() if shape_mode else [],
                "strokes": self._flow.strokes() if shape_mode else [],
            },
            "items": [_item_view(item, ids) for item in self._items],
            "controls": controls,
            "step": self._flow.step(len(shapes) + 1) if shape_mode else None,
            "notice": self.notice,
            "not_exported": self.not_exported,
        }

    def doc_new(self, image: Mapping[str, Any]) -> ModeState:
        """``doc.new``: start annotating a new image (the settings are kept)."""
        info = _image_info(image)
        self._image = info
        self._image_id = image_id_for(info.file_name)
        self._items = []
        self._flow.clear()
        self._mode = "shape"
        self._last_mode_shape = True
        self._open_myofibril = False
        self._base = None
        self._base_text = None
        self._base_content = None
        self._base_scale = None
        self._exported_items = None
        return self._done()

    def doc_load_v1(
        self, text: str, image: Mapping[str, Any] | None = None, confirm: bool = False
    ) -> ModeState:
        """``doc.load_v1``: open an annotation file of one image, replacing the session.

        Parameters
        ----------
        text : str
            The annotation file.
        image : Mapping, optional
            The open image (``file_name``, ``sha256``, ``width_px``,
            ``height_px``); ``None`` when no image is open, so its SHA-256
            is not checked (the notice says so).
        confirm : bool
            The user accepts an image whose SHA-256 differs from the file's.

        Raises
        ------
        Refused
            Not a valid file, a file of several images, or an image of
            another size than the file records.
        NeedsConfirmation
            The image's SHA-256 differs and ``confirm`` is false.
        """
        loaded = read_document(text)
        recorded = loaded.image
        actual = recorded if image is None else _image_info(image)
        if (actual.width_px, actual.height_px) != (recorded.width_px, recorded.height_px):
            raise Refused(
                f"{actual.file_name} is not {recorded.width_px} x {recorded.height_px} px as "
                "recorded in the annotation file."
            )
        if actual.sha256 != recorded.sha256 and not confirm:
            raise NeedsConfirmation(
                f"{actual.file_name} is not the image these annotations were made on (its "
                "SHA-256 differs). Load the annotations anyway?"
            )
        items = loaded.items
        has_shapes = any(item["type"] == "shape" for item in items)
        self._image = actual
        self._image_id = loaded.image_id
        self._settings = loaded.settings
        self._items = copy.deepcopy(items)
        self._flow.clear()
        self._mode = "shape" if has_shapes else "home"
        self._last_mode_shape = has_shapes or loaded.last_mode_shape
        self._open_myofibril = loaded.open_myofibril
        self._base = loaded.doc
        self._base_text = loaded.text
        self._base_content = (
            recorded,
            loaded.settings,
            copy.deepcopy(items),
            loaded.open_myofibril,
        )
        self._base_scale = (loaded.settings.pixel_size, loaded.settings.unit)
        self._exported_items = copy.deepcopy(items)
        kinds = [str(item["type"]) for item in items]
        incomplete = sum(
            1
            for item in items
            if item["skipped"] or (item["type"] == "shape" and "m" not in item["points"])
        )
        missing = [ABSENT_MODES[k] for k in ("chain", "pair") if k in kinds]
        return self._done(
            code(
                "document_loaded",
                shape_items=kinds.count("shape"),
                chain_items=kinds.count("chain"),
                pair_items=kinds.count("pair"),
                incomplete_items=incomplete,
                image_checked=image is not None,
                missing_modes=",".join(missing),
            )
        )

    def doc_export(self, what: str) -> dict[str, str]:
        """``doc.export``: the annotation file (``what="v1"``) as ``{file_name, text}``.

        Raises
        ------
        Refused
            Without an image, without items, or for another kind: the
            results exports are written by :mod:`sarcostat.export` from
            :meth:`results` (the bridge's ``doc.export`` does that).
        """
        if what != "v1":
            raise Refused(
                f"The {what} export is written from the results (sarcostat.export), not here."
            )
        image = self._require_image()
        if not self._items:
            raise Refused("Nothing to export: there are no annotations.")
        doc = self.document()
        if self._base is not None and self._content() == self._base_content:
            assert self._base_text is not None
            text = self._base_text
        else:
            try:
                text = dumps_document(doc)
            except AnnotationFormatError as exc:
                raise Refused(f"The annotations cannot be exported: {exc}") from None
        self._base = doc
        self._base_text = text
        self._base_content = self._content()
        self._base_scale = (self._settings.pixel_size, self._settings.unit)
        self._exported_items = copy.deepcopy(self._items)
        return {"file_name": export_file_name(image.file_name), "text": text}

    def doc_set_scale(self, pixel_size: float, unit: str) -> ModeState:
        """``doc.set_scale``: pixel size and unit; a click error follows the new scale."""
        if not (_finite(pixel_size) and pixel_size > 0):
            return self._done(code("pixel_size_invalid"))
        s = self._settings
        factor = float(pixel_size) / s.pixel_size
        sigma_z, sigma_m = s.click_sigma_z, s.click_sigma_m
        if sigma_z is not None:
            sigma_z *= factor
            sigma_m = None if sigma_m is None else sigma_m * factor
        self._settings = replace(
            s,
            pixel_size=float(pixel_size),
            unit=str(unit).strip() or DEFAULT_UNIT,
            click_sigma_z=sigma_z,
            click_sigma_m=sigma_m,
        )
        return self._done()

    def doc_set_click_error(self, sigma: float | None) -> ModeState:
        """``doc.set_click_error``: one click error in the session unit; ``None`` switches it off."""
        if sigma is not None and not (_finite(sigma) and sigma >= 0):
            return self._done(code("click_error_invalid"))
        value = None if sigma is None else float(sigma)
        self._settings = replace(
            self._settings, click_sigma_z=value, click_sigma_m=value, click_perp_ratio=1.0
        )
        return self._done()

    def doc_estimate_click_error(self) -> ModeState:
        """``doc.estimate_click_error``: the click error from the measured sarcomeres as repeats."""
        self._require_image()
        try:
            est = estimate_click_error(self.document(), str(self._image_id))
        except AnalysisError as exc:
            if exc.code == "too_few_repeats":
                return self._done(
                    code("click_error_too_few_repeats", n_repeats=int(exc.params["n_repeats"]))
                )
            return self._done(code("click_error_not_estimable"))
        self._settings = replace(
            self._settings,
            click_sigma_z=est.click_sigma_z,
            click_sigma_m=est.click_sigma_m,
            click_perp_ratio=est.click_perp_ratio,
        )
        return self._done(code("click_error_estimated", n_repeats=int(est.estimate.n_repeats)))

    def doc_set_reference_direction(self, deg: float | None) -> ModeState:
        """``doc.set_reference_direction``: the V-test direction in degrees, or ``None``."""
        if deg is not None and not _finite(deg):
            return self._done(code("reference_direction_invalid"))
        value = None if deg is None else float(deg)
        self._settings = replace(self._settings, reference_direction_deg=value)
        return self._done()

    def shape_click(self, x: float, y: float) -> ModeState:
        """``shape.click``: place the next landmark at image coordinates ``(x, y)``."""
        image = self._enter_shape()
        inside = (
            _finite(x)
            and _finite(y)
            and -0.5 <= x <= image.width_px - 0.5
            and -0.5 <= y <= image.height_px - 0.5
        )
        if not inside:
            return self._done(code("click_outside_image"))
        return self._done(self._flow.click(float(x), float(y)))

    def shape_undo(self) -> ModeState:
        """``shape.undo``: remove the last pending step, or else the last sarcomere item."""
        self._enter_shape()
        if self._flow.undo():
            return self._done()
        shapes = self._shape_items()
        if not shapes:
            return self._done(code("nothing_to_undo"))
        self._items.remove(shapes[-1])
        return self._done()

    def shape_skip_m_line(self) -> ModeState:
        """``shape.skip_m_line``: the M-line of this sarcomere is not identifiable."""
        self._enter_shape()
        return self._done(self._flow.skip_m_line())

    def shape_confirm(self) -> ModeState:
        """``shape.confirm``: save the pending sarcomere as a ``shape`` item."""
        image = self._enter_shape()
        problem = self._flow.problem()
        if problem is not None:
            return self._done(problem)
        assert self._image_id is not None
        self._items.append(
            shape_item(self._new_item_id(), self._image_id, image, self._flow.lines())
        )
        self._flow.clear()
        return self._done()

    def shape_delete(self, item_id: str) -> ModeState:
        """``shape.delete``: delete the shape item ``item_id``."""
        self._enter_shape()
        for item in self._items:
            if item["item_id"] == item_id:
                if item["type"] != "shape":
                    return self._done(code("item_not_shape", item_id=item_id, type=item["type"]))
                self._items.remove(item)
                return self._done()
        return self._done(code("item_not_found", item_id=str(item_id)))

    def shape_clear(self) -> ModeState:
        """``shape.clear``: drop the pending clicks and every shape item (other items stay)."""
        self._enter_shape()
        self._flow.clear()
        self._items = [item for item in self._items if item["type"] != "shape"]
        return self._done()


__all__ = [
    "ABSENT_MODES",
    "LANDMARKS",
    "LANDMARK_NAMES",
    "LINE_NAMES",
    "MIN_LINE_PX",
    "MODES",
    "M_SKIPPED_LANDMARKS",
    "NOTICE_CODES",
    "NOTICE_TEXT",
    "STEP_CODES",
    "STEP_TEXT",
    "AnnotationSession",
    "CodeObject",
    "ControlsJSON",
    "ItemJSON",
    "MarkJSON",
    "ModeState",
    "PendingJSON",
    "SettingsJSON",
    "ShapeFlow",
    "StrokeJSON",
    "code",
    "describe",
    "settings_json",
]
