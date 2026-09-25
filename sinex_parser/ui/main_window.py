# ui/main_window.py
import logging
import os
import subprocess
import sys
import tempfile

from PyQt6.QtGui import QFont, QAction, QActionGroup
from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QHBoxLayout, QWidget,
    QLabel, QPushButton, QProgressBar, QTabWidget,
    QMessageBox, QFileDialog, QComboBox, QPlainTextEdit, QCheckBox, QApplication, QSizePolicy,
    QSplitter, QMenu, QTableWidget, QTableWidgetItem, QLineEdit, QAbstractItemView,
    QStyleFactory,
    QHeaderView
)
from PyQt6.QtCore import QUrl, pyqtSignal, QStandardPaths
from PyQt6.QtQuickWidgets import QQuickWidget
import numpy as np
from pathlib import Path
from plyer import notification

from PyQt6 import QtGui, QtCore

from .. import __version__

PROGRAM_DIR = Path(__file__).resolve().parents[2]

ICON_PATH = PROGRAM_DIR / 'sinex_parser' / 'ui' / 'icon.ico'
from ..core import (
    logger, benchmark,
    remember_dialog_dir, default_save_path, ensure_suffix,
    get_app_setting, set_app_setting,
    LOG_FILENAME, log_file_path, set_file_logging, set_log_file, clear_log_file,
    default_export_format, default_export_dir, default_skip_validation,
    default_skip_epochs, figure_dpi, set_console_level, get_dialog_dir,
)

from ..io import create_parsers, parallel, library
from ..io import export, raw_export, reader
from ..analysis import episodes
from ..ui.widgets import (
    ParserWorker, CovarianceMatrixWidget, StationsWidget, run_task, TASK_SIGNALS,
    InfoWidget, DatumWidget, FileInfoWidget, QPlainTextEditLogger,
    make_section_header, set_status, tone_color
)
from .library_window import LibraryWindow


