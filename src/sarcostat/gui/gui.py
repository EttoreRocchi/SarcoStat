#!/usr/bin/env python3
"""Sarcomeric Image Analysis Tool - Midpoint-based Segment Analysis (Tk/ttkbootstrap).

Interactive TIFF/JPEG/PNG analysis:
1) Load image -> 2) Start Annotating -> 3) Click two points (segment) -> 4) Confirm
5) Repeat -> 6) Connect Midpoints -> 7) Analyze -> (Export as needed)
"""

from __future__ import annotations

import logging
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox

import numpy as np
import pandas as pd
import tifffile
import ttkbootstrap as tb
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
from matplotlib.figure import Figure
from PIL import Image, ImageTk
from ttkbootstrap.constants import (
    BOTH,
    BOTTOM,
    DANGER,
    DISABLED,
    HORIZONTAL,
    INFO,
    LEFT,
    LIGHT,
    NORMAL,
    OUTLINE,
    PRIMARY,
    SECONDARY,
    SUCCESS,
    TOP,
    WARNING,
    YES,
    W,
    X,
    Y,
)

sys.path.append("..")  # Allows `python app.py` from a /app subfolder
try:
    from sarcostat.core.circular_stats import SequenceMetrics, analyze_segment_sequence
    from sarcostat.core.sarcomere_morphometry import (
        SarcomereGeometry,
        calculate_sarcomere_geometry,
    )
except Exception as _import_err:
    raise SystemExit(
        "Failed to import sarcostat core modules. Install the package "
        "with `pip install -e .` from the repository root, then re-run.\n"
        f"Underlying error: {_import_err}"
    ) from _import_err

from sarcostat.core._serialization import dump_json

from .constants import NAV
from .session import AnalysisMode, Point, UserSegment


def to_degrees(rad: float) -> float:
    """Convert radians to degrees (float)."""
    return float(np.degrees(rad))


def now_stamp() -> str:
    """Timestamp string suitable for filenames."""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


