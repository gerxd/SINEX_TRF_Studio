# ui/widgets.py
import time
import io
import tempfile
import json
import numpy as np
import pandas as pd
import logging
import html
import re
from pathlib import Path
from typing import Dict, List, NamedTuple, Optional, Any, Callable, Tuple

from PyQt6.QtGui import QFont, QPixmap, QImage, QColor, QPalette, QTextCursor, QTextCharFormat, QTextBlockFormat
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QComboBox, QFrame, QSplitter,
    QTextEdit, QPlainTextEdit, QListWidget, QListWidgetItem, QTableView,
    QDialog, QSpacerItem, QGridLayout, QStackedLayout, QApplication,
    QCheckBox, QDoubleSpinBox, QRadioButton, QScrollArea, QLineEdit,
    QSizePolicy, QGroupBox, QStyleFactory
)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QUrl, QStandardPaths, QObject
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings
import pyqtgraph as pg
import matplotlib
from matplotlib import cm, colors as mcolors
pg.setConfigOptions(imageAxisOrder="row-major", useOpenGL=True, antialias=False)
matplotlib.use("QtAgg")  # ensure Qt5 backend
import matplotlib.pyplot as plt
import seaborn as sns
import folium
from branca.element import Element, MacroElement
from jinja2 import Template
from plyer import notification
from .. import __version__

from ..core import (
    logger, benchmark, remember_dialog_dir, default_save_path, ensure_suffix,
    default_export_format, default_pos_threshold, default_vel_threshold,
)
from ..io import SinexBlockParser, MatrixEstimateParser
from ..io import export, figures, reader, library
from ..analysis import datum as datum_math
from ..analysis import normal as normal_math
from ..analysis import reporting


CONTROL_COLUMN_WIDTH = 470
CONTROL_COLUMN_MIN = 400


class StationsMapPage(QWebEnginePage):
    pass


EPISODE_JS = """
(function () {
var map = __MAP__;
var Ring = L.CircleMarker.extend({
  _project: function () {
    this._point = this._map.latLngToLayerPoint(this._latlng).add(this.options.offset);
    this._updateBounds();
  }
});
var groups = {kept: L.featureGroup(), filtered: L.featureGroup()};
var byCode = {};
var selected = null;
function look(m, on) {
  var size = on ? 14 : 10;
  m.options.offset = L.point(m.row.dx * size / 10, m.row.dy * size / 10);
  m.setStyle({radius: on ? 6 : 4.5, color: on ? "#111111" : "#ffffff", weight: on ? 2 : 1});
}
window.sinexData = function (rows) {
  groups.kept.clearLayers();
  groups.filtered.clearLayers();
  byCode = {};
  rows.forEach(function (r) {
    var m = new Ring([r.lat, r.lon], {offset: L.point(r.dx, r.dy), radius: 4.5, color: "#ffffff",
      weight: 1, opacity: 0.92, fillColor: r.color, fillOpacity: 0.92});
    m.row = r;
    m.bindPopup(r.popup, {maxWidth: 320});
    m.bindTooltip(r.label, {sticky: true});
    groups[r.filtered ? "filtered" : "kept"].addLayer(m);
    (byCode[r.code] = byCode[r.code] || []).push(m);
  });
  var code = selected;
  selected = null;
  sinexSelect(code);
};
window.sinexShow = function (kept, filtered) {
  if (kept) { groups.kept.addTo(map); } else { groups.kept.remove(); }
  if (filtered) { groups.filtered.addTo(map); } else { groups.filtered.remove(); }
};
window.sinexSelect = function (code, lat, lon) {
  (byCode[selected] || []).forEach(function (m) { look(m, false); });
  selected = code;
  var ms = byCode[code] || [];
  ms.forEach(function (m) { look(m, true); m.bringToFront(); });
  if (lat === undefined || lat === null) { return; }
  map.setView([lat, lon], 6, {animate: false});
  var shown = ms.filter(function (m) { return map.hasLayer(m); });
  if (shown.length) { shown[0].openPopup(map.layerPointToLatLng(shown[0]._point)); }
};
sinexData(__ROWS__);
sinexShow(__KEPT__, __FILTERED__);
sinexSelect(__SELECTED__);
})();
"""

###############################################################################
#Plot lifetime
###############################################################################
PLOT_FOOTER = "produced with SINEX TRF Studio - Dossas G. - IHU"


def add_plot_footer(fig):
    fig.text(0.99, 0.01, PLOT_FOOTER, ha="right", va="bottom",
             fontsize=8, style="italic", color="gray")


def set_current_figure(owner, fig=None):
    previous = getattr(owner, '_figure', None)
    if previous is not None:
        plt.close(previous)
    owner._figure = fig

###############################################################################
#Worker Thread
###############################################################################
class ParserWorker(QThread):
    finished = pyqtSignal()
    error = pyqtSignal(str)

    def __init__(self, filename: Path, block_parsers: Dict[str, SinexBlockParser], skip_epochs_block: bool = False, skip_validation: bool = False,
                 library_root=None, keep=False, entry=None):
        super().__init__()
        self.filename = filename
        self.library_root = library_root
        self.keep = keep
        self.entry = entry
        self.library_entry = None
        self.block_parsers = block_parsers
        self.skip_epochs_block = skip_epochs_block  # Store the setting
        self.skip_validation = skip_validation
        self.result_data = None
        self.parse_generation = None
        self.structure_error = False
        self.reused = False
    def run(self):
        try:
            if self.entry is not None:
                start = time.time()
                self.result_data = library.open_entry(self.entry)
                self.library_entry = self.entry
                self.reused = True
                library.touch(self.entry)
                library.log_loaded(self.result_data, Path(self.filename).name, self.entry, time.time() - start)
            else:
                status = {}
                self.result_data, self.library_entry = library.load(
                    self.filename, self.block_parsers, self.library_root, self.keep,
                    skip_epochs_block=self.skip_epochs_block, skip_validation=self.skip_validation,
                    parse_generation=self.parse_generation, check_structure=not self.skip_validation,
                    status=status,
                )
                self.reused = status.get("reused", False)
            self.finished.emit()
        except Exception as e:
            self.structure_error = isinstance(e, reader.SinexStructureError)
            self.error.emit(str(e))

_TASKS = set()


class _TaskSignals(QObject):
    busy = pyqtSignal(str)


TASK_SIGNALS = _TaskSignals()


class TaskWorker(QThread):
    def __init__(self, func, *args):
        super().__init__()
        self.func = func
        self.args = args
        self.result = None
        self.error = None

    def run(self):
        try:
            self.result = self.func(*self.args)
        except Exception as exc:
            self.error = exc


def run_task(func, args, on_done, label="Working"):
    worker = TaskWorker(func, *args)
    worker.label = label
    _TASKS.add(worker)
    QApplication.setOverrideCursor(Qt.CursorShape.BusyCursor)
    TASK_SIGNALS.busy.emit(label)

    def finish():
        _TASKS.discard(worker)
        QApplication.restoreOverrideCursor()
        TASK_SIGNALS.busy.emit(next(iter(_TASKS)).label if _TASKS else "")
        on_done(worker)

    worker.finished.connect(finish)
    worker.start()
    return worker


def wait_for_tasks():
    while _TASKS:
        for worker in list(_TASKS):
            worker.wait()
        QApplication.processEvents()

###############################################################################
#Custom Logger Widget
###############################################################################
TONES = {
    "ok": ("#15803d", "#4ade80"),
    "warn": ("#b45309", "#fbbf24"),
    "error": ("#c2410c", "#fb923c"),
    "muted": ("#6b7280", "#9ca3af"),
}
_OK_RE = re.compile(r"^(PASS|Success|Exported|Finished|Image exported)|passed check|\bcomplete\b|calculated$")
_WARN_RE = re.compile(r"^(WARNING|FAIL|Export failed)| error: |error occurred|Missing$|\(custom\)$")


def tone_of(text, levelno=logging.INFO):
    if text.startswith("===="):
        return None
    if levelno >= logging.ERROR:
        return "error"
    if levelno >= logging.WARNING or _WARN_RE.search(text):
        return "warn"
    if _OK_RE.search(text):
        return "ok"
    return None


def tone_color(tone, widget):
    dark = widget.palette().color(QPalette.ColorRole.Text).lightness() > 128
    return TONES[tone][dark]


def set_status(label, text, base=""):
    label.setText(text)
    tone = tone_of(text)
    label.setStyleSheet(base + (f" color: {tone_color(tone, label)};" if tone else ""))


class _LogSink(QObject):
    line = pyqtSignal(str, str)

    def __init__(self, widget):
        super().__init__()
        self.widget = widget
        self.line.connect(self.write, Qt.ConnectionType.QueuedConnection)

    def write(self, msg, tone):
        w = self.widget
        if w is None:
            return
        bar = w.verticalScrollBar()
        at_end = bar.value() >= bar.maximum()
        fmt = QTextCharFormat()
        if tone:
            fmt.setForeground(QColor(tone_color(tone, w)))
        doc = w.document()
        cursor = QTextCursor(doc)
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not doc.isEmpty():
            cursor.insertBlock(QTextBlockFormat(), fmt)
        cursor.insertText(msg, fmt)
        if at_end:
            bar.setValue(bar.maximum())