class SINEXParserApp(QMainWindow):
    benchmark_updated = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.block_parsers = create_parsers()
        self.current_data = None
        self.custom_variance_factor = None
        self._parse_generation = 0
        self._active_workers = set()
        self._file_loaded = False
        self._source_path = None
        self._raw_export_running = False
        self._library_entry = None
        self._library_fallback_logged = False
        self.discontinuities = []
        self.episode_labels = {}
        self._keep_binary = get_app_setting('library/keep', True) not in (False, 'false')
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
        self._record_benchmark = get_app_setting('benchmark/record', False) in (True, 'true')
        parallel.ENABLED = get_app_setting('parse/parallel', True) not in (False, 'false')
        parallel.MAX_WORKERS = int(get_app_setting('parse/workers', 4) or 4)
        self._apply_log_settings()
        set_console_level(self._console_level)
        self._apply_theme(self._theme)
        self.init_ui()
        self.benchmark_updated.connect(self._refresh_benchmark_button)
        benchmark.on_update = self.benchmark_updated.emit
        benchmark.set_enabled(self._record_benchmark)
        # warning
        logger.warning(f"======== SINEX TRF Studio version {__version__} ========")
        logger.warning(" ")
        #logger.warning("Preliminary test version. Verify all results before use.")

    def init_ui(self):
        self.setWindowTitle(f'SINEX TRF Studio {__version__}')
        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(
            screen.x() + 50,
            screen.y() + 50,
            min(1600, int(screen.width() * 0.85)),
            min(1000, int(screen.height() * 0.9))
        )
        self._build_settings_menu()

        main_layout = QVBoxLayout()
        self.setWindowIcon(QtGui.QIcon(str(ICON_PATH)))

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

        self.select_file_button = QPushButton("Select SINEX File")
        self.select_file_button.setFont(QFont('Arial', 16))
        self.select_file_button.setFixedSize(440, 64)
        self.select_file_button.clicked.connect(self.select_sinex_file)
        controls_layout.addWidget(self.select_file_button)

        file_row = QHBoxLayout()
        file_row.setContentsMargins(0, 0, 0, 0)
        file_row.setSpacing(8)

        self.library_button = QPushButton("Library")
        self.library_button.setFont(QFont('Arial', 16))
        self.library_button.setFixedSize(372, 56)
        self.library_button.setToolTip("Files kept in the library")
        self.library_button.clicked.connect(self.show_library)
        file_row.addWidget(self.library_button)

        self.settings_button = QPushButton("⚙")
        self.settings_button.setFont(QFont('Arial', 16))
        self.settings_button.setFixedSize(60, 56)
        self.settings_button.setToolTip("Settings")
        self.settings_button.clicked.connect(self._show_settings_menu)
        file_row.addWidget(self.settings_button)

        controls_layout.addLayout(file_row)
        controls_layout.addStretch()

        controls_widget = QWidget()
        controls_widget.setLayout(controls_layout)
        controls_widget.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        top_layout.addWidget(controls_widget, stretch=0)

        top_container.setMaximumHeight(top_container.sizeHint().height())
        main_layout.addWidget(top_container, stretch=0)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(True)
        self.progress_label = QLabel()
        self.progress_label.setVisible(False)
        progress_row = QHBoxLayout()
        progress_row.addWidget(self.progress_label)
        progress_row.addWidget(self.progress_bar, 1)
        main_layout.addLayout(progress_row)
        TASK_SIGNALS.busy.connect(self._show_task)

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
        self.stations_widget.discontinuity_btn.clicked.connect(self.load_discontinuity_list)
        self.tab_widget.addTab(self.stations_widget, "Stations")
        QQuickWidget(self).hide()

        self.tab_widget.addTab(self._build_raw_export_tab(), "Block Export")

        # Tab5: Info
        self.info_widget = InfoWidget()
        self.tab_widget.addTab(self.info_widget, "Information")

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

    def _build_raw_export_tab(self):
        tab = QWidget()
        tab.setStyleSheet(
            "QPushButton {font-size: 14px;} QComboBox {font-size: 14px;} QLabel {font-size: 14px;} "
            "QCheckBox {font-size: 14px;} QLineEdit {font-size: 14px;}")
        layout = QVBoxLayout(tab)

        blocks_panel = QWidget()
        blocks_panel.setMinimumWidth(560)
        blocks_layout = QVBoxLayout(blocks_panel)
        blocks_layout.setContentsMargins(0, 0, 0, 0)
        make_section_header("Blocks in the file", blocks_layout)
        self.block_table = QTableWidget(0, 4)
        self.block_table.setHorizontalHeaderLabels(["Block", "Kind", "Size", "Estimated output"])
        self.block_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.block_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.block_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.block_table.verticalHeader().setVisible(False)
        self.block_table.setWordWrap(False)
        self._table_style = QStyleFactory.create('Fusion')
        self.block_table.setStyle(self._table_style)
        header = self.block_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        self.block_table.currentCellChanged.connect(lambda *_: self.update_block_display())
        self.block_table.itemChanged.connect(lambda *_: self._update_raw_summary())
        blocks_layout.addWidget(self.block_table)
        select_row = QHBoxLayout()
        select_all = QPushButton("Select all")
        select_all.clicked.connect(lambda: self._set_raw_checks(True))
        select_none = QPushButton("Select none")
        select_none.clicked.connect(lambda: self._set_raw_checks(False))
        select_row.addWidget(select_all)
        select_row.addWidget(select_none)
        select_row.addStretch()
        blocks_layout.addLayout(select_row)

        preview_panel = QWidget()
        preview_layout = QVBoxLayout(preview_panel)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        make_section_header("Preview", preview_layout)
        self.preview_label = QLabel("No file loaded")
        self.preview_label.setWordWrap(True)
        preview_layout.addWidget(self.preview_label)
        self.preview_table = QTableWidget()
        self.preview_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.preview_table.setStyle(self._table_style)
        preview_layout.addWidget(self.preview_table)

        splitter = QSplitter(QtCore.Qt.Orientation.Horizontal)
        splitter.addWidget(blocks_panel)
        splitter.addWidget(preview_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        layout.addWidget(splitter, 1)

        make_section_header("Export", layout)
        options_row = QHBoxLayout()
        options_row.addWidget(QLabel("Format:"))
        self.format_combo = QComboBox()
        self.format_combo.addItems(list(export.FORMAT_EXTENSIONS))
        self.format_combo.setCurrentText(default_export_format())
        self.format_combo.currentIndexChanged.connect(lambda *_: self._refresh_raw_estimates())
        options_row.addWidget(self.format_combo)
        options_row.addSpacing(16)
        self.param_labels_check = QCheckBox("Parameter labels for matrices")
        self.param_labels_check.setChecked(True)
        self.param_labels_check.setToolTip(
            "Write a _params.csv beside each matrix with the parameter of every row")
        options_row.addWidget(self.param_labels_check)
        self.manifest_check = QCheckBox("Write manifest")
        self.manifest_check.setChecked(True)
        self.manifest_check.setToolTip(
            f"Write {raw_export.MANIFEST_NAME} with the size and SHA-256 of every file")
        options_row.addWidget(self.manifest_check)
        options_row.addStretch()
        layout.addLayout(options_row)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        self.export_dir_edit = QLineEdit(default_export_dir() or get_dialog_dir())
        folder_row.addWidget(self.export_dir_edit, 1)
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._choose_raw_export_dir)
        folder_row.addWidget(browse)
        self.export_button = QPushButton("Export")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(self.export_data)
        folder_row.addWidget(self.export_button)
        layout.addLayout(folder_row)

        self.raw_status_label = QLabel("No file loaded")
        layout.addWidget(self.raw_status_label)
        return tab

    def _create_desktop_shortcut(self, name):
        target = PROGRAM_DIR / name
        desktop = Path(QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DesktopLocation))
        try:
            if os.name == 'nt':
                link = desktop / 'SINEX TRF Studio.lnk'
                q = lambda p: str(p).replace("'", "''")
                script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(link)}'); "
                          f"$s.TargetPath = '{q(target)}'; $s.WorkingDirectory = '{q(PROGRAM_DIR)}'; "
                          f"$s.IconLocation = '{q(ICON_PATH)}'; $s.Save()")
                subprocess.run(['powershell', '-NoProfile', '-NonInteractive', '-Command', script],
                               check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
            elif sys.platform == 'darwin':
                link = desktop / 'SINEX TRF Studio.command'
                link.write_text(f'#!/bin/bash\ncd "{PROGRAM_DIR}"\nexec bash "{target}"\n')
                link.chmod(0o755)
            else:
                icon = PROGRAM_DIR / 'sinex_parser' / 'ui' / 'icon.png'
                self.windowIcon().pixmap(256, 256).save(str(icon))
                link = desktop / 'SINEX TRF Studio.desktop'
                link.write_text("[Desktop Entry]\nType=Application\nName=SINEX TRF Studio\n"
                                f"Exec=bash \"{target}\"\nPath={PROGRAM_DIR}\nIcon={icon}\nTerminal=true\n")
                link.chmod(0o755)
        except Exception as exc:
            logger.error(f"Could not create the desktop shortcut: {exc}")
            QMessageBox.warning(self, "Desktop shortcut", f"Could not create the desktop shortcut: {exc}")
            return
        logger.info(f"Created desktop shortcut {link}")
        QMessageBox.information(self, "Desktop shortcut", f"Created {link.name} on the desktop.")

    def _show_settings_menu(self):
        corner = self.settings_button.rect().bottomLeft()
        self._settings_menu.exec(self.settings_button.mapToGlobal(corner))

    def _build_settings_menu(self):
        settings_menu = QMenu(self)
        settings_menu.setToolTipsVisible(True)
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

        parse_menu = settings_menu.addMenu('Parsing')
        parse_menu.setToolTipsVisible(True)

        self._skip_validation_action = QAction('Skip file validation', self, checkable=True)
        self._skip_validation_action.setChecked(default_skip_validation())
        self._skip_validation_action.setToolTip(
            "Skip the block structure validation pass before parsing.\n"
            "Saves time on large files from trusted sources.")
        self._skip_validation_action.triggered.connect(
            lambda checked: self.set_parse_option('parse/skip_validation', checked))
        parse_menu.addAction(self._skip_validation_action)

        self._skip_epochs_action = QAction('Skip parsing SOLUTION/EPOCHS', self, checkable=True)
        self._skip_epochs_action.setChecked(default_skip_epochs())
        self._skip_epochs_action.setToolTip(
            "Ignore the SOLUTION/EPOCHS block to speed up parsing of very large files.\n"
            "Episode data spans need this block.")
        self._skip_epochs_action.triggered.connect(
            lambda checked: self.set_parse_option('parse/skip_epochs', checked))
        parse_menu.addAction(self._skip_epochs_action)
        parse_menu.addSeparator()

        self._parallel_action = QAction('Parse large files in parallel', self, checkable=True)
        self._parallel_action.setChecked(parallel.ENABLED)
        self._parallel_action.triggered.connect(self._toggle_parallel)
        parse_menu.addAction(self._parallel_action)

        workers_menu = parse_menu.addMenu('Parallel workers')
        self._workers_group = QActionGroup(self)
        self._workers_group.setExclusive(True)
        for n in (2, 4, 8):
            act = QAction(str(n), self, checkable=True)
            act.setChecked(n == parallel.MAX_WORKERS)
            act.triggered.connect(lambda _checked, n=n: self._set_parse_workers(n))
            self._workers_group.addAction(act)
            workers_menu.addAction(act)

        self._remember_filter_action = QAction('Remember filter thresholds', self, checkable=True)
        self._remember_filter_action.setChecked(self._remember_filter)
        self._remember_filter_action.triggered.connect(self._toggle_remember_filter)
        parse_menu.addAction(self._remember_filter_action)

        library_menu = settings_menu.addMenu('Library')

        self._keep_binary_action = QAction('Keep a binary copy of loaded files', self, checkable=True)
        self._keep_binary_action.setChecked(self._keep_binary)
        self._keep_binary_action.triggered.connect(self._toggle_keep_binary)
        library_menu.addAction(self._keep_binary_action)

        self._library_dir_action = QAction('', self)
        self._library_dir_action.setEnabled(False)
        library_menu.addAction(self._library_dir_action)

        choose_library = QAction('Library folder...', self)
        choose_library.triggered.connect(self._choose_library_dir)
        library_menu.addAction(choose_library)

        shortcut_menu = settings_menu.addMenu('Create desktop shortcut')
        for name, native in (('run_windows.bat', os.name == 'nt'),
                             ('run_macos_linux.sh', os.name != 'nt')):
            act = QAction(name, self)
            act.setEnabled(native and (PROGRAM_DIR / name).exists())
            act.triggered.connect(lambda _checked, name=name: self._create_desktop_shortcut(name))
            shortcut_menu.addAction(act)

        settings_menu.addSeparator()

        self._benchmark_action = QAction('Record benchmark', self, checkable=True)
        self._benchmark_action.setChecked(self._record_benchmark)
        self._benchmark_action.setToolTip("Record timings and peak process memory")
        self._benchmark_action.triggered.connect(self._set_benchmark_enabled)
        settings_menu.addAction(self._benchmark_action)

        self._benchmark_export_action = QAction('Export benchmark report...', self)
        self._benchmark_export_action.setEnabled(benchmark.has_data())
        self._benchmark_export_action.triggered.connect(self.export_benchmark_report)
        settings_menu.addAction(self._benchmark_export_action)

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
        self._refresh_library_dir_action()

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

    def _toggle_parallel(self, checked: bool):
        parallel.ENABLED = bool(checked)
        set_app_setting('parse/parallel', bool(checked))

    def _set_parse_workers(self, n: int):
        parallel.MAX_WORKERS = n
        set_app_setting('parse/workers', n)

    def _toggle_keep_binary(self, checked: bool):
        self._keep_binary = bool(checked)
        set_app_setting('library/keep', self._keep_binary)

    def _refresh_library_dir_action(self):
        text = str(self.library_root())
        self._library_dir_action.setToolTip(text)
        self._library_dir_action.setText(text if len(text) <= 60 else '...' + text[-57:])

    def _choose_library_dir(self):
        chosen = QFileDialog.getExistingDirectory(self, "Choose library folder", str(self.library_root().parent))
        if not chosen:
            return
        set_app_setting('library/dir', chosen)
        logger.info(f"Library folder: {self.library_root()}")
        self._refresh_library_dir_action()

    def library_root(self) -> Path:
        chosen = get_app_setting('library/dir', '')
        if chosen:
            return Path(chosen) / library.FOLDER_NAME
        root = (library.PROGRAM_DIR or PROGRAM_DIR) / 'library'
        try:
            root.mkdir(parents=True, exist_ok=True)
            tempfile.TemporaryFile(dir=root).close()
            return root
        except OSError:
            fallback = Path(QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.AppDataLocation)) / 'library'
            if not self._library_fallback_logged:
                self._library_fallback_logged = True
                logger.warning(f"The program folder is not writable, the library is in {fallback}")
            return fallback

    def show_library(self):
        self._library_window = LibraryWindow(self)
        self._library_window.show()

    def set_parse_option(self, key: str, checked: bool):
        set_app_setting(key, bool(checked))
        action = self._skip_validation_action if key == 'parse/skip_validation' else self._skip_epochs_action
        action.setChecked(bool(checked))
        if self._file_loaded or self._active_workers:
            logger.info(f"{action.text()} is {'on' if checked else 'off'}, it applies to the next load")

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
        self._record_benchmark = bool(enabled)
        set_app_setting('benchmark/record', self._record_benchmark)
        self._benchmark_action.setChecked(self._record_benchmark)
        benchmark.set_enabled(self._record_benchmark)
        self._refresh_benchmark_button()

    def _refresh_benchmark_button(self) -> None:
        self._benchmark_export_action.setEnabled(benchmark.has_data())

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
            "SINEX files (*.sinex *.snx *.snx.gz *.gz);;All files (*.*)"
        )
        if fname:
            remember_dialog_dir(fname)
            self.start_parse(Path(fname))

    def load_library_entry(self, folder, source):
        self.start_parse(Path(source), entry=Path(folder))

    def start_parse(self, fpath, entry=None):
        if self._file_loaded:
            logger.info("----- New File -----")

        logger.info(f"Selected SINEX file: {fpath.name}")
        if self.file_info_widget:  # Update new widget
            self.file_info_widget.clear_display()  # Clear previous info

        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        skip_epochs = default_skip_epochs()
        skip_val = default_skip_validation()
        self._parse_generation += 1
        try:
            size = fpath.stat().st_size
        except OSError:
            size = 0

        benchmark.start_file(fpath.name, size, generation=self._parse_generation)
        worker = ParserWorker(fpath, create_parsers(), skip_epochs_block=skip_epochs, skip_validation=skip_val,
                              library_root=self.library_root(), keep=self._keep_binary, entry=entry)
        # tag worker with the current parse generation so a stale worker that finishes late cannot overwrite newer data
        worker.parse_generation = self._parse_generation
        self._active_workers.add(worker)
        worker.finished.connect(self.handle_parsing_complete)
        worker.error.connect(self.handle_parsing_error)
        self.worker = worker
        worker.start()

    def _show_task(self, label):
        self.progress_label.setText(label)
        self.progress_label.setVisible(bool(label))
        if label:
            self.progress_bar.setRange(0, 0)
            self.progress_bar.setVisible(True)
        elif not self._active_workers:
            self.progress_bar.setVisible(False)

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
        self._file_loaded = True
        if self.datum_widget:
            self.datum_widget.log_new_file_loaded(Path(self.worker.filename).name)
        #reset computed data from any previous file
        if self.covariance_widget and self.covariance_widget.operations_widget:
            self.covariance_widget.operations_widget.reset_for_new_file()
        self.progress_bar.setVisible(False)
        self._source_path = Path(self.worker.filename)
        self._library_entry = self.worker.library_entry
        self.file_label.setText(self._source_path.name)
        self._refresh_benchmark_button()
        # update ui
        if self.datum_widget:
            self.datum_widget.sigma_theta_btn.setEnabled(True)

        if self.file_info_widget:
            self.file_info_widget.update_display()
        # Update the visualization widget
        self.covariance_widget.setup_data(data['blocks'])
        site_data = data['blocks'].get('SITE/ID')
        self._update_episode_labels()
        station_episodes = episodes.positions(data['blocks'].get('SOLUTION/ESTIMATE'))
        if site_data:
            if not self.worker.reused:
                logger.info(f"Parsed {len(site_data)} stations from SITE/ID.")
            self.stations_widget.set_data(site_data, station_episodes)
        else:
            logger.info("No SITE/ID data found.")
            # clear stations left over from any previous file
            self.stations_widget.set_data([], station_episodes)
        self._refresh_raw_blocks()
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
        if getattr(worker, "structure_error", False):
            QMessageBox.critical(self, "Error", "Invalid SINEX block structure!")
            return
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
            self.datum_widget._clear_station_cache()
        if self.covariance_widget and self.covariance_widget.operations_widget:
            self.covariance_widget.operations_widget.reset_for_new_file()
        if self.covariance_widget:
            self.covariance_widget.setup_data({})
        if self.stations_widget:
            self._update_episode_labels()
            self.stations_widget.set_data([])
        self._source_path = None
        self._library_entry = None
        self._refresh_raw_blocks()

    def _update_episode_labels(self):
        blocks = (self.current_data or {}).get('blocks', {})
        self.episode_labels = episodes.labels(blocks.get('SOLUTION/EPOCHS'), self.discontinuities)
        self.stations_widget.labels = self.episode_labels

    def load_discontinuity_list(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Load discontinuity list", get_dialog_dir(),
            "SINEX files (*.snx *.SNX *.gz);;All files (*)")
        if not path:
            return
        remember_dialog_dir(path)
        try:
            records = reader.read_discontinuities(path)
        except Exception as exc:
            logger.error(f"Discontinuity list not read: {exc}")
            QMessageBox.warning(self, "Discontinuity list", str(exc))
            return
        self.discontinuities = records
        self.stations_widget.discontinuity_label.setText(f"{Path(path).name}: {len(records)} records")
        self._update_episode_labels()
        self.stations_widget.update_station_map()
        self.datum_widget._refresh_filtered_dialog_if_open()

    def _raw_ext(self):
        return export.FORMAT_EXTENSIONS[self.format_combo.currentText()][1:]

    def _raw_blocks(self):
        return self.current_data['blocks'] if self.current_data else {}

    def _raw_selected(self):
        keys = []
        for row in range(self.block_table.rowCount()):
            item = self.block_table.item(row, 0)
            if (item.checkState() == QtCore.Qt.CheckState.Checked
                    and item.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable):
                keys.append(item.text())
        return keys

    def _set_raw_checks(self, checked):
        state = QtCore.Qt.CheckState.Checked if checked else QtCore.Qt.CheckState.Unchecked
        self.block_table.blockSignals(True)
        for row in range(self.block_table.rowCount()):
            item = self.block_table.item(row, 0)
            if item.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable:
                item.setCheckState(state)
        self.block_table.blockSignals(False)
        self._update_raw_summary()

    def _refresh_raw_blocks(self):
        blocks = self._raw_blocks()
        entries = raw_export.list_blocks(blocks)
        self.block_table.blockSignals(True)
        self.block_table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            name = QTableWidgetItem(entry['block'])
            name.setCheckState(QtCore.Qt.CheckState.Checked)
            self.block_table.setItem(row, 0, name)
            self.block_table.setItem(row, 1, QTableWidgetItem(entry['kind']))
            self.block_table.setItem(row, 2, QTableWidgetItem(
                raw_export.describe_shape(blocks[entry['block']])))
            self.block_table.setItem(row, 3, QTableWidgetItem(''))
        self.block_table.blockSignals(False)
        if not self.export_dir_edit.text():
            self.export_dir_edit.setText(default_export_dir() or get_dialog_dir())
        self._refresh_raw_estimates()
        if entries:
            self.block_table.setCurrentCell(0, 0)
        self.update_block_display()

    def _refresh_raw_estimates(self):
        blocks = self._raw_blocks()
        ext = self._raw_ext()
        warn = QtGui.QColor(tone_color('warn', self.block_table))
        checkable = QtCore.Qt.ItemFlag.ItemIsUserCheckable
        self.block_table.blockSignals(True)
        for row in range(self.block_table.rowCount()):
            name = self.block_table.item(row, 0)
            data = blocks.get(name.text())
            if data is None:
                continue
            self.block_table.item(row, 3).setText(raw_export.describe_estimate(name.text(), data, ext))
            problem = raw_export.xlsx_problem(data) if ext == 'xlsx' else None
            items = [self.block_table.item(row, col) for col in range(4)]
            if problem:
                if name.flags() & checkable:
                    name.setData(QtCore.Qt.ItemDataRole.UserRole,
                                 name.checkState() == QtCore.Qt.CheckState.Checked)
                name.setCheckState(QtCore.Qt.CheckState.Unchecked)
                name.setFlags(name.flags() & ~checkable)
                for item in items:
                    item.setForeground(warn)
                    item.setToolTip(problem)
            else:
                if not name.flags() & checkable:
                    name.setFlags(name.flags() | checkable)
                    if name.data(QtCore.Qt.ItemDataRole.UserRole):
                        name.setCheckState(QtCore.Qt.CheckState.Checked)
                    name.setData(QtCore.Qt.ItemDataRole.UserRole, None)
                for item in items:
                    item.setData(QtCore.Qt.ItemDataRole.ForegroundRole, None)
                    item.setToolTip('')
        self.block_table.blockSignals(False)
        self._update_raw_summary()

    def _update_raw_summary(self):
        if self._raw_export_running:
            return
        if not self.current_data:
            set_status(self.raw_status_label, "No file loaded")
            self.export_button.setEnabled(False)
            return
        if not self.block_table.rowCount():
            set_status(self.raw_status_label, "No exportable blocks in this file")
            self.export_button.setEnabled(False)
            return
        blocks = self._raw_blocks()
        ext = self._raw_ext()
        keys = self._raw_selected()
        total = sum(raw_export.estimate_size(k, blocks[k], ext)[0] for k in keys)
        set_status(self.raw_status_label,
                   f"{len(keys)} of {self.block_table.rowCount()} blocks selected, "
                   f"about {raw_export.format_size(total)}")
        self.export_button.setEnabled(bool(keys))

    def update_block_display(self):
        row = self.block_table.currentRow()
        blocks = self._raw_blocks()
        self.preview_table.clear()
        if row < 0 or not blocks:
            self.preview_table.setRowCount(0)
            self.preview_table.setColumnCount(0)
            self.preview_label.setText("No block selected" if self.current_data else "No file loaded")
            return
        key = self.block_table.item(row, 0).text()
        headers, labels, cells, caption = raw_export.preview(blocks[key])
        self.preview_table.setUpdatesEnabled(False)
        self.preview_table.setRowCount(len(cells))
        self.preview_table.setColumnCount(len(headers))
        self.preview_table.setHorizontalHeaderLabels(headers)
        self.preview_table.setVerticalHeaderLabels(labels)
        for i, line in enumerate(cells):
            for j, text in enumerate(line):
                self.preview_table.setItem(i, j, QTableWidgetItem(text))
        ranks = raw_export.sigma_ranks(blocks[key])
        if ranks is not None:
            col = headers.index('sigma')
            base = QtGui.QColor(tone_color("warn", self.preview_table))
            for i, rank in enumerate(ranks):
                if np.isfinite(rank):
                    item = self.preview_table.item(i, col)
                    shade = QtGui.QColor(base)
                    shade.setAlpha(int(200 * rank ** 2))
                    item.setBackground(shade)
                    ptype = cells[i][headers.index('type')]
                    item.setToolTip(f"Percentile {rank * 100:.0f} among the {ptype} sigmas")
        self.preview_table.setUpdatesEnabled(True)
        self.preview_table.resizeColumnsToContents()
        self.preview_label.setText(f"{key}: {caption}")

    def _choose_raw_export_dir(self):
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose export folder", self.export_dir_edit.text() or default_save_path(""))
        if chosen:
            self.export_dir_edit.setText(chosen)

    def export_data(self):
        if not self.current_data or self._raw_export_running:
            return
        keys = self._raw_selected()
        if not keys:
            set_status(self.raw_status_label, "Select at least one block")
            return
        if not self.export_dir_edit.text().strip():
            self._choose_raw_export_dir()
        text = self.export_dir_edit.text().strip()
        if not text:
            return
        folder = Path(text)
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.critical(self, "Export Error", f"Cannot write to {folder}: {exc}")
            return
        ext = self._raw_ext()
        blocks = self.current_data['blocks']
        source_name = self.current_data['metadata'].get('filename', '')
        labels = self.param_labels_check.isChecked()
        manifest = self.manifest_check.isChecked()
        names = raw_export.planned_files(blocks, keys, ext, source_name, labels, manifest)
        existing = [n for n in names if (folder / n).exists()]
        if existing:
            answer = QMessageBox.question(
                self, "Replace files",
                f"{len(existing)} of the {len(names)} files already exist in {folder}. Replace them?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                set_status(self.raw_status_label, "Export cancelled")
                return
        remember_dialog_dir(os.path.join(str(folder), ''))
        self._raw_export_running = True
        self.export_button.setEnabled(False)
        set_status(self.raw_status_label, f"Exporting {len(keys)} blocks to {folder}...")
        logger.info(f"Raw export of {len(keys)} blocks as .{ext} to {folder}")
        run_task(raw_export.export_blocks,
                 (blocks, keys, folder, ext, source_name, self._source_path, labels, manifest),
                 self._raw_export_done, "Exporting blocks")

    def _raw_export_done(self, worker):
        self._raw_export_running = False
        self.export_button.setEnabled(bool(self.current_data) and bool(self._raw_selected()))
        if worker.error is not None:
            logger.error(f"Export error: {worker.error}")
            set_status(self.raw_status_label, f"Export failed: {worker.error}")
            QMessageBox.critical(self, "Error", f"Failed to export blocks: {worker.error}")
            return
        result = worker.result
        count = len(result['written']) + (1 if result['manifest'] else 0)
        failures = result['failures']
        lines = [f"{count} files written to {result['folder']}"]
        if failures:
            lines.append(f"{len(failures)} failed:")
            lines += [f"  {f.get('file') or f['block']}: {f['error']}" for f in failures]
        if result['warnings']:
            lines.append("Warnings:")
            lines += [f"  {w}" for w in result['warnings']]
        message = "\n".join(lines)
        if failures:
            set_status(self.raw_status_label,
                       f"Export failed for {len(failures)} item(s), {count} files written to {result['folder']}")
            QMessageBox.warning(self, "Export", message)
        else:
            set_status(self.raw_status_label, f"Exported {count} files to {result['folder']}")
            QMessageBox.information(self, "Success", message)

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
            run_task(matrix_parser.export, (arr, Path(out_file), extension[1:]),
                     lambda w: self._operations_export_done(w, orig_file), "Exporting the matrix")

    def _operations_export_done(self, worker, orig_file):
        if worker.error is not None:
            logger.error(f"Export error: {worker.error}")
            QMessageBox.critical(self, "Error", f"Export fail: {worker.error}")
        else:
            QMessageBox.information(self, "Success", "Data exported.")
        self._notify(f"{orig_file} exported.")
