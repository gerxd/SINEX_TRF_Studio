# ui/widgets.py
import time
import io
import tempfile
import json
import numpy as np
import pandas as pd
import logging
import html
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable, Tuple

from PyQt5.QtGui import QFont, QPixmap, QImage, QColor
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QFileDialog, QMessageBox, QComboBox, QFrame, QSplitter,
    QTextEdit, QPlainTextEdit, QListWidget, QListWidgetItem, QTableView,
    QDialog, QSpacerItem, QGridLayout, QStackedLayout, QApplication,
    QCheckBox, QDoubleSpinBox, QRadioButton, QScrollArea, QLineEdit,
    QSizePolicy
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QUrl, QStandardPaths
from PyQt5.QtWebEngineWidgets import QWebEngineView, QWebEnginePage, QWebEngineSettings
import pyqtgraph as pg
import matplotlib
from matplotlib import cm, colors as mcolors
from matplotlib.patches import Patch
from pyqtgraph.exporters import ImageExporter
pg.setConfigOptions(imageAxisOrder="row-major", useOpenGL=True, antialias=False)
matplotlib.use("Qt5Agg")  # ensure Qt5 backend
import matplotlib.pyplot as plt
import seaborn as sns
import folium
from branca.element import Element
from plyer import notification
from .. import __version__

from ..core import (
    logger, benchmark, remember_dialog_dir, default_save_path, ensure_suffix,
)
from ..core import (
    logger, benchmark, remember_dialog_dir, default_save_path, ensure_suffix,
)
from ..parsers import SinexBlockParser, MatrixEstimateParser


class StationsMapPage(QWebEnginePage):
    pass

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

    def __init__(self, filename: Path, block_parsers: Dict[str, SinexBlockParser], skip_epochs_block: bool = False, skip_validation: bool = False):
        super().__init__()
        self.filename = filename
        self.block_parsers = block_parsers
        self.skip_epochs_block = skip_epochs_block  # Store the setting
        self.skip_validation = skip_validation
        self.result_data = None
        self.parse_generation = None
    def run(self):
        start_time = time.time()
        logger.info(f"Starting parse of file: {self.filename.name}")
        gen = self.parse_generation
        parse_token = benchmark.begin('parse file (all blocks)', gen)
        stream_token = None

        sinex_data = {
            'header': [],
            'blocks': {},
            'metadata': {'filename': self.filename.name}
        }
        cur_block = None
        cur_block_data = []
        total_lines = 0
        skipping_epoch = False
        statistics = None
        # streaming state for matrix blocks
        streaming_parser = None

        try:
            with open(self.filename, 'r', encoding='utf-8') as file:
                for line in file:
                    total_lines += 1
                    ln = line.rstrip()

                    if len(ln) == 0:
                        continue

                    if ln[0].startswith('*'):
                        sinex_data['header'].append(ln)
                    elif ln.startswith('+'):
                        cur_block = ln[1:].strip()
                        cur_block_data = []
                        streaming_parser = None

                        if cur_block in ['SOLUTION/EPOCHS']:
                            if self.skip_epochs_block: 
                                skipping_epoch = True
                                logger.info(f"Skipping SOLUTION/EPOCHS block")
                            else:
                                skipping_epoch = False
                                logger.info(f"Parsing block: {cur_block}")

                        if cur_block in self.block_parsers:
                            parser = self.block_parsers[cur_block]
                            if isinstance(parser, MatrixEstimateParser):
                                # find matrix dim from SOLUTION/ESTIMATE
                                est = sinex_data['blocks'].get('SOLUTION/ESTIMATE')
                                if est is not None:
                                    sz = len(est)
                                    parser.init_stream(sz)
                                    streaming_parser = parser
                                    stream_token = benchmark.begin(f'stream {cur_block}', gen)
                                    logger.info(f"Stream-parsing {cur_block} ({sz}x{sz})")
                                else:
                                    streaming_parser = None

                    elif ln.startswith('-'):
                        logger.info(f"End of reading block: {cur_block}")

                        if streaming_parser is not None and streaming_parser.is_streaming:
                            parsed = streaming_parser.finalize_stream()
                            sinex_data['blocks'][cur_block] = parsed
                            benchmark.end(stream_token)
                            stream_token = None
                            streaming_parser = None

                        elif cur_block in self.block_parsers and len(cur_block_data) > 0:
                            parser = self.block_parsers[cur_block]
                            if self.skip_validation or parser.validate(cur_block_data):
                                parsed = parser.parse(cur_block_data)
                                sinex_data['blocks'][cur_block] = parsed
                                logger.info(f"Parsed {cur_block} with {len(cur_block_data)} lines.")
                                if cur_block == 'SOLUTION/STATISTICS':
                                    statistics = cur_block_data
                                    print(statistics)

                                    logger.info(f"Values read:\n{cur_block_data}")
                                else:
                                    pass
                            else:
                                logger.debug(f"Couldn't parse block: {cur_block}")
                        else:
                            logger.debug(f"{cur_block} is not a valid block")
                        cur_block = None
                        cur_block_data = []
                        skipping_epoch = False
                    elif cur_block:
                        if not skipping_epoch:
                            if streaming_parser is not None:
                            #stream into numpy array
                                streaming_parser.feed_line(ln)
                            else:
                                cur_block_data.append(ln)
            self.result_data = sinex_data

            logger.info(f"Finished parse of {self.filename.name} in {time.time()-start_time:.3f}s.")
            logger.info(f"Total lines read: {total_lines}, blocks: {len(sinex_data['blocks'])}.")
            benchmark.end(parse_token)
            print(statistics)
            self.finished.emit()

        except Exception as e:
            benchmark.cancel(stream_token)
            benchmark.cancel(parse_token)
            logger.exception("Parsing error")
            self.error.emit(str(e))