class QPlainTextEditLogger(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
        self._sink = _LogSink(text_widget)
        self.setLevel(logging.INFO)
        try:
            self.text_widget.destroyed.connect(self._on_widget_destroyed)
        except Exception:
            pass

    def _on_widget_destroyed(self):
        try:
            logger.removeHandler(self)
        except Exception:
            pass
        self.text_widget = None
        self._sink.widget = None

    def emit(self, record):
        #gui updates on main thread
        msg = self.format(record)
        if self.text_widget is None:
            return
        try:
            self._sink.line.emit(msg, tone_of(record.getMessage(), record.levelno) or "")
        except Exception:
            pass

def make_section_header(title: str, layout):
    label = QLabel(title)
    label.setStyleSheet("font-size: 15px; font-weight: bold;")
    layout.addWidget(label)
    rule = QFrame()
    rule.setFrameShape(QFrame.Shape.HLine)
    rule.setFrameShadow(QFrame.Shadow.Sunken)
    layout.addWidget(rule)


def make_log_panel(parent=None):
    container = QWidget(parent)
    panel_layout = QVBoxLayout(container)
    panel_layout.setContentsMargins(0, 0, 0, 0)
    label = QLabel("Log:")
    label.setStyleSheet("font-size: 15px; font-weight: bold;")
    panel_layout.addWidget(label)
    text = QPlainTextEdit()
    text.setReadOnly(True)
    text_font = text.font()
    text_font.setPointSize(12)
    text.setFont(text_font)
    panel_layout.addWidget(text)
    logger.addHandler(QPlainTextEditLogger(text))
    return container, text


###############################################################################
#Visualization Widget
###############################################################################
class MatrixVisualizerWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.blocks = {}  # store the parsed block data
        # keepp references to PG windows
        self._pg_img_win = None
        self._pg_hist_widget = None
        self._pg_view = None
        self._pg_img_item = None
        self._pg_plot_win = None  # for eigenvalue histogram
        self._pg_scene = None
        self._pg_hover_source = None
        self._pg_hover_meta = None
        self._pg_title_label = None
        self._pg_info_label = None
        self._pg_hover_label = None
        self._generation = 0
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setStyleSheet(
            "QPushButton {font-size: 14px;} "
            "QComboBox {font-size: 14px;} "
            "QLabel {font-size: 14px;}"
        )

        header_label = QLabel("Visualizer:")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header_label.setFont(header_font)
        layout.addWidget(header_label)

        self.info_label = QLabel("Load a SINEX file to enable plotting.")
        self.info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.info_label.setWordWrap(True)
        self.info_label.setMinimumWidth(0)
        self.info_label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        layout.addSpacing(6)
        layout.addWidget(self.info_label)

        layout.addStretch()

        matrix_row = QHBoxLayout()
        matrix_row.addWidget(QLabel("Matrix to Plot:"))
        self.matrix_combo = QComboBox()
        self.matrix_combo.setMinimumContentsLength(24)
        self.matrix_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.matrix_combo.setMinimumWidth(320)
        matrix_row.addWidget(self.matrix_combo)
        matrix_row.addStretch()
        layout.addLayout(matrix_row)

        type_row = QHBoxLayout()
        type_row.addWidget(QLabel("Plot Type:"))
        self.plot_type_combo = QComboBox()
        self.plot_type_combo.addItems(["Heatmap", "Eigenvalue Distribution"])
        self.plot_type_combo.currentTextChanged.connect(self._on_plot_type_changed)
        type_row.addWidget(self.plot_type_combo)
        type_row.addStretch()
        layout.addLayout(type_row)

        btn_font = self.font()
        btn_font.setPointSize(12)
        self.plot_button = QPushButton("Render plot")
        self.plot_button.clicked.connect(self.generate_and_show_plot)
        self.plot_button.setEnabled(False)
        self.plot_button.setMinimumHeight(40)
        self.plot_button.setFont(btn_font)

        self.display_mode_combo = QComboBox()
        self.display_mode_combo.addItems(["Positive", "Correlation"])

        self.ignore_diag_levels_checkbox = QCheckBox("Ignore diagonal for scaling")
        self.ignore_diag_levels_checkbox.setChecked(True)

        self.pg_plot_button = QPushButton("Render full 1:1 plot")
        self.pg_plot_button.clicked.connect(self.generate_and_show_plot_pg)
        self.pg_plot_button.setEnabled(False)
        self.pg_plot_button.setMinimumHeight(40)
        self.pg_plot_button.setFont(btn_font)

        self.save_full_btn = QPushButton("Save full 1:1 plot")
        self.save_full_btn.clicked.connect(self.save_full_res_image)
        self.save_full_btn.setEnabled(False)
        self.save_full_btn.setMinimumHeight(36)
        self.save_full_btn.setFont(btn_font)

        sections_layout = QVBoxLayout()

        mpl_frame = QFrame()
        mpl_frame.setFrameShape(QFrame.Shape.StyledPanel)
        mpl_layout = QVBoxLayout(mpl_frame)
        mpl_header_row = QHBoxLayout()
        mpl_title = QLabel("Matplotlib")
        mpl_title_font = mpl_title.font()
        mpl_title_font.setBold(True)
        mpl_title.setFont(mpl_title_font)
        mpl_header_row.addWidget(mpl_title)
        mpl_header_row.addStretch()
        mpl_layout.addLayout(mpl_header_row)
        mpl_layout.addWidget(self.plot_button)
        sections_layout.addWidget(mpl_frame)

        pg_frame = QFrame()
        pg_frame.setFrameShape(QFrame.Shape.StyledPanel)
        pg_layout = QVBoxLayout(pg_frame)
        pg_header_row = QHBoxLayout()
        pg_title = QLabel("PyQtGraph")
        pg_title_font = pg_title.font()
        pg_title_font.setBold(True)
        pg_title.setFont(pg_title_font)
        pg_header_row.addWidget(pg_title)
        pg_header_row.addStretch()
        pg_layout.addLayout(pg_header_row)

        pg_note = QLabel("use for very large matrices")
        pg_note.setStyleSheet("font-size: 12px;")
        pg_layout.addWidget(pg_note)

        pg_controls_row = QHBoxLayout()
        pg_controls_row.addWidget(QLabel("Display Scaling:"))
        pg_controls_row.addWidget(self.display_mode_combo)
        pg_controls_row.addWidget(self.ignore_diag_levels_checkbox)
        pg_controls_row.addStretch()
        pg_layout.addLayout(pg_controls_row)

        pg_buttons_row = QHBoxLayout()
        pg_buttons_row.addWidget(self.pg_plot_button)
        pg_buttons_row.addWidget(self.save_full_btn)
        pg_layout.addLayout(pg_buttons_row)
        sections_layout.addWidget(pg_frame)

        layout.addLayout(sections_layout)
        layout.addStretch(1)
        self._has_plottable_matrix = False

        self.setLayout(layout)
        for frame in (mpl_frame, pg_frame):
            frame.setMinimumHeight(frame.sizeHint().height())
        self.setMinimumHeight(self.sizeHint().height())

    def _format_cbar_heatmap(self, ax):
        import numpy as np
        from matplotlib.ticker import FormatStrFormatter, AutoMinorLocator

        if not ax.collections:
            return
        cbar = ax.collections[0].colorbar
        if cbar is None:
            return

        decimals = 3
        nbins = 12
        minor_div = 2

        cbar.set_label("Value", rotation=90, labelpad=12)
        mappable = ax.collections[0]
        vmin, vmax = mappable.get_clim()
        if vmin == vmax:
            vmax = vmin + 1e-12
        ticks = np.linspace(vmin, vmax, nbins)
        cbar.set_ticks(ticks)
        cbar.formatter = FormatStrFormatter(f"%.{decimals}e")

        if minor_div > 1:
            cbar.ax.minorticks_on()
            cbar.ax.yaxis.set_minor_locator(AutoMinorLocator(minor_div))

        cbar.update_ticks()

    def setup_data(self, blocks: dict):
        # for asynchronous processing
        set_current_figure(self)
        self._generation += 1
        self._disconnect_pg_hover()
        for win in (self._pg_img_win, self._pg_plot_win):
            if win is not None:
                win.close()
        self._pg_img_win = self._pg_plot_win = self._pg_img_item = None
        self._pg_hist_widget = self._pg_view = None
        self._pg_title_label = self._pg_info_label = self._pg_hover_label = None
        self._pg_hover_source = None
        self._pg_hover_meta = None
        self.blocks = blocks
        self.matrix_combo.clear()
        matrix_keys = [k for k, v in blocks.items() if isinstance(v, np.ndarray) and v.ndim == 2]
        self.matrix_combo.addItems(matrix_keys)
        has_matrix = bool(matrix_keys)
        self._has_plottable_matrix = has_matrix
        self.plot_button.setEnabled(has_matrix)
        self.pg_plot_button.setEnabled(has_matrix)
        self._update_plot_buttons_state()

        set_status(self.info_label, 
            "Select a matrix and plot type, then choose a plotting option."
            if has_matrix
            else "No plottable matrix data found in this file."
        )

    def _on_plot_type_changed(self, text: str):
        self._update_plot_buttons_state()

    def _update_plot_buttons_state(self):
#update button states to prevent pqtgraph being used for eigens
        is_eigenvalue = self.plot_type_combo.currentText() == "Eigenvalue Distribution"
        allow_save = self._has_plottable_matrix and not is_eigenvalue
        self.save_full_btn.setEnabled(allow_save)
        self.pg_plot_button.setEnabled(self._has_plottable_matrix and not is_eigenvalue)

    @staticmethod
    def _display_mode_label(mode: str) -> str:
        return mode.title()

    def _resolve_display_mode(self, selected_key: str, matrix: np.ndarray) -> str:
        _ = selected_key, matrix
        selected_mode = self.display_mode_combo.currentText().strip().lower()
        if selected_mode in {"positive", "correlation"}:
            return selected_mode
        return "positive"

    @staticmethod
    def _sample_for_levels(matrix: np.ndarray, ignore_diagonal: bool = False) -> np.ndarray:
        arr = np.asarray(matrix, dtype=np.float64)
        if arr.ndim != 2:
            return np.asarray([], dtype=np.float64)

        max_points = 2_000_000
        if arr.size > max_points:
            step = int(np.ceil(np.sqrt(arr.size / max_points)))
            arr = arr[::step, ::step]

        flat = arr.ravel()
        if ignore_diagonal and arr.shape[0] == arr.shape[1] and arr.shape[0] > 1:
            diag_idx = np.arange(0, arr.size, arr.shape[1] + 1)
            flat = np.delete(flat, diag_idx)

        return flat[np.isfinite(flat)]

    def generate_and_show_plot(self):
        # async
        selected_key = self.matrix_combo.currentText()
        plot_type = self.plot_type_combo.currentText()

        data = self.blocks.get(selected_key)

        if data is None:
            QMessageBox.warning(self, "No Data", "Selected matrix data is not available.")
            return

        if plot_type == "Heatmap" and data.size > 5000 * 5000:
            QMessageBox.warning(
                self, "Matrix too large for this plot",
                f"Use Render Plot instead, which draws one screen pixel per element."
            )
            return

        if data.size > 5000 * 5000:
            reply = QMessageBox.question(self, 'Large Matrix Warning',
                                         f"The selected matrix is very large ({data.shape}).\n"
                                         "Plot generation may be slow. Continue?",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.No:
                return

        set_status(self.info_label, f"Generating '{plot_type}' for '{selected_key}'...")
        QApplication.processEvents()  # force ui update
        set_current_figure(self)
        try:
            title = f"{plot_type} for {selected_key}\nShape: {data.shape}"

            if plot_type == "Heatmap":
                fig, ax = plt.subplots(figsize=(10, 8))
                set_current_figure(self, fig)
                sns.heatmap(
                    data,
                    ax=ax,
                    cmap="inferno",
                    cbar=True,
                    cbar_kws={"label": "Value"},
                )
                ax.set_title(title)
                fig.tight_layout()
                self._format_cbar_heatmap(ax)
            elif plot_type == "Eigenvalue Distribution":
                self.plot_button.setEnabled(False)
                self.pg_plot_button.setEnabled(False)
                gen = self._generation
                run_task(np.linalg.eigvals, (data,),
                         lambda w: self._show_eigen_plot(w, gen, selected_key, data.shape),
                         "Computing eigenvalues")
                return

            ax.set_title(title)
            fig.tight_layout()
            add_plot_footer(fig)

            plt.show()

            set_status(self.info_label, "Plot generation complete. Select another plot or load a new file.")

        except Exception as e:
            logger.exception("Plotting error")
            set_status(self.info_label, f"An error occurred during plotting: {e}")
            QMessageBox.critical(self, "Plotting Error", f"Failed to generate plot: {e}")

    def _show_eigen_plot(self, worker, gen, selected_key, shape):
        self.plot_button.setEnabled(self._has_plottable_matrix)
        self._update_plot_buttons_state()
        if gen != self._generation:
            logger.info("Ignoring eigenvalues computed for a previous file.")
            return
        try:
            if worker.error is not None:
                raise worker.error
            vals = worker.result
            real_vals = np.real(vals)
            
            fig, ax = plt.subplots(figsize=(16, 10))
            set_current_figure(self, fig)
            bars = ax.bar(range(len(real_vals)), real_vals, color="#4c72b0")
            ax.axhline(0.0, color="black", linewidth=0.8)
            ax.set_xlabel("Eigenvalue Index")
            ax.set_ylabel("Eigenvalue")
            ax.set_title(f"Eigenvalue Distribution for {selected_key}\nShape: {shape}")
            
            # Add value labels on bars for smaller datasets
            if len(real_vals) <= 20:
                ax.bar_label(bars, fmt="%.4g", padding=3)
            
            fig.tight_layout()
            add_plot_footer(fig)
            plt.show()
            set_status(self.info_label, "Plot generation complete. Select another plot or load a new file.")

        except Exception as e:
            logger.exception("Plotting error")
            set_status(self.info_label, f"An error occurred during plotting: {e}")
            QMessageBox.critical(self, "Plotting Error", f"Failed to generate plot: {e}")

    @staticmethod
    def _auto_positive_levels(A: np.ndarray, ignore_diagonal: bool = False) -> tuple:
        a = MatrixVisualizerWidget._sample_for_levels(A, ignore_diagonal=ignore_diagonal)
        if a.size == 0:
            return (0.0, 1.0)
        vmax = float(np.percentile(a, 99.0))
        if vmax <= 0:
            vmax = float(a.max(initial=1.0))
        return (0.0, vmax)

    @staticmethod
    def _pg_colormap_from_matplotlib(name: str) -> pg.ColorMap:
        mpl_cmap = matplotlib.colormaps[name]
        positions = np.linspace(0.0, 1.0, 256)
        colors = (mpl_cmap(positions) * 255).astype(np.ubyte)
        return pg.ColorMap(positions, colors)

    def _current_filename(self) -> str:
        host = self.window()
        if hasattr(host, "current_data") and host.current_data:
            filename = host.current_data.get("metadata", {}).get("filename", "")
            if filename:
                return Path(filename).name
        return "Unknown file"

    def _build_heatmap_state(self, selected_key: str, matrix: np.ndarray, full_resolution: bool = False) -> Tuple[np.ndarray, pg.ColorMap, dict]:
        mode = self._resolve_display_mode(selected_key, matrix)
        ignore_diagonal = self.ignore_diag_levels_checkbox.isChecked()

        if mode == "correlation":
            levels = (-1.0, 1.0)
            scale_policy = "fixed [-1, 1]"
            cmap_name = "RdBu_r"
        else:
            levels = self._auto_positive_levels(matrix, ignore_diagonal=ignore_diagonal)
            scale_policy = "0 to 99th percentile"
            cmap_name = "viridis"

        preview_matrix = np.asarray(matrix, dtype=np.float32)
        # ==========================================================================
        #preview_matrix = np.asarray(matrix, dtype=np.float32)
        source_shape = tuple(int(v) for v in matrix.shape)
        display_shape = source_shape
        metadata = {
            "matrix_name": selected_key,
            "filename": self._current_filename(),
            "mode": mode,
            "mode_label": self._display_mode_label(mode),
            "levels": levels,
            "scale_policy": scale_policy,
            "colormap": cmap_name,
            "ignore_diagonal_for_scaling": ignore_diagonal,
            "source_shape": source_shape,
            "display_shape": display_shape,
            "display_step": 1,
            "full_resolution": full_resolution,
        }
        return preview_matrix, self._pg_colormap_from_matplotlib(cmap_name), metadata

    def _format_pg_title_text(self, metadata: dict) -> str:
        return f"Matrix: {metadata['matrix_name']} | File: {metadata['filename']}"

    def _format_preview_text(self, metadata: dict) -> str:
        source_rows, source_cols = metadata["source_shape"]
        diagonal_text = "Yes" if metadata["ignore_diagonal_for_scaling"] else "No"
        return (
            f"Dimensions: {source_rows}x{source_cols} | "
            f"Scaling: {metadata['mode_label']} ({metadata['scale_policy']}) | "
            f"Diagonals ignored: {diagonal_text}"
        )

    @staticmethod
    def _safe_name_fragment(text: str) -> str:
        return (
            str(text)
            .replace("/", "_")
            .replace("\\", "_")
            .replace(" ", "_")
            .replace(":", "_")
        )

    @staticmethod
    def _default_pg_hover_text() -> str:
        return "Hover: row -, col -, value -"

    def _disconnect_pg_hover(self):
        if self._pg_scene is None:
            return
        try:
            self._pg_scene.sigMouseMoved.disconnect(self._handle_pg_hover)
        except (TypeError, RuntimeError):
            pass
        self._pg_scene = None
        self._pg_hover_source = None
        self._pg_hover_meta = None

    def _handle_pg_hover(self, pos):
        if self._pg_img_item is None or self._pg_hover_source is None or self._pg_hover_meta is None:
            return
        try:
            if not self._pg_img_item.sceneBoundingRect().contains(pos):
                if self._pg_hover_label is not None:
                    self._pg_hover_label.setText(self._default_pg_hover_text())
                return

            point = self._pg_img_item.mapFromScene(pos)
            row = int(np.floor(point.y()))
            col = int(np.floor(point.x()))
            display_rows, display_cols = self._pg_hover_meta["display_shape"]
            if row < 0 or col < 0 or row >= display_rows or col >= display_cols:
                if self._pg_hover_label is not None:
                    self._pg_hover_label.setText(self._default_pg_hover_text())
                return

            stride = self._pg_hover_meta["display_step"]
            src_row = min(row * stride, self._pg_hover_source.shape[0] - 1)
            src_col = min(col * stride, self._pg_hover_source.shape[1] - 1)
            value = self._pg_hover_source[src_row, src_col]
            if self._pg_hover_label is not None:
                self._pg_hover_label.setText(
                    f"Hover: row {src_row}, col {src_col}, value {float(value):.6e}"
                )
        except Exception:
            if self._pg_hover_label is not None:
                self._pg_hover_label.setText(self._default_pg_hover_text())

    def generate_and_show_plot_pg(self):
        selected_key = self.matrix_combo.currentText()
        plot_type = self.plot_type_combo.currentText()
        A = self.blocks.get(selected_key)

        if A is None:
            QMessageBox.warning(self, "No Data", "Selected matrix data is not available.")
            return

        set_status(self.info_label, 
            f"Rendering '{plot_type}' for '{selected_key}' (PyQtGraph)"
        )
        QApplication.processEvents()

        try:
            if plot_type == "Heatmap":
                self._disconnect_pg_hover()
                M32, cmap, metadata = self._build_heatmap_state(selected_key, A, full_resolution=False)
                vmin, vmax = metadata["levels"]

                # PG window with HistogramLUT + ImageItem
                self._pg_hist_widget = pg.HistogramLUTWidget()
                self._pg_view = pg.GraphicsLayoutWidget()
                vb = self._pg_view.addViewBox(lockAspect=True, enableMenu=False)
                vb.setMouseEnabled(x=True, y=True)
            
                self._pg_img_item = pg.ImageItem(axisOrder="row-major")
                #self._pg_img_item = pg.ImageItem(axisOrder="row-major")
                vb.addItem(self._pg_img_item)
                vb.invertY(True)

                # Link histogram and set colormap on the histogram gradient
                self._pg_hist_widget.setImageItem(self._pg_img_item)
                self._pg_hist_widget.gradient.setColorMap(cmap)

                # Also set LUT on the image (ensures color)
                lut = cmap.getLookupTable(0.0, 1.0, 256)
                self._pg_img_item.setLookupTable(lut)

                # Set image and levels
                self._pg_img_item.setImage(M32, autoLevels=False, levels=(vmin, vmax))

                # Show window
                win = pg.Qt.QtWidgets.QWidget()
                win.setWindowTitle(
                    f"PyQtGraph: {selected_key} — {metadata['filename']}"
                )
                outer = pg.Qt.QtWidgets.QVBoxLayout(win)
                self._pg_title_label = QLabel(self._format_pg_title_text(metadata))
                title_font = self._pg_title_label.font()
                title_font.setBold(True)
                self._pg_title_label.setFont(title_font)
                self._pg_title_label.setWordWrap(True)
                outer.addWidget(self._pg_title_label)
                self._pg_info_label = QLabel(self._format_preview_text(metadata))
                self._pg_info_label.setWordWrap(True)
                outer.addWidget(self._pg_info_label)
                h = pg.Qt.QtWidgets.QHBoxLayout()
                h.addWidget(self._pg_hist_widget, stretch=0)
                h.addWidget(self._pg_view, stretch=1)
                outer.addLayout(h, stretch=1)
                self._pg_hover_label = QLabel(self._default_pg_hover_text())
                self._pg_hover_label.setWordWrap(True)
                outer.addWidget(self._pg_hover_label)
                win.resize(1200, 800)
                win.show()
                self._pg_img_win = win
                self._pg_scene = self._pg_view.scene()
                self._pg_hover_source = A
                self._pg_hover_meta = metadata
                if self._pg_scene is not None:
                    self._pg_scene.sigMouseMoved.connect(self._handle_pg_hover)

            else:
                self._disconnect_pg_hover()
                self.plot_button.setEnabled(False)
                self.pg_plot_button.setEnabled(False)
                gen = self._generation
                run_task(np.linalg.eigvalsh, (A,),
                         lambda w: self._show_eigen_plot_pg(w, gen, selected_key),
                         "Computing eigenvalues")
                return

            set_status(self.info_label, "PyQtGraph rendering complete.")
        except Exception as e:
            logger.exception("PyQtGraph plotting error")
            set_status(self.info_label, f"PyQtGraph plotting error: {e}")
            QMessageBox.critical(self, "PyQtGraph Error", f"Failed to render with PyQtGraph: {e}")

    def _show_eigen_plot_pg(self, worker, gen, selected_key):
        self.plot_button.setEnabled(self._has_plottable_matrix)
        self._update_plot_buttons_state()
        if gen != self._generation:
            logger.info("Ignoring eigenvalues computed for a previous file.")
            return
        try:
            if worker.error is not None:
                raise worker.error
            vals = worker.result
            v = np.real(vals)
            y, x = np.histogram(v, bins=150)
            x_centers = 0.5 * (x[:-1] + x[1:])

            self._pg_plot_win = pg.plot(
                x_centers,
                y,
                pen=None,
                symbol=None,
                title=f"Eigenvalue Distribution — {selected_key}",
            )
            self._pg_plot_win.setLabel("left", "Frequency")
            self._pg_plot_win.setLabel("bottom", "Eigenvalue")
            bar = pg.BarGraphItem(x=x_centers, height=y, width=x[1] - x[0])
            self._pg_plot_win.addItem(bar)
            self._pg_title_label = None
            self._pg_info_label = None
            self._pg_hover_label = None

            set_status(self.info_label, "PyQtGraph rendering complete.")
        except Exception as e:
            logger.exception("PyQtGraph plotting error")
            set_status(self.info_label, f"PyQtGraph plotting error: {e}")
            QMessageBox.critical(self, "PyQtGraph Error", f"Failed to render with PyQtGraph: {e}")

    def save_full_res_image(self):
#export full res image with 1:1 pixel mapping using pqtgraph
        selected_key = self.matrix_combo.currentText()
        plot_type = self.plot_type_combo.currentText()
        A = self.blocks.get(selected_key)

        if A is None:
            QMessageBox.warning(self, "No Data", "Selected matrix data is not available.")
            return
        if plot_type == "Eigenvalue Distribution":
            QMessageBox.information(
                self, "Not Applicable",
                "Full-resolution image export is only available for 2D heatmap matrices."
            )
            return

        M = A
        #M32 = np.asarray(M, dtype=np.float32) removed with v1.1
        #n, m = M32.shape removed with v1.1
        n, m = M.shape
        
        
        reply = QMessageBox.question(
            self,
            "Confirm Export",
            (
                "Proceed with export?\n\n"
                "Large matrices may take some time to export."
            ),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return


        M32, cmap, export_metadata = self._build_heatmap_state(selected_key, A, full_resolution=True)
        # ==========================================================================
        #_, _, export_metadata = self._build_heatmap_state(selected_key, A, full_resolution=True) - removed with v1.1
        source_tag = self._safe_name_fragment(Path(export_metadata["filename"]).stem)
        matrix_tag = self._safe_name_fragment(selected_key)
        plot_tag = self._safe_name_fragment(plot_type)
        mode_tag = export_metadata["mode"].replace(" ", "_")
        default_name = (
            f"{source_tag}_{matrix_tag}_{plot_tag}_{mode_tag}_fullres.png"
        )
        fname, _ = QFileDialog.getSaveFileName(
            self,
            "Save Full-Resolution Image",
            default_save_path(default_name),
            "PNG Files (*.png);;TIFF Files (*.tiff *.tif);;All Files (*)",
        )
        if not fname:
            return
        remember_dialog_dir(fname)

        original_text = self.info_label.text()
        set_status(self.info_label, f"Exporting {n}×{m} image to {Path(fname).name}...")
        QApplication.processEvents()

        try:
            t_start = time.time()
            
            # Always create a new scene for export to ensure reliability
            # Scene reuse was causing issues when switching between different matrices
            logger.info("Creating off-screen scene for export")

            vmin, vmax = export_metadata["levels"]

            # M32, cmap, export_metadata = self._build_heatmap_state(selected_key, M, full_resolution=True) removed with v1.1
            # vmin, vmax = export_metadata["levels"] removed with v1.1

            lut = cmap.getLookupTable(0.0, 1.0, 256)
            logger.info(f"Exporting {n}×{m} matrix image to {fname}")
            argb, alpha = pg.makeARGB(M32, lut=lut, levels=(vmin, vmax))
            if not pg.makeQImage(argb, alpha, transpose=False).save(fname):
                raise OSError(f"the image could not be written to {fname}")

            elapsed = time.time() - t_start # metrics
            
            set_status(self.info_label, f"Image exported to {Path(fname).name} ({elapsed:.1f}s)")
            logger.info(
                f"Image export completed in {elapsed:.1f}s"
            )

        except MemoryError:
            error_msg = (
                f"Insufficient memory to export {n}×{m} image.\n\n"
                "Try:\n"
                "• Closing other applications\n"
                "• Exporting a smaller matrix\n"
                "• Using a system with more RAM"
            )
            logger.error(f"MemoryError during image export: {n}×{m} matrix")
            set_status(self.info_label, "Export failed: insufficient memory")
            QMessageBox.critical(self, "Memory Error", error_msg)
            
        except Exception as e:
            logger.exception("Full-resolution image export error")
            set_status(self.info_label, f"Export failed: {type(e).__name__}")
            QMessageBox.critical(
                self, "Export Error",
                f"Failed to export image:\n\n{type(e).__name__}: {e}"
            )
        
        finally:
            if 'original_text' in locals():
                from PyQt6.QtCore import QTimer
                QTimer.singleShot(3000, lambda: set_status(self.info_label, original_text))

class StationsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stations_data = []
        self.episodes = []
        self.excluded = set()
        self.labels = {}
        self.filtered_codes = set()
        self._selected_station_code = None
        self._geojson_cache = {}
        self._map_html_path = None
        self._stale_map_html_paths = []
        self._map_ready = False
        self._pending_js = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        upper_layout = QHBoxLayout()
        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.station_list = QListWidget()
        self._list_style = QStyleFactory.create('Fusion')
        self.station_list.setStyle(self._list_style)
        self.info_panel = QWidget()
        self.info_panel.setMaximumWidth(240)
        self.init_info_panel()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self._rebuild_map)
        self.map_view = QWebEngineView()
        self._configure_map_view()
        self.station_list.setMinimumWidth(50)
        self.station_list.setMaximumWidth(120)
        self.station_list.itemClicked.connect(self.on_click)

        vis_layout = QHBoxLayout()
        self.show_kept_chk = QCheckBox("Kept Episodes")
        self.show_kept_chk.setChecked(True)
        self.show_kept_chk.toggled.connect(self._show_groups)
        self.show_filtered_chk = QCheckBox("Filtered Episodes")
        self.show_filtered_chk.setChecked(True)
        self.show_filtered_chk.toggled.connect(self._show_groups)
        self.discontinuity_btn = QPushButton("Load discontinuity list")
        self.discontinuity_label = QLabel("")
        vis_layout.addWidget(self.show_kept_chk)
        vis_layout.addWidget(self.show_filtered_chk)
        vis_layout.addStretch(1)
        vis_layout.addWidget(self.discontinuity_label)
        vis_layout.addWidget(self.discontinuity_btn)

        self.splitter.addWidget(self.station_list)
        self.splitter.addWidget(self.info_panel)
        self.splitter.addWidget(self.map_view)
        self.map_view.setMinimumWidth(420)
        self.splitter.setSizes([100, 200, 1200])
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 0)
        self.splitter.setStretchFactor(2, 1)
        upper_layout.addWidget(self.refresh_button)
        upper_layout.addLayout(vis_layout)
        layout.addLayout(upper_layout)
        layout.addWidget(self.splitter)
        self.setLayout(layout)

    def _set_map_message(self, heading: str, details: str = ""):
        details_html = f"<p>{html.escape(details)}</p>" if details else ""
        self.map_view.setHtml(
            "<html><body>"
            f"<h3>{html.escape(heading)}</h3>"
            f"{details_html}"
            "</body></html>"
        )

    def _queue_map_html_cleanup(self):
        self._map_ready = False
        self._pending_js = []
        if self._map_html_path is None:
            return

        self._stale_map_html_paths.append(self._map_html_path)
        self._map_html_path = None

    def _prune_map_html(self):
        if not self._stale_map_html_paths:
            return

        remaining_paths = []
        for map_path in self._stale_map_html_paths:
            try:
                map_path.unlink(missing_ok=True)
            except OSError:
                remaining_paths.append(map_path)

        self._stale_map_html_paths = remaining_paths

    def _on_map_load_finished(self, ok: bool):
        self._prune_map_html()
        if ok and self._map_html_path is not None:
            self._map_ready = True
            for js in self._pending_js:
                self.map_page.runJavaScript(js)
            self._pending_js = []

    def _map_js(self, js):
        if self._map_ready:
            self.map_page.runJavaScript(js)
        else:
            self._pending_js.append(js)

    def _rebuild_map(self):
        self._queue_map_html_cleanup()
        self.update_station_map()

    def _show_groups(self):
        if self._map_html_path is None:
            self.update_station_map()
            return
        self._map_js(f"sinexShow({json.dumps(self.show_kept_chk.isChecked())}, "
                     f"{json.dumps(self.show_filtered_chk.isChecked())})")

    def init_info_panel(self):
        layout = QVBoxLayout(self.info_panel)
        self.title = QLabel('Station Info:')
        self.display = QTextEdit()
        self.display.setReadOnly(True)
        self.display.setText("select a station")
        layout.addWidget(self.title)
        layout.addWidget(self.display)

    def _configure_map_view(self):
        temp_root = Path(
            QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
            or tempfile.gettempdir()
        )
        self._map_html_dir = temp_root / "sinex_studio_offline_folium_map"
        self._map_html_dir.mkdir(parents=True, exist_ok=True)

        self._map_assets_dir = Path(__file__).resolve().parent / "map_assets"
        self._leaflet_js_path = self._map_assets_dir / "leaflet" / "leaflet.js"
        self._leaflet_css_path = self._map_assets_dir / "leaflet" / "leaflet.css"
        self._jquery_js_path = self._map_assets_dir / "jquery-3.7.1.min.js"
        self._land_geojson_path = self._map_assets_dir / "ne_50m_land.geojson"
        self._coastline_geojson_path = self._map_assets_dir / "ne_50m_coastline.geojson"
        self._boundary_geojson_path = self._map_assets_dir / "ne_50m_admin_0_boundary_lines_land.geojson"

        self.map_page = StationsMapPage(self.map_view)
        self.map_view.setPage(self.map_page)
        self.map_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True
        )
        self.map_view.settings().setAttribute(
            QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False
        )
        self.map_view.loadFinished.connect(self._on_map_load_finished)

    def set_excluded_episodes(self, episodes: set):
        self.excluded = set(episodes or [])
        self.filtered_codes = {ep[0] for ep in self.excluded}
        self.populate_station_list()

    def set_data(self, stations_data: List[dict], episodes=()):
        self.stations_data = stations_data
        self.episodes = list(episodes)
        #new stations -> new file -> ignore past filtering data
        self.excluded = set()
        self.filtered_codes = set()

        available_codes = {station.get("code") for station in stations_data}
        if self._selected_station_code not in available_codes:
            self._selected_station_code = None
        if self._selected_station_code is None and stations_data:
            self._selected_station_code = min(str(s.get("code", "????")) for s in stations_data)
        if self._selected_station_code is None:
            self.display.setText("select a station")

        self.populate_station_list()
        self._rebuild_map()

    def populate_station_list(self):
        self.station_list.clear()
        for station in sorted(self.stations_data, key=lambda s: str(s.get("code", "????"))):
            code = station.get("code", "????")
            item = QListWidgetItem(code)
            item.setForeground(
                QColor("#c0392b") if code in self.filtered_codes else QColor("#2e8b57")
            )
            self.station_list.addItem(item)

        if self._selected_station_code is not None:
            self._set_current_station_item(self._selected_station_code)

    def _local_asset_url(self, asset_path: Path) -> str:
        return QUrl.fromLocalFile(str(asset_path.resolve())).toString()

    def _load_geojson_asset(self, asset_path: Path):
        cache_key = str(asset_path)
        if cache_key not in self._geojson_cache:
            self._geojson_cache[cache_key] = json.loads(
                asset_path.read_text(encoding="utf-8")
            )
        return self._geojson_cache[cache_key]

    def _episode_key(self, ep):
        return ep["code"], ep["pt"], ep["soln"]

    def _episode_lines(self, ep):
        label = self.labels.get(self._episode_key(ep)) or {}
        lines = [ep["label"], "Filtered" if self._episode_key(ep) in self.excluded else "Kept"]
        if label.get("span"):
            lines.append(f"Span: {label['span']}")
        if label.get("break"):
            lines.append(f"Break: {label['break']}")
        return lines

    def _visible_stations(self, show_kept: bool, show_filtered: bool) -> List[dict]:
        visible = []
        for station in self.episodes:
            is_filtered = self._episode_key(station) in self.excluded
            if is_filtered and not show_filtered:
                continue
            if (not is_filtered) and not show_kept:
                continue
            visible.append(station)
        return visible

    def _marker_rows(self) -> List[dict]:
        groups = {}
        for ep in self.episodes:
            groups.setdefault((round(ep["latitude"], 2), round(ep["longitude"], 2)), []).append(ep["label"])
        rows = []
        for ep in self.episodes:
            lat, lon = ep["latitude"], ep["longitude"]
            members = groups[(round(lat, 2), round(lon, 2))]
            dx = dy = 0.0
            if len(members) > 1:
                angle = 2 * np.pi * members.index(ep["label"]) / len(members) - np.pi / 2
                ring = 10 * max(0.7, 0.18 * len(members))
                dx, dy = ring * np.cos(angle), ring * np.sin(angle)
            is_filtered = self._episode_key(ep) in self.excluded
            first, *rest = self._episode_lines(ep)
            lines = [f"<b>{html.escape(first)}</b>"] + [html.escape(t) for t in rest]
            lines.append(f"Lat: {lat:.4f}<br>Lon: {lon:.4f}")
            rows.append({"lat": lat, "lon": lon, "dx": float(dx), "dy": float(dy),
                         "code": ep["code"], "label": ep["label"], "filtered": is_filtered,
                         "color": "#c0392b" if is_filtered else "#2e8b57", "popup": "<br>".join(lines)})
        return rows

    def _rows_json(self) -> str:
        return json.dumps(self._marker_rows()).replace("</", "<\\/")

    def _build_offline_folium_map(
        self,
        visible_stations: List[dict],
        focus_selected: bool = False,
    ):
        lat_avg = sum(station["latitude"] for station in visible_stations) / len(visible_stations)
        lon_avg = sum(station["longitude"] for station in visible_stations) / len(visible_stations)

        fol_map = folium.Map(
            location=[lat_avg, lon_avg],
            zoom_start=3,
            tiles=None,
            control_scale=True,
            prefer_canvas=True,
        )
        fol_map.default_js = [
            ("leaflet", self._local_asset_url(self._leaflet_js_path)),
            ("jquery", self._local_asset_url(self._jquery_js_path)),
        ]
        fol_map.default_css = [
            ("leaflet_css", self._local_asset_url(self._leaflet_css_path)),
        ]

        fol_map.get_root().header.add_child(
            Element(
                "<style>"
                ".leaflet-container {background: #dce9f6; font-size: 1rem;}"
                ".leaflet-control-attribution {font-size: 10px;}"
                "</style>"
            ),
            name="offline_map_style",
        )

        folium.GeoJson(
            self._load_geojson_asset(self._land_geojson_path),
            name="NaturalEarthLand50m",
            smooth_factor=0.2,
            style_function=lambda _: {
                "fillColor": "#d9e4c7",
                "color": "#8a957b",
                "weight": 0.7,
                "fillOpacity": 0.88,
            },
        ).add_to(fol_map)

        folium.GeoJson(
            self._load_geojson_asset(self._coastline_geojson_path),
            name="NaturalEarthCoastline50m",
            smooth_factor=0.1,
            style_function=lambda _: {
                "color": "#5f6f63",
                "weight": 1.0,
                "opacity": 0.95,
            },
        ).add_to(fol_map)

        folium.GeoJson(
            self._load_geojson_asset(self._boundary_geojson_path),
            name="NaturalEarthBoundaries50m",
            smooth_factor=0.1,
            style_function=lambda _: {
                "color": "#9aa58b",
                "weight": 0.7,
                "opacity": 0.7,
            },
        ).add_to(fol_map)

        selected_station = self._station_by_code(self._selected_station_code)
        layer = MacroElement()
        layer._template = Template("{% macro script(this, kwargs) %}{{ this.js }}{% endmacro %}")
        layer.js = (EPISODE_JS.replace("__MAP__", fol_map.get_name())
                    .replace("__ROWS__", self._rows_json())
                    .replace("__KEPT__", json.dumps(self.show_kept_chk.isChecked()))
                    .replace("__FILTERED__", json.dumps(self.show_filtered_chk.isChecked()))
                    .replace("__SELECTED__", json.dumps(self._selected_station_code)))
        fol_map.add_child(layer)

        if selected_station is not None and focus_selected:
            lat = float(selected_station["latitude"])
            lon = float(selected_station["longitude"])
            fol_map.location = [lat, lon]
            fol_map.options["zoom"] = 6
        elif len(visible_stations) > 1:
            lats = [float(station["latitude"]) for station in visible_stations]
            lons = [float(station["longitude"]) for station in visible_stations]
            lat_span = max(lats) - min(lats)
            lon_span = max(lons) - min(lons)

            if lat_span <= 8.0 and lon_span <= 12.0:
                fol_map.location = [lat_avg, lon_avg]
                fol_map.options["zoom"] = 5
            elif lat_span <= 18.0 and lon_span <= 30.0:
                fol_map.location = [lat_avg, lon_avg]
                fol_map.options["zoom"] = 4
            else:
                fol_map.fit_bounds([[min(lats), min(lons)], [max(lats), max(lons)]])
        elif visible_stations:
            fol_map.location = [
                float(visible_stations[0]["latitude"]),
                float(visible_stations[0]["longitude"]),
            ]
            fol_map.options["zoom"] = 6

        return fol_map

    def update_station_map(self, focus_selected: bool = False):
        if not self.episodes:
            self._queue_map_html_cleanup()
            self._set_map_message("No Station Data")
            self._prune_map_html()
            return

        show_kept = self.show_kept_chk.isChecked()
        show_filtered = self.show_filtered_chk.isChecked()
        visible_stations = self._visible_stations(show_kept, show_filtered)

        if self._selected_station_code is not None:
            self._update_station_info(self._selected_station_code)

        if self._map_html_path is not None:
            self._map_js(f"sinexData({self._rows_json()}); sinexShow({json.dumps(show_kept)}, "
                         f"{json.dumps(show_filtered)})")
            return

        if not visible_stations:
            self._queue_map_html_cleanup()
            self._set_map_message(
                "No Stations Visible",
                "Current filter toggles hide all stations on the map.",
            )
            self._prune_map_html()
            return

        try:
            fol_map = self._build_offline_folium_map(
                visible_stations,
                focus_selected=focus_selected,
            )
        except Exception:
            self._queue_map_html_cleanup()
            self._set_map_message("Stations map error")
            self._prune_map_html()
            return

        self._queue_map_html_cleanup()
        map_html = fol_map.get_root().render()
        map_html_path = self._map_html_dir / f"stations_map_{int(time.time() * 1000)}.html"
        map_html_path.write_text(map_html, encoding="utf-8")
        self._map_html_path = map_html_path
        self.map_view.load(QUrl.fromLocalFile(str(map_html_path)))

    def _set_current_station_item(self, station_code: str):
        matches = self.station_list.findItems(station_code, Qt.MatchFlag.MatchExactly)
        if matches:
            self.station_list.setCurrentItem(matches[0])

    def _station_by_code(self, station_code: str) -> Optional[dict]:
        for station in self.stations_data:
            if station.get("code") == station_code:
                return station
        return None

    def _update_station_info(self, station_code: str):
        station_info = self._station_by_code(station_code)
        if station_info is None:
            self.display.setText("No data")
            return

        lat = station_info.get("latitude")
        lon = station_info.get("longitude")
        lat_str = f"{lat:.4f}°" if isinstance(lat, (int, float)) else str(lat)
        lon_str = f"{lon:.4f}°" if isinstance(lon, (int, float)) else str(lon)

        detail_tokens = [
            f"{key.upper()}={value:.4f}" if isinstance(value, float)
            else f"{key.upper()}={value}"
            for key, value in station_info.items()
            if key not in {"code", "latitude", "longitude"}
        ]
        details_str = ", ".join(detail_tokens) if detail_tokens else "None"

        info_str = (
            f"CODE: {station_code}\n\n"
            f"LAT: {lat_str}\n"
            f"LON: {lon_str}\n\n"
            f"Other:\n{details_str}"
        )
        for ep in self.episodes:
            if ep["code"] == station_code:
                info_str += "\n\n" + "\n".join(self._episode_lines(ep))
        self.display.setText(info_str)

    def on_click(self, item):
        self._selected_station_code = item.text()
        self._set_current_station_item(self._selected_station_code)
        self._update_station_info(self._selected_station_code)
        station = self._station_by_code(self._selected_station_code)
        if station is None or self._map_html_path is None:
            return
        self._map_js(f"sinexSelect({json.dumps(station['code'])}, {float(station['latitude'])}, "
                     f"{float(station['longitude'])})")