class SarcomericImageAnalyzerApp:
    """Sarcomeric image analysis tool using the midpoint method.

    - Annotate segments as pairs of points.
    - Connect their midpoints.
    - Analyze geometry with circular statistics.
    """

    # UI constants (imported from sarcostat.gui.constants for centralized configuration)

    def __init__(self, root: tb.Window) -> None:
        self.root = root
        self.root.title("SarcoStat")

        self.log = logging.getLogger("SarcoStat")
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )

        self.image: np.ndarray | None = None
        self.image_path: Path | None = None
        self.pixel_size: float = 1.0
        self.unit_name: str = "um"

        self.analysis_mode: str = AnalysisMode.MYOFIBRIL_LINEARITY

        self.annotating: bool = False
        self.pending_points: list[Point] = []
        self.segments: list[UserSegment] = []
        self.midpoints: list[Point] = []

        self.sarcomere_points: list[Point] = []
        self.sarcomere_geometries: list[SarcomereGeometry] = []
        self.sarcomere_artists: list = []

        self.temp_artists = []
        self.segment_line_artists = []
        self.midpoint_artists = []
        self.midpoint_link_artists = []
        self.text_artists = []

        self.current_analysis: SequenceMetrics | None = None
        self.analysis_history: list[SequenceMetrics] = []

        self._is_panning: bool = False
        self._space_held: bool = False
        self._pan_start_xy: Point | None = None
        self._pan_xlim0: tuple[float, float] | None = None
        self._pan_ylim0: tuple[float, float] | None = None

        self.status_var = tk.StringVar()
        self.pixel_size_var = tk.StringVar(value=str(self.pixel_size))
        self.unit_var = tk.StringVar(value=self.unit_name)
        self.theme_var = tk.StringVar(value="Day Mode")
        self.mode_var = tk.StringVar(value=AnalysisMode.MYOFIBRIL_LINEARITY)
        self.confirm_btn: tb.Button | None = None
        self.summary_text: tk.Text
        self.history_text: tk.Text
        self.morphometry_text: tk.Text
        self.segments_table: tb.Treeview
        self.fig: Figure
        self.ax = None
        self.canvas: FigureCanvasTkAgg
        self.canvas_widget = None
        self.logo_img = None  # Keep a reference or Tk will GC it

        self._build_ui()
        self._bind_keys()

    def _build_ui(self) -> None:
        """Construct all UI widgets."""
        # Wider default to leave the image room to breathe.
        self.root.geometry("1600x900")

        self._build_menubar()

        header = tb.Frame(self.root, padding=8)
        header.pack(side=TOP, fill=X)

        # Try to load the packaged logo. The image is shipped inside the
        # ``sarcostat.gui`` package so the path resolves both in editable
        # installs and in wheels (matched by the ``*.png`` entry in
        # ``[tool.setuptools.package-data]``).
        logo_path = Path(__file__).parent / "logo.png"
        try:
            if logo_path.exists():
                img = Image.open(logo_path)
                target_h = 40
                target_w = int(img.width * (target_h / img.height))
                img = img.resize((target_w, target_h), Image.LANCZOS)
                self.logo_img = ImageTk.PhotoImage(img)
                tb.Label(header, image=self.logo_img).pack(side=tk.LEFT, padx=(0, 10))
        except Exception as e:
            self.log.warning("Could not load logo.png: %s", e)

        tb.Label(
            header,
            text="SarcoStat - sarcomeric linearity annotations from image analysis",
            bootstyle=PRIMARY,
            font=("Segoe UI", 16, "bold"),
        ).pack(side=LEFT, padx=(0, 10))

        bar1 = tb.Frame(self.root, padding=(6, 4, 6, 2))
        bar1.pack(side=TOP, fill=X)

        tb.Label(bar1, text="Mode:").pack(side=LEFT, padx=(0, 4))
        mode_combo = tb.Combobox(
            bar1,
            textvariable=self.mode_var,
            values=[AnalysisMode.MYOFIBRIL_LINEARITY, AnalysisMode.SINGLE_SARCOMERE],
            width=18,
            state="readonly",
        )
        mode_combo.pack(side=LEFT, padx=(0, 8))
        mode_combo.bind("<<ComboboxSelected>>", lambda e: self._on_mode_change())

        tb.Button(bar1, text="Load Image", bootstyle=PRIMARY, command=self.load_image).pack(
            side=LEFT, padx=2
        )
        tb.Button(
            bar1, text="Start Annotating", bootstyle=INFO, command=self.start_annotation
        ).pack(side=LEFT, padx=2)

        self.confirm_btn = tb.Button(
            bar1,
            text="Confirm Segment",
            bootstyle=SUCCESS,
            command=self.confirm_segment,
            state=DISABLED,
        )
        self.confirm_btn.pack(side=LEFT, padx=2)

        self.connect_btn = tb.Button(
            bar1, text="Connect Midpoints", bootstyle=SECONDARY, command=self.connect_midpoints
        )
        self.connect_btn.pack(side=LEFT, padx=2)
        self.analyze_btn = tb.Button(bar1, text="Analyze", bootstyle=SUCCESS, command=self.analyze)
        self.analyze_btn.pack(side=LEFT, padx=2)

        tb.Separator(bar1, orient="vertical").pack(side=LEFT, fill=Y, padx=8)

        tb.Button(bar1, text="Undo", bootstyle=WARNING, command=self.delete_last).pack(
            side=LEFT, padx=2
        )
        tb.Button(bar1, text="Clear All", bootstyle=DANGER, command=self.clear_all).pack(
            side=LEFT, padx=2
        )

        bar2 = tb.Frame(self.root, padding=(6, 2, 6, 4))
        bar2.pack(side=TOP, fill=X)

        tb.Label(bar2, text="View:").pack(side=LEFT, padx=(0, 4))
        tb.Button(
            bar2, text="Zoom In", bootstyle=OUTLINE, command=lambda: self.zoom_button(+1)
        ).pack(side=LEFT, padx=2)
        tb.Button(
            bar2, text="Zoom Out", bootstyle=OUTLINE, command=lambda: self.zoom_button(-1)
        ).pack(side=LEFT, padx=2)
        tb.Button(bar2, text="Reset View", bootstyle=OUTLINE, command=self.reset_view).pack(
            side=LEFT, padx=2
        )

        tb.Separator(bar2, orient="vertical").pack(side=LEFT, fill=Y, padx=8)

        tb.Label(bar2, text="Scale: 1 px =").pack(side=LEFT, padx=(0, 2))
        tb.Entry(bar2, width=8, textvariable=self.pixel_size_var).pack(side=LEFT)
        tb.Entry(bar2, width=6, textvariable=self.unit_var).pack(side=LEFT, padx=(4, 4))
        tb.Button(bar2, text="Apply", bootstyle=OUTLINE, command=self.apply_scale).pack(side=LEFT)

        tb.Separator(bar2, orient="vertical").pack(side=LEFT, fill=Y, padx=8)

        tb.Label(bar2, text="Export:").pack(side=LEFT, padx=(0, 4))
        tb.Button(bar2, text="Results", bootstyle=LIGHT, command=self.export_results).pack(
            side=LEFT, padx=2
        )
        tb.Button(bar2, text="History", bootstyle=LIGHT, command=self.export_history).pack(
            side=LEFT, padx=2
        )

        # Main split pane. Stash a reference so we can set the initial sash
        # position once Tk has computed real geometry (the weight= argument
        # only governs how *extra* space is divided after the initial layout).
        main = tb.Panedwindow(self.root, orient=HORIZONTAL)
        main.pack(fill=BOTH, expand=YES, padx=6, pady=(0, 6))
        self._main_pane = main

        # Left panel: image canvas (initial sash placed at ~82% of the window)
        left = tb.Frame(main, padding=4)
        main.add(left, weight=6)

        # The figure margins are zeroed so that the imshow output fills the
        # entire canvas. Without this, matplotlib leaves the default 10-12%
        # whitespace on each side, leaving a tiny image inside a huge frame.
        self.fig = Figure(figsize=(10, 8), dpi=96)
        self.fig.subplots_adjust(left=0.0, right=1.0, top=1.0, bottom=0.0)
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.ax.set_position([0.0, 0.0, 1.0, 1.0])
        self.canvas = FigureCanvasTkAgg(self.fig, master=left)
        self.canvas_widget = self.canvas.get_tk_widget()
        self.canvas_widget.pack(fill=BOTH, expand=YES)

        for ev in ("<Enter>", "<Button-1>", "<Button-2>", "<Button-3>"):
            self.canvas_widget.bind(ev, lambda e: self.canvas_widget.focus_set())

        self.canvas.mpl_connect("button_press_event", self._mpl_on_press)
        self.canvas.mpl_connect("button_release_event", self._mpl_on_release)
        self.canvas.mpl_connect("motion_notify_event", self._mpl_on_motion)
        self.canvas.mpl_connect("scroll_event", self._mpl_on_scroll)

        # Right panel: tabs (narrower; readable font for monospaced data)
        right = tb.Frame(main, padding=4)
        main.add(right, weight=1)

        # Centralised font choices for the right-panel widgets. Two sizes so
        # that summary/morphometry/history (verbose prose) read comfortably,
        # and the segments table (dense numeric grid) stays compact.
        text_font = ("TkDefaultFont", 12)
        table_font = ("TkDefaultFont", 11)
        table_heading_font = ("TkDefaultFont", 11, "bold")

        tabs = tb.Notebook(right, bootstyle=PRIMARY)
        tabs.pack(fill=BOTH, expand=YES)

        self.summary_text = tk.Text(tabs, wrap=tk.WORD, height=8, font=text_font)
        self._set_text(self.summary_text, "Load a TIFF image to begin.\n")
        tabs.add(self.summary_text, text="Summary")

        seg_frame = tb.Frame(tabs, padding=4)
        style = tb.Style()
        style.configure("Treeview", font=table_font, rowheight=24)
        style.configure("Treeview.Heading", font=table_heading_font)
        cols = ("id", "len", "ang", "rel", "cum")
        self.segments_table = tb.Treeview(seg_frame, columns=cols, show="headings", bootstyle=INFO)
        headers = {
            "id": "ID",
            "len": f"Length ({self.unit_name})",
            "ang": "Heading (°)",
            "rel": "Δ heading (°)",
            "cum": "Σ Δ heading (°)",
        }
        widths = {"id": 40, "len": 100, "ang": 95, "rel": 110, "cum": 125}
        anchors = {"id": "center"}
        for c in cols:
            self.segments_table.heading(c, text=headers[c])
            self.segments_table.column(
                c, width=widths[c], minwidth=widths[c], stretch=True, anchor=anchors.get(c, "e")
            )
        self.segments_table.pack(fill=BOTH, expand=YES)
        tabs.add(seg_frame, text="Segments")

        self.morphometry_text = tk.Text(tabs, wrap=tk.WORD, font=text_font)
        self._set_text(
            self.morphometry_text,
            "Single Sarcomere Morphometry\n"
            "============================\n\n"
            "Switch to 'Single Sarcomere' mode to analyze\n"
            "sarcomere shape parameters (AsR, Cur, Shape Index)\n"
            "following Cisterna et al. (2021).\n\n"
            "6-Point Annotation:\n"
            "- Points A, B: Z-line 1 (red)\n"
            "- Points C, D: M-line (blue)\n"
            "- Points E, F: Z-line 2 (green)\n",
        )
        tabs.add(self.morphometry_text, text="Morphometry")

        self.history_text = tk.Text(tabs, wrap=tk.WORD, font=text_font)
        self._set_text(self.history_text, "")
        tabs.add(self.history_text, text="History")

        self.status_var.set(
            "Press 'Start Annotating' to begin. Click two points per segment, then Confirm."
        )
        status = tb.Label(
            self.root, textvariable=self.status_var, anchor=W, padding=(10, 6), bootstyle=SECONDARY
        )
        status.pack(side=BOTTOM, fill=X)

        # Defer the initial sash placement until Tk has realised the panes;
        # without this the sash sits at the geometry midpoint regardless of
        # the weight= ratios we requested.
        self.root.after(50, self._set_initial_sash)

    def _set_initial_sash(self) -> None:
        """Place the main paned-window sash at 65% of the window width."""
        try:
            self._main_pane.update_idletasks()
            width = self._main_pane.winfo_width()
            if width > 100:
                self._main_pane.sashpos(0, int(width * 0.65))
        except Exception as exc:
            self.log.debug("Could not set initial sash position: %s", exc)

    def _build_menubar(self) -> None:
        """Create the application menu bar."""
        menubar = tk.Menu(self.root)

        file_menu = tk.Menu(menubar, tearoff=False)
        file_menu.add_command(label="Load Image...", command=self.load_image)
        file_menu.add_separator()
        file_menu.add_command(label="Export Results...", command=self.export_results)
        file_menu.add_command(label="Export History...", command=self.export_history)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.destroy)
        menubar.add_cascade(label="File", menu=file_menu)

        view_menu = tk.Menu(menubar, tearoff=False)
        view_menu.add_command(label="Zoom In", command=lambda: self.zoom_button(+1))
        view_menu.add_command(label="Zoom Out", command=lambda: self.zoom_button(-1))
        view_menu.add_command(label="Reset View", command=self.reset_view)
        view_menu.add_separator()
        view_menu.add_command(label="Day Mode", command=lambda: self._set_theme("litera"))
        view_menu.add_command(label="Night Mode", command=lambda: self._set_theme("solar"))
        menubar.add_cascade(label="View", menu=view_menu)

        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Keyboard Shortcuts", command=self._show_shortcuts)
        help_menu.add_command(label="About", command=self._show_about)
        menubar.add_cascade(label="Help", menu=help_menu)

        self.root.config(menu=menubar)

    def _bind_keys(self) -> None:
        """Bind keyboard shortcuts."""
        self.root.bind(
            "c",
            lambda e: (
                self.connect_midpoints()
                if self.analysis_mode != AnalysisMode.SINGLE_SARCOMERE
                else None
            ),
        )
        self.root.bind("a", lambda e: self.analyze())
        self.root.bind("<Delete>", lambda e: self.delete_last())
        self.root.bind("<Escape>", lambda e: self.clear_all())
        self.root.bind("s", lambda e: self.confirm_segment())

        for sym in ("<plus>", "=", "+"):
            self.root.bind(sym, lambda e: self.zoom_button(+1))
        for sym in ("<minus>", "-"):
            self.root.bind(sym, lambda e: self.zoom_button(-1))
        self.root.bind("0", lambda e: self.reset_view())

        # Pan arrows (Shift = fast)
        self.root.bind("<Left>", lambda e: self._pan_key(-1, 0, fast=bool(e.state & 0x0001)))
        self.root.bind("<Right>", lambda e: self._pan_key(+1, 0, fast=bool(e.state & 0x0001)))
        self.root.bind("<Up>", lambda e: self._pan_key(0, -1, fast=bool(e.state & 0x0001)))
        self.root.bind("<Down>", lambda e: self._pan_key(0, +1, fast=bool(e.state & 0x0001)))

        self.root.bind("<KeyPress-space>", self._on_space_down)
        self.root.bind("<KeyRelease-space>", self._on_space_up)

    def _set_theme(self, theme_name: str) -> None:
        """Apply a ttkbootstrap theme by name."""
        try:
            self.root.style.theme_use(theme_name)
        except Exception as e:
            messagebox.showerror("Theme Error", str(e))

    def _on_mode_change(self) -> None:
        """Handle analysis mode change."""
        new_mode = self.mode_var.get()
        if new_mode == self.analysis_mode:
            return

        self.analysis_mode = new_mode
        self._reset_annotations()
        self._reset_sarcomere_annotations()

        if new_mode == AnalysisMode.SINGLE_SARCOMERE:
            self.connect_btn.pack_forget()
            self._set_text(
                self.summary_text,
                "SINGLE SARCOMERE MODE\n"
                "=====================\n\n"
                "Annotate 6 points per sarcomere:\n"
                "1-2: Z-line 1 (A, B) - Red\n"
                "3-4: M-line (C, D) - Blue\n"
                "5-6: Z-line 2 (E, F) - Green\n\n"
                "Click 'Start Annotating' to begin.",
            )
            self.status_var.set(
                "Single Sarcomere mode: Click 'Start Annotating' to begin 6-point annotation."
            )
        else:
            self.connect_btn.pack(side=LEFT, padx=2, before=self.analyze_btn)
            self._set_text(
                self.summary_text,
                "MYOFIBRIL LINEARITY MODE\n"
                "========================\n\n"
                "Annotate segment pairs (2 points each).\n"
                "Midpoints will be connected for analysis.\n\n"
                "Click 'Start Annotating' to begin.",
            )
            self.status_var.set("Myofibril Linearity mode: Click 'Start Annotating' to begin.")

        self.log.info("Switched to mode: %s", new_mode)

    def _reset_sarcomere_annotations(self) -> None:
        """Reset single sarcomere annotation state."""
        self.sarcomere_points.clear()
        self.sarcomere_geometries.clear()
        for art in self.sarcomere_artists:
            try:
                art.remove()
            except (ValueError, AttributeError):
                pass
        self.sarcomere_artists.clear()

    def load_image(self) -> None:
        """Load an image (TIFF, JPEG, or PNG)."""
        path = filedialog.askopenfilename(
            title="Select image",
            filetypes=[
                ("All supported", "*.tif *.tiff *.jpg *.jpeg *.png"),
                ("TIFF files", "*.tif *.tiff"),
                ("JPEG files", "*.jpg *.jpeg"),
                ("PNG files", "*.png"),
            ],
        )
        if not path:
            return
        try:
            path_obj = Path(path)
            suffix = path_obj.suffix.lower()

            if suffix in (".tif", ".tiff"):
                img = tifffile.imread(path)
            else:
                pil_img = Image.open(path)
                img = np.array(pil_img)

            if img.ndim > 2:
                if img.ndim == 3 and img.shape[2] in (3, 4):
                    img = np.mean(img[:, :, :3], axis=2)
                else:
                    img = np.squeeze(img)

            self.image = img.astype(np.float32)
            self.image_path = path_obj

            vmin, vmax = np.percentile(self.image, [1, 99])
            self.ax.clear()
            self.ax.imshow(self.image, cmap="gray", vmin=vmin, vmax=vmax, aspect="equal")
            self.ax.set_axis_off()
            # Keep the image flush with the panel edges - clear() resets the
            # subplot margins, so we re-apply the zero-margin layout.
            self.ax.set_position([0.0, 0.0, 1.0, 1.0])
            self.reset_view()

            self._reset_annotations()
            self._set_text(
                self.summary_text,
                f"Image: {path_obj.name}\nShape: {self.image.shape}\nType: {self.image.dtype}",
            )
            self.log.info("Loaded image: %s", path_obj.name)
        except Exception as e:
            self.log.exception("Failed to load image")
            messagebox.showerror("Error", f"Failed to load image:\n{e}")

    def start_annotation(self) -> None:
        """Enter annotation mode and clear any existing annotations."""
        if self.image is None:
            messagebox.showinfo("Info", "Load an image first.")
            return
        self.annotating = True
        self._reset_annotations()
        self._update_status()
        self.log.info("Annotation mode started")

    def confirm_segment(self) -> None:
        """Confirm the current annotation based on mode."""
        if self.analysis_mode == AnalysisMode.SINGLE_SARCOMERE:
            self._confirm_sarcomere()
        else:
            self._confirm_linearity_segment()

    def _confirm_linearity_segment(self) -> None:
        """Confirm the current pair of points as a segment (Myofibril Linearity mode)."""
        if len(self.pending_points) != 2:
            return

        p1, p2 = self.pending_points

        if p1 == p2:
            messagebox.showwarning(
                "Invalid Segment",
                "Start and end points are identical. Please select different points.",
            )
            return

        dist = np.hypot(p2[0] - p1[0], p2[1] - p1[1])
        if dist < 1.0:
            messagebox.showwarning(
                "Invalid Segment",
                "Points are too close together (< 1 pixel). Please select points further apart.",
            )
            return

        seg = UserSegment(p1=p1, p2=p2)
        self.segments.append(seg)
        self.midpoints.append(seg.midpoint)

        line = self.ax.plot(
            [p1[0], p2[0]], [p1[1], p2[1]], linewidth=2, color="yellow", alpha=0.75
        )[0]
        self.segment_line_artists.append(line)

        mx, my = seg.midpoint
        mid = self.ax.plot(
            mx, my, marker="o", markersize=8, markerfacecolor="lime", markeredgecolor="darkgreen"
        )[0]
        self.midpoint_artists.append(mid)
        idx = len(self.segments)
        t = self.ax.text(
            mx,
            my - 6,
            f"S{idx}",
            color="white",
            fontsize=8,
            ha="center",
            va="top",
            bbox={"boxstyle": "round,pad=0.3", "facecolor": "darkgreen", "alpha": 0.7},
        )
        self.text_artists.append(t)

        for art in self.temp_artists:
            art.remove()
        self.temp_artists.clear()
        self.pending_points.clear()

        if self.confirm_btn:
            self.confirm_btn.config(state=DISABLED)
        self.canvas.draw_idle()
        self._update_status()
        self.log.info("Segment #%d confirmed. Midpoint at (%.1f, %.1f)", idx, mx, my)

    def _confirm_sarcomere(self) -> None:
        """Confirm and analyze 6-point sarcomere annotation (Single Sarcomere mode)."""
        if len(self.sarcomere_points) != 6:
            messagebox.showinfo("Info", "Need exactly 6 points for sarcomere analysis.")
            return

        point_a, point_b = self.sarcomere_points[0], self.sarcomere_points[1]
        point_c, point_d = self.sarcomere_points[2], self.sarcomere_points[3]
        point_e, point_f = self.sarcomere_points[4], self.sarcomere_points[5]

        line_pairs = [
            (point_a, point_b, "Z-line 1 (A-B)"),
            (point_c, point_d, "M-line (C-D)"),
            (point_e, point_f, "Z-line 2 (E-F)"),
        ]
        for p1, p2, name in line_pairs:
            dist = np.hypot(p2[0] - p1[0], p2[1] - p1[1])
            if dist < 1.0:
                messagebox.showwarning(
                    "Invalid Annotation",
                    f"{name} has near-zero length (< 1 pixel). Please select points further apart.",
                )
                return

        try:
            geom = calculate_sarcomere_geometry(
                point_a=point_a,
                point_b=point_b,
                point_c=point_c,
                point_d=point_d,
                point_e=point_e,
                point_f=point_f,
                sarcomere_id=len(self.sarcomere_geometries) + 1,
                pixel_to_unit=self.pixel_size,
                unit=self.unit_name,
            )
            self.sarcomere_geometries.append(geom)

            self._draw_sarcomere_overlay(geom)
            self._render_morphometry_results()

            for art in self.temp_artists:
                art.remove()
            self.temp_artists.clear()
            self.sarcomere_points.clear()

            if self.confirm_btn:
                self.confirm_btn.config(state=DISABLED)
            self.canvas.draw_idle()
            self._update_status()
            self.log.info(
                "Sarcomere #%d analyzed: AsR=%.3f, Cur=%.3f, Shape=%s",
                geom.sarcomere_id,
                geom.aspect_ratio,
                geom.curviness,
                geom.shape_classification.value,
            )

        except Exception as e:
            self.log.exception("Sarcomere analysis failed")
            messagebox.showerror("Error", f"Sarcomere analysis failed:\n{e}")

    def _draw_sarcomere_overlay(self, geom: SarcomereGeometry) -> None:
        """Draw permanent overlay for confirmed sarcomere."""
        mx1, my1 = geom.midpoint_z1
        mx2, my2 = geom.midpoint_z2
        axis_line = self.ax.plot(
            [mx1, mx2],
            [my1, my2],
            linewidth=2,
            color="cyan",
            alpha=0.8,
            linestyle="-.",
            marker="s",
            markersize=5,
        )[0]
        self.sarcomere_artists.append(axis_line)

        cx = (mx1 + mx2) / 2
        cy = (my1 + my2) / 2
        label = f"S{geom.sarcomere_id}\nAsR={geom.aspect_ratio:.2f}\nCur={geom.curviness:.2f}\n{geom.shape_classification.value}"
        txt = self.ax.text(
            cx,
            cy,
            label,
            color="white",
            fontsize=8,
            ha="center",
            va="center",
            bbox={"boxstyle": "round,pad=0.4", "facecolor": "purple", "alpha": 0.7},
        )
        self.sarcomere_artists.append(txt)

    def _render_morphometry_results(self) -> None:
        """Update the Morphometry tab with sarcomere analysis results."""
        if not self.sarcomere_geometries:
            return

        lines = ["SINGLE SARCOMERE MORPHOMETRY RESULTS\n", "=" * 40 + "\n\n"]

        for geom in self.sarcomere_geometries:
            lines.append(f"Sarcomere #{geom.sarcomere_id}\n")
            lines.append("-" * 30 + "\n")
            lines.append(f"Z-line 1 length (AB): {geom.z_line_1_length:.2f} {geom.unit}\n")
            lines.append(f"Z-line 2 length (EF): {geom.z_line_2_length:.2f} {geom.unit}\n")
            lines.append(f"M-line length (CD):   {geom.m_line_length:.2f} {geom.unit}\n")
            lines.append(f"Sarcomere length (MN): {geom.sarcomere_length:.2f} {geom.unit}\n\n")
            lines.append(f"Aspect Ratio (AsR):   {geom.aspect_ratio:.4f}\n")
            lines.append(f"Curviness (Cur):      {geom.curviness:.4f}\n")
            lines.append(f"Shape Index (Cur/AsR): {geom.shape_index:.4f}\n")
            lines.append(f"Shape Classification: {geom.shape_classification.value.upper()}\n\n")

        if len(self.sarcomere_geometries) > 1:
            asr_vals = [g.aspect_ratio for g in self.sarcomere_geometries]
            cur_vals = [g.curviness for g in self.sarcomere_geometries]
            si_vals = [g.shape_index for g in self.sarcomere_geometries]

            lines.append("\nSUMMARY STATISTICS\n")
            lines.append("=" * 40 + "\n")
            lines.append(f"n = {len(self.sarcomere_geometries)} sarcomeres\n\n")
            lines.append(f"Aspect Ratio:  {np.mean(asr_vals):.4f} +/- {np.std(asr_vals):.4f}\n")
            lines.append(f"Curviness:     {np.mean(cur_vals):.4f} +/- {np.std(cur_vals):.4f}\n")
            lines.append(f"Shape Index:   {np.mean(si_vals):.4f} +/- {np.std(si_vals):.4f}\n")

        self._set_text(self.morphometry_text, "".join(lines))

    def _on_space_down(self, _event) -> None:
        """Handle spacebar press for pan mode activation."""
        self._space_held = True

    def _on_space_up(self, _event) -> None:
        """Handle spacebar release to exit pan mode."""
        self._space_held = False

    def _mpl_on_press(self, event) -> None:
        """Handle mouse button press on the matplotlib canvas."""
        if self.image is None or event.inaxes != self.ax:
            return
        if event.button in (2, 3) or (event.button == 1 and self._space_held):
            if event.xdata is None or event.ydata is None:
                return
            self._is_panning = True
            self._pan_start_xy = (float(event.xdata), float(event.ydata))
            self._pan_xlim0 = self.ax.get_xlim()
            self._pan_ylim0 = self.ax.get_ylim()
            return

        if event.button == 1 and self.annotating:
            self._add_point(event)

    def _mpl_on_release(self, _event) -> None:
        """Handle mouse button release to end panning."""
        self._is_panning = False
        self._pan_start_xy = None
        self._pan_xlim0 = None
        self._pan_ylim0 = None

    def _mpl_on_motion(self, event) -> None:
        """Handle mouse motion for panning the image view."""
        if not self._is_panning or self.image is None or event.inaxes != self.ax:
            return
        if event.xdata is None or event.ydata is None or self._pan_start_xy is None:
            return
        x0, y0 = self._pan_start_xy
        dx = float(event.xdata) - x0
        dy = float(event.ydata) - y0
        xlim0 = self._pan_xlim0 or self.ax.get_xlim()
        ylim0 = self._pan_ylim0 or self.ax.get_ylim()
        new_xlim = (xlim0[0] - dx, xlim0[1] - dx)
        new_ylim = (ylim0[0] - dy, ylim0[1] - dy)
        self._apply_view(new_xlim, new_ylim)

    def _mpl_on_scroll(self, event) -> None:
        """Handle mouse scroll for zooming at cursor position."""
        if self.image is None or event.inaxes != self.ax:
            return
        direction = +1 if event.step > 0 else -1
        self._zoom_at(float(event.xdata), float(event.ydata), direction)

    def _add_point(self, event) -> None:
        """Add a point based on current analysis mode."""
        if event.xdata is None or event.ydata is None:
            return

        if self.analysis_mode == AnalysisMode.SINGLE_SARCOMERE:
            self._add_sarcomere_point(event)
        else:
            self._add_linearity_point(event)

    def _add_linearity_point(self, event) -> None:
        """Add a point to the pending segment (Myofibril Linearity mode)."""
        if len(self.pending_points) >= 2:
            messagebox.showinfo("Info", "Confirm the current segment before adding more points.")
            return

        if event.xdata is None or event.ydata is None:
            return
        x, y = float(event.xdata), float(event.ydata)
        if self.image is not None:
            h, w = self.image.shape[:2]
            if not (0 <= x < w and 0 <= y < h):
                self.status_var.set("Click inside the image bounds.")
                return

        self.pending_points.append((x, y))

        color = "red" if len(self.pending_points) == 1 else "blue"
        pt = self.ax.plot(
            x, y, marker="o", markersize=6, markerfacecolor=color, markeredgecolor="white"
        )[0]
        self.temp_artists.append(pt)

        if len(self.pending_points) == 2:
            p1, p2 = self.pending_points
            ln = self.ax.plot(
                [p1[0], p2[0]], [p1[1], p2[1]], linewidth=1, color="cyan", alpha=0.6, linestyle="--"
            )[0]
            self.temp_artists.append(ln)
            if self.confirm_btn:
                self.confirm_btn.config(state=NORMAL)

        self.canvas.draw_idle()
        self._update_status()

    @staticmethod
    def _sarcomere_point_color_label(point_index: int) -> tuple[str, str]:
        """Return ``(color, letter_label)`` for the 1-based ``point_index`` 1..6."""
        if point_index <= 2:
            return "red", ["A", "B"][point_index - 1]
        if point_index <= 4:
            return "blue", ["C", "D"][point_index - 3]
        return "green", ["E", "F"][point_index - 5]

    def _validate_sarcomere_click(self, event) -> tuple[float, float] | None:
        """Return ``(x, y)`` for a valid click, or ``None`` to abort.

        Aborts on missing coordinates and on clicks outside the loaded
        image. The status bar is updated when a click falls outside the
        image bounds.
        """
        if event.xdata is None or event.ydata is None:
            return None
        x, y = float(event.xdata), float(event.ydata)
        if self.image is None:
            return x, y
        h, w = self.image.shape[:2]
        if not (0 <= x < w and 0 <= y < h):
            self.status_var.set("Click inside the image bounds.")
            return None
        return x, y

    def _draw_sarcomere_point_marker(self, x: float, y: float, color: str, label: str) -> None:
        """Draw the marker and the letter-label for one annotated point."""
        marker = self.ax.plot(
            x,
            y,
            marker="o",
            markersize=8,
            markerfacecolor=color,
            markeredgecolor="white",
            linewidth=2,
        )[0]
        self.temp_artists.append(marker)
        text_artist = self.ax.text(
            x + 5,
            y - 5,
            label,
            color=color,
            fontsize=10,
            fontweight="bold",
            bbox={"boxstyle": "round,pad=0.2", "facecolor": "white", "alpha": 0.7},
        )
        self.temp_artists.append(text_artist)

    _SARC_LINE_STYLES: dict[int, dict[str, object]] = {
        2: {"start": 0, "color": "red", "linestyle": "-"},
        4: {"start": 2, "color": "blue", "linestyle": "--"},
        6: {"start": 4, "color": "green", "linestyle": "-"},
    }

    def _maybe_draw_sarcomere_line(self, point_index: int) -> bool:
        """Draw the pair line when ``point_index`` completes a Z-line or M-line.

        Returns ``True`` when a sixth point completed the annotation,
        signalling the caller to enable the confirm button.
        """
        spec = self._SARC_LINE_STYLES.get(point_index)
        if spec is None:
            return False
        start = int(spec["start"])
        p1, p2 = self.sarcomere_points[start], self.sarcomere_points[start + 1]
        line = self.ax.plot(
            [p1[0], p2[0]],
            [p1[1], p2[1]],
            linewidth=2,
            color=str(spec["color"]),
            alpha=0.8,
            linestyle=str(spec["linestyle"]),
        )[0]
        self.temp_artists.append(line)
        return point_index == 6

    def _add_sarcomere_point(self, event) -> None:
        """Add a point for 6-point sarcomere annotation (Single Sarcomere mode)."""
        if len(self.sarcomere_points) >= 6:
            messagebox.showinfo("Info", "All 6 points placed. Click 'Confirm Segment' to analyze.")
            return
        coords = self._validate_sarcomere_click(event)
        if coords is None:
            return
        x, y = coords
        self.sarcomere_points.append((x, y))
        point_index = len(self.sarcomere_points)
        color, label = self._sarcomere_point_color_label(point_index)
        self._draw_sarcomere_point_marker(x, y, color, label)
        if self._maybe_draw_sarcomere_line(point_index) and self.confirm_btn:
            self.confirm_btn.config(state=NORMAL)
        self.canvas.draw_idle()
        self._update_status()

    def zoom_button(self, direction: int) -> None:
        """Zoom in or out centered on the current view."""
        if self.image is None:
            return
        xlim = self.ax.get_xlim()
        ylim = self.ax.get_ylim()
        cx = (xlim[0] + xlim[1]) / 2.0
        cy = (ylim[0] + ylim[1]) / 2.0
        self._zoom_at(cx, cy, direction)

    def _zoom_at(self, x: float, y: float, direction: int) -> None:
        """Zoom at a specific point (x, y) with direction (+1=in, -1=out)."""
        if x is None or y is None:
            return
        factor = NAV.ZOOM_STEP if direction > 0 else (1.0 / NAV.ZOOM_STEP)
        cur_xlim = self.ax.get_xlim()
        cur_ylim = self.ax.get_ylim()
        dx_left = x - cur_xlim[0]
        dx_right = cur_xlim[1] - x
        dy_bottom = y - cur_ylim[0]
        dy_top = cur_ylim[1] - y
        new_xlim = (x - dx_left / factor, x + dx_right / factor)
        new_ylim = (y - dy_bottom / factor, y + dy_top / factor)
        self._apply_view(new_xlim, new_ylim)

    def reset_view(self) -> None:
        """Reset axes to show the full image."""
        if self.image is None:
            return
        h, w = self.image.shape[:2]
        self.ax.set_xlim(0, w - 1)
        self.ax.set_ylim(h - 1, 0)
        self.canvas.draw_idle()

    def _pan_key(self, sx: int, sy: int, fast: bool = False) -> None:
        """Pan the view using arrow keys (sx, sy: direction, fast: shift held)."""
        if self.image is None:
            return
        frac = NAV.PAN_FRAC_FAST if fast else NAV.PAN_FRAC
        x0, x1 = self.ax.get_xlim()
        y0, y1 = self.ax.get_ylim()
        vw = x1 - x0
        vh = y1 - y0
        dx = frac * vw * sx
        dy = frac * vh * sy
        if y0 > y1:
            dy = -dy
        new_xlim = (x0 + dx, x1 + dx)
        new_ylim = (y0 + dy, y1 + dy)
        self._apply_view(new_xlim, new_ylim)

    def _apply_view(self, xlim: tuple[float, float], ylim: tuple[float, float]) -> None:
        """Apply view limits while clamping inside image bounds."""
        if self.image is None:
            return
        h, w = self.image.shape[:2]
        vw = abs(xlim[1] - xlim[0])
        vh = abs(ylim[1] - ylim[0])

        # X limits
        x0 = max(0.0, min(xlim[0], w - vw))
        x1 = x0 + vw

        # Y limits (account for inverted image axis)
        y_min, y_max = 0.0, h - 1.0
        yl, _yh = min(ylim[0], ylim[1]), max(ylim[0], ylim[1])
        y0 = max(y_min, min(yl, y_max - vh))
        y1 = y0 + vh
        new_ylim = (y1, y0) if ylim[0] > ylim[1] else (y0, y1)

        self.ax.set_xlim((x0, x1))
        self.ax.set_ylim(new_ylim)
        self.canvas.draw_idle()

    def connect_midpoints(self) -> None:
        """Connect consecutive midpoints with cyan lines."""
        if len(self.midpoints) < 2:
            messagebox.showinfo("Info", "Need at least 2 segments to connect midpoints.")
            return

        for art in self.midpoint_link_artists:
            art.remove()
        self.midpoint_link_artists.clear()

        for i in range(len(self.midpoints) - 1):
            (x0, y0), (x1, y1) = self.midpoints[i], self.midpoints[i + 1]
            ln = self.ax.plot([x0, x1], [y0, y1], linewidth=3, color="cyan", alpha=0.85)[0]
            self.midpoint_link_artists.append(ln)

        self.canvas.draw_idle()
        self.log.info("Connected %d midpoints", len(self.midpoints))
        self._set_text(
            self.summary_text, f"Connected {len(self.midpoints)} midpoints.\nReady for analysis."
        )

    def delete_last(self) -> None:
        """Delete pending points or the last confirmed item based on mode."""
        if self.analysis_mode == AnalysisMode.SINGLE_SARCOMERE:
            self._delete_last_sarcomere()
        else:
            self._delete_last_linearity()

    def _drop_pending_linearity(self) -> bool:
        """Drop pending linearity points and their preview artists.

        Returns
        -------
        bool
            True when there were pending points to drop (caller should
            short-circuit), False otherwise.
        """
        if not self.pending_points:
            return False
        self.pending_points.clear()
        for art in self.temp_artists:
            art.remove()
        self.temp_artists.clear()
        if self.confirm_btn:
            self.confirm_btn.config(state=DISABLED)
        return True

    def _pop_last_segment_artists(self) -> None:
        """Remove the most-recent segment, midpoint, and label artists."""
        if self.segment_line_artists:
            self.segment_line_artists.pop().remove()
        if self.midpoint_artists:
            self.midpoint_artists.pop().remove()
        if self.text_artists:
            self.text_artists.pop().remove()

    def _rebuild_midpoint_links(self) -> None:
        """Clear and (if needed) rebuild the midpoint connector polyline."""
        for art in self.midpoint_link_artists:
            art.remove()
        self.midpoint_link_artists.clear()
        if len(self.midpoints) >= 2:
            self.connect_midpoints()

    def _delete_last_linearity(self) -> None:
        """Delete last item in Myofibril Linearity mode."""
        if self._drop_pending_linearity():
            self.canvas.draw_idle()
            self._update_status()
            return
        if not self.segments:
            return
        self.segments.pop()
        if self.midpoints:
            self.midpoints.pop()
        self._pop_last_segment_artists()
        self._rebuild_midpoint_links()
        self.canvas.draw_idle()
        self._update_status()

    def _delete_last_sarcomere(self) -> None:
        """Delete last item in Single Sarcomere mode."""
        if self.sarcomere_points:
            self.sarcomere_points.clear()
            for art in self.temp_artists:
                art.remove()
            self.temp_artists.clear()
            if self.confirm_btn:
                self.confirm_btn.config(state=DISABLED)
            self.canvas.draw_idle()
            self._update_status()
            return

        if not self.sarcomere_geometries:
            return

        self.sarcomere_geometries.pop()

        for _ in range(2):
            if self.sarcomere_artists:
                self.sarcomere_artists.pop().remove()

        self._render_morphometry_results()
        self.canvas.draw_idle()
        self._update_status()

    def clear_all(self) -> None:
        """Clear all annotations and overlays for both modes."""
        self.pending_points.clear()
        self.segments.clear()
        self.midpoints.clear()

        self.sarcomere_points.clear()
        self.sarcomere_geometries.clear()

        self._clear_artists()
        self._clear_sarcomere_artists()

        self.annotating = False
        if self.confirm_btn:
            self.confirm_btn.config(state=DISABLED)
        self._set_text(self.summary_text, "All annotations cleared.")
        self._set_text(
            self.morphometry_text,
            "Single Sarcomere Morphometry\n"
            "============================\n\n"
            "Annotations cleared. Start new analysis.",
        )
        self.log.info("All annotations cleared")

    def _clear_sarcomere_artists(self) -> None:
        """Remove all sarcomere visualization artists."""
        for art in self.sarcomere_artists:
            try:
                art.remove()
            except (ValueError, AttributeError):
                pass
        self.sarcomere_artists.clear()
        self.canvas.draw_idle()

    def _clear_artists(self) -> None:
        """Remove all matplotlib artists drawn by the app."""
        for group in (
            self.temp_artists,
            self.segment_line_artists,
            self.midpoint_artists,
            self.midpoint_link_artists,
            self.text_artists,
        ):
            for art in group:
                art.remove()
            group.clear()
        self.canvas.draw_idle()

    def _reset_annotations(self) -> None:
        """Reset annotation state and remove all overlays."""
        self.pending_points.clear()
        self.segments.clear()
        self.midpoints.clear()
        self._clear_artists()
        if self.confirm_btn:
            self.confirm_btn.config(state=DISABLED)

    def analyze(self) -> None:
        """Run analysis on connected midpoints and display results/overlay."""
        if len(self.midpoints) < 2:
            messagebox.showwarning("Warning", "Need at least 2 segments for analysis.")
            return

        try:
            pts = np.array(self.midpoints, dtype=float)
            metrics: SequenceMetrics = analyze_segment_sequence(
                pts, pixel_to_unit=self.pixel_size, unit_name=self.unit_name
            )

            if self.image_path:
                metrics.metadata = metrics.metadata or {}
                metrics.metadata["image_file"] = str(self.image_path)
                metrics.metadata["n_original_segments"] = len(self.segments)

            self.current_analysis = metrics
            self.analysis_history.append(metrics)
            self._render_results(metrics)
            self._render_overlay(metrics)
            self.annotating = False
            self.log.info("Analysis completed using midpoint method")
        except Exception as e:
            self.log.exception("Analysis failed")
            messagebox.showerror("Error", f"Analysis failed:\n{e}")

    def _render_results(self, metrics: SequenceMetrics) -> None:
        """Update summary pane and segments table."""
        lengths = [s.length for s in metrics.segments]
        rel_angles_deg = [to_degrees(s.angle_relative) for s in metrics.segments[1:]]

        stats_block = f"""
MIDPOINT ANALYSIS RESULTS
=========================

Original Segments: {len(self.segments)}
Midpoint Connections: {len(metrics.segments)}

Length Statistics ({self.unit_name}):
-------------------------------------
Mean: {np.mean(lengths):.3f}
Std: {np.std(lengths):.3f}
CV: {np.std(lengths) / max(np.mean(lengths), 1e-9) * 100:.1f}%

Angular Distribution (relative angles):
---------------------------------------
Range: {np.ptp(rel_angles_deg) if rel_angles_deg else 0:.1f} deg
"""

        full_summary = metrics.summary_string().rstrip() + "\n\n" + stats_block.strip() + "\n"
        self._set_text(self.summary_text, full_summary)

        for row in self.segments_table.get_children():
            self.segments_table.delete(row)
        for seg in metrics.segments:
            self.segments_table.insert(
                "",
                tk.END,
                values=(
                    seg.segment_id,
                    f"{seg.length:.3f}",
                    f"{to_degrees(seg.angle_absolute):.1f}",
                    f"{to_degrees(seg.angle_relative):.1f}",
                    f"{to_degrees(seg.cumulative_angle):.1f}",
                ),
            )

        self._update_history_text()

    def _render_overlay(self, metrics: SequenceMetrics) -> None:
        """Add labels along midpoint connections showing IDs and lengths."""
        keep = []
        for art in self.text_artists:
            # Keep green-boxed per-segment labels above midpoints;
            # only the yellow overlay labels on midpoint connections are refreshed.
            if getattr(art, "_overlay", False):
                art.remove()
            else:
                keep.append(art)
        self.text_artists = keep

        for seg in metrics.segments:
            mx = (seg.start_point[0] + seg.end_point[0]) / 2.0
            my = (seg.start_point[1] + seg.end_point[1]) / 2.0
            label = f"M{seg.segment_id}\n{seg.length:.1f}{self.unit_name}"
            t = self.ax.text(
                mx,
                my,
                label,
                color="yellow",
                fontsize=8,
                ha="center",
                va="center",
                bbox={"boxstyle": "round,pad=0.3", "facecolor": "red", "alpha": 0.5},
            )
            t._overlay = True
            self.text_artists.append(t)

        self.canvas.draw_idle()

    def _update_history_text(self) -> None:
        """Refresh the analysis history pane (last 10 analyses)."""
        lines = ["ANALYSIS HISTORY\n================\n"]
        for i, a in enumerate(self.analysis_history[-10:], 1):
            lines.append(f"Analysis {i}:")
            lines.append(f"  Time: {a.timestamp}")
            if a.metadata and a.metadata.get("image_file"):
                lines.append(f"  Image: {Path(a.metadata['image_file']).name}")
            lines.append(f"  Segments: {a.n_segments}")
            lines.append(f"  Linearity: {a.linearity_ratio:.4f}")
            lines.append(f"  MVL: {a.mean_vector_length:.4f}")
            lines.append("-" * 40)
        self._set_text(self.history_text, "\n".join(lines))

    def apply_scale(self) -> None:
        """Apply pixel-to-unit scale from toolbar entries."""
        try:
            px = float(self.pixel_size_var.get())
            unit = self.unit_var.get().strip() or "um"
            if px <= 0:
                raise ValueError("Pixel size must be > 0")
            self.pixel_size = px
            self.unit_name = unit
            self._set_text(self.summary_text, f"Scale updated:\n1 pixel = {px} {unit}")
        except Exception as e:
            messagebox.showerror("Error", f"Invalid scale values:\n{e}")

    def export_results(self) -> None:
        """Export the latest analysis (JSON + CSVs + summary)."""
        if not self.current_analysis:
            messagebox.showwarning("Warning", "No analysis to export.")
            return
        outdir = filedialog.askdirectory(title="Select output directory")
        if not outdir:
            return
        try:
            output_dir = Path(outdir)
            output_dir.mkdir(parents=True, exist_ok=True)
            stamp = now_stamp()

            dump_json(self.current_analysis, output_dir / f"analysis_{stamp}.json")

            rows = []
            for seg in self.current_analysis.segments:
                rows.append(
                    {
                        "segment_id": seg.segment_id,
                        f"length_{self.unit_name}": seg.length,
                        "angle_degrees": to_degrees(seg.angle_absolute),
                        "relative_angle_degrees": to_degrees(seg.angle_relative),
                        "cumulative_angle_degrees": to_degrees(seg.cumulative_angle),
                    }
                )
            pd.DataFrame(rows).to_csv(output_dir / f"segments_{stamp}.csv", index=False)

            if self.segments:
                seg_rows = []
                for i, s in enumerate(self.segments, 1):
                    (x1, y1), (x2, y2) = s.p1, s.p2
                    mx, my = s.midpoint
                    seg_rows.append(
                        {
                            "segment_id": i,
                            "point1_x": x1,
                            "point1_y": y1,
                            "point2_x": x2,
                            "point2_y": y2,
                            "midpoint_x": mx,
                            "midpoint_y": my,
                        }
                    )
                pd.DataFrame(seg_rows).to_csv(
                    output_dir / f"original_segments_{stamp}.csv", index=False
                )

            with open(output_dir / f"summary_{stamp}.txt", "w", encoding="utf-8") as f:
                f.write(self.summary_text.get("1.0", "end-1c"))

            messagebox.showinfo("Success", f"Results exported to:\n{output_dir}")
        except Exception as e:
            self.log.exception("Export failed")
            messagebox.showerror("Error", f"Export failed:\n{e}")

    def export_history(self) -> None:
        """Export the analysis history as CSV."""
        if not self.analysis_history:
            messagebox.showwarning("Warning", "No history to export.")
            return
        outfile = filedialog.asksaveasfilename(
            title="Save history CSV", defaultextension=".csv", filetypes=[("CSV", ".csv")]
        )
        if not outfile:
            return
        try:
            rows = []
            for a in self.analysis_history:
                rows.append(
                    {
                        "timestamp": a.timestamp,
                        "image": a.metadata.get("image_file", "N/A") if a.metadata else "N/A",
                        "n_segments": a.n_segments,
                        f"total_length_{self.unit_name}": a.total_length,
                        f"head_to_tail_{self.unit_name}": a.head_to_tail_distance,
                        "linearity_ratio": a.linearity_ratio,
                        "mean_vector_length": a.mean_vector_length,
                        "circular_mean_degrees": to_degrees(a.circular_mean),
                    }
                )
            pd.DataFrame(rows).to_csv(outfile, index=False)
            messagebox.showinfo("Success", f"History exported to:\n{outfile}")
        except Exception as e:
            self.log.exception("Export history failed")
            messagebox.showerror("Error", f"Export history failed:\n{e}")

    def _set_text(self, widget: tk.Text, text: str) -> None:
        """Set text content of a read-only Text widget."""
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert(tk.END, text)
        widget.configure(state=tk.DISABLED)

    def _update_status(self) -> None:
        """Update status bar based on current annotation state and mode."""
        if not self.annotating:
            self.status_var.set("Press 'Start Annotating' to begin.")
            return

        if self.analysis_mode == AnalysisMode.SINGLE_SARCOMERE:
            self._update_sarcomere_status()
        else:
            self._update_linearity_status()

    def _update_linearity_status(self) -> None:
        """Update status for Myofibril Linearity mode."""
        seg_index = len(self.segments) + 1
        if len(self.pending_points) == 0:
            self.status_var.set(f"Click the first point of segment #{seg_index}.")
        elif len(self.pending_points) == 1:
            self.status_var.set(f"Click the second point of segment #{seg_index}.")
        elif len(self.pending_points) == 2:
            self.status_var.set(f"Segment #{seg_index} ready - press 'Confirm Segment' or 'S'.")

    def _update_sarcomere_status(self) -> None:
        """Update status for Single Sarcomere mode (6-point annotation)."""
        sarc_index = len(self.sarcomere_geometries) + 1
        n_points = len(self.sarcomere_points)

        point_names = [
            "A (Z-line 1 start)",
            "B (Z-line 1 end)",
            "C (M-line start)",
            "D (M-line end)",
            "E (Z-line 2 start)",
            "F (Z-line 2 end)",
        ]

        if n_points < 6:
            self.status_var.set(
                f"Sarcomere #{sarc_index}: Click point {point_names[n_points]} ({n_points + 1}/6)"
            )
        else:
            self.status_var.set(
                f"Sarcomere #{sarc_index} complete - press 'Confirm Segment' or 'S' to analyze."
            )

    def _show_shortcuts(self) -> None:
        """Display keyboard shortcuts dialog."""
        shortcuts = (
            "Keyboard Shortcuts\n"
            "===================\n"
            "S: Confirm current segment\n"
            "C: Connect midpoints\n"
            "A: Analyze\n"
            "Del: Delete last (pending points or last segment)\n"
            "Esc: Clear all\n"
            "Arrow Keys: Pan (hold Shift for faster)\n"
            "+ / = : Zoom in\n"
            "- : Zoom out\n"
            "0 : Reset view\n"
            "Mouse wheel: Zoom at cursor\n"
            "Right/Middle drag or Space+Left drag: Pan\n"
        )
        messagebox.showinfo("Keyboard Shortcuts", shortcuts)

    def _show_about(self) -> None:
        """Display application about dialog."""
        messagebox.showinfo(
            "About",
            "Sarcomeric Image Analysis Tool - Midpoint Method\n"
            "Modernized UI with ttkbootstrap.\n"
            "(c) 2025 - MIT License (example)\n",
        )


def main() -> None:
    """Launch the SarcoStat GUI application."""
    print(
        """
SARCOMERIC IMAGE ANALYSIS TOOL - MIDPOINT METHOD
Interactive Image Analysis with Segment Midpoint Connections

Workflow:
1. Load image (TIFF, JPEG, or PNG)
2. Start Annotating
3. Click two points to define a segment
4. Confirm the segment (button or 'S')
5. Repeat for all segments
6. Connect Midpoints ('C')
7. Analyze ('A')

Controls:
- Left-click: place points (2 per segment)
- 'S' or button: confirm current segment
- 'C': connect midpoints
- 'A': analyze
- Del: remove last point/segment
- Esc: clear all
- Arrow keys: pan (Shift = faster)
- +/= zoom in, - zoom out, 0 reset
- Mouse wheel: zoom at cursor
- Right/middle drag or Space+Left: pan
"""
    )
    # Default to Day theme; users can switch from the toolbar/menu
    icon_path = Path(__file__).parent / "logo.png"
    window_kwargs = {"themename": "litera", "title": "SarcoStat"}
    if icon_path.exists():
        window_kwargs["iconphoto"] = str(icon_path)
    root = tb.Window(**window_kwargs)
    SarcomericImageAnalyzerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
