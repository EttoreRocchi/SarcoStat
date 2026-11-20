"""Bridge handlers of the annotation commands: ``doc.*``, ``shape.*`` and ``results.get``.

Each handler takes the bridge and the checked arguments of one command and
calls the one :class:`~sarcostat.engine.annotate.AnnotationSession` method of
that command; the session lives in ``bridge.state["annotation"]`` (created on
the first command). Results come from :mod:`sarcostat.analysis` on the
session's document; ``results.get`` formats them with
:mod:`sarcostat.engine.reports`, and ``doc.export`` writes the results files
with :mod:`sarcostat.export`, named after the image file without its
extension, as ``sarcostat analyze`` names them.

The bridge imports this module on the first annotation command only.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from sarcostat.engine.annotate import AnnotationSession
from sarcostat.engine.bridge import Refused

if TYPE_CHECKING:
    from sarcostat.analysis import AnalysisResults
    from sarcostat.engine.bridge import Bridge, Handler

STATE_KEY = "annotation"


def session(bridge: Bridge) -> AnnotationSession:
    """The bridge's annotation session (a new one on first use)."""
    current = bridge.state.get(STATE_KEY)
    if not isinstance(current, AnnotationSession):
        current = AnnotationSession()
        bridge.state[STATE_KEY] = current
    return current


def _results(s: AnnotationSession) -> AnalysisResults:
    results = s.results()
    if results is None:
        raise Refused("Open an image first.")
    return results


def _stem(s: AnnotationSession) -> str:
    image = s.image
    if image is None:
        raise Refused("Open an image first.")
    return PurePosixPath(image.file_name).stem


def doc_export(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    """``doc.export``: the annotation file, the results JSON or CSV, or the provenance sidecar."""
    s = session(bridge)
    what = str(args["what"])
    if what == "v1":
        return dict(s.doc_export("v1"))
    from sarcostat.export import (
        export_file_names,
        provenance_sidecar,
        results_csv,
        results_json,
    )

    names = export_file_names(_stem(s))
    results = _results(s)
    if what == "results_json":
        text = results_json(results)
    elif what == "results_csv":
        text = results_csv(results)
    else:
        image = s.image
        assert image is not None
        listed = {(im.file_name, im.sha256) for im in results.images}
        inputs = (
            [] if (image.file_name, image.sha256) in listed else [(image.file_name, image.sha256)]
        )
        text = provenance_sidecar(results, [names["results_json"], names["results_csv"]], inputs)
    return {"file_name": names[what], "text": text}


def results_get(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
    """``results.get``: sentences, tables and plot data of the image (:func:`~sarcostat.engine.reports.results_view`)."""
    from sarcostat.engine.reports import results_view

    image = session(bridge).analysis()
    if image is None:
        raise Refused("Open an image first.")
    view: dict[str, Any] = results_view(image)
    return view


def _call(method: Callable[..., Any], *names: str) -> Handler:
    def run(bridge: Bridge, args: dict[str, Any]) -> dict[str, Any]:
        result: dict[str, Any] = dict(method(session(bridge), *(args[n] for n in names)))
        return result

    return run


def handlers() -> dict[str, Handler]:
    """The handlers of the ``doc``, ``shape`` and ``results`` families, by command."""
    a = AnnotationSession
    return {
        "doc.new": _call(a.doc_new, "image"),
        "doc.load_v1": _call(a.doc_load_v1, "text", "image", "confirm"),
        "doc.export": doc_export,
        "doc.set_scale": _call(a.doc_set_scale, "pixel_size", "unit"),
        "doc.set_click_error": _call(a.doc_set_click_error, "sigma"),
        "doc.estimate_click_error": _call(a.doc_estimate_click_error),
        "doc.set_reference_direction": _call(a.doc_set_reference_direction, "deg"),
        "shape.click": _call(a.shape_click, "x", "y"),
        "shape.undo": _call(a.shape_undo),
        "shape.skip_m_line": _call(a.shape_skip_m_line),
        "shape.confirm": _call(a.shape_confirm),
        "shape.delete": _call(a.shape_delete, "item_id"),
        "shape.clear": _call(a.shape_clear),
        "results.get": results_get,
    }


__all__ = ["STATE_KEY", "doc_export", "handlers", "results_get", "session"]