###############################################################################
#OperationsWidget
###############################################################################
class OperationsWidget(QWidget):
    def __init__(self, parent=None, get_current_matrix_func=None, export_func=None,
                 log_widget=None):
        super().__init__(parent)
        self.get_current_matrix_func = get_current_matrix_func
        self.export_func = export_func
        self._shared_log = log_widget

        self._normal_matrix = None
        self._apriori_matrix = None
        self._u_vector = None
        self._dx_vector = None
        self._generation = 0

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setStyleSheet("QPushButton {font-size: 14px;} QComboBox {font-size: 14px;} QLabel {font-size: 14px;}")

        button_layout = QVBoxLayout()
        make_section_header("Covariance Operations", button_layout)

        self.compute_normal_btn = QPushButton("Compute Normal Matrix")
        self.compute_normal_btn.setToolTip(
            "N = Qₓ⁻¹ (no apriori) or N = Qₓ⁻¹ − C₀⁻¹ (with apriori), where Qₓ = Cₓ/σ₀²."
        )
        self.compute_normal_btn.clicked.connect(self.compute_normal_matrix)
        #self.compute_normal_btn.setFixedHeight(50)
        button_layout.addWidget(self.compute_normal_btn)

        self.compute_u_btn = QPushButton("Compute u = N*(Xest - Xapr)")
        self.compute_u_btn.setToolTip(
            "Builds Δx from SOLUTION/ESTIMATE minus SOLUTION/APRIORI and multiplies by N to obtain u = N*Δx."
        )
        self.compute_u_btn.clicked.connect(self.compute_u_vector)
        #self.compute_u_btn.setFixedHeight(50)
        button_layout.addWidget(self.compute_u_btn)

        self.ver_btn = QPushButton("Recomputation Check")
        self.ver_btn.setToolTip(
            "Inverts stored N to recover Δx (Δx' = N⁻¹·u) and reports residual norms as a consistency check."
        )
        #self.ver_btn.setFixedHeight(50)
        self.ver_btn.clicked.connect(self.reverse_verify)
        button_layout.addWidget(self.ver_btn)
        self.rank_btn = QPushButton("Compute Rank of N")
        self.rank_btn.setToolTip(
            "Singular value decomposition of N -- Computation heavy!"
        )
        self.rank_btn.setEnabled(False)
        self.rank_btn.clicked.connect(self.compute_rank)
        button_layout.addWidget(self.rank_btn)

        layout.addLayout(button_layout)

        # Log 
        if self._shared_log is not None:
            self.log_text = self._shared_log
            layout.addStretch(1)
        else:
            self.log_label = QLabel("Log:")
            self.log_label.setFont(QFont("Arial", 14))
            layout.addWidget(self.log_label)

            self.log_text = QPlainTextEdit()
            self.log_text.setReadOnly(True)
            cur_font = self.log_text.font()
            cur_font.setPointSize(14)
            self.log_text.setFont(cur_font)
            layout.addWidget(self.log_text)

            self.log_handler = QPlainTextEditLogger(self.log_text)
            if self.log_handler not in logger.handlers:
                logger.addHandler(self.log_handler)

        self.matrix_combo = QComboBox()
        self.matrix_combo.addItems([
            "Normal Matrix (NxN)",
            "Apriori Covariance Matrix (NxN)",
            "Vector u (Nx1)",
            "Reconstructed Covariance Matrix (NxN)"
        ])
        layout.addWidget(QLabel("Matrix to Export:"))
        layout.addWidget(self.matrix_combo)

        export_line = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Excel (.xlsx)","CSV (.csv)","Text (.txt)","NumPy (.npy)"])
        self.format_combo.setCurrentText(default_export_format())
        export_line.addWidget(QLabel("Export Format:"))
        export_line.addWidget(self.format_combo)

        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.export_data)
        export_line.addWidget(self.export_btn)

        layout.addLayout(export_line)

    def reset_for_new_file(self):
        self._generation += 1
        self._normal_matrix = None
        self._apriori_matrix = None
        self._u_vector = None
        self._dx_vector = None
        self.rank_btn.setEnabled(False)

    def _set_busy(self, busy):
        for btn in (self.compute_normal_btn, self.compute_u_btn, self.ver_btn, self.export_btn):
            btn.setEnabled(not busy)
        self.rank_btn.setEnabled(not busy and self._normal_matrix is not None)

    def _start(self, func, args, on_done, label):
        gen = self._generation
        self._set_busy(True)

        def done(worker):
            self._set_busy(False)
            if gen != self._generation:
                logger.info("Ignoring a result computed for a previous file.")
                return
            if worker.error is not None and not isinstance(worker.error, normal_math.NormalMatrixError):
                logger.error(f"Computation failed: {worker.error}")
                QMessageBox.critical(self, "Error", str(worker.error))
                return
            on_done(worker)

        run_task(func, args, done, label)
    
    _RANK_WARN_DIM = 3000
    _RANK_SECONDS_AT_2500 = 1.73

    def compute_rank(self):
        if self._normal_matrix is None:
            QMessageBox.warning(self, "Warning", "Compute Normal matrix first.")
            return
        N = self._normal_matrix
        if N.shape[0] > self._RANK_WARN_DIM:
            minutes = self._RANK_SECONDS_AT_2500 * (N.shape[0] / 2500.0) ** 3 / 60.0
            answer = QMessageBox.question(
                self, "Compute Rank of N",
                f"Rank is computed by singular value decomposition. For {N.shape[0]} "
                f"parameters this is expected to take the order of "
                f"{minutes:.0f} minutes.{chr(10)}{chr(10)}Proceed?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.log_text.appendPlainText(
            f"Computing rank of a {N.shape[0]}x{N.shape[0]} matrix by SVD, this may take a while."
        )
        self._start(normal_math.rank_of_normal_matrix, (N,), lambda w: self._rank_done(w, N),
                    "Computing the rank of N")

    def _rank_done(self, worker, N):
        rank_n, elapsed = worker.result
        deficiency = N.shape[0] - int(rank_n)
        self.log_text.appendPlainText(
            f"rank(N) = {rank_n} of {N.shape[0]}, deficiency {deficiency}, {elapsed:.3f} s"
        )

    def compute_normal_matrix(self):
        t0 = time.time()
        parent_app = self.window()
        # reconstructed final cova
        Cov_final = self.get_current_matrix_func()
        #print(Cov_final)
        if Cov_final is None:
            QMessageBox.critical(self, "Error", "No final covariance loaded.")
            return
        var_factor = parent_app.get_variance_factor()
        if var_factor is None:
            QMessageBox.critical(self, "Error", "No variance factor found.")
            return

        normal_bench = benchmark.begin('compute normal matrix N')


        apr_key = "SOLUTION/MATRIX_APRIORI L COVA"
        apr_data = parent_app.current_data['blocks'].get(apr_key)
        if apr_data is None:
            apr_key = "SOLUTION/MATRIX_APRIORI U COVA"
            apr_data = parent_app.current_data['blocks'].get(apr_key)

        self._start(normal_math.build_normal_matrix, (Cov_final, var_factor, apr_data, apr_key),
                    lambda w: self._normal_done(w, t0, normal_bench), "Computing the normal matrix")

    def _normal_done(self, worker, t0, normal_bench):
        parent_app = self.window()
        if worker.error is not None:
            benchmark.cancel(normal_bench)
            QMessageBox.critical(self, "Error", str(worker.error))
            return
        N, self._apriori_matrix = worker.result

        self._normal_matrix = N
        # new N -> clear past data
        self._u_vector = None
        self._dx_vector = None
        self.rank_btn.setEnabled(True)
        benchmark.end(normal_bench)
        elapsed = time.time()-t0
        logger.info(f"Normal matrix computed in {elapsed:.3f} seconds.")

        #  auto-update attempt
        if hasattr(parent_app, 'viz_widget') and hasattr(parent_app, 'current_data'):
            if parent_app.current_data and 'blocks' in parent_app.current_data:
                parent_app.current_data['blocks']['COMPUTED/NORMAL_MATRIX'] = N
                parent_app.viz_widget.setup_data(parent_app.current_data['blocks'])
                logger.info("Normal matrix added to visualization options.")

        QMessageBox.information(self, "Success", "Normal matrix computed.")

    def compute_u_vector(self):
        t0 = time.time()
        parent_app = self.window()
        if self._normal_matrix is None:
            QMessageBox.warning(self, "Warning", "Compute Normal matrix first.")
            return

        est_data = parent_app.current_data['blocks'].get('SOLUTION/ESTIMATE')
        apr_data = parent_app.current_data['blocks'].get('SOLUTION/APRIORI')

        if not est_data:
            QMessageBox.critical(self, "Error", "SOLUTION/ESTIMATE block is missing.")
            return
        if not apr_data:
            QMessageBox.critical(self, "Error", "SOLUTION/APRIORI block is missing.")
            return

        n = len(est_data)
        if self._normal_matrix.shape[0] != n:
            QMessageBox.critical(
                self,
                "Error",
                "Dimension mismatch between Normal matrix and parameter count."
            )
            return
        try:
            est_data = datum_math.index_ordered(est_data, n, normal_math.NormalMatrixError)
        except normal_math.NormalMatrixError as exc:
            QMessageBox.critical(self, "Error", str(exc))
            return

        u, dx = normal_math.compute_u(self._normal_matrix, est_data, apr_data)
        self._u_vector = u
        self._dx_vector = dx  # store for reverse ver
        dur = time.time() - t0
        logger.info(f"Computed u in {dur:.3f}s, shape={u.shape}")
        QMessageBox.information(self, "Success", "u = N*(Xest - Xapr) computed.")

    def reverse_verify(self):
        self._start(normal_math.recomputation_check,
                    (self._normal_matrix, self._u_vector, self._dx_vector),
                    self._verify_done, "Running the recomputation check")

    def _verify_done(self, worker):
        for line in worker.result:
            self.log_text.appendPlainText(line)






    def export_data(self):
        choice = self.matrix_combo.currentText()
        fmt = self.format_combo.currentText()
        logger.info(f"Exported {choice} as {fmt}")

        if choice=="Normal Matrix (NxN)":
            if self._normal_matrix is None:
                QMessageBox.warning(self,"Warning","No Normal matrix computed.")
                return
            data_to_export = self._normal_matrix
            prefix = "NormalMatrix"
        elif choice=="Apriori Covariance Matrix (NxN)":
            if self._apriori_matrix is None:
                QMessageBox.warning(self,"Warning","No apriori matrix found.")
                return
            data_to_export = self._apriori_matrix
            prefix = "Covariance_Apriori_Matrix"
        elif choice=="Vector u (Nx1)":
            if self._u_vector is None:
                QMessageBox.warning(self,"Warning","u vector not computed.")
                return
            data_to_export = self._u_vector
            prefix = "u_Vector"
        elif choice=="Reconstructed Covariance Matrix (NxN)":
            parent_app = self.window()
            if not parent_app.current_data:
                QMessageBox.warning(self,"Warning","No data loaded.")
                return
            rec_L = parent_app.current_data['blocks'].get('SOLUTION/MATRIX_ESTIMATE L COVA')
            if rec_L is not None:
                rec = rec_L
            else:
                rec = parent_app.current_data['blocks'].get('SOLUTION/MATRIX_ESTIMATE U COVA')
            if rec is None:
                QMessageBox.warning(self,"Warning","No reconstructed matrix found.")
                return
            data_to_export = rec
            prefix="Reconstructed"
        else:
            QMessageBox.warning(self,"Warning",f"Unknown choice: {choice}")
            return

        if (fmt.startswith("Excel") and getattr(data_to_export, "ndim", 0) == 2
                and data_to_export.shape[1] > 16384):
            QMessageBox.warning(
                self, "Export Error",
                f"This matrix has {data_to_export.shape[1]} columns, more than the "
                f"16384 an .xlsx file can hold. Export it as NumPy (.npy) instead."
            )
            return
        if fmt.startswith("Excel") and getattr(data_to_export, "size", 0) > export.XLSX_MAX_CELLS:
            QMessageBox.warning(self, "Export Error", export.XLSX_TOO_LARGE)
            return

        self.export_func(data_to_export, fmt, prefix)

