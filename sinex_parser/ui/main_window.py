# ui/main_window.py
import logging
import os

from PyQt6.QtGui import QFont, QAction, QActionGroup
from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QPushButton, QProgressBar, QTabWidget,
    QMessageBox, QFileDialog, QComboBox, QPlainTextEdit, QCheckBox, QApplication, QSizePolicy,
    QSplitter, QMenu
)
from PyQt6.QtCore import QUrl, pyqtSignal
import numpy as np
from pathlib import Path
from plyer import notification

from PyQt6 import QtGui, QtCore

from .. import __version__
from ..core import (
    logger, SinexFileValidator, benchmark,
    remember_dialog_dir, default_save_path, ensure_suffix,
    get_app_setting, set_app_setting,
    LOG_FILENAME, log_file_path, set_file_logging, set_log_file, clear_log_file,
    default_export_format, default_export_dir, default_skip_validation,
    default_skip_epochs, figure_dpi, set_console_level,
)

from ..io import create_parsers
from ..io import export
from ..ui.widgets import (
    ParserWorker, CovarianceMatrixWidget, StationsWidget,
    InfoWidget, DatumWidget, FileInfoWidget, QPlainTextEditLogger
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
        self._theme = get_app_setting('ui/theme', 'light') or 'light'
        self._log_visible = get_app_setting('ui/log_visible', True) not in (False, 'false')
        self._remember_geometry = get_app_setting('ui/remember_geometry', False) in (True, 'true')
        self._notifications = get_app_setting('ui/notifications', True) not in (False, 'false')
        self._log_enabled = get_app_setting('log/enabled', True) not in (False, 'false')
        self._log_max_bytes = int(get_app_setting('log/max_bytes', 0) or 0)
        self._log_dir = str(get_app_setting('log/dir', '') or '')
        self._console_level = int(get_app_setting('log/console_level', logging.WARNING)
                                  or logging.WARNING)
        self._remember_filter = get_app_setting('filter/remember', False) in (True, 'true')
        self._apply_log_settings()
        set_console_level(self._console_level)
        self._apply_theme(self._theme)
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
            min(1600, int(screen.width() * 0.85)),
            min(1000, int(screen.height() * 0.9))
        )
        self._build_settings_menu()

        main_layout = QVBoxLayout()
        icon_path = "sinex_parser/ui/icon.ico"
        self.setWindowIcon(QtGui.QIcon(icon_path))

        top_container = QWidget()
        top_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
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
        self.file_info_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.file_info_widget.setMaximumHeight(220)
        left_panel.addWidget(self.file_info_widget, stretch=1)

        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        top_layout.addWidget(left_widget, stretch=1)

        controls_layout = QVBoxLayout()
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(8)

        self.select_file_button = QPushButton("Select SINEX File")
        self.select_file_button.setFont(QFont('Arial', 16))
        self.select_file_button.setFixedSize(500, 60)
        self.select_file_button.clicked.connect(self.select_sinex_file)
        file_row.addWidget(self.select_file_button)

        self.settings_button = QPushButton("⚙")
        self.settings_button.setFont(QFont('Arial', 16))
        self.settings_button.setFixedSize(60, 60)
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self._show_settings_menu)
        file_row.addWidget(self.settings_button)

        controls_layout.addLayout(file_row)

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
        self.skip_validation.setChecked(default_skip_validation())
        self.skip_validation.setToolTip(
            "When enabled, the parser skips the block-structure validation pass\n"
            "before parsing. Saves time on large files from trusted sources."
        )
        self.skip_validation.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WhatsThisCursor))
        controls_layout.addWidget(self.skip_validation)

        self.skip_epoch = QCheckBox("Skip parsing SOLUTION/EPOCHS")
        self.skip_epoch.setChecked(default_skip_epochs())
        self.skip_epoch.setToolTip(
            "When enabled, the parser will ignore the SOLUTION/EPOCHS block\n"
            "to speed up parsing for very large files."
        )
        self.skip_epoch.setCursor(QtGui.QCursor(QtCore.Qt.CursorShape.WhatsThisCursor))

        controls_layout.addWidget(self.skip_epoch)
        controls_layout.addStretch()

        controls_widget = QWidget()
        controls_widget.setLayout(controls_layout)
        controls_widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
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
        self.format_combo.setCurrentText(default_export_format())
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
        self._apply_log_visibility()
        self._copy_app_palette()

        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        if self._remember_geometry:
            saved = get_app_setting('window/geometry')
            if saved is not None:
                self.restoreGeometry(saved)

    def _show_settings_menu(self):
        corner = self.settings_button.rect().bottomLeft()
        self._settings_menu.exec(self.settings_button.mapToGlobal(corner))

    def _build_settings_menu(self):
        settings_menu = QMenu(self)
        self._settings_menu = settings_menu

        theme_menu = settings_menu.addMenu('Theme')
        self._theme_group = QActionGroup(self)
        self._theme_group.setExclusive(True)
        for label, name in (('Light', 'light'), ('Dark', 'dark'), ('Follow system', 'system')):
            act = QAction(label, self, checkable=True)
            act.setData(name)
            act.setChecked(name == self._theme)
            act.triggered.connect(lambda _checked, n=name: self._apply_theme(n, store=True))
            self._theme_group.addAction(act)
            theme_menu.addAction(act)

        settings_menu.addSeparator()

        self._log_action = QAction('Show log panel', self, checkable=True)
        self._log_action.setChecked(self._log_visible)
        self._log_action.triggered.connect(self._toggle_log_panel)
        settings_menu.addAction(self._log_action)

        self._geometry_action = QAction('Remember window size', self, checkable=True)
        self._geometry_action.setChecked(self._remember_geometry)
        self._geometry_action.triggered.connect(self._toggle_remember_geometry)
        settings_menu.addAction(self._geometry_action)

        self._notify_action = QAction('Desktop notifications', self, checkable=True)
        self._notify_action.setChecked(self._notifications)
        self._notify_action.triggered.connect(self._toggle_notifications)
        settings_menu.addAction(self._notify_action)

        settings_menu.addSeparator()

        export_menu = settings_menu.addMenu('Export defaults')

        format_menu = export_menu.addMenu('Format')
        self._format_group = QActionGroup(self)
        self._format_group.setExclusive(True)
        current_format = default_export_format()
        for label in ('NumPy (.npy)', 'CSV (.csv)', 'Text (.txt)', 'Excel (.xlsx)'):
            act = QAction(label, self, checkable=True)
            act.setChecked(label == current_format)
            act.triggered.connect(lambda _checked, f=label: self._set_export_format(f))
            self._format_group.addAction(act)
            format_menu.addAction(act)

        self._export_dir_action = QAction('', self)
        self._export_dir_action.setEnabled(False)
        export_menu.addAction(self._export_dir_action)

        choose_export = QAction('Choose folder...', self)
        choose_export.triggered.connect(self._choose_export_dir)
        export_menu.addAction(choose_export)

        clear_export = QAction('Use the last used folder', self)
        clear_export.triggered.connect(self._clear_export_dir)
        export_menu.addAction(clear_export)

        dpi_menu = export_menu.addMenu('Figure dpi')
        self._dpi_group = QActionGroup(self)
        self._dpi_group.setExclusive(True)
        current_dpi = figure_dpi()
        for dpi in (100, 150, 200, 300):
            act = QAction(str(dpi), self, checkable=True)
            act.setChecked(dpi == current_dpi)
            act.triggered.connect(lambda _checked, d=dpi: self._set_figure_dpi(d))
            self._dpi_group.addAction(act)
            dpi_menu.addAction(act)

        parse_menu = settings_menu.addMenu('Parsing defaults')

        self._skip_validation_action = QAction('Skip file validation', self, checkable=True)
        self._skip_validation_action.setChecked(default_skip_validation())
        self._skip_validation_action.triggered.connect(
            lambda checked: self._set_parse_default('parse/skip_validation', checked))
        parse_menu.addAction(self._skip_validation_action)

        self._skip_epochs_action = QAction('Skip parsing SOLUTION/EPOCHS', self, checkable=True)
        self._skip_epochs_action.setChecked(default_skip_epochs())
        self._skip_epochs_action.triggered.connect(
            lambda checked: self._set_parse_default('parse/skip_epochs', checked))
        parse_menu.addAction(self._skip_epochs_action)

        self._remember_filter_action = QAction('Remember filter thresholds', self, checkable=True)
        self._remember_filter_action.setChecked(self._remember_filter)
        self._remember_filter_action.triggered.connect(self._toggle_remember_filter)
        parse_menu.addAction(self._remember_filter_action)

        settings_menu.addSeparator()

        log_menu = settings_menu.addMenu('Log file')

        self._log_file_action = QAction('Write log file', self, checkable=True)
        self._log_file_action.setChecked(self._log_enabled)
        self._log_file_action.triggered.connect(self._toggle_log_file)
        log_menu.addAction(self._log_file_action)

        self._log_path_action = QAction('', self)
        self._log_path_action.setEnabled(False)
        log_menu.addAction(self._log_path_action)

        cap_menu = log_menu.addMenu('Size limit')
        self._log_cap_group = QActionGroup(self)
        self._log_cap_group.setExclusive(True)
        for label, size in (('No limit', 0), ('1 MB', 1 << 20),
                            ('10 MB', 10 << 20), ('50 MB', 50 << 20)):
            act = QAction(label, self, checkable=True)
            act.setChecked(size == self._log_max_bytes)
            act.triggered.connect(lambda _checked, s=size: self._set_log_cap(s))
            self._log_cap_group.addAction(act)
            cap_menu.addAction(act)

        choose_action = QAction('Choose folder...', self)
        choose_action.triggered.connect(self._choose_log_dir)
        log_menu.addAction(choose_action)

        clear_action = QAction('Clear log file', self)
        clear_action.triggered.connect(self._clear_log_file)
        log_menu.addAction(clear_action)

        console_menu = log_menu.addMenu('Console level')
        self._console_group = QActionGroup(self)
        self._console_group.setExclusive(True)
        for label, level in (('Warnings', logging.WARNING), ('Info', logging.INFO),
                             ('Debug', logging.DEBUG)):
            act = QAction(label, self, checkable=True)
            act.setChecked(level == self._console_level)
            act.triggered.connect(lambda _checked, lv=level: self._set_console_level(lv))
            self._console_group.addAction(act)
            console_menu.addAction(act)

        self._refresh_log_path_action()
        self._refresh_export_dir_action()

    def _apply_theme(self, name: str, store: bool = False):
        scheme = {
            'light': QtCore.Qt.ColorScheme.Light,
            'dark': QtCore.Qt.ColorScheme.Dark,
        }.get(name, QtCore.Qt.ColorScheme.Unknown)
        app = QApplication.instance()
        hints = app.styleHints()
        if not getattr(self, '_scheme_hooked', False):
            hints.colorSchemeChanged.connect(self._refresh_palettes)
            self._scheme_hooked = True
        hints.setColorScheme(scheme)
        self._theme = name
        if store:
            set_app_setting('ui/theme', name)

    def _apply_log_visibility(self):
        for widget in (self.covariance_widget, self.datum_widget):
            container = getattr(widget, 'log_container', None)
            if container is not None:
                container.setVisible(self._log_visible)

    def _refresh_palettes(self, *_):
        QtCore.QTimer.singleShot(0, self._copy_app_palette)

    def _copy_app_palette(self):
        app = QApplication.instance()
        palette = app.palette()
        for widget in app.allWidgets():
            widget.setPalette(palette)
        app.setStyleSheet(
            'QSplitter::handle {background: palette(mid);} '
            'QPlainTextEdit, QTextEdit {background: palette(base); color: palette(text);} '
        )

    def _toggle_log_panel(self, checked: bool):
        self._log_visible = bool(checked)
        self._apply_log_visibility()
        set_app_setting('ui/log_visible', self._log_visible)

    def _toggle_remember_geometry(self, checked: bool):
        self._remember_geometry = bool(checked)
        set_app_setting('ui/remember_geometry', self._remember_geometry)

    def _toggle_notifications(self, checked: bool):
        self._notifications = bool(checked)
        set_app_setting('ui/notifications', self._notifications)

    def _notify(self, message: str):
        if not self._notifications:
            return
        try:
            notification.notify(title="SINEX Studio", message=message)
        except Exception:
            logger.debug("desktop notification failed", exc_info=True)

    def _set_export_format(self, name: str):
        set_app_setting('export/format', name)
        for widget in (self.operations_widget, self.datum_widget):
            combo = getattr(widget, 'format_combo', None)
            if combo is not None:
                combo.setCurrentText(name)
        self.format_combo.setCurrentText(name)

    def _refresh_export_dir_action(self):
        directory = default_export_dir()
        text = directory or 'the last used folder'
        self._export_dir_action.setToolTip(text)
        self._export_dir_action.setText(text if len(text) <= 60 else '...' + text[-57:])

    def _choose_export_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose export folder", default_export_dir())
        if not chosen:
            return
        set_app_setting('export/dir', chosen)
        self._refresh_export_dir_action()

    def _clear_export_dir(self):
        set_app_setting('export/dir', '')
        self._refresh_export_dir_action()

    def _set_figure_dpi(self, dpi: int):
        set_app_setting('figures/dpi', int(dpi))

    def _set_parse_default(self, key: str, checked: bool):
        set_app_setting(key, bool(checked))
        if key == 'parse/skip_validation':
            self.skip_validation.setChecked(bool(checked))
        else:
            self.skip_epoch.setChecked(bool(checked))

    def _toggle_remember_filter(self, checked: bool):
        self._remember_filter = bool(checked)
        set_app_setting('filter/remember', self._remember_filter)
        if self._remember_filter:
            self.remember_filter_thresholds()

    def remember_filter_thresholds(self):
        if not self._remember_filter:
            return
        widget = getattr(self, 'datum_widget', None)
        if widget is None:
            return
        set_app_setting('filter/pos_threshold', float(widget._pos_threshold_m))
        set_app_setting('filter/vel_threshold', float(widget._vel_threshold_m_per_y))

    def _set_console_level(self, level: int):
        self._console_level = int(level)
        set_console_level(self._console_level)
        set_app_setting('log/console_level', self._console_level)

    def _apply_log_settings(self):
        if self._log_dir:
            set_log_file(str(Path(self._log_dir) / LOG_FILENAME), self._log_max_bytes)
        else:
            set_log_file(LOG_FILENAME, self._log_max_bytes)
        set_file_logging(self._log_enabled)

    def _refresh_log_path_action(self):
        path = str(log_file_path())
        self._log_path_action.setToolTip(path)
        self._log_path_action.setText(path if len(path) <= 60 else '...' + path[-57:])

    def _toggle_log_file(self, checked: bool):
        self._log_enabled = bool(checked)
        set_file_logging(self._log_enabled)
        set_app_setting('log/enabled', self._log_enabled)

    def _set_log_cap(self, max_bytes: int):
        self._log_max_bytes = int(max_bytes)
        set_app_setting('log/max_bytes', self._log_max_bytes)
        self._apply_log_settings()
        self._refresh_log_path_action()

    def _choose_log_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose log folder", str(log_file_path().parent))
        if not chosen:
            return
        self._log_dir = chosen
        set_app_setting('log/dir', self._log_dir)
        self._apply_log_settings()
        self._refresh_log_path_action()

    def _clear_log_file(self):
        try:
            clear_log_file()
        except OSError as exc:
            QMessageBox.warning(self, "Log file", f"Could not clear the log file: {exc}")
            return
        QMessageBox.information(self, "Log file", "Log file cleared.")

    def closeEvent(self, event):
        if self._remember_geometry:
            set_app_setting('window/geometry', self.saveGeometry())
        super().closeEvent(event)

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
        self._refresh_block_combo()
        self.update_block_display()
        QMessageBox.information(self, "Success", "SINEX file parsed successfully!")
        self._notify("SINEX file parsed successfully!")

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
        filters = export.FORMAT_FILTERS
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
        self._notify(f"{orig_file} exported.")

    def _refresh_block_combo(self):
        keys = list(self.current_data['blocks'].keys()) if self.current_data else []
        previous = self.block_combo.currentText()
        self.block_combo.blockSignals(True)
        self.block_combo.clear()
        self.block_combo.addItems(keys)
        if previous in keys:
            self.block_combo.setCurrentText(previous)
        self.block_combo.blockSignals(False)
        self.export_button.setEnabled(bool(keys))

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
