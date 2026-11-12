"""Settings > Plugins > Browse gallery: pick a plugin, see what it needs, install it."""
import threading

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMessageBox,
    QPushButton, QVBoxLayout,
)

import plugin_gallery
import plugin_permissions

from .theming import _theme, build_voice_browser_style


class PluginGalleryDialog(QDialog):
    _loaded_signal = Signal(bool, str, list)

    def __init__(self, parent, dark=False):
        super().__init__(parent)
        self.setWindowTitle("Plugin gallery")
        self.resize(620, 560)
        self._dark = dark
        self._entries: list[dict] = []
        self.installed_any = False
        self.setStyleSheet(build_voice_browser_style(dark))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        header = QFrame()
        header.setObjectName("pageHeader")
        h_layout = QVBoxLayout(header)
        h_layout.setContentsMargins(16, 12, 16, 12)
        h_layout.setSpacing(2)
        title = QLabel("Plugin Gallery")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Extra skills for Alyssa. Each one lists what it needs before you install it.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        h_layout.addWidget(title)
        h_layout.addWidget(subtitle)
        layout.addWidget(header)

        self.status_label = QLabel("Loading the gallery…")
        self.status_label.setStyleSheet(f"color: {_theme(dark)['subtext']}; font-size: 11.5px;")
        layout.addWidget(self.status_label)

        self.list_widget = QListWidget()
        self.list_widget.setObjectName("pluginList")
        self.list_widget.currentItemChanged.connect(self._on_selected)
        layout.addWidget(self.list_widget, 1)

        self.details_label = QLabel("")
        self.details_label.setWordWrap(True)
        self.details_label.setTextFormat(Qt.PlainText)
        layout.addWidget(self.details_label)

        row = QHBoxLayout()
        close_btn = QPushButton("Close")
        close_btn.setObjectName("secondaryButton")
        close_btn.clicked.connect(self.accept)
        self.install_btn = QPushButton("Install")
        self.install_btn.setObjectName("primaryButton")
        self.install_btn.setEnabled(False)
        self.install_btn.clicked.connect(self._on_install)
        row.addStretch(1)
        row.addWidget(close_btn)
        row.addWidget(self.install_btn)
        layout.addLayout(row)

        self._loaded_signal.connect(self._on_loaded)
        threading.Thread(target=self._fetch, daemon=True).start()

    def _fetch(self):
        try:
            self._loaded_signal.emit(True, "", plugin_gallery.fetch_index())
        except plugin_gallery.GalleryError as e:
            self._loaded_signal.emit(False, str(e), [])

    def _on_loaded(self, ok: bool, message: str, entries: list):
        if not ok:
            self.status_label.setText(message)
            return
        self._entries = entries
        self.status_label.setText(f"{len(entries)} plugin(s) available." if entries else "The gallery is empty right now.")
        self._refresh_list()

    def _refresh_list(self, select: str = ""):
        self.list_widget.clear()
        for entry in self._entries:
            installed = plugin_gallery.installed_path(entry["name"]) is not None
            item = QListWidgetItem(f"{'✓ ' if installed else ''}{entry['title']}")
            item.setData(Qt.UserRole, entry)
            self.list_widget.addItem(item)
            if entry["name"] == select:
                self.list_widget.setCurrentItem(item)
        if not select and self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _on_selected(self, current, _previous=None):
        entry = current.data(Qt.UserRole) if current is not None else None
        if not entry:
            self.details_label.setText("")
            self.install_btn.setEnabled(False)
            return
        needs = plugin_permissions.describe(entry["permissions"]) if entry["permissions"] else "• Nothing special"
        self.details_label.setText(f"{entry['description']}\n\nIt needs to:\n{needs}")
        installed = plugin_gallery.installed_path(entry["name"]) is not None
        self.install_btn.setText("Reinstall" if installed else "Install")
        self.install_btn.setEnabled(True)

    def _on_install(self):
        item = self.list_widget.currentItem()
        entry = item.data(Qt.UserRole) if item is not None else None
        if not entry:
            return
        if entry["permissions"]:
            answer = QMessageBox.question(
                self, "Install plugin",
                f"{entry['title']} will be able to:\n{plugin_permissions.describe(entry['permissions'])}\n\nInstall it?",
            )
            if answer != QMessageBox.Yes:
                return
        try:
            plugin_gallery.install(entry)
        except (plugin_gallery.GalleryError, OSError) as e:
            self.status_label.setText(f"⚠ {e}")
            return
        self.installed_any = True
        self.status_label.setText(f"✓ Installed {entry['title']}. It's ready to use.")
        self._refresh_list(select=entry["name"])