class CovarianceMatrixWidget(QWidget):
#wrapper for operations + mvw
    def __init__(self, parent=None, get_current_matrix_func=None, export_func=None,
                 log_widget=None):
        super().__init__(parent)
        self.log_container, self.log_text = make_log_panel(self)
        self.operations_widget = OperationsWidget(
            log_widget=self.log_text,
            parent=self,
            get_current_matrix_func=get_current_matrix_func,
            export_func=export_func,
        )
        self.visualizer_widget = MatrixVisualizerWidget(parent=self)

        right_column = QSplitter(Qt.Orientation.Vertical)
        right_column.setChildrenCollapsible(False)
        right_column.addWidget(self.operations_widget)
        visualizer_scroll = QScrollArea()
        visualizer_scroll.setWidgetResizable(True)
        visualizer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        visualizer_scroll.setWidget(self.visualizer_widget)
        right_column.addWidget(visualizer_scroll)
        right_column.setStretchFactor(0, 0)
        right_column.setStretchFactor(1, 1)
        right_column.setSizes([self.operations_widget.minimumSizeHint().height(), 10000])
        right_column.setFixedWidth(CONTROL_COLUMN_WIDTH)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.log_container)
        splitter.addWidget(right_column)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 0)
        splitter.setSizes([900, CONTROL_COLUMN_WIDTH])
        layout = QVBoxLayout(self)
        layout.addWidget(splitter)
        self.setLayout(layout)

    def setup_data(self, blocks: dict):
        self.visualizer_widget.setup_data(blocks)