###############################################################################
#Custom Logger Widget
###############################################################################
class QPlainTextEditLogger(logging.Handler):
    def __init__(self, text_widget):
        super().__init__()
        self.text_widget = text_widget
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

    def emit(self, record):
        #gui updates on main thread
        msg = self.format(record)
        w = self.text_widget
        if w is None:
            return
        from PyQt5.QtCore import QTimer
        def _append():
            try:
                w.appendPlainText(msg)
            except Exception:
                pass
        QTimer.singleShot(0, _append)

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
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setStyleSheet(
            "QPushButton {color: black; font-size: 16px;} "
            "QComboBox {color: black; font-size: 16px;} "
            "QLabel {color: black; font-size: 16px;}"
        )

        header_label = QLabel("Visualizer:")
        header_font = QFont()
        header_font.setPointSize(14)
        header_font.setBold(True)
        header_label.setFont(header_font)
        layout.addWidget(header_label)

        self.info_label = QLabel("Load a SINEX file to enable plotting.")
        self.info_label.setAlignment(Qt.AlignCenter)
        self.info_label.setWordWrap(True)
        self.info_label.setMinimumWidth(0)
        self.info_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        layout.addSpacing(6)
        layout.addWidget(self.info_label)

        layout.addStretch()

        matrix_row = QHBoxLayout()
        matrix_row.addWidget(QLabel("Matrix to Plot:"))
        self.matrix_combo = QComboBox()
        self.matrix_combo.setMinimumContentsLength(24)
        self.matrix_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
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
        mpl_frame.setFrameShape(QFrame.StyledPanel)
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
        pg_frame.setFrameShape(QFrame.StyledPanel)
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
        pg_note.setStyleSheet("color: #555555; font-size: 12px;")
        pg_layout.addWidget(pg_note)

        pg_controls_row = QHBoxLayout()
        pg_controls_row.addWidget(QLabel("Display Scaling:"))
        pg_controls_row.addWidget(self.display_mode_combo)
        pg_controls_row.addWidget(self.ignore_diag_levels_checkbox)
        pg_controls_row.addStretch()
        pg_layout.addLayout(pg_controls_row)

        pg_layout.addWidget(self.pg_plot_button)
        pg_layout.addWidget(self.save_full_btn)
        sections_layout.addWidget(pg_frame)

        layout.addLayout(sections_layout)
        self._has_plottable_matrix = False

        self.setLayout(layout)

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

        self.info_label.setText(
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
                                         QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply == QMessageBox.No:
                return

        self.info_label.setText(f"Generating '{plot_type}' for '{selected_key}'...")
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
                vals = np.linalg.eigvals(data)
                real_vals = np.real(vals)
                
                fig, ax = plt.subplots(figsize=(16, 10))
                set_current_figure(self, fig)
                bars = ax.bar(range(len(real_vals)), real_vals, color="#4c72b0")
                ax.axhline(0.0, color="black", linewidth=0.8)
                ax.set_xlabel("Eigenvalue Index")
                ax.set_ylabel("Eigenvalue")
                ax.set_title(f"Eigenvalue Distribution for {selected_key}\nShape: {data.shape}")
                
                # Add value labels on bars for smaller datasets
                if len(real_vals) <= 20:
                    ax.bar_label(bars, fmt="%.4g", padding=3)
                
                fig.tight_layout()
                add_plot_footer(fig)
                plt.show()
                self.info_label.setText("Plot generation complete. Select another plot or load a new file.")
                return

            ax.set_title(title)
            fig.tight_layout()
            add_plot_footer(fig)

            plt.show()

            self.info_label.setText("Plot generation complete. Select another plot or load a new file.")

        except Exception as e:
            logger.exception("Plotting error")
            self.info_label.setText(f"An error occurred during plotting: {e}")
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

        self.info_label.setText(
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
                vb.invertY(False)

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
                vals = np.linalg.eigvalsh(A)
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

            self.info_label.setText("PyQtGraph rendering complete.")
        except Exception as e:
            logger.exception("PyQtGraph plotting error")
            self.info_label.setText(f"PyQtGraph plotting error: {e}")
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
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
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
        self.info_label.setText(f"Exporting {n}×{m} image to {Path(fname).name}...")
        QApplication.processEvents()

        try:
            t_start = time.time()
            
            # Always create a new scene for export to ensure reliability
            # Scene reuse was causing issues when switching between different matrices
            logger.info("Creating off-screen scene for export")

            vmin, vmax = export_metadata["levels"]

            # M32, cmap, export_metadata = self._build_heatmap_state(selected_key, M, full_resolution=True) removed with v1.1
            # vmin, vmax = export_metadata["levels"] removed with v1.1

            img_item = pg.ImageItem(axisOrder="row-major")
            lut = cmap.getLookupTable(0.0, 1.0, 256)
            img_item.setLookupTable(lut)
            img_item.setImage(M32, autoLevels=False, levels=(vmin, vmax))

            tmp_widget = pg.GraphicsLayoutWidget()
            vb = tmp_widget.addViewBox(lockAspect=False, enableMenu=False)
            vb.invertY(False)  # Standard image orientation: top-left origin
            vb.addItem(img_item)
            vb.setRange(xRange=(0, m), yRange=(0, n), padding=0.0)
            exporter = ImageExporter(vb)
         
            exporter.parameters()["width"] = int(m)
            if "height" in exporter.parameters():
                exporter.parameters()["height"] = int(n)
            
            
            if "antialias" in exporter.parameters():
                exporter.parameters()["antialias"] = False

            logger.info(f"Exporting {n}×{m} matrix image to {fname}")
            exporter.export(fname)

            
            try:
                tmp_widget.close()
                tmp_widget.deleteLater()
            except RuntimeError:
                pass  

            elapsed = time.time() - t_start # metrics
            
            self.info_label.setText(f"Image exported to {Path(fname).name} ({elapsed:.1f}s)")
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
            self.info_label.setText("Export failed: insufficient memory")
            QMessageBox.critical(self, "Memory Error", error_msg)
            
        except Exception as e:
            logger.exception("Full-resolution image export error")
            self.info_label.setText(f"Export failed: {type(e).__name__}")
            QMessageBox.critical(
                self, "Export Error",
                f"Failed to export image:\n\n{type(e).__name__}: {e}"
            )
        
        finally:
            if 'original_text' in locals():
                from PyQt5.QtCore import QTimer
                QTimer.singleShot(3000, lambda: self.info_label.setText(original_text))

class StationsWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.stations_data = []
        self.filtered_codes = set()
        self._selected_station_code = None
        self._geojson_cache = {}
        self._map_html_path = None
        self._stale_map_html_paths = []
        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout()
        upper_layout = QHBoxLayout()
        self.splitter = QSplitter(Qt.Horizontal, self)
        self.station_list = QListWidget()
        self.info_panel = QWidget()
        self.info_panel.setMaximumWidth(300)
        self.init_info_panel()
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.update_station_map)
        self.map_view = QWebEngineView()
        self._configure_map_view()
        self.station_list.setMinimumWidth(50)
        self.station_list.setMaximumWidth(120)
        self.station_list.itemClicked.connect(self.on_click)

        vis_layout = QHBoxLayout()
        self.show_kept_chk = QCheckBox("Passed Stations")
        self.show_kept_chk.setChecked(True)
        self.show_kept_chk.toggled.connect(self.update_station_map)
        self.show_filtered_chk = QCheckBox("Filtered Stations")
        self.show_filtered_chk.setChecked(True)
        self.show_filtered_chk.toggled.connect(self.update_station_map)
        vis_layout.addWidget(self.show_kept_chk)
        vis_layout.addWidget(self.show_filtered_chk)
        vis_layout.addStretch(1)

        self.splitter.addWidget(self.station_list)
        self.splitter.addWidget(self.info_panel)
        self.splitter.addWidget(self.map_view)
        self.splitter.setSizes([150, 300, 550])
        self.splitter.setStretchFactor(0, 1)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 4)
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
            QStandardPaths.writableLocation(QStandardPaths.TempLocation)
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
            QWebEngineSettings.LocalContentCanAccessFileUrls, True
        )
        self.map_view.settings().setAttribute(
            QWebEngineSettings.LocalContentCanAccessRemoteUrls, False
        )
        self.map_view.loadFinished.connect(self._on_map_load_finished)

    def set_filtered_codes(self, codes: set):
        self.filtered_codes = set(codes or [])
        self.populate_station_list()

    def set_data(self, stations_data: List[dict]):
        self.stations_data = stations_data
        #new stations -> new file -> ignore past filtering data
        self.filtered_codes = set()

        available_codes = {station.get("code") for station in stations_data}
        if self._selected_station_code not in available_codes:
            self._selected_station_code = None
        if self._selected_station_code is None and stations_data:
            self._selected_station_code = stations_data[0].get("code")
        if self._selected_station_code is None:
            self.display.setText("select a station")

        self.populate_station_list()
        self.update_station_map()

    def populate_station_list(self):
        self.station_list.clear()
        for station in self.stations_data:
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

    def _visible_stations(self, show_kept: bool, show_filtered: bool) -> List[dict]:
        visible = []
        for station in self.stations_data:
            code = station.get("code")
            is_filtered = code in self.filtered_codes
            if is_filtered and not show_filtered:
                continue
            if (not is_filtered) and not show_kept:
                continue
            visible.append(station)
        return visible

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
        for station in visible_stations:
            code = station["code"]
            lat = station["latitude"]
            lon = station["longitude"]
            is_filtered = code in self.filtered_codes
            marker_color = "#c0392b" if is_filtered else "#2e8b57"
            is_selected = selected_station is not None and code == selected_station.get("code")

            popup_html = f"<b>{code}</b><br>Lat: {lat:.4f}<br>Lon: {lon:.4f}"
            folium.CircleMarker(
                [lat, lon],
                radius=8 if is_selected else 6,
                color="#111111" if is_selected else marker_color,
                fill=True,
                fill_color=marker_color,
                fill_opacity=0.92,
                weight=2 if is_selected else 1,
                popup=popup_html,
                tooltip=code,
            ).add_to(fol_map)

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
        if not self.stations_data:
            self._queue_map_html_cleanup()
            self._set_map_message("No Station Data")
            self._prune_map_html()
            return

        show_kept = self.show_kept_chk.isChecked()
        show_filtered = self.show_filtered_chk.isChecked()
        visible_stations = self._visible_stations(show_kept, show_filtered)

        if self._selected_station_code is not None:
            self._update_station_info(self._selected_station_code)

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
        matches = self.station_list.findItems(station_code, Qt.MatchExactly)
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
        self.display.setText(info_str)

    def on_click(self, item):
        self._selected_station_code = item.text()
        self._set_current_station_item(self._selected_station_code)
        self._update_station_info(self._selected_station_code)
        self.update_station_map(focus_selected=True)

