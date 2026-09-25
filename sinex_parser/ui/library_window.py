# ui/library_window.py
import gc
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QColor, QDesktopServices
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QTableWidget, QTableWidgetItem, QPushButton,
    QLabel, QAbstractItemView, QHeaderView, QMessageBox,
)

from .. import __version__
from ..core import logger
from ..io import library
from ..io.raw_export import format_size
from .widgets import tone_color

COLUMNS = ["File", "Folder", "Compressed", "Parameters", "Station episodes", "Blocks",
           "Size on disk", "Source size", "Added", "Last opened", "Version"]


def _date(text):
    try:
        return datetime.fromisoformat(text).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return text or ""


class LibraryWindow(QDialog):
    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.setWindowTitle("Library")
        self.resize(1300, 480)
        self.setStyleSheet("QPushButton {font-size: 14px;} QLabel {font-size: 14px;}")
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        self.table.setStyle(app._table_style)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        self.table.doubleClicked.connect(lambda *_: self.load_selected())
        layout.addWidget(self.table)

        row = QHBoxLayout()
        self.total_label = QLabel()
        row.addWidget(self.total_label, 1)
        load = QPushButton("Load")
        load.clicked.connect(self.load_selected)
        delete = QPushButton("Delete")
        delete.clicked.connect(self.delete_selected)
        open_folder = QPushButton("Open folder")
        open_folder.clicked.connect(self.open_folder)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for button in (load, delete, open_folder, close):
            row.addWidget(button)
        layout.addLayout(row)
        self.status_label = QLabel()
        layout.addWidget(self.status_label)
        self.refresh()

    def _status(self, text, tone=None):
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {tone_color(tone, self.status_label)};" if tone else "")

    def refresh(self):
        self.root = self.app.library_root()
        self.rows = library.entries(self.root)
        warn = QColor(tone_color("warn", self.table))
        self.table.setRowCount(len(self.rows))
        for r, entry in enumerate(self.rows):
            meta = entry["meta"]
            if meta is None:
                cells = ["(incomplete entry)", entry["folder"].name] + [""] * 4 + [
                    format_size(entry["disk_size"])] + [""] * 4
            else:
                cells = [meta.get("source_name", ""), meta.get("source_folder", ""),
                         "yes" if meta.get("compressed") else "no",
                         str(meta.get("parameters", "")), str(meta.get("station_episodes", "")),
                         ", ".join(meta.get("blocks", [])), format_size(entry["disk_size"]),
                         format_size(meta.get("source_size", 0)), _date(meta.get("added")),
                         _date(meta.get("last_opened")), str(meta.get("app_version", ""))]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                self.table.setItem(r, c, item)
            if meta is None or meta.get("app_version") != __version__:
                tip = ("Incomplete, it is written again when its file is loaded" if meta is None
                       else "Written by another version, the file is parsed again when loaded")
                for c in range(len(COLUMNS)):
                    self.table.item(r, c).setForeground(warn)
                    self.table.item(r, c).setToolTip(tip)
            if entry["folder"] == self.app._library_entry:
                font = self.table.item(r, 0).font()
                font.setBold(True)
                self.table.item(r, 0).setFont(font)
        self.table.resizeColumnsToContents()
        for c in range(len(COLUMNS)):
            self.table.setColumnWidth(c, min(self.table.columnWidth(c), 220))
        total = sum(e["disk_size"] for e in self.rows)
        self.total_label.setText(f"{len(self.rows)} entries, {format_size(total)} on disk in {self.root}")

    def _selected(self):
        row = self.table.currentRow()
        if row < 0 or row >= len(self.rows):
            self._status("Select an entry first", "warn")
            return None
        return self.rows[row]

    def open_folder(self):
        self.root.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root)))

    def load_selected(self):
        entry = self._selected()
        if entry is None:
            return
        meta = entry["meta"]
        if meta is None:
            self._status("This entry is incomplete. Load its file to write it again.", "warn")
            return
        source = Path(meta.get("source_folder", "")) / meta.get("source_name", "")
        if meta.get("app_version") != __version__:
            if not source.is_file():
                self._status(f"This entry was written by version {meta.get('app_version')} "
                             f"and {source} is missing, so it cannot be parsed again.", "warn")
                return
            self.app.start_parse(source)
        else:
            self.app.load_library_entry(entry["folder"], source)
        self.close()

    def delete_selected(self):
        entry = self._selected()
        if entry is None:
            return
        name = (entry["meta"] or {}).get("source_name", entry["folder"].name)
        if entry["folder"] == self.app._library_entry:
            self._status(f"{name} is loaded. Load another file before deleting its entry.", "warn")
            return
        answer = QMessageBox.question(
            self, "Delete entry", f"Delete the library entry of {name} ({format_size(entry['disk_size'])})?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        gc.collect()
        try:
            library.delete(self.root, entry["folder"].name)
        except (OSError, ValueError) as exc:
            logger.warning(f"Could not delete the library entry of {name}: {exc}")
            self._status(f"Could not delete the entry of {name}: {exc}", "warn")
            return
        logger.info(f"Deleted the library entry of {name}")
        self.refresh()
        self._status(f"Deleted the entry of {name}", "ok")
