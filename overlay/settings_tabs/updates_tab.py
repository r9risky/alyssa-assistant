import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox, QLabel,
    QSizePolicy,
)

import actions
import updater

from ..rendering import _BASE_DIR


class UpdatesTabMixin:
    def _build_updates_tab(self) -> QWidget:
        self._update_installing = False
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(18, 16, 18, 18)
        outer.setSpacing(14)

        cur_ver = updater.current_version(_BASE_DIR)
        outer.addWidget(
            self._page_header(
                "Software Updates & Lifecycle",
                "Keep Alyssa up to date from GitHub releases while preserving all your personal settings and memories.",
                cur_ver,
            )
        )

        card, form = self._create_card(
            "Release Channel & Version Control",
            "Check for new releases or restart Alyssa cleanly after installing plugins or system changes.",
            "↻",
        )

        self.current_version_label = QLabel(f"{cur_ver}  ·  latest release")
        self.current_version_label.setWordWrap(False)
        self.current_version_label.setStyleSheet(
            f"font-size: 13px; font-weight: 700; color: {self._t()['accent']};"
        )
        self._add_row(form, "Installed build:", self.current_version_label)

        intro = QLabel(
            "Updating replaces core application files with the latest release while keeping "
            "your API keys, memory.json, personality notes, and overlay preferences intact."
        )
        intro.setWordWrap(True)
        intro.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        intro.setStyleSheet(f"color: {self._t()['subtext']}; font-size: 11.5px;")
        self._add_row(form, "What's preserved:", intro)

        self.check_update_btn = QPushButton("Check Update")
        self.check_update_btn.setObjectName("primaryButton")
        self.check_update_btn.setToolTip("Check GitHub for a newer release.")
        self.check_update_btn.clicked.connect(self._on_check_update)

        self.restart_alyssa_btn = QPushButton("Restart Alyssa")
        self.restart_alyssa_btn.setObjectName("secondaryButton")
        self.restart_alyssa_btn.setToolTip("Close and reopen Alyssa.")
        self.restart_alyssa_btn.clicked.connect(self._on_restart_alyssa)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        button_row.addWidget(self.check_update_btn)
        button_row.addWidget(self.restart_alyssa_btn)
        button_row.addStretch(1)

        self.update_status_label = self._help_label("")
        self._add_row(
            form,
            "Actions:",
            self._stack_under(button_row, self.update_status_label),
        )
        outer.addWidget(card)
        outer.addStretch(1)
        return w

    def _set_update_installing(self, installing: bool) -> None:
        self._update_installing = installing
        self.check_update_btn.setEnabled(not installing)
        self.restart_alyssa_btn.setEnabled(not installing)
        self.restart_alyssa_btn.setToolTip(
            "Update in progress - do not close or restart Alyssa."
            if installing
            else "Close and reopen Alyssa."
        )

    def _on_check_update(self):
        self._flush_pending_applies()
        self.check_update_btn.setEnabled(False)
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['subtext']}; font-size: 11.5px;"
        )
        self.update_status_label.setText("Checking GitHub for the latest release…")

        def _worker():
            try:
                self._update_checked_signal.emit(True, updater.check_latest(_BASE_DIR))
            except Exception as error:
                self._update_checked_signal.emit(False, str(error))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_restart_alyssa(self):
        if getattr(self, "_update_installing", False):
            self.update_status_label.setText(
                "Update in progress - do not close or restart Alyssa."
            )
            return
        self._flush_pending_applies()
        self.update_status_label.setText("Restarting Alyssa…")
        try:
            actions.relaunch_alyssa()
        except OSError as error:
            self.update_status_label.setText(f"Restart failed: {error}")
            QMessageBox.warning(self, "Restart Failed", str(error))

    def _on_update_checked(self, ok: bool, detail):
        self.check_update_btn.setEnabled(True)
        if not ok:
            self.update_status_label.setStyleSheet(
                f"color: {self._t()['danger']}; font-size: 11.5px;"
            )
            self.update_status_label.setText(f"Update check failed: {detail}")
            return

        release = detail or {}
        latest = str(release.get("latest_version") or "").strip() or "unknown"
        current = str(release.get("current_version") or updater.CURRENT_VERSION).strip()
        if not release.get("update_available"):
            self.update_status_label.setStyleSheet(
                f"color: {self._t()['success']}; font-size: 11.5px;"
            )
            self.update_status_label.setText(
                f"You are on the latest version ({current}). No update needed."
            )
            return

        self.update_status_label.setStyleSheet(
            f"color: {self._t()['warning']}; font-size: 11.5px;"
        )
        self.update_status_label.setText(
            f"Update available: {latest} (current: {current})."
        )

        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Information)
        prompt.setWindowTitle(f"Alyssa {latest} Update")
        prompt.setText(f"Current Version: {current} | Latest Version: {latest}")
        try:
            rendered_notes = updater.render_release_notes_html(detail.get("notes", ""))
        except Exception:
            prompt.setTextFormat(Qt.PlainText)
            informative_text = "Changes in this release:\n\n" + str(detail.get("notes", ""))
        else:
            prompt.setTextFormat(Qt.RichText)
            informative_text = (
                "<p><b>Changes in this release:</b></p>" + rendered_notes
            )
        prompt.setInformativeText(informative_text)
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.No)
        if prompt.button(QMessageBox.Yes) is not None:
            prompt.button(QMessageBox.Yes).setText("Download and Install")
        if prompt.exec() != QMessageBox.Yes:
            self.update_status_label.setStyleSheet(
                f"color: {self._t()['subtext']}; font-size: 11.5px;"
            )
            self.update_status_label.setText("Update cancelled. No files were changed.")
            return

        self._start_update_install(release)

    def _start_update_install(self, release: dict):
        self._set_update_installing(True)
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['subtext']}; font-size: 11.5px;"
        )
        self.update_status_label.setText(
            "Installing update… Please wait and do not close Alyssa."
        )

        def _worker():
            try:
                version = updater.install_release(_BASE_DIR, release)
                self._update_installed_signal.emit(True, version)
            except Exception as error:
                self._update_installed_signal.emit(False, str(error))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_installed(self, ok: bool, detail: str):
        self._set_update_installing(False)
        if not ok:
            self.update_status_label.setStyleSheet(
                f"color: {self._t()['danger']}; font-size: 11.5px;"
            )
            self.update_status_label.setText(f"Update failed: {detail}")
            QMessageBox.warning(self, "Update Failed", f"Couldn't install the update:\n{detail}")
            return

        self.current_version_label.setText(
            f"Current Version: {updater.current_version(_BASE_DIR)}"
        )
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['success']}; font-size: 11.5px;"
        )
        self.update_status_label.setText(
            f"Updated to {detail}. Click Restart Alyssa to run the new version."
        )