###############################################################################
#OperationsWidget
###############################################################################
class OperationsWidget(QWidget):
    def __init__(self, parent=None, get_current_matrix_func=None, export_func=None):
        super().__init__(parent)
        self.get_current_matrix_func = get_current_matrix_func
        self.export_func = export_func

        self._normal_matrix = None
        self._apriori_matrix = None
        self._u_vector = None
        self._dx_vector = None

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        self.setStyleSheet("QPushButton {color: black; font-size: 16px;} QComboBox {color: black; font-size: 16px;} QLabel {color: black; font-size: 16px;}")

        button_layout = QHBoxLayout()
        
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
        export_line.addWidget(QLabel("Export Format:"))
        export_line.addWidget(self.format_combo)

        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.export_data)
        export_line.addWidget(self.export_btn)

        layout.addLayout(export_line)

    def reset_for_new_file(self):
        self._normal_matrix = None
        self._apriori_matrix = None
        self._u_vector = None
        self._dx_vector = None
        self.rank_btn.setEnabled(False)
    
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
                f"{minutes:.0f} minutes, during which the window will not "
                f"respond.{chr(10)}{chr(10)}Proceed?",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                return
        self.log_text.appendPlainText(
            f"Computing rank of a {N.shape[0]}x{N.shape[0]} matrix by SVD, this may take a while."
        )
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            t0 = time.time()
            with benchmark.span('rank of N (SVD)'):
                rank_n = np.linalg.matrix_rank(N)
            elapsed = time.time() - t0
        finally:
            QApplication.restoreOverrideCursor()
        logger.info(f"rank(N): {rank_n}/{N.shape[0]}")
        logger.info(f"Rank computed in {elapsed:.3f} seconds.")
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


        Qx = Cov_final / var_factor

        apr_key = "SOLUTION/MATRIX_APRIORI L COVA"
        apr_data = parent_app.current_data['blocks'].get(apr_key)
        if apr_data is None:
            apr_key = "SOLUTION/MATRIX_APRIORI U COVA"
            apr_data = parent_app.current_data['blocks'].get(apr_key)

        if apr_data is not None:
            from ..core import align_apriori_info_matrix, inflate_or_trim_matrix
            final_dim = Qx.shape[0]
            self._apriori_matrix = inflate_or_trim_matrix(apr_data, final_dim)
            logger.info(f"Apriori covariance block found in {apr_key}, dimension={apr_data.shape}.")
            # N = inv(Qx) - inv(C0): remove apriori constraint in information space
            try:
                N = np.linalg.inv(Qx) - align_apriori_info_matrix(apr_data, final_dim)
            except np.linalg.LinAlgError:
                benchmark.cancel(normal_bench)
                QMessageBox.critical(self, "Error", "Inversion failed during N = inv(Qx) - inv(C0).")
                logger.warning("Inversion failed during N = inv(Qx) - inv(C0) => Normal matrix not set.")
                return
            logger.info("Normal matrix computed as N = inv(Qx) - inv(C0).")
        else:
            self._apriori_matrix = None
            logger.info("No apriori covariance block found.")
            try:
                N = np.linalg.inv(Qx)
            except np.linalg.LinAlgError:
                benchmark.cancel(normal_bench)
                QMessageBox.critical(self, "Error", "Inversion of Qx failed.")
                logger.warning("Failed to invert Qx => Normal matrix not set.")
                return
            logger.info("Normal matrix computed as N = inv(Qx).")

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

        apr_lookup = {
            (p['code'], p['type'], p['pt'], p['soln']): p['value']
            for p in apr_data
        }

        n = len(est_data)
        if self._normal_matrix.shape[0] != n:
            QMessageBox.critical(
                self,
                "Error",
                "Dimension mismatch between Normal matrix and parameter count."
            )
            return

        u_bench = benchmark.begin('compute u = N*dx')
        dx = np.zeros((n, 1), dtype=float)
        unmatched_count = 0

        # make dx by going through the estimate data and matching each parameter to its a priori value.
        for i, p_est in enumerate(est_data):
            key = (p_est['code'], p_est['type'], p_est['pt'], p_est['soln'])
            val_apr = apr_lookup.get(key, 0.0)
            if key not in apr_lookup:
                unmatched_count += 1
            dx[i, 0] = p_est['value'] - val_apr

        if unmatched_count > 0:
            logger.warning(
                f"{unmatched_count} parameters in ESTIMATE were not found in APRIORI. "
                "Their a priori values were assumed to be 0."
            )

        u = self._normal_matrix @ dx
        self._u_vector = u
        self._dx_vector = dx  # store for reverse ver

        benchmark.end(u_bench)
        dur = time.time() - t0
        logger.info(f"Computed u in {dur:.3f}s, shape={u.shape}")
        QMessageBox.information(self, "Success", "u = N*(Xest - Xapr) computed.")

    def reverse_verify(self):
        if self._normal_matrix is None:
            self.log_text.appendPlainText("Error: Normal Matrix (N) not computed yet.")
            return
        if self._u_vector is None:
            self.log_text.appendPlainText("Error: Vector u not computed yet.")
            return
        if self._dx_vector is None:  
            self.log_text.appendPlainText("Error: Original dx vector not available.")
            return

        self.log_text.appendPlainText("\n------ Performing Recomputation Check ------")
        u_computed = self._u_vector
        dx_original = self._dx_vector

        # Verification: dx' = N^-1 * u  (since u = N * dx must never give dx)
        try:
            N_inv = np.linalg.inv(self._normal_matrix)
        except np.linalg.LinAlgError:
            self.log_text.appendPlainText("Error: Cannot invert N for verification.")
            return

        dx_recomputed = N_inv @ u_computed
        difference_vector = dx_original - dx_recomputed
        #for informative purposes mostly
        # L2 Norm
        l2_norm_of_difference = np.linalg.norm(difference_vector)
        # Infinity Norm
        inf_norm_of_difference = np.linalg.norm(difference_vector, ord=np.inf)
        # Relative error
        norm_of_original = np.linalg.norm(dx_original)
        self.log_text.appendPlainText(f"Original dx norm: {norm_of_original:.6e}")
        self.log_text.appendPlainText(f"Recomputed dx norm: {np.linalg.norm(dx_recomputed):.6e}")
        self.log_text.appendPlainText(f"Difference Norm (L2): {l2_norm_of_difference:.6e}")
        self.log_text.appendPlainText(f"Max Absolute Difference: {inf_norm_of_difference:.6e}")
        if norm_of_original == 0:
            # if dx is zero u = N*dx is zero for any N 
            self.log_text.appendPlainText(
                "recomp check is invalid because dx is zero "              
            )
            return
        relative_error = l2_norm_of_difference / norm_of_original
        self.log_text.appendPlainText(f"Relative Error: {relative_error:.6e} (or {relative_error:.4%})")
        if relative_error > 1e-6:
            self.log_text.appendPlainText(
                "WARNING: relative_error > 1e-6")
        else:
            self.log_text.appendPlainText("PASS: relative_error <= 1e-6")



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

        self.export_func(data_to_export, fmt, prefix)