###############################################################################
# Info Widget
###############################################################################
class InfoWidget(QWidget):
    DEPENDENCIES: Tuple[str, ...] = (
        "PyQt6==6.11.0",
        "PyQt6-WebEngine==6.11.0",
        "pyqtgraph==0.14.0",
        "numpy==2.5.3",
        "pandas==3.0.6",
        "matplotlib==3.11.2",
        "seaborn==0.13.2",
        "folium==0.20.0",
        "openpyxl==3.1.5",
        "plyer==2.1.0",
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(18)

        header_layout = QHBoxLayout()
        header_layout.setSpacing(24)

        self.logo_label = QLabel()
        self.logo_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo_label.setMinimumSize(160, 160)
        self.logo_label.setStyleSheet(
            "QLabel {background: palette(base); border: 1px solid palette(mid); padding: 12px;}"
        )

        logo_path = Path(__file__).resolve().parents[2] / "logo2.jpg"
        pixmap = QPixmap(str(logo_path)) if logo_path.exists() else QPixmap()
        if pixmap.isNull():
            self.logo_label.setText("SINEX Studio")
        else:
            self.logo_label.setPixmap(
                pixmap.scaled(180, 180, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )

        header_layout.addWidget(self.logo_label, 0, Qt.AlignmentFlag.AlignTop)
        details_layout = QVBoxLayout()
        details_layout.setSpacing(6)
        title_label = QLabel("SINEX TRF Studio")
        title_font = title_label.font()
        title_font.setPointSize(20)
        title_font.setBold(True)
        title_label.setFont(title_font)

        subtitle_label = QLabel("International Hellenic University\n"
                                "\nPython3 Processing Software with Applications to Global and Regional Terrestrial Reference Frames for the SINEX File Format.\n"
                                "\nSpecial thanks to professor Ampatzidis D. for his contribution to the development of this software.")
        subtitle_label.setWordWrap(True)

        version_label = QLabel(f"Version: {__version__}")
        version_label.setWordWrap(True)
        contact_header = QLabel("Contact")
        header_font = contact_header.font()
        header_font.setBold(True)
        contact_header.setFont(header_font)

        authors_header = QLabel("Authors")
        authors_header.setFont(header_font)
        authors_body = QLabel("Gerasimos M. Dossas\nDimitrios Ampatzidis")
        authors_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        contact_body = QLabel(
            "Gerasimos M. Dossas\n @ gerasimos.dossas@gmail.com"
        )
        contact_body.setWordWrap(True)
        contact_body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)

        details_layout.addWidget(title_label)
        details_layout.addWidget(subtitle_label)
        details_layout.addWidget(version_label)
        details_layout.addSpacing(8)
        details_layout.addWidget(authors_header)
        details_layout.addWidget(authors_body)
        details_layout.addSpacing(8)
        details_layout.addWidget(contact_header)
        details_layout.addWidget(contact_body)
        details_layout.addSpacing(8)
        details_layout.addStretch(1)

        header_layout.addLayout(details_layout, 1)

        layout.addLayout(header_layout)

        note_label = QLabel(
            "Results are provided as-is; validate numerical outputs before critical use."
        )
        note_label.setWordWrap(True)
        note_label.setStyleSheet("")
        layout.addWidget(note_label)

        requirements_widget = QTextEdit()
        requirements_widget.setReadOnly(True)
        requirements_widget.setStyleSheet(
            "QTextEdit {background: palette(base); border: 1px solid palette(mid); padding: 8px;}"
        )

        requirements_html = ["<h3>Dependencies</h3><ul>"]
        for dep in self.DEPENDENCIES:
            requirements_html.append(f"<li>{html.escape(dep)}</li>")
        requirements_html.append("</ul>")

        requirements_widget.setHtml("".join(requirements_html))

        overview_widget = QTextEdit()
        overview_widget.setReadOnly(True)
        overview_widget.setStyleSheet(
            "QTextEdit {background: palette(base); border: 1px dashed palette(mid); padding: 12px;}"
        )
        overview_widget.document().setDefaultStyleSheet(
            "body { font-size: 13px; }"
            "h3 { margin: 0 0 10px 0; font-size: 16px; font-weight: 600; }"
            "h4 { margin: 14px 0 6px 0; font-size: 13px; font-weight: 600; }"
            "p { margin: 0 0 10px 0; }"
            "ul { margin: 0 0 0 18px; }"
            "li { margin-bottom: 5px; }"
        )
        overview_widget.setHtml(
            "<h3>Functions:</h3>"

            "<h4>Covariance Matrix</h4>"
            "<ul>"
            "<li><b>Inspect matrices</b> with heatmaps and eigenvalue views.</li>"
            "<li><b>Rebuild the normal equation matrix</b> from parsed covariance data.</li>"
            "<li><b>Check reversibility</b> by comparing reconstructed parameter differences.</li>"
            "</ul>"

            "<h4>Datum Effect</h4>"
            "<ul>"
            "<li><b>Filter episodes</b> for datum analysis.</li>"
            "<li><b>Transform uncertainties</b> into correlation views.</li>"
            "<li><b>Estimate Helmert parameters</b></li>"
            "</ul>"

            "<h4>Stations</h4>"
            "<ul>"
            "<li><b>Review station records</b> in a list and on the offline map.</li>"
            "</ul>"

            
        )

        bottom_layout = QHBoxLayout()
        bottom_layout.setSpacing(18)
        bottom_layout.addWidget(requirements_widget, 15)
        bottom_layout.addWidget(overview_widget, 85)
        layout.addLayout(bottom_layout, 1)


AppliedFilter = datum_math.AppliedFilter


def _sigma_theta_job(sol, Cx, applied):
    excluded, details = datum_math.select_excluded_episodes(sol, Cx, applied)
    try:
        return excluded, details, datum_math.sigma_theta_from_covariance(sol, Cx, excluded)
    except (datum_math.DatumError, np.linalg.LinAlgError) as exc:
        return excluded, details, exc


class DatumWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._station_cache = None  # Dict[label] -> {'x', 'y', 'z'}
        self._station_labels_cached = None  # Cached sorted list of station labels
        self.stations_data = {}  #legacy
        self.sigma_theta_matrix = None
        self.cross_correlation_matrix = None
        self.helmert_params = None
        self.filtered_solution_estimate = None
        self.filtered_Cx = None
        self.filtered_row_idx = None
        self._manual_filter_enabled = False  #Manual episode selection mode
        self._manual_selected_episodes = set()  #episodes to INCLUDE
        self._applied_filter = None
        self._filter_enabled = True
        self._file_loaded = False
        self._sigma_generation = 0
        self._pos_threshold_m = default_pos_threshold()
        self._vel_threshold_m_per_y = default_vel_threshold()
        self._setup_ui()
        self._filtered_episodes_info = []  # list of dicts with details
        self._filtered_dialog = None  # dialog instance
        self._datum_run = None

    def _setup_ui(self):
        # defaults for matrices
        self._legend_sigma_decimals = 3
        self._legend_sigma_nbins = 12
        self._legend_sigma_minor_div = 2


        outer_layout = QHBoxLayout(self)
        self.setStyleSheet(
            "QPushButton {font-size: 14px;} QComboBox {font-size: 14px;} QLabel {font-size: 14px;}")

        self.log_container, self.log_text = make_log_panel(self)
        outer_layout.addWidget(self.log_container, 1)

        sidebar = QWidget()
        sidebar.setFixedWidth(CONTROL_COLUMN_WIDTH)
        layout = QVBoxLayout(sidebar)
        outer_layout.addWidget(sidebar, 0)

        buttons_layout = QVBoxLayout()

        make_section_header("Filtering", buttons_layout)

        self.filter_options_btn = QPushButton("Filter Options")
        self.filter_options_btn.setToolTip("Set the auto sigma thresholds or pick episodes by hand")
        self.filter_options_btn.clicked.connect(self.open_filter_options_dialog)
        buttons_layout.addWidget(self.filter_options_btn)

        self.show_filtered_btn = QPushButton("Show Filtered Stations")
        self.show_filtered_btn.setToolTip("List episodes removed by current filtering")
        self.show_filtered_btn.setEnabled(False)
        self.show_filtered_btn.clicked.connect(self.open_filtered_episodes_dialog)
        buttons_layout.addWidget(self.show_filtered_btn)

        self.filter_status_label = QLabel()
        self.filter_status_label.setTextFormat(Qt.TextFormat.RichText)
        buttons_layout.addWidget(self.filter_status_label)

        make_section_header("Datum Effect", buttons_layout)

        self.sigma_theta_btn = QPushButton("Calculate SigmaTheta")
        self.sigma_theta_btn.setToolTip(
            "Computes Σθ = (E^T E)^{-1} E^T Cx E (E^T E)^{-1} using the filtered "
            "station covariance Cx to obtain datum-parameter variances."
        )
        self.sigma_theta_btn.clicked.connect(self.calculate_sigma_theta)
        self.sigma_theta_btn.setEnabled(True)
        buttons_layout.addWidget(self.sigma_theta_btn)

        self.cross_corr_btn = QPushButton("Calculate Cross Correlations")
        self.cross_corr_btn.setToolTip(
            "Derives the correlation matrix R where R_ij = Σθ_ij / (σ_i σ_j) to expose "
            "interactions between datum parameters."
        )
        self.cross_corr_btn.clicked.connect(self.calculate_cross_correlations)
        self.cross_corr_btn.setEnabled(False)
        buttons_layout.addWidget(self.cross_corr_btn)

        self.helmert_btn = QPushButton("Calculate Helmert Parameters (Σ)")
        self.helmert_btn.setToolTip(
            "Takes the square root of diag(Σθ) to report 1σ uncertainties for the "
            "Helmert translation, scale, and rotation parameters."
        )
        self.helmert_btn.clicked.connect(self.calculate_helmert)
        self.helmert_btn.setEnabled(False)
        buttons_layout.addWidget(self.helmert_btn)

        layout.addLayout(buttons_layout)
        self._update_filter_status()

        self.inspect_btn = QPushButton("Inspect Matrices")
        self.inspect_btn.setToolTip("Open matrix/episode selector and preview")
        self.inspect_btn.clicked.connect(self.open_matrix_inspector)
        layout.addWidget(self.inspect_btn)

        self.plot_btn = QPushButton("Plot Matrix")
        self.plot_btn.clicked.connect(self.plot_matrix_async)
        self.plot_btn.setEnabled(False)

        layout.addStretch(1)

        self.matrix_display_combo = QComboBox()
        self.matrix_display_combo.addItems([
            "Sigma Theta (Σ_θ)",
            "Cross Correlations (R)",
            "Helmert Parameters",
        ])
        layout.addWidget(QLabel("Matrix to Export:"))
        layout.addWidget(self.matrix_display_combo)

        export_line = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Excel (.xlsx)", "CSV (.csv)", "Text (.txt)", "NumPy (.npy)"])
        self.format_combo.setCurrentText(default_export_format())
        export_line.addWidget(QLabel("Export Format:"))
        export_line.addWidget(self.format_combo)

        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.export_data)
        self.export_btn.setEnabled(False)
        export_line.addWidget(self.export_btn)

        layout.addLayout(export_line)

        self.stats_btn = QPushButton("Diagnostics report")
        self.stats_btn.setToolTip(
            "Write the diagnostics report of the datum as text and JSON"
        )
        self.stats_btn.clicked.connect(self.export_diagnostics_report)
        self.stats_btn.setEnabled(False)

        report_line = QHBoxLayout()
        report_line.addWidget(self.stats_btn)
        report_line.addWidget(self.plot_btn)
        layout.addLayout(report_line)

        self.last_matrix_selection = "Sigma Theta (Σ_θ)"
        self.last_station_selection = ""

        self._matrix_inspector = None

    def _append_status(self, message: str) -> None:
        logger.info(message.strip())

    def _append_section(self, title: str) -> None:
        logger.info("")
        logger.info(f"--- {title.strip()} ---")

    def _update_stats_button(self) -> None: #disabled untill everything has been run
        ready = (
            self.sigma_theta_matrix is not None
            and self.cross_correlation_matrix is not None
            and self.helmert_params is not None
        )
        self.stats_btn.setEnabled(ready)

    def _clear_computed_products(self) -> None:
        #clear the old results from a previous SigmaTheta run and disable the related buttons 
        #call at the start of every calculate_sigma_theta run so a recompute successful or failed can never leave behind an old cross-corr, helmert


        self._sigma_generation += 1
        self.sigma_theta_matrix = None
        self.cross_correlation_matrix = None
        self.helmert_params = None
        self.filtered_solution_estimate = None
        self.filtered_Cx = None
        self.filtered_row_idx = None
        self._datum_run = None
        self.cross_corr_btn.setEnabled(False)
        self.helmert_btn.setEnabled(False)
        self.export_btn.setEnabled(False)
        self.plot_btn.setEnabled(False)
        self._update_stats_button()
        self._clear_filter_products()


    def _clear_filter_products(self) -> None:
        self._filtered_episodes_info = []
        self.show_filtered_btn.setEnabled(False)
        self._refresh_filtered_dialog_if_open()
        parent_app = self.window()
        stations = getattr(parent_app, "stations_widget", None)

        if stations is not None and getattr(stations, "excluded", None):
            stations.set_excluded_episodes(set())
            stations.update_station_map()

    def _reset_output_state(self, cleared=True) -> None:
        self._clear_computed_products()

        if self.matrix_display_combo.count():
            self.matrix_display_combo.setCurrentIndex(0)
        self.last_matrix_selection = "Sigma Theta (Σ_θ)"
        self.last_station_selection = ""

        if self._filtered_dialog is not None:
            try:
                self._filtered_dialog.close()
            except RuntimeError:
                pass
            self._filtered_dialog = None

        if self._matrix_inspector is not None:
            try:
                self._matrix_inspector.close()
            except RuntimeError:
                pass
            self._matrix_inspector = None

        self._clear_matrix_display("Cleared previous results for new file" if cleared else "")

    def log_new_file_loaded(self, filename: str) -> None:
        self._clear_station_cache()  # Reset cache on new file load
        self._manual_filter_enabled = False  # Reset manual filter mode
        self._manual_selected_episodes = set()  # Clear manual selections
        self._applied_filter = None
        self._update_filter_status()
        label = Path(filename).name
        if self._file_loaded:
            self._append_section(f"NEW FILE: {label}")
        self._reset_output_state(self._file_loaded)
        self._file_loaded = True

    def open_filtered_episodes_dialog(self):
        if not getattr(self, "_filtered_episodes_info", None):
            QMessageBox.information(self, "Filtered Stations", "No filtered episodes to show.")
            return
