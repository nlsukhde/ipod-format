from __future__ import annotations

import os
from pathlib import Path
from typing import Iterable, List, Set

from PySide6 import QtCore, QtGui, QtWidgets


AUDIO_EXTS = {
    ".flac", ".alac", ".wav", ".aiff", ".aif", ".m4a", ".aac",
    ".mp3", ".ogg", ".oga", ".opus", ".wma"
}


class DropList(QtWidgets.QListWidget):
    filesDropped = QtCore.Signal(list)  # List[str]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setDragDropMode(QtWidgets.QAbstractItemView.NoDragDrop)
        self.setPlaceholderText("Drop album folders or audio files here…")

    def dragEnterEvent(self, e: QtGui.QDragEnterEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragEnterEvent(e)

    def dragMoveEvent(self, e: QtGui.QDragMoveEvent) -> None:
        if e.mimeData().hasUrls():
            e.acceptProposedAction()
        else:
            super().dragMoveEvent(e)

    def dropEvent(self, e: QtGui.QDropEvent) -> None:
        urls = e.mimeData().urls()
        paths: list[str] = []
        for u in urls:
            if u.isLocalFile():
                paths.append(u.toLocalFile())
        if paths:
            self.filesDropped.emit(paths)
            e.acceptProposedAction()
        else:
            super().dropEvent(e)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("iPod Format — GUI")
        self.resize(1000, 640)

        # central layout
        central = QtWidgets.QWidget()
        root = QtWidgets.QHBoxLayout(central)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(12)

        # left: queue list
        self.list = DropList()
        self.list.filesDropped.connect(self._on_files_dropped)
        root.addWidget(self.list, 2)

        # right: options panel
        opts_panel = QtWidgets.QWidget()
        opts = QtWidgets.QVBoxLayout(opts_panel)
        opts.setSpacing(10)

        # Replace in place
        self.chk_replace = QtWidgets.QCheckBox(
            "Replace in place (trash originals for non-MP3)")
        self.chk_replace.setChecked(True)
        opts.addWidget(self.chk_replace)

        # Hard delete
        self.chk_hard_delete = QtWidgets.QCheckBox(
            "Hard delete (no Recycle Bin)")
        self.chk_hard_delete.setChecked(False)
        self.chk_hard_delete.setToolTip(
            "If enabled, originals are permanently deleted for non-MP3 sources.")
        opts.addWidget(self.chk_hard_delete)

        # Jobs
        jobs_row = QtWidgets.QHBoxLayout()
        jobs_row.addWidget(QtWidgets.QLabel("Parallel jobs:"))
        self.spin_jobs = QtWidgets.QSpinBox()
        self.spin_jobs.setRange(1, max(1, (os.cpu_count() or 4)))
        self.spin_jobs.setValue(min(4, os.cpu_count() or 2))
        jobs_row.addWidget(self.spin_jobs, 1)
        jobs_row.addStretch(1)
        opts.addLayout(jobs_row)

        # Buttons
        btn_row = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton("Add…")
        self.btn_add.clicked.connect(self._on_add_clicked)
        self.btn_clear = QtWidgets.QPushButton("Clear")
        self.btn_clear.clicked.connect(self._on_clear_clicked)
        self.btn_preview = QtWidgets.QPushButton("Preview Plan")
        self.btn_preview.setEnabled(False)  # wired in Set GUI-2
        self.btn_start = QtWidgets.QPushButton("Start")
        self.btn_start.setEnabled(False)     # wired in Set GUI-2
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_clear)
        btn_row.addStretch(1)
        btn_row.addWidget(self.btn_preview)
        btn_row.addWidget(self.btn_start)
        opts.addLayout(btn_row)

        # Log area (read-only for now)
        self.txt_log = QtWidgets.QPlainTextEdit()
        self.txt_log.setReadOnly(True)
        self.txt_log.setPlaceholderText("Logs will appear here…")
        self.txt_log.setMinimumWidth(360)
        opts.addWidget(self.txt_log, 1)

        root.addWidget(opts_panel, 1)
        self.setCentralWidget(central)

        # Status bar
        self.statusBar().showMessage("Drop album folders or audio files to begin.")

        # internal model
        self._queue: List[Path] = []
        self._queue_set: Set[str] = set()

    # ========== Queue management ==========

    def _normalize_paths(self, paths: Iterable[str]) -> list[Path]:
        normed: list[Path] = []
        for p in paths:
            try:
                path = Path(p).resolve()
            except Exception:
                continue
            if not path.exists():
                continue
            # Allow directories or supported audio files
            if path.is_dir() or path.suffix.lower() in AUDIO_EXTS:
                normed.append(path)
        return normed

    def _add_to_queue(self, items: Iterable[Path]) -> None:
        added = 0
        for p in items:
            key = str(p)
            if key in self._queue_set:
                continue
            self._queue.append(p)
            self._queue_set.add(key)
            item = QtWidgets.QListWidgetItem(key)
            # Light icon for dirs vs files
            icon = self.style().standardIcon(
                QtWidgets.QStyle.SP_DirIcon if p.is_dir() else QtWidgets.QStyle.SP_FileIcon
            )
            item.setIcon(icon)
            self.list.addItem(item)
            added += 1
        if added:
            self.statusBar().showMessage(
                f"Queued {added} item(s). Total: {len(self._queue)}")
        self._update_buttons()

    def _remove_selected(self) -> None:
        for item in self.list.selectedItems():
            row = self.list.row(item)
            path_str = item.text()
            self.list.takeItem(row)
            self._queue_set.discard(path_str)
            try:
                self._queue.remove(Path(path_str))
            except ValueError:
                pass
        self._update_buttons()

    def _clear_all(self) -> None:
        self.list.clear()
        self._queue.clear()
        self._queue_set.clear()
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_items = len(self._queue) > 0
        # will flip to True in next set when wired
        self.btn_start.setEnabled(False)
        self.btn_preview.setEnabled(has_items)
        self.btn_clear.setEnabled(has_items)

    # ========== UI Handlers ==========

    @QtCore.Slot()
    def _on_add_clicked(self) -> None:
        dlg = QtWidgets.QFileDialog(
            self, "Select album folders or audio files")
        dlg.setFileMode(QtWidgets.QFileDialog.ExistingFiles)
        dlg.setOption(QtWidgets.QFileDialog.DontUseNativeDialog, False)
        dlg.setNameFilter("Audio or folders (*.*)")
        if dlg.exec():
            self._add_to_queue(self._normalize_paths(dlg.selectedFiles()))

    @QtCore.Slot()
    def _on_clear_clicked(self) -> None:
        self._clear_all()

    @QtCore.Slot(list)
    def _on_files_dropped(self, paths: list[str]) -> None:
        self._add_to_queue(self._normalize_paths(paths))

    # ========== Settings snapshot for pipeline (used in next set) ==========

    def collect_options(self) -> dict:
        return {
            "replace_in_place": self.chk_replace.isChecked(),
            "hard_delete": self.chk_hard_delete.isChecked(),
            "jobs": int(self.spin_jobs.value()),
        }

    # ========== Public API for later wiring ==========

    def get_queue(self) -> list[Path]:
        return list(self._queue)


def main() -> int:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    w = MainWindow()
    w.show()
    return app.exec()
