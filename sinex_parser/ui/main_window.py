# ui/main_window.py
import os

from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QPushButton, QProgressBar, QTabWidget,
    QMessageBox, QFileDialog, QComboBox, QPlainTextEdit, QCheckBox, QApplication, QSizePolicy
)
from PyQt5.QtCore import QUrl, pyqtSignal
import numpy as np
from pathlib import Path
from plyer import notification

from PyQt5 import QtGui, QtCore

from .. import __version__
from ..core import (
    logger, SinexFileValidator, benchmark,
    remember_dialog_dir, default_save_path, ensure_suffix,
)

from ..parsers import create_parsers
from ..ui.widgets import (
    ParserWorker, CovarianceMatrixWidget, StationsWidget,
    InfoWidget, DatumWidget, FileInfoWidget
)


class SINEXParserApp(QMainWindow):
    benchmark_updated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.block_parsers = create_parsers()
        self.validator = SinexFileValidator()
        self.current_data = None
        self.custom_variance_factor = None
        self._parse_generation = 0
        self._active_workers = set()
        self.init_ui()
        self.benchmark_updated.connect(self._refresh_benchmark_button)
        benchmark.on_update = self.benchmark_updated.emit
        # warning
        logger.warning(f"======== SINEX TRF Studio version {__version__} ========")
        logger.warning(" ")
        #logger.warning("Preliminary test version. Verify all results before use.")

    def init_ui(self):
        self.setWindowTitle('SINEX TRF Studio')
        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(
            screen.x() + 50,
            screen.y() + 50,
            int(screen.width() * 0.9),
            int(screen.height() * 0.9)
        )

        main_layout = QVBoxLayout()
        icon_path = "sinex_parser/ui/icon.ico"
        self.setWindowIcon(QtGui.QIcon(icon_path))

        top_container = QWidget()
        top_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        top_layout = QHBoxLayout(top_container)
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(16)

        left_panel = QVBoxLayout()
        left_panel.setContentsMargins(0, 0, 0, 0)
        left_panel.setSpacing(8)

        self.file_label = QLabel("No file selected")
        self.file_label.setStyleSheet("QLabel {color:red; font-size:18px }")
        left_panel.addWidget(self.file_label)

        self.file_info_widget = FileInfoWidget(self)
        self.file_info_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.file_info_widget.setMaximumHeight(220)
        left_panel.addWidget(self.file_info_widget, stretch=1)

        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        top_layout.addWidget(left_widget, stretch=1)

        controls_layout = QVBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self.select_file_button = QPushButton("Select SINEX File")
        self.select_file_button.setFont(QFont('Arial', 16))
        self.select_file_button.setFixedSize(500, 60)
        self.select_file_button.clicked.connect(self.select_sinex_file)
        controls_layout.addWidget(self.select_file_button)

        self.benchmark_checkbox = QCheckBox("Record benchmark")
        self.benchmark_checkbox.setChecked(False)
        self.benchmark_checkbox.setToolTip(
            "Record timings and peak process memory"   
        )
        self.benchmark_checkbox.toggled.connect(self._set_benchmark_enabled)

        self.benchmark_export_button = QPushButton("Export Benchmark Report")
        self.benchmark_export_button.setEnabled(False)
        self.benchmark_export_button.clicked.connect(self.export_benchmark_report)

        benchmark_row = QHBoxLayout()
        benchmark_row.addWidget(self.benchmark_checkbox)
        benchmark_row.addWidget(self.benchmark_export_button)
        controls_layout.addLayout(benchmark_row)

        self.skip_validation = QCheckBox("Skip file validation")
        self.skip_validation.setChecked(True)
        self.skip_validation.setToolTip(
            "When enabled, the parser skips the block-structure validation pass\n"
            "before parsing. Saves time on large files from trusted sources."
        )
        self.skip_validation.setCursor(QtGui.QCursor(QtCore.Qt.WhatsThisCursor))
        controls_layout.addWidget(self.skip_validation)

        self.skip_epoch = QCheckBox("Skip parsing SOLUTION/EPOCHS")
        self.skip_epoch.setChecked(True)
        self.skip_epoch.setToolTip(
            "When enabled, the parser will ignore the SOLUTION/EPOCHS block\n"
            "to speed up parsing for very large files."
        )
        self.skip_epoch.setCursor(QtGui.QCursor(QtCore.Qt.WhatsThisCursor))

        controls_layout.addWidget(self.skip_epoch)
        controls_layout.addStretch()

        controls_widget = QWidget()
        controls_widget.setLayout(controls_layout)
        controls_widget.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        top_layout.addWidget(controls_widget, stretch=0)

        top_container.setMaximumHeight(top_container.sizeHint().height())
        main_layout.addWidget(top_container, stretch=0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(True)
        main_layout.addWidget(self.progress_bar)

        self.tab_widget = QTabWidget()
        tab_font = self.tab_widget.font()
        tab_font.setPointSize(12)
        self.tab_widget.setFont(tab_font)
        self.tab_widget.setMinimumHeight(650)

        # Tab1: Covariance Matrix (operations + visualization)
        self.covariance_widget = CovarianceMatrixWidget(
            get_current_matrix_func=self.get_loaded_matrix,
            export_func=self.operations_export_handler
        )
        self.tab_widget.addTab(self.covariance_widget, "Covariance Matrix")
        self.operations_widget = self.covariance_widget.operations_widget
        self.viz_widget = self.covariance_widget.visualizer_widget
        # new tab: datum
        self.datum_widget = DatumWidget()
        self.tab_widget.addTab(self.datum_widget, "Datum Effect")
        # Tab3: Stations
        self.stations_widget = StationsWidget()
        self.tab_widget.addTab(self.stations_widget, "Stations")

        # tab 4: raw export
        export_widget = QWidget()
        export_layout = QVBoxLayout(export_widget)

        self.block_display = QPlainTextEdit()
        self.block_display.setReadOnly(True)
        self.block_display.setStyleSheet(
            """
            QPlainTextEdit {
                font-size: 18;
                border: 1px solid #cccccc;
                padding: 5px;
            }
            """
        )

        export_layout.addWidget(self.block_display)

        self.block_combo = QComboBox()
        self.block_combo.addItems([
            'SOLUTION/MATRIX_ESTIMATE L COVA',
            'SOLUTION/MATRIX_APRIORI L COVA',
            'SOLUTION/MATRIX_ESTIMATE U COVA',
            'SOLUTION/MATRIX_APRIORI U COVA',
            'SITE/ID',
            'SOLUTION/STATISTICS',
            'SOLUTION/ESTIMATE',
            'SOLUTION/APRIORI'
        ])
        export_label = QLabel("Select Block to Export as-is:")
        export_label.setStyleSheet("font-weight: bold; font-size: 14;")
        export_layout.addWidget(export_label)
        export_layout.addWidget(self.block_combo)

        self.block_combo.currentIndexChanged.connect(self.update_block_display)

        export_line = QHBoxLayout()
        self.format_combo = QComboBox()
        self.format_combo.addItems(['Excel (.xlsx)', 'CSV (.csv)', 'Text (.txt)', 'NumPy (.npy)'])
        export_line.addWidget(QLabel("Export Format:"))
        export_line.addWidget(self.format_combo)
        self.export_button = QPushButton("Export Data")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_data)
        export_line.addWidget(self.export_button)
        export_layout.addLayout(export_line)
        self.tab_widget.addTab(export_widget, "Raw export")

        # Tab5: Info
        self.info_widget = InfoWidget()
        self.tab_widget.addTab(self.info_widget, "Info")

        main_layout.addWidget(self.tab_widget)

        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

    def _set_benchmark_enabled(self, enabled: bool) -> None:
        benchmark.set_enabled(bool(enabled))
        self._refresh_benchmark_button()

    def _refresh_benchmark_button(self) -> None:
        self.benchmark_export_button.setEnabled(benchmark.has_data())

    def export_benchmark_report(self) -> None:
        if not benchmark.has_data():
            QMessageBox.warning(self, "Benchmark Report", "No benchmark data is available.")
            return
        original_file = benchmark.file_name or "sinex"
        def_name = f"{Path(original_file).stem}_benchmark.txt"
        out_file, _ = QFileDialog.getSaveFileName(
            self, "Save Benchmark Report", default_save_path(def_name), "Text Files (*.txt)"
        )
        if not out_file:
            return
        remember_dialog_dir(out_file)
        out_file = ensure_suffix(out_file, ".txt")
        try:
            with open(out_file, "w", encoding="utf-8") as f:
                f.write(benchmark.render_text())
            logger.info(f"Exported benchmark report to {out_file}")
        except Exception as e:
            logger.exception("Benchmark export error")
            QMessageBox.critical(self, "Error", f"Failed to export benchmark report: {e}")

    def select_sinex_file(self):
        fname, _ = QFileDialog.getOpenFileName(
            self,
            "Select SINEX File",
            default_save_path(""),
            "SINEX files (*.sinex *.snx);;All files (*.*)"
        )
        if fname:
            remember_dialog_dir(fname)
            logger.info("----- New File -----")
            fpath = Path(fname)
            self.file_label.setText(fpath.name)

            logger.info(f"Selected SINEX file: {fpath.name}")
            if self.file_info_widget:  # Update new widget
                self.file_info_widget.clear_display()  # Clear previous info

            if not self.skip_validation.isChecked():
                if not self.validator.validate_block_structure(fpath):
                    QMessageBox.critical(self, "Error", "Invalid SINEX block structure!")
                    return

            self.progress_bar.setVisible(True)
            self.progress_bar.setRange(0, 0)
            skip_epochs = self.skip_epoch.isChecked()
            skip_val = self.skip_validation.isChecked()
            self._parse_generation += 1
            try:
                size = fpath.stat().st_size
            except OSError:
                size = 0

            benchmark.start_file(fpath.name, size, generation=self._parse_generation)
            worker = ParserWorker(fpath, self.block_parsers, skip_epochs_block=skip_epochs, skip_validation=skip_val)
            # tag worker with the current parse generation so a stale worker that finishes late cannot overwrite newer data
            worker.parse_generation = self._parse_generation
            self._active_workers.add(worker)
            worker.finished.connect(self.handle_parsing_complete)
            worker.error.connect(self.handle_parsing_error)
            self.worker = worker
            worker.start()

    def get_loaded_matrix(self) -> np.ndarray:
        if not self.current_data:
            return None
        if 'SOLUTION/MATRIX_ESTIMATE L COVA' in self.current_data['blocks']:
            return self.current_data['blocks'].get('SOLUTION/MATRIX_ESTIMATE L COVA')
        elif 'SOLUTION/MATRIX_ESTIMATE U COVA' in self.current_data['blocks']:
            return self.current_data['blocks'].get('SOLUTION/MATRIX_ESTIMATE U COVA')
        else:
            return None

    def get_variance_factor(self):
        if self.custom_variance_factor is not None:
            return self.custom_variance_factor
        if self.current_data and 'blocks' in self.current_data:
            return self.current_data['blocks'].get('SOLUTION/STATISTICS')
        return None

    def handle_parsing_complete(self):
        # called after parserworker ends
        # avoids passing large objects through the Qt signal system
        worker = self.sender()
        if worker is not None:
            self._active_workers.discard(worker)
        if worker is not self.worker or getattr(worker, "parse_generation", 0) != self._parse_generation:
            logger.info("Ignoring completion signal from a superseded parse worker.")
            return
        if self.worker.result_data is None:
            QMessageBox.critical(self, "Error", "Parsing finished, but no data was returned from the worker.")
            self.progress_bar.setVisible(False)
            return

        data = self.worker.result_data
        # Update with the new data
        self.current_data = data
        self.custom_variance_factor = None
        if self.datum_widget:
            filename = data.get("metadata", {}).get("filename", "")
            self.datum_widget.log_new_file_loaded(filename)
        #reset computed data from any previous file
        if self.covariance_widget and self.covariance_widget.operations_widget:
            self.covariance_widget.operations_widget.reset_for_new_file()
        self.progress_bar.setVisible(False)
        self.export_button.setEnabled(True)
        self._refresh_benchmark_button()
        # update ui
        if self.datum_widget:
            self.datum_widget.sigma_theta_btn.setEnabled(True)

        if self.file_info_widget:
            self.file_info_widget.update_display()
        # Update the visualization widget
        self.covariance_widget.setup_data(data['blocks'])
        site_data = data['blocks'].get('SITE/ID')
        if site_data:
            logger.info(f"Parsed {len(site_data)} stations from SITE/ID.")
            self.stations_widget.set_data(site_data)
        else:
            logger.info("No SITE/ID data found.")
            # clear stations left over from any previous file
            self.stations_widget.set_data([])
        # Update the raw export display.
        self.update_block_display()
        QMessageBox.information(self, "Success", "SINEX file parsed successfully!")
        notification.notify(
            title="SINEX Studio",
            message="SINEX file parsed successfully!"
        )

    def handle_parsing_error(self, err):
        worker = self.sender()
        if worker is not None:
            self._active_workers.discard(worker)
        if worker is not self.worker or getattr(worker, "parse_generation", 0) != self._parse_generation:
            # for a past worker that reported an error leave the newer parse unaffected
            logger.info("Ignoring error from a superseded parse worker.")
            return
        self.progress_bar.setVisible(False)
        self.discard_loaded_file()
        QMessageBox.critical(self, "Error", f"Parsing error: {err}")


    def discard_loaded_file(self) -> None:
        self.current_data = None
        self.custom_variance_factor = None
        self.file_label.setText("No file selected")
        self.export_button.setEnabled(False)
        if self.file_info_widget:
            self.file_info_widget.clear_display()
        if self.datum_widget:
            self.datum_widget.sigma_theta_btn.setEnabled(False)
            self.datum_widget._reset_output_state()
        if self.covariance_widget and self.covariance_widget.operations_widget:
            self.covariance_widget.operations_widget.reset_for_new_file()
        if self.stations_widget:
            self.stations_widget.set_data([])
        self.block_combo.clear()

    def export_data(self):
        if not self.current_data:
            return
        block_key = self.block_combo.currentText()
        fmt = self.format_combo.currentText()
        logger.info(f"Exporting {block_key} as {fmt}...")

        filters = {
            'Excel (.xlsx)': ('Excel Files (*.xlsx)', '.xlsx'),
            'CSV (.csv)': ('CSV Files (*.csv)', '.csv'),
            'Text (.txt)': ('Text Files (*.txt)', '.txt'),
            'NumPy (.npy)': ('NumPy Files (*.npy)', '.npy')
        }
        file_filter, extension = filters[fmt]

        original_file = self.current_data['metadata'].get('filename', '')
        def_name = f"{Path(original_file).stem}_{block_key.replace('/', '_')}{extension}"

        out_file, _ = QFileDialog.getSaveFileName(
            self, "Save Block Data", default_save_path(def_name), file_filter
        )
        if out_file:
            remember_dialog_dir(out_file)

            data_to_export = self.current_data['blocks'].get(block_key)
            if (fmt.startswith("Excel") and getattr(data_to_export, "ndim", 0) == 2
                    and data_to_export.shape[1] > 16384):
                QMessageBox.warning(
                    self, "Export Error",
                    f"This block has {data_to_export.shape[1]} columns, more than the "
                    f"16384 an .xlsx file can hold. Export it as NumPy (.npy) instead."
                )
                return
            try:
            # data_to_export = self.current_data['blocks'].get(block_key)
            # try:
                if data_to_export is None:
                    raise ValueError(f"No data for block: {block_key}")
                parser = self.block_parsers.get(block_key)
                if parser is None:
                    raise ValueError(f"No parser for block: {block_key}")
                parser.export(data_to_export, Path(out_file), extension[1:])
                QMessageBox.information(self, "Success", "Block data exported.")
            except Exception as e:
                logger.exception("Export error")
                QMessageBox.critical(self, "Error", f"Failed to export block: {e}")

    def operations_export_handler(self, arr: np.ndarray, format_str: str, prefix: str):
        logger.info(f"Operations export: {prefix} as {format_str}")
        filters = {
            'Excel (.xlsx)': ('Excel Files (*.xlsx)', '.xlsx'),
            'CSV (.csv)': ('CSV Files (*.csv)', '.csv'),
            'Text (.txt)': ('Text Files (*.txt)', '.txt'),
            'NumPy (.npy)': ('NumPy Files (*.npy)', '.npy')
        }
        if format_str not in filters:
            QMessageBox.critical(self, "Error", f"Unsupported format: {format_str}")
            return
        file_filter, extension = filters[format_str]

        orig_file = self.current_data['metadata'].get('filename', '')
        def_name = f"{Path(orig_file).stem}_{prefix}{extension}"

        out_file, _ = QFileDialog.getSaveFileName(
            self, "Save Data", default_save_path(def_name), file_filter
        )
        if out_file:
            remember_dialog_dir(out_file)
            out_file = ensure_suffix(out_file, extension)
            matrix_parser = self.block_parsers.get('SOLUTION/MATRIX_ESTIMATE L COVA')
            if matrix_parser is None:
                QMessageBox.critical(self, "Error", "No parser for matrix export!")
                return
            try:
                matrix_parser.export(arr, Path(out_file), extension[1:])
                QMessageBox.information(self, "Success", "Data exported.")
            except Exception as e:
                logger.exception("Export error")
                QMessageBox.critical(self, "Error", f"Export fail: {e}")
        notification.notify(
            title="SINEX Studio",
            message=(f"{orig_file} exported.")
        )

    def update_block_display(self):
        if self.current_data and 'blocks' in self.current_data:
            block_keys = list(self.current_data['blocks'].keys())
            if block_keys:
                display_text = "Blocks detected:\n" + "\n".join(block_keys)
            else:
                display_text = "No blocks detected in the file."
        else:
            display_text = "No data available."

        self.block_display.setPlainText(display_text)