class CovarianceMatrixWidget(QWidget):
#wrapper for operations + mvw
    def __init__(self, parent=None, get_current_matrix_func=None, export_func=None):
        super().__init__(parent)
        self.operations_widget = OperationsWidget(
            parent=self,
            get_current_matrix_func=get_current_matrix_func,
            export_func=export_func,
        )
        self.visualizer_widget = MatrixVisualizerWidget(parent=self)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self.operations_widget)
        splitter.addWidget(self.visualizer_widget)
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([1100, 450])
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
        "PyQt5>=5.15.0",
        "PyQtWebEngine>=5.15.0",
        "pyqtgraph>=0.13.0",
        "numpy>=1.19.0",
        "pandas>=1.1.0",
        "matplotlib>=3.3.0",
        "seaborn>=0.11.0",
        "folium>=0.12.0",
        "openpyxl>=3.0.0",
        "plyer>=2.0.0",
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
        self.logo_label.setAlignment(Qt.AlignCenter)
        self.logo_label.setMinimumSize(160, 160)
        self.logo_label.setStyleSheet(
            "QLabel {background-color: #ffffff; border: 1px solid #dcdcdc; padding: 12px;}"
        )

        logo_path = Path(__file__).resolve().parents[2] / "logo2.jpg"
        pixmap = QPixmap(str(logo_path)) if logo_path.exists() else QPixmap()
        if pixmap.isNull():
            self.logo_label.setText("SINEX Studio")
        else:
            self.logo_label.setPixmap(
                pixmap.scaled(180, 180, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            )

        header_layout.addWidget(self.logo_label, 0, Qt.AlignTop)
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

        contact_body = QLabel(
            "Gerasimos M. Dossas\n @ gerasimos.dossas@gmail.com"
        )
        contact_body.setWordWrap(True)
        contact_body.setTextInteractionFlags(Qt.TextSelectableByMouse)

        details_layout.addWidget(title_label)
        details_layout.addWidget(subtitle_label)
        details_layout.addWidget(version_label)
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
        note_label.setStyleSheet("color: #555555;")
        layout.addWidget(note_label)

        requirements_widget = QTextEdit()
        requirements_widget.setReadOnly(True)
        requirements_widget.setStyleSheet(
            "QTextEdit {background-color: #ffffff; border: 1px solid #dcdcdc; padding: 8px;}"
        )

        requirements_html = ["<h3>Dependencies</h3><ul>"]
        for dep in self.DEPENDENCIES:
            requirements_html.append(f"<li>{html.escape(dep)}</li>")
        requirements_html.append("</ul>")

        requirements_widget.setHtml("".join(requirements_html))

        overview_widget = QTextEdit()
        overview_widget.setReadOnly(True)
        overview_widget.setStyleSheet(
            "QTextEdit {background-color: #ffffff; border: 1px dashed #b0b0b0; padding: 12px;}"
        )
        overview_widget.document().setDefaultStyleSheet(
            "body { color: #222222; font-size: 13px; }"
            "h3 { margin: 0 0 10px 0; font-size: 16px; font-weight: 600; }"
            "h4 { margin: 14px 0 6px 0; font-size: 13px; font-weight: 600; color: #3a3a3a; }"
            "p { margin: 0 0 10px 0; color: #5a5a5a; }"
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
        self._setup_ui()
        self._filtered_episodes_info = []  # list of dicts with details
        self._filtered_dialog = None  # dialog instance

    def _setup_ui(self):
        # defaults for matrices
        self._legend_sigma_decimals = 3
        self._legend_sigma_nbins = 12
        self._legend_sigma_minor_div = 2


        layout = QVBoxLayout(self)
        self.setStyleSheet(
            "QPushButton {color: black; font-size: 16px;} QComboBox {color: black; font-size: 16px;} QLabel {color: black; font-size: 16px;}")

        buttons_layout = QHBoxLayout()

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

        # --- Filtering Controls ---
        self.filter_chk = QCheckBox("Enable STDEV Filtering: Position|Velocity")
        self.filter_chk.setChecked(True)
        self.filter_chk.setToolTip("Exclude station episodes when stdev in either Cx or the ESTIMATE block exceed the set threshold")
        buttons_layout.addWidget(self.filter_chk)

        self.thresh_spinbox = QDoubleSpinBox()
        self.thresh_spinbox.setDecimals(3)
        self.thresh_spinbox.setRange(0.001, 100.0)
        self.thresh_spinbox.setSingleStep(0.01)
        self.thresh_spinbox.setValue(0.050)  # Default 5 cm
        self.thresh_spinbox.setSuffix(" m")
        buttons_layout.addWidget(self.thresh_spinbox)

        self.vel_thresh_spinbox = QDoubleSpinBox()
        self.vel_thresh_spinbox.setDecimals(4)
        self.vel_thresh_spinbox.setRange(0.0001, 1.0)
        self.vel_thresh_spinbox.setSingleStep(0.001)
        self.vel_thresh_spinbox.setValue(0.003)  # 3 mm/yr
        self.vel_thresh_spinbox.setSuffix(" m/yr")
        self.vel_thresh_spinbox.setToolTip(
               "Exclude episodes whose velocity std dev exceeds this threshold"
           )
        buttons_layout.addWidget(self.vel_thresh_spinbox)

          # thresholds toggle
        self.filter_chk.toggled.connect(self._on_filter_toggled)
        self._on_filter_toggled(self.filter_chk.isChecked())

        self.show_filtered_btn = QPushButton("Show Filtered Stations")
        self.show_filtered_btn.setToolTip("List episodes removed by current filtering")
        self.show_filtered_btn.setEnabled(False)
        self.show_filtered_btn.clicked.connect(self.open_filtered_episodes_dialog)
        buttons_layout.addWidget(self.show_filtered_btn)
        
        self.manual_select_btn = QPushButton("Manual Episode Selection")
        self.manual_select_btn.setToolTip("Manually select episodes to include (bypasses auto-filter)")
        self.manual_select_btn.clicked.connect(self.open_manual_selection_dialog)
        buttons_layout.addWidget(self.manual_select_btn)
        
        layout.addLayout(buttons_layout)
        middle_layout = QHBoxLayout()

        self.status_text = QPlainTextEdit()
        cur_font = self.status_text.font()
        cur_font.setPointSize(14)
        self.status_text.setFont(cur_font)
        self.status_text.setReadOnly(True)
        middle_layout.addWidget(self.status_text)


        layout.addLayout(middle_layout)

        controls_layout = QGridLayout()
        self.inspect_btn = QPushButton("Inspect Matrices")
        self.inspect_btn.setToolTip("Open matrix/episode selector and preview")
        self.inspect_btn.clicked.connect(self.open_matrix_inspector)
        controls_layout.addWidget(self.inspect_btn, 0, 0, 1, 4)

        self.matrix_display_combo = QComboBox()
        self.matrix_display_combo.addItems([
            "Sigma Theta (Σ_θ)",
            "Cross Correlations (R)",
            "Helmert Parameters",
        ])

        controls_layout.addWidget(QLabel("Matrix to Export:"), 1, 0)
        controls_layout.addWidget(self.matrix_display_combo, 1, 1)

        self.stats_btn = QPushButton("Export Statistics Report")
        self.stats_btn.setToolTip(
            "Export a text report with the analysis' relevant statistics"
        )
        self.stats_btn.clicked.connect(self.export_stats_report)
        self.stats_btn.setEnabled(False)
        controls_layout.addWidget(self.stats_btn, 1, 2, 1, 2)

        # Export/plot row
        controls_layout.addWidget(QLabel("Export Format:"), 2, 0)
        self.format_combo = QComboBox()
        self.format_combo.addItems(["Excel (.xlsx)", "CSV (.csv)", "Text (.txt)", "NumPy (.npy)"])
        controls_layout.addWidget(self.format_combo, 2, 1)

        self.export_btn = QPushButton("Export")
        self.export_btn.clicked.connect(self.export_data)
        self.export_btn.setEnabled(False)
        controls_layout.addWidget(self.export_btn, 2, 2)

        self.plot_btn = QPushButton("Plot Matrix")
        self.plot_btn.clicked.connect(self.plot_matrix_async)
        self.plot_btn.setEnabled(False)
        controls_layout.addWidget(self.plot_btn, 2, 3)

        layout.addLayout(controls_layout)

        self.last_matrix_selection = "Sigma Theta (Σ_θ)"
        self.last_station_selection = ""

        self._matrix_inspector = None

    def _append_status(self, message: str) -> None:
        self.status_text.appendPlainText(message.strip())

    def _append_section(self, title: str) -> None:
        if self.status_text.toPlainText().strip():
            self.status_text.appendPlainText("")
        self.status_text.appendPlainText(f"--- {title.strip()} ---")

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


        self.sigma_theta_matrix = None
        self.cross_correlation_matrix = None
        self.helmert_params = None
        self.filtered_solution_estimate = None
        self.filtered_Cx = None
        self.filtered_row_idx = None
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

        if stations is not None and getattr(stations, "filtered_codes", None):
            stations.set_filtered_codes(set())
            stations.update_station_map()

    def _reset_output_state(self) -> None:
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

        self._clear_matrix_display("Cleared previous results for new file")

    def log_new_file_loaded(self, filename: str) -> None:
        self._clear_station_cache()  # Reset cache on new file load
        self._manual_filter_enabled = False  # Reset manual filter mode
        self._manual_selected_episodes = set()  # Clear manual selections
        label = Path(filename).name
        self._append_section(f"NEW FILE: {label}")
        self._reset_output_state()

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

    def open_manual_selection_dialog(self):
        dlg = ManualEpisodeSelectionDialog(self, parent=self)
        if dlg.exec_() == QDialog.Accepted:
            manual_enabled, selected_episodes = dlg.get_selected_mode_and_episodes()
            self._manual_filter_enabled = manual_enabled
            self._manual_selected_episodes = selected_episodes
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

    def _on_filter_toggled(self, checked: bool) -> None:
        self.thresh_spinbox.setEnabled(checked)
        # vel limit counts only when filtering is on
        self.vel_thresh_spinbox.setEnabled(checked)

    def detect_bad_episodes_cx(
            self,
            sol: list,
            Cx: np.ndarray,
            pos_threshold_m: float = 0.05,
            vel_threshold_m_per_y: float = 0.003,
            include_vel: bool = True,
    ):
        """
        Detect episodes with excessive coordinate uncertainties by examining diagonal covariance elements.
        
        Filters episodes where position (STAX/STAY/STAZ) or velocity (VELX/VELY/VELZ) standard deviations
        exceed specified thresholds. Uses the maximum component uncertainty for each episode.
        
        Returns (excluded_episodes_set, details_list).
        details_list contains dicts with episode label, sds, thresholds, and exceedance/score.
        """
        # Map parameter indices and identify which episodes have position/velocity data
        idx_map, coords, have_pos, have_vel = self._collect_indices(sol)

        def _sd(i: int) -> float:
            """Extract standard deviation from diagonal covariance element, clamping negatives to zero."""
            v = float(Cx[i, i])
            if v < 0.0:
                v = 0.0
            return float(np.sqrt(v))

        excluded = set()
        details = []

        # Statistics for reporting
        n_total_pos = 0
        n_excl_pos = 0
        n_considered_vel = 0
        n_excl_vel = 0

        for episode in sorted(have_pos):
            code, pt, soln = episode
            try:
                # Retrieve matrix indices for X, Y, Z position components
                ix = idx_map[(code, "STAX", pt, soln)]
                iy = idx_map[(code, "STAY", pt, soln)]
                iz = idx_map[(code, "STAZ", pt, soln)]
            except KeyError:
                continue

            n_total_pos += 1
            # Compute standard deviations from covariance diagonal
            sx, sy, sz = _sd(ix), _sd(iy), _sd(iz)
            pos_max = max(sx, sy, sz)
            pos_excess = max(0.0, pos_max - pos_threshold_m)

            # Check velocity
            vel_present = include_vel and (episode in have_vel)
            svx = svy = svz = np.nan
            vel_max = 0.0
            vel_excess = 0.0
            if vel_present:
                ivx = idx_map.get((code, "VELX", pt, soln))
                ivy = idx_map.get((code, "VELY", pt, soln))
                ivz = idx_map.get((code, "VELZ", pt, soln))
                if None not in (ivx, ivy, ivz):
                    n_considered_vel += 1
                    svx, svy, svz = _sd(ivx), _sd(ivy), _sd(ivz)
                    vel_max = max(svx, svy, svz)
                    vel_excess = max(0.0, vel_max - vel_threshold_m_per_y)

            # Determine if episode should be excluded and which component triggered it
            trig = ""
            exclude = False
            if pos_excess > 0.0:
                exclude = True
                trig = "pos"
                n_excl_pos += 1
            if vel_present and vel_excess > 0.0:
                exclude = True
                trig = "vel" if vel_excess >= pos_excess else trig
                if trig == "vel":
                    n_excl_vel += 1

            if exclude:
                excluded.add(episode)

            # Compute normalized exceedance score (how many times over threshold)
            score = 0.0
            if pos_threshold_m > 0.0 and pos_excess > 0.0:
                score = max(score, pos_excess / pos_threshold_m)
            if vel_threshold_m_per_y > 0.0 and vel_excess > 0.0:
                score = max(score, vel_excess / vel_threshold_m_per_y)

            details.append(
                {
                    "label": f"{code}.{pt}.{soln}",
                    "code": code,
                    "pt": pt,
                    "soln": soln,
                    "trigger": trig if exclude else "",
                    "pos_max": pos_max,
                    "pos_thr": pos_threshold_m,
                    "pos_excess": pos_excess,
                    "vel_max": vel_max if vel_present else np.nan,
                    "vel_thr": vel_threshold_m_per_y if vel_present else np.nan,
                    "vel_excess": vel_excess if vel_present else np.nan,
                    "sx": sx,
                    "sy": sy,
                    "sz": sz,
                    "svx": svx,
                    "svy": svy,
                    "svz": svz,
                    "score": score if exclude else 0.0,
                    "excluded": exclude,
                }
            )

        self._append_status(
            "[Filter] episode filter (Cx diag): "
            f"pos_total={n_total_pos}, pos_excluded={n_excl_pos}, "
            f"vel_considered={n_considered_vel}, vel_excluded={n_excl_vel}"
        )
        return excluded, details

    def _build_keep_mask(self, sol: list, episodes_to_exclude: set, remove_vel: bool = True) -> np.ndarray:
        """
        Build boolean mask over sol est values.
        Omits STAX/STAY/STAZ/VELX/VELY/VELZ of excluded episodes.
        If remove_vel is True, omits all VELX/VELY/VELZ regardless.
        """
        station_pos = {"STAX", "STAY", "STAZ"}
        station_vel = {"VELX", "VELY", "VELZ"}
        keep = np.ones(len(sol), dtype=bool)
        filtered_pos = 0
        filtered_vel = 0

        for i, p in enumerate(sol):
            t = p.get("type", "")

            # Only check parameters that are part of an episode
            if t in station_pos or t in station_vel:
                key = (p.get("code"), p.get("pt", ""), p.get("soln"))

                # Condition 1: Exclude if the episode is in the bad list
                if key in episodes_to_exclude:
                    keep[i] = False
                    if t in station_pos:
                        filtered_pos += 1
                    else:
                        filtered_vel += 1

                # Condition 2: ALSO exclude if it's a velocity and remove_vel is flagged
                elif remove_vel and t in station_vel:
                    keep[i] = False
                    filtered_vel += 1

        self._append_status(
            f"[Filter] filter mask: kept={int(keep.sum())}/{len(keep)} "
            f"(filtered_pos={filtered_pos}, filtered_vel={filtered_vel})"
        )
        return keep

    def detect_bad_episodes(self, sol, threshold=0.05):
        """
        Flag episodes where any parameter's sigma exceeds the threshold.
        Returns a set of episode keys (station_code, point_code, solution_id).
        """
        # Group parameters by episode: each episode may have multiple parameter types (STAX, STAY, etc.)
        episodes: Dict[tuple, list] = {}
        for p in sol:
            key = (p.get("code"), p.get("pt"), p.get("soln"))
            episodes.setdefault(key, []).append(p)

        excluded_episodes = set()
        self._append_status(f"[Filter] filtering episodes with sigma > {threshold:.3f}")

        zero_sigma = sum(1 for p in sol if p.get("sigma", 0.0) == 0.0)
        if zero_sigma:
            message = f"[Filter] {zero_sigma} of {len(sol)} parameters have sigma 0 and cannot be flagged by this filter"
            self._append_status(message)
            logger.warning(message)
        
        # For each episode, check if any parameter exceeds the threshold and flag entire episode if any single parameter is bad
        for key, params in episodes.items():
            for p in params:
                sigma = p.get("sigma", float("inf"))
                if sigma > threshold:
                    excluded_episodes.add(key)
                    logger.debug(
                        f"[Filter] Flagging episode {key} for exclusion (sigma = {sigma:.3f})"
                    )
                    break  # No point checking remaining parameterss for this episode

        if excluded_episodes:
            logger.info(
                f"[Filter] flagged {len(excluded_episodes)} episodes for exclusion"
            )
            self._append_status(
                f"[Filter] flagged {len(excluded_episodes)} episodes for exclusion"
            )
        return excluded_episodes

    def calculate_cross_correlations(self):
       # cross-correlation matrix R(i,j) = cov(ij)/(σi * σj)
        if self.sigma_theta_matrix is None:
            QMessageBox.warning(self, "Warning", "Calculate Sigma Theta matrix first.")
            return

        try:
            bench = benchmark.span('compute cross correlations')
            bench.__enter__()
            self._append_section("Cross Correlation calculation")

            sigma_theta = self.sigma_theta_matrix
            n = sigma_theta.shape[0]
            self._append_status(f"sigma theta shape: {sigma_theta.shape}")
            diagonal_elements = np.diag(sigma_theta)
            std_devs = np.sqrt(diagonal_elements)
            self._append_status(
                f"std dev range: {np.min(std_devs):.6e} to {np.max(std_devs):.6e}"
            )
            cross_corr = np.zeros_like(sigma_theta)

            for i in range(n):
                for j in range(n):
                    if std_devs[i] <= 1e-15 or std_devs[j] <= 1e-15:
                        self._append_status(
                            f"Warning: Near-zero std dev for indices {i}, {j}"
                        )
                    with np.errstate(divide='ignore', invalid='ignore'):
                        cross_corr[i, j] = sigma_theta[i, j] / (std_devs[i] * std_devs[j])
            self.cross_correlation_matrix = cross_corr

            # statistics
            off_diagonal_mask = ~np.eye(n, dtype=bool)
            off_diagonal_values = cross_corr[off_diagonal_mask]
            min_corr = np.min(off_diagonal_values)
            max_corr = np.max(off_diagonal_values)
            mean_abs_corr = np.mean(np.abs(off_diagonal_values))

            self._append_status(
                "cross correlation stats: "
                f"shape={cross_corr.shape}, min={min_corr:.6f}, "
                f"max={max_corr:.6f}, mean_abs={mean_abs_corr:.6f}"
            )
            self._append_status(f"cond(R): {np.linalg.cond(cross_corr):.2e}")
            # check matrix is symmetric
            symmetry_error = np.max(np.abs(cross_corr - cross_corr.T))
            if symmetry_error > 1e-12:
                self._append_status(
                    f"matrix symmetry delta {symmetry_error:.6e} (exceeds tolerance)"
                )
            else:
                self._append_status(f"matrix symmetry passed check: max|R - R^T| = {symmetry_error:.6e}")

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
        arr = np.asarray(values, dtype=float).flatten()
        if arr.size == 0:
            raise ValueError("No Helmert parameters available for plotting")

        if arr.size == 7:
            labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz"]
        elif arr.size == 14:
            labels = ["tx", "ty", "tz", "δs", "εx", "εy", "εz",
                      "tx_v", "ty_v", "tz_v", "δs_v", "εx_v", "εy_v", "εz_v"]
        else:
            labels = [f"p{i + 1}" for i in range(arr.size)]

        # Display only, stored values stay in SI. Rotation and scale times the Earth
        # radius give the displacement at the surface, so all bars read in mm.
        conv_val = 6378137000.0  # GRS80 semi-major axis in mm
        GROUPS = {
            "translation": (1e3, "mm", "#4c72b0"),
            "scale": (conv_val, "mm", "#dd8452"),
            "rotation": (conv_val, "mm", "#55a868"),
            "other": (1.0, "", "#8172b3"),
        }

        def group_of(label):
            base = label.split("_")[0]
            if base in ("tx", "ty", "tz"):
                return "translation"
            if base in ("δs",):
                return "scale"
            if base in ("εx", "εy", "εz"):
                return "rotation"
            return "other"

        groups = [group_of(l) for l in labels]
        scaled = np.array([arr[i] * GROUPS[g][0] for i, g in enumerate(groups)])
        colors = [GROUPS[g][2] for g in groups]
        rate = [l.endswith("_v") for l in labels]
        tick_labels = [
            f"{l}\n[{GROUPS[g][1]}{'/yr' if r else ''}]" if GROUPS[g][1] else l
            for l, g, r in zip(labels, groups, rate)
        ]

        fig, ax = plt.subplots(figsize=(16, 10))
        set_current_figure(self, fig)
        ax.format_coord = lambda x, y: ""  # blank the toolbar cursor readout
        bars = ax.bar(tick_labels, scaled, color=colors)
        ax.grid(axis="y", linestyle=":", linewidth=0.6, color="gray")
        ax.set_axisbelow(True)
        ax.axhline(0.0, color="black", linewidth=0.8)
        present = [g for g in GROUPS if g in groups]
        if len(present) > 1:
            ax.legend(
                handles=[Patch(facecolor=GROUPS[g][2],
                               label=f"{g} [{GROUPS[g][1]}]" if GROUPS[g][1] else g)
                         for g in present],
                loc="best",
            )
        if subtitle:
            ax.set_title(f"{title}\n{subtitle}")
        else:
            ax.set_title(title)
        ax.bar_label(bars, fmt="%.4g", padding=6)
        fig.tight_layout()

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
                sigma_theta = self.sigma_theta_matrix
                st_diag = np.diag(sigma_theta)
                hparam = np.sqrt(st_diag).reshape(-1, 1)
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
        
        if not self.filter_chk.isChecked():
            return False
        if not getattr(self, "_filtered_episodes_info", None):
            return False
        if selection is None:
            return True
        return selection in ("Sigma Theta (Σ_θ)", "Cross Correlations (R)", "Helmert Parameters")

    def _filter_tag(self) -> str:
        try:
            if self.is_filtered():
                pos_mm = int(round(float(self.thresh_spinbox.value()) * 1000.0))
                vel_mm = int(round(float(self.vel_thresh_spinbox.value()) * 1000.0))
                return f"_filtered_p{pos_mm}mm_v{vel_mm}mmyr"
        except Exception:
            pass
        return ""

    def _filter_disp(self) -> str:
        try:
            if self.is_filtered():
                p = float(self.thresh_spinbox.value())
                v = float(self.vel_thresh_spinbox.value())
                return f" [filtered p={p:.3f} m, v={v:.3f} m/yr]" #
        except Exception:
            pass
        return ""

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
        from PyQt5.QtCore import QAbstractTableModel, Qt, QModelIndex

        class PandasModel(QAbstractTableModel):
            def __init__(self, data, title=""):
                super().__init__()
                self._data = data
                self._title = title
            def rowCount(self, parent=QModelIndex()):
                return self._data.shape[0]
            def columnCount(self, parent=QModelIndex()):
                return self._data.shape[1]
            def data(self, index, role=Qt.DisplayRole):
                if not index.isValid():
                    return None

                if role == Qt.DisplayRole:
                    value = self._data.iloc[index.row(), index.column()]
                    if isinstance(value, (int, float, np.number)):
                        return f"{value:.6e}"
                    return str(value)
                return None

            def headerData(self, section, orientation, role=Qt.DisplayRole):
                if role == Qt.DisplayRole:
                    if orientation == Qt.Horizontal:
                        return str(self._data.columns[section])
                    if orientation == Qt.Vertical:
                        return str(self._data.index[section])
                return None

        return PandasModel(dataframe, title)


    def _format_cbar_correlation(self, ax):
        # plot formatting
        from matplotlib.ticker import FormatStrFormatter, MultipleLocator
        if not ax.collections:
            return
        cbar = ax.collections[0].colorbar
        if cbar is None:
            return
        decimals = int(getattr(self, "_legend_corr_decimals", 2))  # e.g., 2 -> 0.01
        major_step = float(getattr(self, "_legend_corr_major_step", 0.2))  # e.g., 0.2
        minor_div = int(getattr(self, "_legend_corr_minor_div", 2))  # e.g., 2 -> minor=0.1
        cbar.set_label("Correlation coefficient r", rotation=90, labelpad=12)
        ticks = np.arange(-1.0, 1.0 + 0.5 * major_step, major_step)
        cbar.set_ticks(ticks)
        cbar.formatter = FormatStrFormatter(f"%.{decimals}f")
        if minor_div > 1:
            minor_step = major_step / minor_div
            cbar.ax.yaxis.set_minor_locator(MultipleLocator(minor_step))
            cbar.ax.minorticks_on()
        cbar.update_ticks()

    def _format_cbar_sigma(self, ax):
        from matplotlib.ticker import FormatStrFormatter, AutoMinorLocator
        if not ax.collections:
            return
        cbar = ax.collections[0].colorbar
        if cbar is None:
            return

        decimals = int(getattr(self, "_legend_sigma_decimals", 3))  # more suitable formatting
        nbins = int(getattr(self, "_legend_sigma_nbins", 12))  
        minor_div = int(getattr(self, "_legend_sigma_minor_div", 2)) 

        # Label
        cbar.set_label("Variance", rotation=90, labelpad=12)
        # Get clim and place ticks including endpoints
        mappable = ax.collections[0]
        vmin, vmax = mappable.get_clim()
        if vmin == vmax:
            vmax = vmin + 1e-12  # avoid degenerate ticks
        ticks = np.linspace(vmin, vmax, nbins)
        cbar.set_ticks(ticks)
        # Scientific notation with 'e'
        cbar.formatter = FormatStrFormatter(f"%.{decimals}e")
        # Minor ticks
        if minor_div > 1:
            cbar.ax.minorticks_on()
            cbar.ax.yaxis.set_minor_locator(AutoMinorLocator(minor_div))
        cbar.update_ticks()

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
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No
            )
            if reply == QMessageBox.No:
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
                fig = plt.gcf()
                add_plot_footer(fig)
                plt.show()
            else:
                fig, ax = plt.subplots(figsize=(12, 10))
                set_current_figure(self, fig)

                if selection == "Cross Correlations (R)":
                    sns.heatmap(
                        matrix,
                        ax=ax,
                        cmap="RdBu_r",
                        center=0,
                        vmin=-1,
                        vmax=1,
                        square=True,
                        cbar=True,
                        cbar_kws={"label": "Correlation coefficient r"},
                        xticklabels=labels[: matrix.shape[1]] if labels else "auto",
                        yticklabels=labels[: matrix.shape[0]] if labels else "auto",
                    )
                    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right")
                    ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
                    self._format_cbar_correlation(ax)
                elif selection == "Sigma Theta (Σ_θ)":
                    sns.heatmap(
                        matrix,
                        ax=ax,
                        cmap="inferno",
                        square=True,
                        cbar=True,
                        cbar_kws={"label": "Variance"},
                        xticklabels=labels[: matrix.shape[1]] if labels else "auto",
                        yticklabels=labels[: matrix.shape[0]] if labels else "auto",
                    )
                    ax.set_xticklabels(ax.get_xticklabels(), rotation=45, ha="right")
                    ax.set_yticklabels(ax.get_yticklabels(), rotation=0)
                    self._format_cbar_sigma(ax)
                else:
                    sns.heatmap(matrix, ax=ax, cmap='viridis', annot=True, fmt='.2e', square=True)

                disp_tag = self._filter_disp() if self._is_filtered_product(selection) else ""
                ax.set_title(f"{title}{disp_tag}\nShape: {matrix.shape}")

                try:
                    default_name = self._default_plot_filename(selection, ext="png")
                    mng = plt.get_current_fig_manager()
                    if hasattr(mng, "set_window_title"):
                        mng.set_window_title(default_name)
                    fig.canvas.get_default_filename = lambda dn=default_name: dn
                except Exception:
                    pass

                plt.tight_layout()
                add_plot_footer(fig)
                plt.show()

            self._append_status("plot generated")
        except Exception as e:
            error_msg = f"Failed to generate plot: {e}"
            self._append_status(f"plot error: {error_msg}")
            logger.exception("Plotting error")
            QMessageBox.critical(self, "Plot Error", error_msg)

    def parse_station_coordinates(self, solution_estimate_data):

        #take all station episodes (code, pt, soln) with complete STAX/STAY/STAZ
        #
        #  dict[label] -> {'code','pt','soln','x','y','z'}
        #format: CODE.PT.SOLN (e.g., 'SODA.A.3'), or 'CODE.PT' if soln is None.

        episodes = {}

        for p in solution_estimate_data:
            ptype = p.get("type", "")
            code = p.get("code", "")
            if ptype not in ("STAX", "STAY", "STAZ") or not code:
                continue

            pt = p.get("pt", "") or ""
            soln = p.get("soln", None)
            label = f"{code}.{pt}.{soln}" if soln is not None else f"{code}.{pt}"

            ep = episodes.get(label)
            if ep is None:
                ep = {
                    "code": code,
                    "pt": pt,
                    "soln": soln,
                    "x": None,
                    "y": None,
                    "z": None,
                }
                episodes[label] = ep

            v = p.get("value", 0.0)
            if ptype == "STAX":
                ep["x"] = v
            elif ptype == "STAY":
                ep["y"] = v
            elif ptype == "STAZ":
                ep["z"] = v

        # keep only complete x,y,z
        return {
            label: meta
            for label, meta in episodes.items()
            if (meta["x"] is not None and meta["y"] is not None and meta["z"] is not None)
        }

    def create_matrix_Ei(self, x, y, z):
        #{1,0,0,x,0,z,-y}
        #{0,1,0,y,-z,0,x}
        #{0,0,1,z,y,-x,0}

        matrix = np.zeros((3, 7))
        matrix[0, 0] = 1
        matrix[1, 1] = 1
        matrix[2, 2] = 1

        matrix[0, 3] = x
        matrix[0, 5] = z
        matrix[0, 6] = -y

        matrix[1, 3] = y
        matrix[1, 4] = -z
        matrix[1, 6] = x

        matrix[2, 3] = z
        matrix[2, 4] = y
        matrix[2, 5] = -x

        return matrix

    def _collect_indices(self, sol):
        idx_map, coords, have_pos, have_vel = {}, {}, set(), set()
        for i, p in enumerate(sol):
            code, ptype = p.get("code"), p.get("type")
            if not code or not ptype: continue
            pt, soln = p.get("pt", ""), p.get("soln")
            param_key = (code, ptype, pt, soln)
            idx_map[param_key] = i
            episode_key = (code, pt, soln)
            if ptype in ("STAX", "STAY", "STAZ"):
                have_pos.add(episode_key)
                c = coords.setdefault(episode_key, {"x": None, "y": None, "z": None})
                if ptype == "STAX": c["x"] = p.get("value", 0.0)
                elif ptype == "STAY": c["y"] = p.get("value", 0.0)
                elif ptype == "STAZ": c["z"] = p.get("value", 0.0)
            if ptype in ("VELX", "VELY", "VELZ"):
                have_vel.add(episode_key)
        return idx_map, coords, have_pos, have_vel

    def build_E(self, sol, include_vel=True, exclude_episodes=None):
        if exclude_episodes is None: exclude_episodes = set()
        idx_map, coords, have_pos, have_vel = self._collect_indices(sol)
        use_vel_cols = include_vel and len(have_vel - exclude_episodes) > 0
        k = 14 if use_vel_cols else 7
        rows, row_idx, used_episodes = [], [], 0
        for episode in sorted(have_pos):
            if episode in exclude_episodes: continue
            c = coords.get(episode)
            if not c or None in (c["x"], c["y"], c["z"]): continue
            x, y, z = c["x"], c["y"], c["z"]
            Ei = self.create_matrix_Ei(x, y, z)
            code, pt, soln = episode
            try:
                ix, iy, iz = idx_map[(code, "STAX", pt, soln)], idx_map[(code, "STAY", pt, soln)], idx_map[(code, "STAZ", pt, soln)]
            except KeyError: continue
            if k == 7: rows.append(Ei)
            else:
                Ei_pos = np.zeros((3, 14)); Ei_pos[:, :7] = Ei
                rows.append(Ei_pos)
            row_idx.extend([ix, iy, iz])
            used_episodes += 1
            if k == 14 and episode in have_vel:
                try:
                    ivx, ivy, ivz = idx_map[(code, "VELX", pt, soln)], idx_map[(code, "VELY", pt, soln)], idx_map[(code, "VELZ", pt, soln)]
                except KeyError: continue
                Ei_vel = np.zeros((3, 14)); Ei_vel[:, 7:] = Ei
                rows.append(Ei_vel)
                row_idx.extend([ivx, ivy, ivz])
        if not rows: return np.zeros((0, k)), np.zeros((0,), dtype=int), k, 0
        E = np.vstack(rows)
        return E, np.asarray(row_idx, int), k, used_episodes

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

        self._append_section("Sigma Theta computation")

        self._clear_computed_products()
        sigma_bench = benchmark.begin('compute sigma theta')

        # Check if manual filtering is enabled
        if self._manual_filter_enabled:
            # Manual mode: invert selection (selected = keep, all others = exclude)
            all_episodes = set()
            for p in sol:
                ptype = p.get("type", "")
                if ptype in ("STAX", "STAY", "STAZ", "VELX", "VELY", "VELZ"):
                    episode = (p.get("code"), p.get("pt", ""), p.get("soln", ""))
                    all_episodes.add(episode)
            
            episodes_to_exclude = all_episodes - self._manual_selected_episodes
            self._append_status(
                f"[Filter] Manual selection: {len(self._manual_selected_episodes)} included, "
                f"{len(episodes_to_exclude)} excluded"
            )
            
            # Build details for excluded episodes (for filtered stations dialog)
            idx_map, coords, have_pos, have_vel = self._collect_indices(sol)
            val_map = {
                (p.get("code"), p.get("type"), p.get("pt", ""), p.get("soln")): p.get("value", np.nan)
                for p in sol
            }
            
            def _sd(i: Optional[int]) -> float:
                if i is None:
                    return float("nan")
                v = float(Cx[i, i])
                if v < 0.0:
                    v = 0.0
                return float(np.sqrt(v))
            
            details = []
            for ep in sorted(episodes_to_exclude):
                code, pt, soln = ep
                ix = idx_map.get((code, "STAX", pt, soln))
                iy = idx_map.get((code, "STAY", pt, soln))
                iz = idx_map.get((code, "STAZ", pt, soln))
                ivx = idx_map.get((code, "VELX", pt, soln))
                ivy = idx_map.get((code, "VELY", pt, soln))
                ivz = idx_map.get((code, "VELZ", pt, soln))
                
                sx, sy, sz = _sd(ix), _sd(iy), _sd(iz)
                svx, svy, svz = _sd(ivx), _sd(ivy), _sd(ivz)
                
                x = val_map.get((code, "STAX", pt, soln), np.nan)
                y = val_map.get((code, "STAY", pt, soln), np.nan)
                z = val_map.get((code, "STAZ", pt, soln), np.nan)
                vx = val_map.get((code, "VELX", pt, soln), np.nan)
                vy = val_map.get((code, "VELY", pt, soln), np.nan)
                vz = val_map.get((code, "VELZ", pt, soln), np.nan)
                
                details.append({
                    "label": f"{code}.{pt}.{soln}",
                    "code": code, "pt": pt, "soln": soln,
                    "pos_excess": float("nan"),  # No sigma threshold in manual mode
                    "vel_excess": float("nan"),
                    "x": x, "y": y, "z": z,
                    "vx": vx, "vy": vy, "vz": vz,
                    "sx": sx, "sy": sy, "sz": sz,
                    "svx": svx, "svy": svy, "svz": svz,
                })
        
        #Combined filtering: Cx diag + STD_DEV column
        elif self.filter_chk.isChecked():
            pos_thresh = float(self.thresh_spinbox.value())  # m
            vel_thresh = float(self.vel_thresh_spinbox.value())  # m/yr

            # Cx
            episodes_cx, _ = self.detect_bad_episodes_cx(
                sol=sol,
                Cx=Cx,
                pos_threshold_m=pos_thresh,
                vel_threshold_m_per_y=vel_thresh,
                include_vel=True,
            )

            # STD_DEV
            pos_params = [p for p in sol if p.get("type") in ("STAX", "STAY", "STAZ")]
            vel_params = [p for p in sol if p.get("type") in ("VELX", "VELY", "VELZ")]
            episodes_sigma_pos = self.detect_bad_episodes(pos_params, threshold=pos_thresh)
            episodes_sigma_vel = self.detect_bad_episodes(vel_params, threshold=vel_thresh)
            # Union
            episodes_to_exclude = set(episodes_cx) | set(episodes_sigma_pos) | set(episodes_sigma_vel)
            # Prepare lookups for building details (parameter values and sigmas)
            idx_map, coords, have_pos, have_vel = self._collect_indices(sol)
            # values map
            val_map = {
                (p.get("code"), p.get("type"), p.get("pt", ""), p.get("soln")): p.get("value", np.nan)
                for p in sol
            }

            def _sd(i: Optional[int]) -> float:
                if i is None:
                    return float("nan")
                v = float(Cx[i, i])
                if v < 0.0:
                    v = 0.0
                return float(np.sqrt(v))

            details = []
            for ep in sorted(episodes_to_exclude):
                code, pt, soln = ep
                # sigma (from Cx)
                ix = idx_map.get((code, "STAX", pt, soln))
                iy = idx_map.get((code, "STAY", pt, soln))
                iz = idx_map.get((code, "STAZ", pt, soln))
                ivx = idx_map.get((code, "VELX", pt, soln))
                ivy = idx_map.get((code, "VELY", pt, soln))
                ivz = idx_map.get((code, "VELZ", pt, soln))

                sx, sy, sz = _sd(ix), _sd(iy), _sd(iz)
                svx, svy, svz = _sd(ivx), _sd(ivy), _sd(ivz)
                pos_max = np.nanmax([sx, sy, sz]) if not np.isnan([sx, sy, sz]).all() else float("nan")
                vel_max = np.nanmax([svx, svy, svz]) if not np.isnan([svx, svy, svz]).all() else float("nan")
                pos_excess = max(0.0, pos_max - pos_thresh) if np.isfinite(pos_max) else float("nan")
                vel_excess = max(0.0, vel_max - vel_thresh) if np.isfinite(vel_max) else float("nan")

                # parameter values from SOLUTION/ESTIMATE
                x = val_map.get((code, "STAX", pt, soln), np.nan)
                y = val_map.get((code, "STAY", pt, soln), np.nan)
                z = val_map.get((code, "STAZ", pt, soln), np.nan)
                vx = val_map.get((code, "VELX", pt, soln), np.nan)
                vy = val_map.get((code, "VELY", pt, soln), np.nan)
                vz = val_map.get((code, "VELZ", pt, soln), np.nan)

                details.append(
                    {
                        "label": f"{code}.{pt}.{soln}",
                        "code": code,
                        "pt": pt,
                        "soln": soln,
                        # excesses
                        "pos_excess": pos_excess,
                        "vel_excess": vel_excess,
                        # values (sol block estimate)
                        "x": x, "y": y, "z": z,
                        "vx": vx, "vy": vy, "vz": vz,
                        # sigmas (from Cx)
                        "sx": sx, "sy": sy, "sz": sz,
                        "svx": svx, "svy": svy, "svz": svz,
                    }
                )
        else:
            episodes_to_exclude, details = set(), []
            self._append_status("[Filter] filtering disabled")

        self._filtered_episodes_info = details
        self.show_filtered_btn.setEnabled(len(self._filtered_episodes_info) > 0)
        self._refresh_filtered_dialog_if_open()

        try:
            filtered_codes = {ep[0] for ep in episodes_to_exclude}
            parent_app = self.window()
            if hasattr(parent_app, "stations_widget"):
                parent_app.stations_widget.set_filtered_codes(filtered_codes)
                parent_app.stations_widget.update_station_map()
        except Exception:
            pass

        # Create boolean mask: True = keep parameter, False = exclude parameter
        # Filters out STAX/STAY/STAZ (and optionally VELX/VELY/VELZ) for flagged episodes
        keep_mask = self._build_keep_mask(
            sol=sol, episodes_to_exclude=episodes_to_exclude, remove_vel=False
        )
        if keep_mask.sum() == 0:
            QMessageBox.warning(
                self, "Data Error", "All parameters were filtered out. Adjust thresholds."
            )
            return


        if keep_mask.all():
            filtered_sol = sol
            filtered_Cx = Cx
        else:
            filtered_sol = [p for p, k in zip(sol, keep_mask) if k]
            filtered_Cx = Cx[np.ix_(keep_mask, keep_mask)]


        # filtered_sol = [p for p, k in zip(sol, keep_mask) if k] removed with v1.1
        # filtered_Cx = Cx[np.ix_(keep_mask, keep_mask)] removed with v1.1


        # Build transformation matrix E from filtered data: maps station episodes to Helmert parameters
        # row_idx identifies which rows/cols of filtered_Cx correspond to used station coordinates
        E, row_idx, kdim, n_episodes = self.build_E(
            filtered_sol, include_vel=True, exclude_episodes=None
        )
        if E.size == 0:
            QMessageBox.warning(
                self, "Data Error", "No stations remained after filtering."
            )
            return
        # Extract covariance submatrix for coordinates actually used in Helmert transformation

        if row_idx.size == filtered_Cx.shape[0] and np.array_equal(
                row_idx, np.arange(filtered_Cx.shape[0])):
            Cx_sub = filtered_Cx
        else:
            Cx_sub = filtered_Cx[np.ix_(row_idx, row_idx)]

        # Cx_sub = filtered_Cx[np.ix_(row_idx, row_idx)] removed with v1.1

        self._append_status(f"[Filter] episodes used after filtering: {n_episodes}")
        self._append_status(f"E shape: {E.shape}, Cx_sub shape: {Cx_sub.shape}")

        try:
            At = E.T
            AtA = At @ E
            self._append_status(f"cond(E^T E): {np.linalg.cond(AtA):.2e}")
            #print(At)
            #print("---------")
            #print(AtA)
            #print("---------")
            #print(E)
            AtCxA = (At @ Cx_sub) @ E

            # inv does not raise on a singular E^T E. Columns are scaled to unit norm
            # first because their units differ and that alone breaks matrix_rank.
            col_norms = np.linalg.norm(E, axis=0)
            col_norms[col_norms == 0.0] = 1.0
            rank = np.linalg.matrix_rank(E / col_norms)
            if rank < E.shape[1]:
                message = f"The design matrix E has column rank {rank} of {E.shape[1]}, so E^T E cannot be inverted. Sigma Theta was not computed."
                self._append_status(f"ERROR: {message}")
                logger.error(message)
                QMessageBox.critical(self, "Rank deficient design matrix", message)
                return

            AtA_inv = np.linalg.inv(AtA) 
            #AtA_pinv = np.linalg.pinv(AtA)#pseudo
            sigma_theta = (AtA_inv @ AtCxA) @ AtA_inv
            #sigma_theta_pinv = (AtA_pinv @ AtCxA) @ AtA_pinv
            self._append_status(f"cond(sigma_theta): {np.linalg.cond(sigma_theta):.2e}")
            #if 
           
            self.sigma_theta_matrix = sigma_theta

            self.filtered_solution_estimate = filtered_sol

            # self.filtered_Cx = filtered_Cx obsolete and left substantial memory footprint - removed with v1.1
            self.filtered_row_idx = row_idx

            diag = np.diag(sigma_theta)
            self._append_status(
                f"Σθ diag stats: min={diag.min():.6e}, max={diag.max():.6e}, mean={diag.mean():.6e}"
            )

            negative = int(np.count_nonzero(diag < 0.0))
            if negative:
                self._append_status(f"WARNING: {negative} of {diag.size} Σθ diagonal entries are negative, the solution is degenerate and Helmert parameters will be NaN")

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

        except np.linalg.LinAlgError as e:
            benchmark.cancel(sigma_bench)
            self._clear_computed_products()
            logger.exception("SigmaTheta computation error")
            QMessageBox.critical(self, "Calculation Error", str(e))

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

        extensions = {
            'Excel (.xlsx)': '.xlsx',
            'CSV (.csv)': '.csv',
            'Text (.txt)': '.txt',
            'NumPy (.npy)': '.npy'
        }
        extension = extensions.get(format_str, '.txt')
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
            if extension == '.xlsx':
                df = pd.DataFrame(matrix)
                df.to_excel(out_file, index=False, header=False)
            elif extension == '.csv':
                np.savetxt(out_file, matrix, delimiter=',', fmt='%.17g')
            elif extension == '.txt':
                np.savetxt(out_file, matrix, fmt='%.17g')
            elif extension == '.npy':
                np.save(out_file, matrix)

            self._append_status(f"exported {selection} to {out_file}")

        except Exception as e:
            logger.exception("Export error")
            QMessageBox.critical(self, "Error", f"Failed to export matrix: {e}")

    def _build_stats_report(self) -> str:
        # plain text report -> collect metrics already computed by the pipeline
        parent_app = self.window()
        lines = []
        add = lines.append

        add("SINEX TRF Studio - Datum Effect Statistics Report")
        add("=" * 52)

        filename = ""
        if hasattr(parent_app, "current_data") and parent_app.current_data:
            filename = parent_app.current_data.get("metadata", {}).get("filename", "")
        add(f"File: {filename}")

        var_factor = None
        if hasattr(parent_app, "get_variance_factor"):
            var_factor = parent_app.get_variance_factor()
        add(f"Variance factor: {var_factor if var_factor is not None else 'n/a'}")
        add(f"Filter tag: {self._filter_tag() or 'none'}")
        add("")

        #SigmaTheta stats
        st = self.sigma_theta_matrix
        add("Sigma Theta (Σθ)")
        add("-" * 52)
        if st is not None:
            diag = np.diag(st)
            add(f"  shape: {st.shape[0]}x{st.shape[1]}")
            add(f"  diag min: {diag.min():.6e}")
            add(f"  diag max: {diag.max():.6e}")
            add(f"  diag mean: {diag.mean():.6e}")
            add(f"  cond(Σθ): {np.linalg.cond(st):.6e}")
            sym_err = float(np.max(np.abs(st - st.T)))
            add(f"  symmetry max|Σθ - Σθ^T|: {sym_err:.6e}")
        else:
            add("  not computed")
        add("")

        # cross-corr
        add("Cross Correlations (R)")
        add("-" * 52)
        cc = self.cross_correlation_matrix
        if cc is not None:
            n = cc.shape[0]
            off = cc[~np.eye(n, dtype=bool)]
            add(f"  shape: {n}x{n}")
            add(f"  off-diagonal min: {off.min():.6f}")
            add(f"  off-diagonal max: {off.max():.6f}")
            add(f"  mean |off-diagonal|: {np.mean(np.abs(off)):.6f}")
            add(f"  cond(R): {np.linalg.cond(cc):.6e}")
            sym_err = float(np.max(np.abs(cc - cc.T)))
            add(f"  symmetry max|R - R^T|: {sym_err:.6e}")
        else:
            add("  not computed")
        add("")

        # Helmert 
        add("Helmert Parameters")
        add("-" * 52)
        hp = self.helmert_params
        if hp is not None:
            flat = np.asarray(hp, dtype=float).flatten()
            if flat.size == 7:
                names = ["tx", "ty", "tz", "ds", "ex", "ey", "ez"]
            elif flat.size == 14:
                names = ["tx", "ty", "tz", "ds", "ex", "ey", "ez",
                         "tx_v", "ty_v", "tz_v", "ds_v", "ex_v", "ey_v", "ez_v"]
            else:
                names = [f"p{i + 1}" for i in range(flat.size)]
            for name, val in zip(names, flat):
                add(f"  {name}: {val:.6e}")
        else:
            add("  not computed")
        add("")
        add("Station Metrics")
        add("-" * 52)
        sol = None
        if hasattr(parent_app, "current_data") and parent_app.current_data:
            sol = parent_app.current_data["blocks"].get("SOLUTION/ESTIMATE")
        if sol:
            episodes = self.parse_station_coordinates(sol)
            add(f"  station episodes with complete xyz: {len(episodes)}")
            add(f"  total estimated parameters: {len(sol)}")
            add("")

            add("  (σ) of the estimates, different units ")
            add("")
            add(f"    {'Type':8} {'Unit':10} {'Count':>7} {'Zero':>7} "
                f"{'Min':>13} {'Max':>13} {'Mean':>13}")
            by_type = {}
            for p in sol:
                key = (str(p.get("type", "")), str(p.get("unit", "")))
                by_type.setdefault(key, []).append(p.get("sigma", np.nan))
            for ptype, unit in sorted(by_type):
                vals = np.asarray(by_type[(ptype, unit)], dtype=float)
                finite = vals[np.isfinite(vals)]
                n_zero = int(np.count_nonzero(finite == 0.0))
                usable = finite[finite > 0.0]
                if usable.size:
                    stats = (f"{usable.min():13.6e} {usable.max():13.6e} "
                             f"{usable.mean():13.6e}")
                else:
                    stats = f"{'n/a':>13} {'n/a':>13} {'n/a':>13}"
                add(f"    {ptype:8} {unit:10} {finite.size:7d} {n_zero:7d} {stats}")
            add("")
            add("  0 = absent or unparseable value - not included in the min, max, and mean")
            add(" ")

        else:
            add("  no SOLUTION/ESTIMATE data")
        add("")
        add("Filtering")
        add("-" * 52)
        info = getattr(self, "_filtered_episodes_info", []) or []
        add(f"  episodes excluded by filter: {len(info)}")
        add(f"  filtering active: {self.is_filtered()}")

        return "\n".join(lines) + "\n"

    def export_stats_report(self):
        if (
            self.sigma_theta_matrix is None
            or self.cross_correlation_matrix is None
            or self.helmert_params is None
        ):
            QMessageBox.warning(
                self, "Statistics Report",
                "Compute SigmaTheta, Cross Correlations, and Helmert Parameters first."
            )
            return

        parent_app = self.window()
        original_file = ""
        if hasattr(parent_app, "current_data") and parent_app.current_data:
            original_file = parent_app.current_data["metadata"].get("filename", "")
        filter_suffix = self._filter_tag() if self.is_filtered() else ""
        def_name = f"{Path(original_file).stem}_datum_stats{filter_suffix}.txt"

        out_file, _ = QFileDialog.getSaveFileName(
            self, "Save Statistics Report", default_save_path(def_name), "Text Files (*.txt)"
        )
        if not out_file:
            return
        remember_dialog_dir(out_file)
        out_file = ensure_suffix(out_file, ".txt")
        try:
            report = self._build_stats_report()
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(report)
            self._append_status(f"exported statistics report to {out_file}")
        except Exception as e:
            logger.exception("Statistics report export error")
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

class ManualEpisodeSelectionDialog(QDialog):
    def __init__(self, host: "DatumWidget", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manual Episode Selection")
        self.host = host
        self.resize(600, 700)
        
        layout = QVBoxLayout(self)
        
        # Radio toggle for filter mode
        mode_layout = QHBoxLayout()
        self.auto_radio = QRadioButton("Auto Filter (Sigma)")
        self.manual_radio = QRadioButton("Manual Selection")
        
        # Set current mode
        if self.host._manual_filter_enabled:
            self.manual_radio.setChecked(True)
        else:
            self.auto_radio.setChecked(True)
        
        self.auto_radio.toggled.connect(self._on_mode_changed)
        mode_layout.addWidget(self.auto_radio)
        mode_layout.addWidget(self.manual_radio)
        layout.addLayout(mode_layout)
        
        # Scroll area for station groups
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        self.checkboxes_layout = QVBoxLayout(scroll_widget)
        scroll.setWidget(scroll_widget)
        layout.addWidget(scroll)
        
        # Store checkboxes by episode key
        self.episode_checkboxes = {}  # {(code, pt, soln): QCheckBox}
        
        # Populate checkboxes grouped by station
        self._populate_episodes()
        
        # OK/Cancel buttons
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("OK")
        cancel_btn = QPushButton("Cancel")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        # Update checkbox enabled state based on mode
        self._update_checkboxes_enabled()
    
    def _on_mode_changed(self):
        self._update_checkboxes_enabled()
    
    def _update_checkboxes_enabled(self):
        # Enable checkboxes only in manual mode
        enabled = self.manual_radio.isChecked()
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

        rows = []
        for it in info:
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
        self.default_label.setStyleSheet("color: #555; font-size: 11px;")

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
        label_style = "font-size: 18px; color: #2c3e50;"
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
        if dlg.exec() == QDialog.Accepted:
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
            self.vf_label.setText(f"Variance Factor: {custom_vf} (custom)")
        elif vf_data is not None and isinstance(vf_data, (float, int)):
            self.vf_label.setText(f"Variance Factor: {vf_data}")
        else:
            self.vf_label.setText("Variance Factor: Missing")
        site_id_block = current_blocks.get('SITE/ID')
        if site_id_block and isinstance(site_id_block, list):
            self.station_label.setText(f"Stations Found (SITE/ID): {len(site_id_block)}")
        else:
            self.station_label.setText("Stations Found (SITE/ID): Missing")

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
        self.vf_label.setText("Variance Factor: --")
        self.station_label.setText("Stations Found (SITE/ID): --")
        self.aprcov_label.setText("Apriori Covariance Found: --")