#reuse dialog instead of creating new one
        dlg = getattr(self, "_filtered_dialog", None)
        if dlg is not None:
            try:
                if dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    dlg.refresh()
                    return
            except RuntimeError:
                self._filtered_dialog = None  # stale wrapper

        self._filtered_dialog = FilteredStationsDialog(self, parent=self)
        # Clear pointer when the dialog is destroyed
        self._filtered_dialog.destroyed.connect(lambda: setattr(self, "_filtered_dialog", None))
        self._filtered_dialog.show()

    def _refresh_filtered_dialog_if_open(self):
        dlg = getattr(self, "_filtered_dialog", None)
        if dlg is None:
            return
        try:
            if dlg.isVisible():
                dlg.refresh()
        except RuntimeError:
            self._filtered_dialog = None

    def open_filter_options_dialog(self):
        dlg = FilterOptionsDialog(self, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            manual_enabled, selected_episodes = dlg.get_selected_mode_and_episodes()
            enabled, pos_m, vel_m = dlg.get_auto_settings()
            self._manual_filter_enabled = manual_enabled
            self._manual_selected_episodes = selected_episodes
            self.set_filter_options(enabled, pos_m, vel_m)
            self._update_filter_status()
            if manual_enabled:
                self._append_status(f"[Filter] Manual selection enabled: {len(selected_episodes)} episodes selected")
            else:
                self._append_status("[Filter] Auto filter mode enabled")

    def open_matrix_inspector(self):
        dlg = getattr(self, "_matrix_inspector", None)
        if dlg is not None:
            try:
                if dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    return
            except RuntimeError:
                self._matrix_inspector = None  # stale wrapper

        self._matrix_inspector = MatrixInspectorDialog(self, parent=self)
        self._matrix_inspector.destroyed.connect(lambda: setattr(self, "_matrix_inspector", None))
        self._matrix_inspector.show()

    def _refresh_matrix_inspector_if_open(self):
        dlg = getattr(self, "_matrix_inspector", None)
        if dlg is None:
            return
        try:
            if dlg.isVisible():
                dlg._reload_station_combo()
                dlg.update_preview()
        except RuntimeError:
            self._matrix_inspector = None

    def _build_station_cache(self) -> dict:
        #Build station cache from SOLUTION/ESTIMATE
        #returns empty dict if SINEX data unavailable
        parent_app = self.window()
        if not hasattr(parent_app, "current_data") or not parent_app.current_data:
            return {}
        solution_estimate = parent_app.current_data["blocks"].get("SOLUTION/ESTIMATE")
        if not solution_estimate:
            return {}
        cache = self.parse_station_coordinates(solution_estimate)
        logger.info(f"Station cache populated with {len(cache)} episodes")
        return cache

    def _station_labels(self):
        #returm sorted list of episode labels with complete xyz
        if self._station_cache is None:
            self._station_cache = self._build_station_cache()
            self._station_labels_cached = sorted(self._station_cache.keys())
        return self._station_labels_cached

    def _get_station_Ei(self, label: str): #calculate ei for station
        if self._station_cache is None:
            self._station_cache = self._build_station_cache()
            self._station_labels_cached = sorted(self._station_cache.keys())
        
        meta = self._station_cache.get(label, {})
        coords = (meta.get("x"), meta.get("y"), meta.get("z"))
        
        if None in coords:
            return None
        
        return self.create_matrix_Ei(*coords)


    def _clear_station_cache(self):#reset cache with new file load
        self._station_cache = None
        self._station_labels_cached = None

#---------------------------------------------------------------------

    def _prepare_display_matrix(self, selection: str, station_label: str | None):
        #Return (matrix, title, labels) based on selection and station_label.
        matrix = None
        title = ""
        labels = []

        if selection == "Sigma Theta (Σ_θ)":
            if self.sigma_theta_matrix is None:
                return None, "Sigma Theta not available", []
            matrix = self.sigma_theta_matrix
            title = "Sigma Theta Matrix (Σ_θ)"
            if matrix.shape[0] == 14:
                labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz",
                          "tx_v", "ty_v", "tz_v", "δs_v", "εx_v", "εy_v", "εz_v"]
            elif matrix.shape[0] == 7:
                labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]

        elif selection == "Cross Correlations (R)":
            if self.cross_correlation_matrix is None:
                return None, "Cross Correlations not available", []
            matrix = self.cross_correlation_matrix
            title = "Cross Correlation Matrix (R)"
            if matrix.shape[0] == 14:
                labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz",
                          "tx_v", "ty_v", "tz_v", "δs_v", "εx_v", "εy_v", "εz_v"]
            elif matrix.shape[0] == 7:
                labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]

        elif selection == "Station Ei Matrix":
            if not station_label:
                return None, "No station selected", []
            matrix = self._get_station_Ei(station_label)
            if matrix is None:
                return None, f"Station {station_label} not available", []
            title = f"Ei Matrix for Episode {station_label}"
            labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]

        elif selection == "Helmert Parameters":
            if self.helmert_params is None:
                return None, "Helmert parameters not available", []
            matrix = self.helmert_params
            title = "Helmert Parameters"

        else:
            return None, f"Unknown selection: {selection}", []

        return matrix, title, labels

    def calculate_cross_correlations(self):
       # cross-correlation matrix R(i,j) = cov(ij)/(σi * σj)
        if self.sigma_theta_matrix is None:
            QMessageBox.warning(self, "Warning", "Calculate Sigma Theta matrix first.")
            return

        try:
            bench = benchmark.span('compute cross correlations')
            bench.__enter__()
            self._append_section("Cross Correlation calculation")

            cross_corr = datum_math.cross_correlations(self.sigma_theta_matrix)
            self.cross_correlation_matrix = cross_corr

            self.export_btn.setEnabled(True)
            self.plot_btn.setEnabled(True)
            self._update_stats_button()

            if self.matrix_display_combo.currentText() == "Cross Correlations (R)":
                self.update_matrix_display()

            self._append_status("cross correlation calculation complete")
            bench.__exit__(None, None, None)
            QMessageBox.information(self, "Success",
                                    f"Cross correlation matrix ({cross_corr.shape[0]}x{cross_corr.shape[1]}) calculated successfully!")
            self._refresh_matrix_inspector_if_open()

        except Exception as e:
            error_msg = f"Error calculating cross correlations: {str(e)}"
            self._append_status(f"cross correlation error: {error_msg}")
            logger.exception("Cross correlation calculation error")
            QMessageBox.critical(self, "Calculation Error", error_msg)

    def _render_helmert_bar(self, values: np.ndarray, title: str, subtitle: str | None = None,
                             default_name: str | None = None):
        fig, ax = figures.build_helmert_bar(values, title, subtitle)
        set_current_figure(self, fig)

        if default_name:
            try:
                manager = plt.get_current_fig_manager()
                if hasattr(manager, "set_window_title"):
                    manager.set_window_title(default_name)
                fig.canvas.get_default_filename = lambda dn=default_name: dn
            except Exception:
                pass

        return fig, ax

    def calculate_helmert(self):
        if self.sigma_theta_matrix is None:
            QMessageBox.warning(self, "Warning", "Calculate Sigma Theta matrix first.")
            return
        else:
            try:
                bench = benchmark.span('compute Helmert parameters')
                bench.__enter__()
                self._append_section("Helmert parameters")
                hparam = datum_math.helmert_parameters(self.sigma_theta_matrix)
                self.helmert_params = hparam
                if self.matrix_display_combo.currentText() == "Helmert Parameters":
                    self.update_matrix_display()
                self._append_status("helmert parameters calculated")
                bench.__exit__(None, None, None)
                self._update_stats_button()

                # self._render_helmert_bar(hparam, "Helmert Parameters")
                # fig = plt.gcf()
                # add_plot_footer(fig)
                # plt.show()
                self._refresh_matrix_inspector_if_open()

            except Exception as e:
                error_msg = f"Error calculating Helmert parameters: {str(e)}"
                self._append_status(f"helmert parameter error: {error_msg}")
                logger.exception("Helmert parameter calculation error")
                QMessageBox.critical(self, "Calculation Error", error_msg)

    def is_filtered(self, selection: str | None = None) -> bool: #####################
        #
        #True only if filtering is enabled and at least one episode is excluded.
        #Optionally restrict to filtered products (Σθ, R, Helmert) via 'selection'.
        
        if not datum_math.is_filtered(self._applied_filter,
                                      getattr(self, "_filtered_episodes_info", None)):
            return False
        if selection is None:
            return True
        return selection in ("Sigma Theta (Σ_θ)", "Cross Correlations (R)", "Helmert Parameters")

    def _filter_tag(self) -> str:
        return datum_math.filter_tag(self._applied_filter,
                                     getattr(self, "_filtered_episodes_info", None))

    def _filter_disp(self) -> str:
        return datum_math.filter_disp(self._applied_filter,
                                      getattr(self, "_filtered_episodes_info", None))

    def _is_filtered_product(self, selection: str) -> bool:
        return self.is_filtered(selection)

    def _default_plot_filename(self, selection: str, ext: str = "png") -> str:
        from pathlib import Path

        try:
            parent_app = self.window()
            base = Path(parent_app.current_data["metadata"].get("filename", "")).stem
            if not base:
                base = "plot"
        except Exception:
            base = "plot"

        if selection == "Sigma Theta (Σ_θ)":
            prefix = "sigma_theta"
        elif selection == "Cross Correlations (R)":
            prefix = "cross_correlations"
        elif selection == "Helmert Parameters":
            prefix = "helmert_parameters"
        else:
            prefix = "matrix"

        suffix = self._filter_tag() if self.is_filtered(selection) else ""
        return f"{base}_{prefix}{suffix}.{ext}"

    def update_matrix_display(self):
        selection = self.matrix_display_combo.currentText()
        try:
            matrix = None
            title = ""
            labels = []

            if selection == "Sigma Theta (Σ_θ)":
                if self.sigma_theta_matrix is None:
                    self._clear_matrix_display("Sigma Theta matrix not calculated yet") # <----- here
                    return
                matrix = self.sigma_theta_matrix
                title = "Sigma Theta Matrix (Σ_θ)"
                # parameter labels
                if matrix.shape[0] == 14:
                    labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz", "tx_v", "ty_v", "tz_v", "δs_v", "εx_v", "εy_v",
                              "εz_v"]
                elif matrix.shape[0] == 7:
                    labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]
                else:
                    labels = [f"P{i + 1}" for i in range(matrix.shape[0])]

            elif selection == "Cross Correlations (R)":
                if self.cross_correlation_matrix is None:
                    self._clear_matrix_display("Cross correlation matrix not calculated yet")
                    return
                matrix = self.cross_correlation_matrix
                title = "Cross Correlation Matrix (R)"
                # format based on shape
                if matrix.shape[0] == 14:
                    labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz", "tx_v", "ty_v", "tz_v", "δs_v", "εx_v", "εy_v",
                              "εz_v"]
                elif matrix.shape[0] == 7:
                    labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]
                else:
                    labels = [f"P{i + 1}" for i in range(matrix.shape[0])]

            elif selection == "Helmert Parameters":
                if self.helmert_params is None:
                    self._clear_matrix_display("Helmert parameters not calculated yet")
                    return
                matrix = self.helmert_params
                title = "Helmert Parameters"

            else:
                self._clear_matrix_display("Unknown matrix selection")
                return

            if matrix.ndim == 2:
                row_labels = (labels[:matrix.shape[0]] if len(labels) >= matrix.shape[0] 
                              else [f"Row_{i}" for i in range(matrix.shape[0])])
                col_labels = (labels[:matrix.shape[1]] if len(labels) >= matrix.shape[1] 
                              else [f"Col_{i}" for i in range(matrix.shape[1])])
                df = pd.DataFrame(matrix, index=row_labels, columns=col_labels)
            else:
                df = pd.DataFrame(matrix)


            model = self.build_pandas_df(df, title)
            self._refresh_matrix_inspector_if_open()
            self._append_status(f"displaying {title} ({matrix.shape[0]}x{matrix.shape[1]})")

        except Exception as e:
            logger.exception("Error updating matrix display")
            self._clear_matrix_display(f"Error: {str(e)}")

    def _clear_matrix_display(self, message="No data to display"):
        #clear display
        empty_df = pd.DataFrame([[message]])
        model = self.build_pandas_df(empty_df, "No Data")
        if message:
            self._append_status(message)
    def build_pandas_df(self, dataframe, title="Matrix"):
        from PyQt6.QtCore import QAbstractTableModel, Qt, QModelIndex

        class PandasModel(QAbstractTableModel):
            def __init__(self, data, title=""):
                super().__init__()
                self._data = data
                self._title = title
            def rowCount(self, parent=QModelIndex()):
                return self._data.shape[0]
            def columnCount(self, parent=QModelIndex()):
                return self._data.shape[1]
            def data(self, index, role=Qt.ItemDataRole.DisplayRole):
                if not index.isValid():
                    return None

                if role == Qt.ItemDataRole.DisplayRole:
                    value = self._data.iloc[index.row(), index.column()]
                    if isinstance(value, (int, float, np.number)):
                        return f"{value:.6e}"
                    return str(value)
                return None

            def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
                if role == Qt.ItemDataRole.DisplayRole:
                    if orientation == Qt.Orientation.Horizontal:
                        return str(self._data.columns[section])
                    if orientation == Qt.Orientation.Vertical:
                        return str(self._data.index[section])
                return None

        return PandasModel(dataframe, title)


    def plot_matrix_async(self):
        selection = self.matrix_display_combo.currentText()

        matrix = None
        title = ""
        labels = []

        if selection == "Sigma Theta (Σ_θ)":
            matrix = self.sigma_theta_matrix
            title = "Sigma Theta Matrix Heatmap"
        elif selection == "Cross Correlations (R)":
            matrix = self.cross_correlation_matrix
            title = "Cross Correlation Matrix Heatmap"
        elif selection == "Helmert Parameters":
            matrix = self.helmert_params
            title = "Helmert Parameters"

        if matrix is None:
            QMessageBox.warning(self, "Plot Error", "No matrix data available for plotting")
            return

        if matrix.shape[0] == 14:
            labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz",
                      "tx_v", "ty_v", "tz_v", "δs_v", "εx_v", "εy_v", "εz_v"]
        elif matrix.shape[0] == 7:
            labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]
        elif selection == "Station Ei Matrix":
            labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]
        else:
            labels = []

        if matrix.size > 10000 * 10000:
            reply = QMessageBox.question(
                self, 'Large Matrix Warning',
                f"The selected matrix is very large ({matrix.shape}).\n"
                "Plot generation may be slow. Continue?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.No:
                return

        self._append_status(f"generating plot: {title}")
        set_current_figure(self)
        QApplication.processEvents()

        try:
            if selection == "Helmert Parameters":
                disp_tag = self._filter_disp() if self._is_filtered_product(selection) else ""
                subtitle = f"Shape: {matrix.shape}"
                default_name = self._default_plot_filename(selection, ext="png")
                self._render_helmert_bar(matrix, f"{title}{disp_tag}", subtitle, default_name)
                figures.add_footer(plt.gcf())
                plt.show()
            else:
                disp_tag = self._filter_disp() if self._is_filtered_product(selection) else ""
                if selection == "Cross Correlations (R)":
                    kind = "correlation"
                elif selection == "Sigma Theta (Σ_θ)":
                    kind = "sigma"
                else:
                    kind = "plain"
                fig, ax = figures.build_heatmap(matrix, kind, f"{title}{disp_tag}", labels)
                set_current_figure(self, fig)

                try:
                    default_name = self._default_plot_filename(selection, ext="png")
                    mng = plt.get_current_fig_manager()
                    if hasattr(mng, "set_window_title"):
                        mng.set_window_title(default_name)
                    fig.canvas.get_default_filename = lambda dn=default_name: dn
                except Exception:
                    pass

                plt.tight_layout()
                figures.add_footer(fig)
                plt.show()

            self._append_status("plot generated")
        except Exception as e:
            error_msg = f"Failed to generate plot: {e}"
            self._append_status(f"plot error: {error_msg}")
            logger.exception("Plotting error")
            QMessageBox.critical(self, "Plot Error", error_msg)

    def parse_station_coordinates(self, solution_estimate_data):
        return datum_math.parse_station_coordinates(solution_estimate_data)

    def create_matrix_Ei(self, x, y, z):
        return datum_math.create_matrix_Ei(x, y, z)

    def _collect_indices(self, sol):
        return datum_math.collect_indices(sol)

    def build_E(self, sol, include_vel=True, exclude_episodes=None):
        return datum_math.build_E(sol, include_vel, exclude_episodes)

    def _capture_filter_settings(self) -> AppliedFilter:
        return AppliedFilter(
            enabled=self._filter_enabled,
            pos_threshold_m=float(self._pos_threshold_m),
            vel_threshold_m_per_y=float(self._vel_threshold_m_per_y),
            manual_enabled=self._manual_filter_enabled,
            manual_episodes=frozenset(self._manual_selected_episodes),
        )

    def set_filter_options(self, enabled: bool, pos_threshold_m: float,
                           vel_threshold_m_per_y: float) -> None:
        self._filter_enabled = bool(enabled)
        self._pos_threshold_m = float(pos_threshold_m)
        self._vel_threshold_m_per_y = float(vel_threshold_m_per_y)

    @staticmethod
    def _describe_filter(settings) -> str:
        if settings.manual_enabled:
            return f"Filtering: ENABLED (manual, {len(settings.manual_episodes)} episodes)"
        if settings.enabled:
            return (f"Filtering: ENABLED (p={settings.pos_threshold_m:.3f} m, "
                    f"v={settings.vel_threshold_m_per_y:.4f} m/yr)")
        return "Filtering: DISABLED"

    def _update_filter_status(self) -> None:
        pending = self._capture_filter_settings()
        label = self.filter_status_label
        text = html.escape(self._describe_filter(pending))
        state, tone = ("ENABLED", "ok") if "ENABLED" in text else ("DISABLED", "muted")
        text = text.replace(state, f'<span style="color:{tone_color(tone, label)}">{state}</span>', 1)
        if self._applied_filter != pending:
            text += f'&nbsp;&nbsp;&nbsp;<span style="color:{tone_color("warn", label)}">(not applied yet)</span>'
        label.setText(text)

    def calculate_sigma_theta(self):
        parent_app = self.window()
        if not hasattr(parent_app, "current_data") or not parent_app.current_data:
            QMessageBox.warning(self, "Data Error", "No SINEX file loaded.")
            return

        CxL = parent_app.current_data["blocks"].get(
            "SOLUTION/MATRIX_ESTIMATE L COVA"
        )
        CxU = parent_app.current_data["blocks"].get(
            "SOLUTION/MATRIX_ESTIMATE U COVA"
        )
        Cx = CxL if CxL is not None else CxU
        if Cx is None:
            QMessageBox.warning(self, "Data Error", "Covariance matrix (Cx) not found.")
            return

        sol = parent_app.current_data["blocks"].get("SOLUTION/ESTIMATE")
        if not sol:
            QMessageBox.warning(
                self, "Data Error", "SOLUTION/ESTIMATE block not found."
            )
            return
        try:
            sol = datum_math.index_ordered(sol, Cx.shape[0])
        except datum_math.DatumError as exc:
            QMessageBox.warning(self, "Data Error", str(exc))
            return

        self._append_section("Sigma Theta computation")

        self._clear_computed_products()
        sigma_bench = benchmark.begin('compute sigma theta')
        applied = self._capture_filter_settings()
        self._applied_filter = applied
        self._update_filter_status()

        gen = self._sigma_generation
        self.sigma_theta_btn.setEnabled(False)
        run_task(_sigma_theta_job, (sol, Cx, applied),
                 lambda worker: self._sigma_theta_done(worker, gen, sol, Cx, applied, sigma_bench),
                 "Computing Sigma Theta")

    def _sigma_theta_done(self, worker, gen, sol, Cx, applied, sigma_bench):
        if gen != self._sigma_generation:
            benchmark.cancel(sigma_bench)
            logger.info("Ignoring a Sigma Theta result computed for a previous file.")
            return
        self.sigma_theta_btn.setEnabled(True)
        if worker.error is not None:
            benchmark.cancel(sigma_bench)
            logger.error(f"SigmaTheta computation error: {worker.error}")
            QMessageBox.critical(self, "Calculation Error", str(worker.error))
            return
        episodes_to_exclude, details, result = worker.result

        self._filtered_episodes_info = details
        self.show_filtered_btn.setEnabled(len(self._filtered_episodes_info) > 0)
        self._refresh_filtered_dialog_if_open()

        try:
            parent_app = self.window()
            if hasattr(parent_app, "stations_widget"):
                parent_app.stations_widget.set_excluded_episodes(episodes_to_exclude)
                parent_app.stations_widget.update_station_map()
        except Exception:
            pass

        if isinstance(result, datum_math.DatumError):
            QMessageBox.warning(self, "Data Error", str(result))
            return
        if isinstance(result, np.linalg.LinAlgError):
            benchmark.cancel(sigma_bench)
            self._clear_computed_products()
            logger.error(f"SigmaTheta computation error: {result}")
            QMessageBox.critical(self, "Calculation Error", str(result))
            return

        sigma_theta = result.sigma_theta
        self.sigma_theta_matrix = sigma_theta
        self.filtered_solution_estimate = result.filtered_sol
        self.filtered_row_idx = result.row_idx
        self._datum_run = (sol, Cx, episodes_to_exclude, details, applied, result)

        self.cross_corr_btn.setEnabled(True)
        self.export_btn.setEnabled(True)
        self.plot_btn.setEnabled(True)
        self.helmert_btn.setEnabled(True)
        benchmark.end(sigma_bench)
        self._update_stats_button()
        self._refresh_matrix_inspector_if_open()

        QMessageBox.information(
            self,
            "Success",
            f"SigmaTheta computed (filtered): "
            f"{sigma_theta.shape[0]}×{sigma_theta.shape[1]}",
        )

    def export_data(self):
        selection = self.matrix_display_combo.currentText()
        format_str = self.format_combo.currentText()

        if selection == "Sigma Theta (Σ_θ)":
            matrix = self.sigma_theta_matrix
            prefix = "sigma_theta"
        elif selection == "Cross Correlations (R)":
            matrix = self.cross_correlation_matrix
            prefix = "cross_correlations"
        elif selection == "Helmert Parameters":
            matrix = self.helmert_params
            prefix = "helmert_parameters"
        else:
            matrix = None
            prefix = "unknown"

        if matrix is None:
            QMessageBox.warning(self, "Export Error", "Selected data not available")
            return

        extension = export.FORMAT_EXTENSIONS.get(format_str, '.txt')
        parent_app = self.window()
        original_file = parent_app.current_data['metadata'].get('filename', '')
        filter_suffix = self._filter_tag() if self.is_filtered(selection) else ""
        def_name = f"{Path(original_file).stem}_{prefix}{filter_suffix}{extension}"
        filter_text = f"{format_str.split(' ')[0]} Files (*{extension})"
        out_file, _ = QFileDialog.getSaveFileName(
            self, "Save Matrix", default_save_path(def_name), filter_text
        )
        if not out_file:
            return
        remember_dialog_dir(out_file)
        out_file = ensure_suffix(out_file, extension)
        try:
            export.write_datum_matrix(matrix, out_file, extension)

            self._append_status(f"exported {selection} to {out_file}")

        except Exception as e:
            logger.exception("Export error")
            QMessageBox.critical(self, "Error", f"Failed to export matrix: {e}")

    def _source_stem(self) -> str:
        parent_app = self.window()
        name = ""
        if hasattr(parent_app, "current_data") and parent_app.current_data:
            name = parent_app.current_data["metadata"].get("filename", "")
        return Path(name).stem

    def _build_diagnostics(self):
        sol, Cx, excluded, details, applied, result = self._datum_run
        parent_app = self.window()
        data = getattr(parent_app, "current_data", None) or {}
        return reporting.build_diagnostics(
            sol, Cx, applied, excluded, details, result,
            self.cross_correlation_matrix, self.helmert_params,
            data.get("metadata", {}).get("filename", ""),
            getattr(parent_app, "_source_path", None), data.get("header"),
            self._episode_labels() or None,
        )

    def _episode_labels(self):
        return getattr(self.window(), "episode_labels", None) or {}

    def export_diagnostics_report(self):
        if (
            self._datum_run is None
            or self.cross_correlation_matrix is None
            or self.helmert_params is None
        ):
            QMessageBox.warning(
                self, "Diagnostics Report",
                "Compute SigmaTheta, Cross Correlations, and Helmert Parameters first."
            )
            return

        filter_suffix = self._filter_tag() if self.is_filtered() else ""
        def_name = reporting.report_name(self._source_stem(), filter_suffix)
        out_file, _ = QFileDialog.getSaveFileName(
            self, "Save Diagnostics Report", default_save_path(def_name), "Text Files (*.txt)"
        )
        if not out_file:
            return
        remember_dialog_dir(out_file)
        out_file = ensure_suffix(out_file, ".txt")
        self._append_section("Diagnostics report")
        self.stats_btn.setEnabled(False)
        run_task(self._build_diagnostics, (), lambda worker: self._diagnostics_done(worker, out_file),
                 "Building the diagnostics report")

    def _diagnostics_done(self, worker, out_file):
        self._update_stats_button()
        if worker.error is not None:
            logger.error(f"diagnostics report error: {worker.error}")
            QMessageBox.critical(self, "Error", f"Failed to build the report: {worker.error}")
            return
        try:
            written = reporting.write_report(worker.result, out_file)
            for name in written:
                self._append_status(f"exported diagnostics report to {name}")
        except Exception as e:
            logger.exception("Diagnostics report export error")
            QMessageBox.critical(self, "Error", f"Failed to export report: {e}")


#################################
class MatrixInspectorDialog(QDialog):
    def __init__(self, host: "DatumWidget", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Inspect Matrices")
        self.host = host

        self.matrix_combo = QComboBox()
        self.matrix_combo.addItems([
            "Sigma Theta (Σ_θ)",
            "Cross Correlations (R)",
            "Station Ei Matrix",
            "Helmert Parameters",
        ])
        # Use last selection if available
        self.matrix_combo.setCurrentText(
            getattr(self.host, "last_matrix_selection", "Sigma Theta (Σ_θ)")
        )

        self.station_combo = QComboBox()
        self._reload_station_combo()
        if getattr(self.host, "last_station_selection", ""):
            self.station_combo.setCurrentText(self.host.last_station_selection)

        row = QHBoxLayout()
        row.addWidget(QLabel("Matrix to Display:"))
        row.addWidget(self.matrix_combo)
        row.addSpacing(16)
        row.addWidget(QLabel("Station Selected:"))
        row.addWidget(self.station_combo)

        #preview
        self.preview_label = QLabel("Current matrix preview:")
        self.table = QTableView()
        self.table.setMinimumHeight(250)

        layout = QVBoxLayout(self)
        layout.addLayout(row)
        layout.addWidget(self.preview_label)
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.resize(900, 600)

        self.matrix_combo.currentIndexChanged.connect(self.update_preview)
        self.station_combo.currentIndexChanged.connect(self.update_preview)

        self.update_preview()

    def _reload_station_combo(self):
        self.station_combo.clear()
        labels = self.host._station_labels()
        self.station_combo.addItems(labels)

    def update_preview(self):
        selection = self.matrix_combo.currentText()
        station_label = self.station_combo.currentText() or None
        self.host.last_matrix_selection = selection
        if station_label:
            self.host.last_station_selection = station_label

        try:
            matrix, title, labels = self.host._prepare_display_matrix(
                selection, station_label
            )
            if matrix is None:
                df = pd.DataFrame([["No data"]])
            else:
                if matrix.ndim == 2:
                    if labels and len(labels) >= matrix.shape[0]:
                        row_labels = labels[: matrix.shape[0]]
                    else:
                        row_labels = [f"Row_{i}" for i in range(matrix.shape[0])]

                    if labels and len(labels) >= matrix.shape[1]:
                        col_labels = labels[: matrix.shape[1]]
                    else:
                        col_labels = [f"Col_{i}" for i in range(matrix.shape[1])]

                    df = pd.DataFrame(matrix, index=row_labels, columns=col_labels)
                else:
                    df = pd.DataFrame(matrix)

            model = self.host.build_pandas_df(df, title or "Matrix")
            self.table.setModel(model)
            self.table.resizeColumnsToContents()
            self.preview_label.setText(f"{title} > shape: {matrix.shape}")
        except Exception as e:
            df = pd.DataFrame([[f"Error: {e}"]])
            model = self.host.build_pandas_df(df, "Error")
            self.table.setModel(model)

class FilterOptionsDialog(QDialog):
    def __init__(self, host: "DatumWidget", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filter Options")
        self.host = host
        self.resize(520, 620)

        layout = QVBoxLayout(self)

        mode_row = QHBoxLayout()
        self.auto_radio = QRadioButton("Auto filter")
        self.manual_radio = QRadioButton("Manual selection")
        mode_row.addWidget(self.auto_radio)
        mode_row.addWidget(self.manual_radio)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self.auto_group = QGroupBox("Sigma thresholds")
        auto_layout = QVBoxLayout(self.auto_group)

        self.filter_chk = QCheckBox("Enable STDEV Filtering: Position|Velocity")
        self.filter_chk.setChecked(host._filter_enabled)
        self.filter_chk.setToolTip("Exclude station episodes when stdev in either Cx or the ESTIMATE block exceed the set threshold")
        auto_layout.addWidget(self.filter_chk)

        self.thresh_spinbox = QDoubleSpinBox()
        self.thresh_spinbox.setDecimals(3)
        self.thresh_spinbox.setRange(0.001, 100.0)
        self.thresh_spinbox.setSingleStep(0.01)
        self.thresh_spinbox.setValue(host._pos_threshold_m)
        self.thresh_spinbox.setSuffix(" m")

        self.vel_thresh_spinbox = QDoubleSpinBox()
        self.vel_thresh_spinbox.setDecimals(4)
        self.vel_thresh_spinbox.setRange(0.0001, 1.0)
        self.vel_thresh_spinbox.setSingleStep(0.001)
        self.vel_thresh_spinbox.setValue(host._vel_threshold_m_per_y)
        self.vel_thresh_spinbox.setSuffix(" m/yr")
        self.vel_thresh_spinbox.setToolTip(
            "Exclude episodes whose velocity std dev exceeds this threshold"
        )

        thresh_form = QGridLayout()
        thresh_form.addWidget(QLabel("Position:"), 0, 0)
        thresh_form.addWidget(self.thresh_spinbox, 0, 1)
        thresh_form.addWidget(QLabel("Velocity:"), 1, 0)
        thresh_form.addWidget(self.vel_thresh_spinbox, 1, 1)
        thresh_form.setColumnStretch(1, 1)
        auto_layout.addLayout(thresh_form)

        self.filter_chk.toggled.connect(self._on_filter_toggled)
        layout.addWidget(self.auto_group)

        self.manual_group = QGroupBox("Episodes to include")
        manual_layout = QVBoxLayout(self.manual_group)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.checkboxes_layout = QVBoxLayout(scroll_widget)
        scroll.setWidget(scroll_widget)
        manual_layout.addWidget(scroll)
        layout.addWidget(self.manual_group, 1)

        self.episode_checkboxes = {}
        self._populate_episodes()

        if not self.episode_checkboxes:
            self.checkboxes_layout.addWidget(
                QLabel("No episodes available. Load a SINEX file first.")
            )
            self.manual_radio.setEnabled(False)

        if host._manual_filter_enabled and self.episode_checkboxes:
            self.manual_radio.setChecked(True)
        else:
            self.auto_radio.setChecked(True)

        self.auto_radio.toggled.connect(self._on_mode_changed)

        button_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addStretch(1)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)

        self._on_mode_changed()

    def _on_filter_toggled(self, checked: bool) -> None:
        self.thresh_spinbox.setEnabled(checked)
        # vel limit counts only when filtering is on
        self.vel_thresh_spinbox.setEnabled(checked)

    def _manual_mode(self) -> bool:
        return self.manual_radio.isChecked()

    def get_auto_settings(self):
        return (self.filter_chk.isChecked(),
                float(self.thresh_spinbox.value()),
                float(self.vel_thresh_spinbox.value()))

    def _on_mode_changed(self):
        manual = self._manual_mode()
        self.auto_group.setEnabled(not manual)
        self.manual_group.setEnabled(manual)
        if not manual:
            self._on_filter_toggled(self.filter_chk.isChecked())
        self._update_checkboxes_enabled()

    def _update_checkboxes_enabled(self):
        # Enable checkboxes only in manual mode
        enabled = self._manual_mode()
        for checkbox in self.episode_checkboxes.values():
            checkbox.setEnabled(enabled)

    
    def _populate_episodes(self):
        # Get SOLUTION/ESTIMATE data
        parent_app = self.host.window()
        if not hasattr(parent_app, "current_data") or not parent_app.current_data:
            return
        
        sol = parent_app.current_data["blocks"].get("SOLUTION/ESTIMATE")
        if not sol:
            return
        
        # Get covariance matrix
        CxL = parent_app.current_data["blocks"].get("SOLUTION/MATRIX_ESTIMATE L COVA")
        CxU = parent_app.current_data["blocks"].get("SOLUTION/MATRIX_ESTIMATE U COVA")
        Cx = CxL if CxL is not None else CxU
        if Cx is None:
            return
        try:
            sol = datum_math.index_ordered(sol, Cx.shape[0])
        except datum_math.DatumError:
            pass

        # Group parameters by station
        station_episodes = {}  # {station_code: [(code, pt, soln), ...]}
        episode_params = {}  # {(code, pt, soln): [params]}
        
        for p in sol:
            code = p.get("code")
            pt = p.get("pt", "")
            soln = p.get("soln", "")
            ptype = p.get("type", "")
            
            # Only track position/velocity parameters
            if ptype not in ("STAX", "STAY", "STAZ", "VELX", "VELY", "VELZ"):
                continue
            
            episode = (code, pt, soln)
            if code not in station_episodes:
                station_episodes[code] = []
            if episode not in station_episodes[code]:
                station_episodes[code].append(episode)
            
            if episode not in episode_params:
                episode_params[episode] = []
            episode_params[episode].append(p)
        
        # Build station index for sigma extraction
        idx_map, _, _, _ = self.host._collect_indices(sol)
        
        def _get_sigma(code, ptype, pt, soln):
            idx = idx_map.get((code, ptype, pt, soln))
            if idx is None:
                return float("nan")
            v = float(Cx[idx, idx])
            if v < 0.0:
                v = 0.0
            return float(np.sqrt(v))
        
        # Create grouped checkboxes
        for station_code in sorted(station_episodes.keys()):
            # Station group label
            station_label = QLabel(f"<b>{station_code}</b>")
            self.checkboxes_layout.addWidget(station_label)
            
            for episode in sorted(station_episodes[station_code]):
                code, pt, soln = episode
                label = f"{code}.{pt}.{soln}"
                
                # Extract sigmas
                sx = _get_sigma(code, "STAX", pt, soln)
                sy = _get_sigma(code, "STAY", pt, soln)
                sz = _get_sigma(code, "STAZ", pt, soln)
                svx = _get_sigma(code, "VELX", pt, soln)
                svy = _get_sigma(code, "VELY", pt, soln)
                svz = _get_sigma(code, "VELZ", pt, soln)
                
                pos_max = max(sx, sy, sz) if not np.isnan(sx) else float("nan")
                vel_max = max(svx, svy, svz) if not np.isnan(svx) else float("nan")
                
                # Format sigma display
                sigma_text = f"σ_pos: {pos_max:.4f}m"
                if not np.isnan(vel_max):
                    sigma_text += f" | σ_vel: {vel_max:.4f}m/yr"
                
                checkbox = QCheckBox(f"  {label}  ({sigma_text})")
                
                # Default state: unchecked (all excluded by default)
                # If manual mode already active, restore previous selections
                if self.host._manual_filter_enabled and episode in self.host._manual_selected_episodes:
                    checkbox.setChecked(True)
                else:
                    checkbox.setChecked(False)
                
                self.episode_checkboxes[episode] = checkbox
                self.checkboxes_layout.addWidget(checkbox)
        
        self.checkboxes_layout.addStretch()
    
    def get_selected_mode_and_episodes(self):
        # Returns (manual_enabled, selected_episodes_set)
        manual_enabled = self.manual_radio.isChecked()
        selected = set()
        
        if manual_enabled:
            for episode, checkbox in self.episode_checkboxes.items():
                if checkbox.isChecked():
                    selected.add(episode)
        
        return manual_enabled, selected

class FilteredStationsDialog(QDialog):
    def __init__(self, host: "DatumWidget", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Filtered Stations")
        self.host = host

        self.table = QTableView()
        self.table.setMinimumHeight(320)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Episodes excluded"))
        layout.addWidget(self.table)
        self.setLayout(layout)
        self.resize(980, 520)

        self.refresh()

    def refresh(self):
        info = getattr(self.host, "_filtered_episodes_info", [])
        if not info:
            df = pd.DataFrame([["No filtered episodes"]], columns=["Info"])
            model = self.host.build_pandas_df(df, "No data")
            self.table.setModel(model)
            return

        labels = self.host._episode_labels()
        rows = []
        for it in info:
            label = labels.get((it.get("code"), it.get("pt", ""), it.get("soln"))) or {}
            # Parameters column
            params = []
            pos_excess = it.get("pos_excess", float("nan"))
            vel_excess = it.get("vel_excess", float("nan"))
            if isinstance(pos_excess, (int, float)) and pos_excess > 0:
                params.append("STAX,STAY,STAZ")
            if isinstance(vel_excess, (int, float)) and vel_excess > 0:
                params.append("VELX,VELY,VELZ")
            params_str = " | ".join(params) if params else "—"
            rows.append(
                {
                    "Episode": it.get("label", ""),  # CODE.PT.SOLN
                    "PT": it.get("pt", ""),
                    "SOLN": it.get("soln", ""),
                    "Span": label.get("span", ""),
                    "Break": label.get("break", ""),
                    "Parameters": params_str,
                    "Pos excess (m)": it.get("pos_excess", np.nan),
                    "Vel excess (m/yr)": it.get("vel_excess", np.nan),
                    # Parameter values (estimate) and sigma (from Cx)
                    "X (m)": it.get("x", np.nan),
                    "Y (m)": it.get("y", np.nan),
                    "Z (m)": it.get("z", np.nan),
                    "Vx (m/yr)": it.get("vx", np.nan),
                    "Vy (m/yr)": it.get("vy", np.nan),
                    "Vz (m/yr)": it.get("vz", np.nan),
                    "σx (m)": it.get("sx", np.nan),
                    "σy (m)": it.get("sy", np.nan),
                    "σz (m)": it.get("sz", np.nan),
                    "σVx (m/yr)": it.get("svx", np.nan),
                    "σVy (m/yr)": it.get("svy", np.nan),
                    "σVz (m/yr)": it.get("svz", np.nan),
                }
            )

        df = pd.DataFrame(rows)
        df = df.sort_values("Episode", ascending=True, kind="mergesort")
        model = self.host.build_pandas_df(df, "Filtered Stations")
        self.table.setModel(model)
        self.table.resizeColumnsToContents()

class VarianceFactorDialog(QDialog):
    def __init__(self, default_value, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Variance Factor")
        self.setFixedWidth(320)
        self._chosen_value = None

        layout = QVBoxLayout(self)

        # default radio
        self.radio_default = QRadioButton("Default (from file)")
        self.radio_default.setChecked(True)
        self.default_label = QLabel(f"  Value: {default_value}" if default_value is not None else "  Value: N/A")
        self.default_label.setStyleSheet("font-size: 11px;")

        # custom radio
        self.radio_custom = QRadioButton("Custom")
        self.custom_input = QDoubleSpinBox()
        self.custom_input.setRange(1e-12, 1e12)
        self.custom_input.setDecimals(15)
        self.custom_input.setValue(1.0)
        self.custom_input.setEnabled(False)

        self.radio_default.toggled.connect(lambda checked: self.custom_input.setEnabled(not checked))

        # buttons
        btn_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self._accept)
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addStretch()
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)

        layout.addWidget(self.radio_default)
        layout.addWidget(self.default_label)
        layout.addWidget(self.radio_custom)
        layout.addWidget(self.custom_input)
        layout.addLayout(btn_layout)

    def _accept(self):
        if self.radio_custom.isChecked():
            self._chosen_value = self.custom_input.value()
        else:
            self._chosen_value = None
        self.accept()

    def chosen_value(self):
        return self._chosen_value


class FileInfoWidget(QWidget):
    def __init__(self, main_app_ref, parent=None):
        super().__init__(parent)
        self.main_app = main_app_ref  # store to the main app
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setLayout(layout)

        # variance factor row with edit button
        vf_row = QHBoxLayout()
        self.vf_label = QLabel("Variance Factor: --")
        self.vf_edit_btn = QPushButton("Edit")
        self.vf_edit_btn.setFixedSize(40, 20)
        self.vf_edit_btn.setStyleSheet("font-size: 10px;")
        self.vf_edit_btn.clicked.connect(self._open_vf_dialog)
        vf_row.addWidget(self.vf_label)
        vf_row.addWidget(self.vf_edit_btn)
        vf_row.addStretch()

        self.station_label = QLabel("Stations Found (SITE/ID): --")
        self.aprcov_label = QLabel("Apriori Covariance Found: --")
        # font styling
        label_style = "font-size: 18px;"
        self.vf_label.setStyleSheet(label_style)
        self.station_label.setStyleSheet(label_style)
        self.aprcov_label.setStyleSheet(label_style)
        layout.addLayout(vf_row)
        layout.addWidget(self.station_label)
        layout.addWidget(self.aprcov_label)
        layout.addStretch(1) # push labels

    def _open_vf_dialog(self):
        # read the parsed default value
        default_vf = None
        if self.main_app.current_data and 'blocks' in self.main_app.current_data:
            raw = self.main_app.current_data['blocks'].get('SOLUTION/STATISTICS')
            if isinstance(raw, (float, int)):
                default_vf = raw

        dlg = VarianceFactorDialog(default_vf, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            if dlg.chosen_value() != self.main_app.get_variance_factor():
                self.main_app.covariance_widget.operations_widget.reset_for_new_file()
            self.main_app.custom_variance_factor = dlg.chosen_value()
            self.update_display()

    def update_display(self):

        if not self.main_app.current_data or 'blocks' not in self.main_app.current_data:
            self.clear_display() # reset if no data
            return

        current_blocks = self.main_app.current_data['blocks']

        # show the active variance factor (custom or parsed)
        custom_vf = getattr(self.main_app, 'custom_variance_factor', None)
        vf_data = current_blocks.get('SOLUTION/STATISTICS')
        if custom_vf is not None:
            set_status(self.vf_label, f"Variance Factor: {custom_vf} (custom)", "font-size: 18px;")
        elif vf_data is not None and isinstance(vf_data, (float, int)):
            set_status(self.vf_label, f"Variance Factor: {vf_data}", "font-size: 18px;")
        else:
            set_status(self.vf_label, "Variance Factor: Missing", "font-size: 18px;")
        site_id_block = current_blocks.get('SITE/ID')
        if site_id_block and isinstance(site_id_block, list):
            set_status(self.station_label, f"Stations Found (SITE/ID): {len(site_id_block)}", "font-size: 18px;")
        else:
            set_status(self.station_label, "Stations Found (SITE/ID): Missing", "font-size: 18px;")

        key_L = 'SOLUTION/MATRIX_APRIORI L COVA'
        data_L = current_blocks.get(key_L)
        key_U = 'SOLUTION/MATRIX_APRIORI U COVA'
        data_U = current_blocks.get(key_U)
        L_check = data_L is not None and data_L.size > 0
        U_check = data_U is not None and data_U.size > 0
        apriori_cov_found = L_check or U_check
        if apriori_cov_found:
            self.aprcov_label.setText("Apriori Covariance Found: True")
        else:
            self.aprcov_label.setText("Apriori Covariance Found: False")

    def clear_display(self):
        set_status(self.vf_label, "Variance Factor: --", "font-size: 18px;")
        set_status(self.station_label, "Stations Found (SITE/ID): --", "font-size: 18px;")
        self.aprcov_label.setText("Apriori Covariance Found: --")

