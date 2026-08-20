import threading

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QMessageBox,
)

import actions
import updater

from ..rendering import _BASE_DIR


class UpdatesTabMixin:
    def _build_updates_tab(self) -> QWidget:
        w = QWidget()
        w.setObjectName("tabPage")
        outer = QVBoxLayout(w)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        outer.addWidget(self._section_header("Application Updates", "↻", first=True))
        intro = self._help_label(
            "Install the latest Alyssa release. Your settings and personal data stay "
            "unchanged while application files are replaced with the latest versions."
        )
        intro.setVisible(True)
        outer.addWidget(intro)

        self.current_version_label = self._help_label(
            f"Current Version: {updater.current_version(_BASE_DIR)}"
        )
        self.current_version_label.setVisible(True)
        outer.addWidget(self.current_version_label)

        self.check_update_btn = QPushButton("Check Update")
        self.check_update_btn.setObjectName("primaryButton")
        self.check_update_btn.setToolTip("Check GitHub for a newer release.")
        self.check_update_btn.clicked.connect(self._on_check_update)

        self.restart_alyssa_btn = QPushButton("Restart Alyssa")
        self.restart_alyssa_btn.setToolTip("Close and reopen Alyssa.")
        self.restart_alyssa_btn.clicked.connect(self._on_restart_alyssa)

        button_row = QHBoxLayout()
        button_row.addWidget(self.check_update_btn)
        button_row.addWidget(self.restart_alyssa_btn)
        button_row.addStretch(1)
        outer.addLayout(button_row)

        self.update_status_label = self._help_label("")
        outer.addWidget(self.update_status_label)
        outer.addStretch(1)
        return w

    def _on_check_update(self):
        self._flush_pending_applies()
        self.check_update_btn.setEnabled(False)
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['subtext']}; font-size: 11px;"
        )
        self.update_status_label.setText("Checking GitHub for the latest release…")

        def _worker():
            try:
                self._update_checked_signal.emit(True, updater.check_latest(_BASE_DIR))
            except Exception as error:
                self._update_checked_signal.emit(False, str(error))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_restart_alyssa(self):
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
                f"color: {self._t()['danger']}; font-size: 11px;"
            )
            self.update_status_label.setText(f"Update failed: {detail}")
            QMessageBox.warning(self, "Update Failed", detail)
            return

        current = detail["current_version"]
        latest = detail["latest_version"]
        self.current_version_label.setText(f"Current Version: {current} | Latest Version: {latest}")
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['success']}; font-size: 11px;"
        )
        if not detail["update_available"]:
            self.update_status_label.setText(
                f"You are on the latest version ({current}). No update needed."
            )
            return

        self.update_status_label.setText(
            f"Update available: {current} → {latest}. Review the changes to continue."
        )
        prompt = QMessageBox(self)
        prompt.setIcon(QMessageBox.Information)
        prompt.setWindowTitle(f"Alyssa {latest} Update")
        prompt.setText(f"Current Version: {current} | Latest Version: {latest}")
        prompt.setInformativeText("Changes in this release:\n\n" + detail["notes"])
        prompt.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
        prompt.setDefaultButton(QMessageBox.No)
        prompt.button(QMessageBox.Yes).setText("Download and Install")
        if prompt.exec() != QMessageBox.Yes:
            self.update_status_label.setText("Update cancelled. No files were changed.")
            return

        self.check_update_btn.setEnabled(False)
        self.update_status_label.setText(f"Downloading and installing {latest}…")

        def _worker():
            try:
                self._update_installed_signal.emit(True, updater.install_release(_BASE_DIR, detail))
            except Exception as error:
                self._update_installed_signal.emit(False, str(error))

        threading.Thread(target=_worker, daemon=True).start()

    def _on_update_installed(self, ok: bool, detail: str):
        self.check_update_btn.setEnabled(True)
        if not ok:
            self.update_status_label.setStyleSheet(
                f"color: {self._t()['danger']}; font-size: 11px;"
            )
            self.update_status_label.setText(f"Update failed: {detail}")
            QMessageBox.warning(self, "Update Failed", detail)
            return

        self.current_version_label.setText(f"Current Version: {detail} | Latest Version: {detail}")
        self.update_status_label.setStyleSheet(
            f"color: {self._t()['success']}; font-size: 11px;"
        )
        message = f"Alyssa {detail} was installed. Restart Alyssa to use the update."
        self.update_status_label.setText(message)
        QMessageBox.information(self, "Update Installed", message)

    # -- Plugins tab ----------------------------------------------------

