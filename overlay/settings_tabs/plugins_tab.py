import os
import re

from PySide6.QtCore import Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QGridLayout, QPushButton, QLabel, QInputDialog,
    QMessageBox, QSplitter, QListWidget, QListWidgetItem, QPlainTextEdit,
)

import actions
import brain
import plugin_loader
import plugin_permissions

from ..theming import _apply_elevation


class PluginsTabMixin:
    def _build_plugins_tab(self) -> QWidget:
        """In-app plugins/*.py editor. `_` prefix = disabled. No live-apply while typing; Save runs reload_plugins."""
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(14)

        outer.addWidget(
            self._page_header(
                "Extensions & Plugin Studio",
                "Extend Alyssa with custom Python tools, automated background watchers, and custom UI panels.",
                "EXTENSIONS",
            )
        )

        intro = self._help_label(
            "Plugins in plugins/*.py load dynamically at startup. Prefix with an underscore (_) to disable. "
            "A plugin can also export GUI_PANEL_TITLE + build_gui_panel to add its own Settings tab."
        )
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        outer.addWidget(intro)

        splitter = QSplitter(Qt.Horizontal)
        self._plugin_splitter = splitter
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(10)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(8)

        self.plugin_list = QListWidget()
        self.plugin_list.setObjectName("pluginList")
        _apply_elevation(self.plugin_list, blur=20, y=3, alpha=80)
        self.plugin_list.currentItemChanged.connect(self._on_plugin_selected)
        left_layout.addWidget(self.plugin_list, 1)

        new_plugin_btn = QPushButton("+ New Plugin")
        new_plugin_btn.setObjectName("secondaryButton")
        new_plugin_btn.clicked.connect(self._on_new_plugin)
        left_layout.addWidget(new_plugin_btn)

        reload_btn = QPushButton("⟳ Reload Plugins")
        reload_btn.setToolTip("Reload plugin changes now.")
        reload_btn.clicked.connect(self._on_reload_plugins)
        left_layout.addWidget(reload_btn)

        splitter.addWidget(left)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(10, 0, 0, 0)
        right_layout.setSpacing(8)

        header_widget = QWidget()
        header_row = QGridLayout(header_widget)
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setHorizontalSpacing(8)
        header_row.setVerticalSpacing(8)
        self._plugin_header_grid = header_row
        self.plugin_file_label = QLabel("Select a plugin to edit")
        self.plugin_file_label.setObjectName("pluginFileLabel")
        header_row.addWidget(self.plugin_file_label, 0, 0)
        header_row.setColumnStretch(0, 1)

        self.plugin_enable_btn = QPushButton("Enable")
        self.plugin_enable_btn.setObjectName("compactButton")
        self.plugin_enable_btn.setCheckable(True)
        self.plugin_enable_btn.clicked.connect(self._on_toggle_plugin_enabled)
        header_row.addWidget(self.plugin_enable_btn, 0, 1)

        self.plugin_save_btn = QPushButton("💾 Save")
        self.plugin_save_btn.setObjectName("primaryButton")
        self.plugin_save_btn.clicked.connect(self._on_save_plugin)
        header_row.addWidget(self.plugin_save_btn, 0, 2)

        self.plugin_delete_btn = QPushButton("Delete")
        self.plugin_delete_btn.setObjectName("pluginDangerButton")
        self.plugin_delete_btn.clicked.connect(self._on_delete_plugin)
        header_row.addWidget(self.plugin_delete_btn, 0, 3)
        right_layout.addWidget(header_widget)

        self.plugin_permission_label = self._help_label("")
        self.plugin_permission_label.setWordWrap(True)
        right_layout.addWidget(self.plugin_permission_label)

        self.plugin_approve_btn = QPushButton("Approve and load")
        self.plugin_approve_btn.setObjectName("primaryButton")
        self.plugin_approve_btn.clicked.connect(self._on_approve_plugin)
        self.plugin_approve_btn.hide()
        right_layout.addWidget(self.plugin_approve_btn)

        self.plugin_status_label = self._help_label("")
        self.plugin_status_label.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11px;")
        right_layout.addWidget(self.plugin_status_label)

        self.plugin_editor = QPlainTextEdit()
        self.plugin_editor.setObjectName("pluginEditor")
        self.plugin_editor.setLineWrapMode(QPlainTextEdit.NoWrap)
        mono = QFontDatabase.systemFont(QFontDatabase.FixedFont)
        mono.setPointSize(11)
        self.plugin_editor.setFont(mono)
        self.plugin_editor.textChanged.connect(self._on_plugin_text_changed)
        right_layout.addWidget(self.plugin_editor, 1)

        splitter.addWidget(right)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([190, 500])
        outer.addWidget(splitter, 1)

        self._current_plugin_file = None
        self._plugin_dirty = False
        self._refresh_plugin_list()
        self._set_plugin_editor_enabled(False)
        return w

    def _refresh_plugin_list(self, select: str = None):
        """Rebuild plugin_list from plugins/*.py. `select` re-selects that filename after create/rename."""
        self.plugin_list.blockSignals(True)
        self.plugin_list.clear()
        plugins_dir = plugin_loader.PLUGINS_DIR
        if os.path.isdir(plugins_dir):
            for filename in sorted(os.listdir(plugins_dir)):
                if not filename.endswith(".py"):
                    continue
                enabled = not filename.startswith("_")
                display_name = filename[1:] if not enabled else filename
                pending = enabled and plugin_permissions.needs_approval(
                    filename, os.path.join(plugins_dir, filename)
                ) is not None
                dot = "🟡" if pending else "🟢" if enabled else "⚪"
                item = QListWidgetItem(f"{dot}  {display_name}")
                item.setData(Qt.UserRole, filename)
                item.setToolTip(
                    "Waiting for your approval" if pending else "Loaded at startup" if enabled else "Not loaded"
                )
                self.plugin_list.addItem(item)
                if select and filename == select:
                    self.plugin_list.setCurrentItem(item)
        self.plugin_list.blockSignals(False)
        if select is None and self.plugin_list.count() and self.plugin_list.currentRow() < 0:
            self.plugin_list.setCurrentRow(0)
        elif self.plugin_list.count() == 0:
            self._current_plugin_file = None
            self._set_plugin_editor_enabled(False)

    def _set_plugin_editor_enabled(self, enabled: bool):
        self.plugin_editor.setEnabled(enabled)
        self.plugin_enable_btn.setEnabled(enabled)
        self.plugin_save_btn.setEnabled(enabled)
        self.plugin_delete_btn.setEnabled(enabled)
        if not enabled:
            self.plugin_editor.blockSignals(True)
            self.plugin_editor.setPlainText("")
            self.plugin_editor.blockSignals(False)
            self.plugin_file_label.setText(
                "No plugins yet - click \"+ New Plugin\" to create one"
                if self.plugin_list.count() == 0 else "Select a plugin to edit"
            )
            self.plugin_status_label.setText("")
            self.plugin_permission_label.setText("")
            self.plugin_approve_btn.hide()

    def _on_plugin_selected(self, current: QListWidgetItem, previous: QListWidgetItem):
        if previous is not None and self._plugin_dirty:
            self._save_plugin_file(previous.data(Qt.UserRole), reload_after=False, quiet=True)
        if current is None:
            self._current_plugin_file = None
            self._set_plugin_editor_enabled(False)
            return

        filename = current.data(Qt.UserRole)
        self._current_plugin_file = filename
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except OSError as e:
            content = f"# Couldn't read this file: {e}"

        self._set_plugin_editor_enabled(True)
        self.plugin_editor.blockSignals(True)
        self.plugin_editor.setPlainText(content)
        self.plugin_editor.blockSignals(False)
        self._plugin_dirty = False

        enabled = not filename.startswith("_")
        self.plugin_enable_btn.setChecked(enabled)
        self.plugin_enable_btn.setText("Enabled ✓" if enabled else "Disabled")
        self.plugin_file_label.setText(filename[1:] if not enabled else filename)
        self.plugin_status_label.setText("")
        self._show_plugin_permissions(filename, path)

    def _show_plugin_permissions(self, filename: str, path: str):
        """What the plugin says it needs, plus an Approve button while it's waiting."""
        if plugin_permissions.is_builtin(filename):
            self.plugin_permission_label.setText("Built-in plugin - ships with Alyssa.")
            self.plugin_approve_btn.hide()
            return
        wanted = plugin_permissions.declared(path) or [plugin_permissions.UNDECLARED]
        pending = plugin_permissions.needs_approval(filename, path) is not None
        heading = "⚠ Not loaded until you approve. It asks to:" if pending else "✓ Approved. It can:"
        self.plugin_permission_label.setText(f"{heading}\n{plugin_permissions.describe(wanted)}")
        self.plugin_approve_btn.setVisible(pending)

    def _on_approve_plugin(self):
        filename = self._current_plugin_file
        if not filename:
            return
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        try:
            plugin_permissions.approve(filename, path)
        except OSError as e:
            self.plugin_status_label.setText(f"⚠ Couldn't save the approval: {e}")
            return
        self._reload_plugins_backend()
        self._refresh_plugin_list(select=filename)
        self._show_plugin_permissions(filename, path)
        self.plugin_status_label.setText(f"✓ Approved and loaded {filename.lstrip('_')}")

    def _on_plugin_text_changed(self):
        self._plugin_dirty = True
        if self._current_plugin_file:
            name = self._current_plugin_file
            name = name[1:] if name.startswith("_") else name
            self.plugin_file_label.setText(f"{name} •")

    def _on_toggle_plugin_enabled(self, checked: bool):
        if not self._current_plugin_file:
            return
        old_name = self._current_plugin_file
        bare_name = old_name[1:] if old_name.startswith("_") else old_name
        new_name = bare_name if checked else f"_{bare_name}"
        if new_name == old_name:
            return

        plugins_dir = plugin_loader.PLUGINS_DIR
        old_path = os.path.join(plugins_dir, old_name)
        new_path = os.path.join(plugins_dir, new_name)
        try:
            # Flush unsaved edits before rename so enable/disable never discards them.
            if self._plugin_dirty:
                with open(old_path, "w", encoding="utf-8") as f:
                    f.write(self.plugin_editor.toPlainText())
                self._plugin_dirty = False
            os.rename(old_path, new_path)
        except OSError as e:
            self.plugin_status_label.setText(f"⚠ Couldn't rename file: {e}")
            self.plugin_enable_btn.setChecked(not checked)
            return

        self._current_plugin_file = new_name
        self.plugin_enable_btn.setText("Enabled ✓" if checked else "Disabled")
        self._reload_plugins_backend()
        self._refresh_plugin_list(select=new_name)
        self.plugin_status_label.setText(
            f"✓ {bare_name} enabled and reloaded" if checked else f"{bare_name} disabled"
        )

    def _save_plugin_file(self, filename: str, reload_after: bool = True, quiet: bool = False) -> bool:
        if not filename:
            return False
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.plugin_editor.toPlainText())
        except OSError as e:
            if not quiet:
                self.plugin_status_label.setText(f"⚠ Couldn't save: {e}")
            return False
        self._plugin_dirty = False
        bare_name = filename[1:] if filename.startswith("_") else filename
        self.plugin_file_label.setText(bare_name)
        self._show_plugin_permissions(filename, path)
        if reload_after:
            self._reload_plugins_backend()
            if not quiet:
                self.plugin_status_label.setText(f"✓ Saved and reloaded {bare_name}")
        return True

    def _on_save_plugin(self):
        filename = self._current_plugin_file
        if filename:
            try:  # pressing Save on code you edited here counts as approving it
                plugin_permissions.approve(filename, os.path.join(plugin_loader.PLUGINS_DIR, filename))
            except OSError:
                pass
            self._save_plugin_file(filename)

    def _reload_plugins_backend(self):
        """Reload plugins into this session (actions.FUNCTIONS/PLUGIN_TOOLS, brain.TOOLS) and rebuild plugin GUI tabs."""
        try:
            actions.reload_plugins()
            brain.reload_plugin_tools()
        except Exception as e:
            self.plugin_status_label.setText(f"⚠ Saved, but reload hit an error: {e}")
        self._rebuild_plugin_gui_tabs()

    def _on_reload_plugins(self):
        if self._current_plugin_file and self._plugin_dirty:
            self._save_plugin_file(self._current_plugin_file, reload_after=False, quiet=True)
        self._reload_plugins_backend()
        selected = self._current_plugin_file
        self._refresh_plugin_list(select=selected)
        errors = plugin_loader.get_load_errors()
        count = len(actions.PLUGIN_FUNCTIONS)
        if errors:
            self.plugin_status_label.setText(f"⚠ Reloaded - {count} loaded, but: {'; '.join(errors)}")
        else:
            self.plugin_status_label.setText(f"✓ Reloaded - {count} plugin function(s) active")

    def _on_new_plugin(self):
        name, ok = QInputDialog.getText(
            self, "New Plugin", "Plugin name (letters, numbers, underscores):"
        )
        if not ok or not name.strip():
            return
        safe = re.sub(r"[^a-zA-Z0-9_]", "_", name.strip().lower())
        if not safe:
            return
        filename = f"{safe}.py"
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        if os.path.exists(path):
            QMessageBox.warning(self, "Already exists", f"'{filename}' already exists in plugins/.")
            return

        template = f'''"""
{safe} plugin - describe what this adds here.
"""

# What this plugin needs, shown to anyone who installs it. Pick from:
# network, files, keyboard_mouse, screen, microphone, camera, shell, background
PERMISSIONS = []

def {safe}_example(message: str) -> str:
    """Replace this with your own function. Whatever it returns is what
    gets spoken/shown back to the user."""
    return f"{{message}}, from the {safe} plugin!"


FUNCTIONS = {{
    "{safe}_example": {safe}_example,
}}

TOOLS = [
    {{
        "type": "function",
        "function": {{
            "name": "{safe}_example",
            "description": "Explain here when the assistant should call this.",
            "parameters": {{
                "type": "object",
                "properties": {{
                    "message": {{"type": "string", "description": "What to say."}}
                }},
                "required": ["message"],
            }},
        }},
    }},
]


# Optional: give this plugin its own tab in Settings, right alongside
# Assistant/Engine/Audio/etc. Uncomment both lines below and fill in
# build_gui_panel to add one - it can be any PySide6 QWidget (buttons,
# lists, a form, a whole mini-app). `parent` is the Settings dialog
# itself, in case you need its theme via parent._t() or want to read a
# config value it exposes.
#
# GUI_PANEL_TITLE = "{safe.title()}"
#
# def build_gui_panel(parent):
#     from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton
#     w = QWidget()
#     layout = QVBoxLayout(w)
#     layout.addWidget(QLabel("Hello from the {safe} plugin's own tab!"))
#     btn = QPushButton("Click me")
#     btn.clicked.connect(lambda: QLabel("Clicked!"))
#     layout.addWidget(btn)
#     layout.addStretch(1)
#     return w
'''
        try:
            os.makedirs(plugin_loader.PLUGINS_DIR, exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(template)
            plugin_permissions.approve(filename, path)
        except OSError as e:
            QMessageBox.critical(self, "Couldn't create plugin", str(e))
            return

        self._reload_plugins_backend()
        self._refresh_plugin_list(select=filename)
        self.plugin_status_label.setText(f"✓ Created {filename} - edit it, then Save")

    def _on_delete_plugin(self):
        if not self._current_plugin_file:
            return
        filename = self._current_plugin_file
        bare_name = filename[1:] if filename.startswith("_") else filename
        confirm = QMessageBox.question(
            self, "Delete plugin",
            f"Permanently delete '{bare_name}'? This can't be undone.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        path = os.path.join(plugin_loader.PLUGINS_DIR, filename)
        try:
            os.remove(path)
        except OSError as e:
            QMessageBox.critical(self, "Couldn't delete", str(e))
            return
        self._current_plugin_file = None
        self._plugin_dirty = False
        self._reload_plugins_backend()
        self._refresh_plugin_list()
        self.plugin_status_label.setText(f"Deleted {bare_name}")

