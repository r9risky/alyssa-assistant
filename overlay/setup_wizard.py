"""First-run setup wizard: pick her brain, check the mic, pick her voice.

Shown instead of the old "add a Gemini key" message box when she starts with
no brain configured. Everything it sets is also in Settings, so "Later" is
always safe.
"""
import os
import threading

import requests
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QLabel, QLineEdit, QProgressBar, QPushButton,
    QRadioButton, QVBoxLayout, QWizard, QWizardPage,
)

import config
import credential_store

from .credential_checks import _atomic_write_text, _patch_config_line, _verify_gemini_key
from .rendering import _BASE_DIR
from .theming import _theme

try:
    import sounddevice as sd
except Exception:  # no PortAudio: the mic page just lists Default
    sd = None

GEMINI_KEY_URL = "https://aistudio.google.com/apikey"
OLLAMA_URL = "http://localhost:11434/api/tags"


def save_setup(choices: dict, config_path: str | None = None) -> list[str]:
    """Persist wizard choices. Returns a list of problems (empty on success).

    choices: provider ("gemini"/"ollama"), gemini_key, tts ("edge"/"kokoro"), microphone.
    Non-secret values go into config.py; the Gemini key goes to the OS keyring.
    """
    problems = []
    updates = {
        "LLM_PROVIDER": choices["provider"],
        "TTS_PROVIDER": choices["tts"],
        "MICROPHONE_DEVICE": choices.get("microphone") or "default",
    }
    path = config_path or os.path.join(_BASE_DIR, "config.py")
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        for key, value in updates.items():
            text = _patch_config_line(text, key, repr(value))
        _atomic_write_text(path, text)
    except OSError as e:
        problems.append(f"Couldn't save to config.py: {e}")
    for key, value in updates.items():
        setattr(config, key, value)  # live: the waiting preflight loop picks it up

    key = (choices.get("gemini_key") or "").strip()
    if choices["provider"] == "gemini" and key:
        config.GEMINI_API_KEY = key
        if not credential_store.set_secret("GEMINI_API_KEY", key):
            problems.append(
                "Couldn't save the Gemini key to Windows Credential Manager, so it "
                "works for now but you'll need to add it again after a restart."
            )
    return problems


def _ollama_running() -> bool:
    try:
        return requests.get(OLLAMA_URL, timeout=1.5).ok
    except requests.RequestException:
        return False


def _heading(layout, title: str, subtitle: str):
    """Title + subtitle inside the page; QWizard's own header ignores the dark theme."""
    head = QLabel(f"<b style='font-size:17px'>{title}</b><br><span style='opacity:0.75'>{subtitle}</span>")
    head.setWordWrap(True)
    layout.addWidget(head)
    layout.addSpacing(6)


def _note(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setOpenExternalLinks(True)
    return label


class _BrainPage(QWizardPage):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        _heading(layout, "Give her a brain", "She needs a language model to think. Both of these are free.")

        self.gemini = QRadioButton("Gemini (free key from Google, works on any PC)")
        self.ollama = QRadioButton("Ollama (runs on your PC, no key; needs a decent GPU)")
        self.group = QButtonGroup(self)
        for button in (self.gemini, self.ollama):
            self.group.addButton(button)
            layout.addWidget(button)
        self.gemini.setChecked(True)

        layout.addWidget(_note(
            f'1. Get a free key at <a style="color:#8ab4ff" href="{GEMINI_KEY_URL}">aistudio.google.com/apikey</a>'
            "<br>2. Paste it here and press Check."
        ))
        self.key = QLineEdit()
        self.key.setPlaceholderText("Paste your Gemini API key")
        self.key.setEchoMode(QLineEdit.Password)
        self.key.textChanged.connect(self._key_edited)
        layout.addWidget(self.key)
        self.check = QPushButton("Check key")
        self.check.clicked.connect(self._check_key)
        layout.addWidget(self.check)
        self.status = _note("")
        layout.addWidget(self.status)
        layout.addStretch(1)

        self.key_ok = False
        self.gemini.toggled.connect(self._provider_changed)

    def _provider_changed(self, gemini: bool):
        for widget in (self.key, self.check):
            widget.setEnabled(gemini)
        if gemini:
            self.status.setText("")
        else:
            self.status.setText(
                "Ollama is running. ✓" if _ollama_running() else
                'Ollama isn\'t running yet. Install it from <a style="color:#8ab4ff" href="https://ollama.com">ollama.com</a>, '
                f"then run <code>ollama pull {config.OLLAMA_MODEL}</code>. She'll tell you if anything's missing."
            )
        self.completeChanged.emit()

    def _key_edited(self):
        self.key_ok = False
        self.status.setText("")
        self.completeChanged.emit()

    def _check_key(self):
        key = self.key.text().strip()
        if not key:
            return
        self.check.setEnabled(False)
        self.status.setText("Checking…")

        def work():
            ok, message, _models = _verify_gemini_key(key)
            from .widgets import run_on_gui_thread
            run_on_gui_thread(lambda: self._key_checked(key, ok, message))

        threading.Thread(target=work, daemon=True).start()

    def _key_checked(self, key: str, ok: bool, message: str):
        self.check.setEnabled(True)
        if key != self.key.text().strip():
            return  # edited while checking
        self.key_ok = ok
        self.status.setText("Key works. ✓" if ok else f"That didn't work: {message}")
        self.completeChanged.emit()

    def isComplete(self) -> bool:
        return self.ollama.isChecked() or self.key_ok


class _MicPage(QWizardPage):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        _heading(layout, "Check your microphone", "Say something. The bar should move when you talk.")
        self.combo = QComboBox()
        self.combo.addItem("Default", "default")
        seen = set()
        try:
            devices = sd.query_devices() if sd is not None else []
        except Exception:
            devices = []
        for device in devices:
            name = device.get("name", "")
            if device.get("max_input_channels", 0) > 0 and name not in seen:
                seen.add(name)
                self.combo.addItem(name, name)
        self.combo.currentIndexChanged.connect(self._restart_meter)
        layout.addWidget(self.combo)
        self.meter = QProgressBar()
        self.meter.setRange(0, 100)
        self.meter.setTextVisible(False)
        layout.addWidget(self.meter)
        self.status = _note("")
        layout.addWidget(self.status)
        layout.addStretch(1)

        self._level = 0.0
        self._stream = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.meter.setValue(int(min(1.0, self._level * 8) * 100)))

    def initializePage(self):
        self._restart_meter()
        self._timer.start(50)

    def cleanupPage(self):
        self.stop_meter()

    def _restart_meter(self):
        self.stop_meter()
        if sd is None:
            self.status.setText("Couldn't open the audio system, but you can still continue.")
            return
        device = self.combo.currentData()

        def callback(indata, _frames, _time, _status):
            self._level = float((indata ** 2).mean() ** 0.5)

        try:
            self._stream = sd.InputStream(
                device=None if device == "default" else device,
                channels=1, samplerate=16000, callback=callback,
            )
            self._stream.start()
            self.status.setText("")
        except Exception as e:
            self._stream = None
            self.status.setText(f"Couldn't open that microphone ({e}). Try another one.")

    def stop_meter(self):
        self._timer.stop()
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._level = 0.0


class _VoicePage(QWizardPage):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        _heading(layout, "Pick her voice", "You can change this any time in Settings.")
        self.edge = QRadioButton("Nanami (Microsoft Edge voice; free, needs internet)")
        self.kokoro = QRadioButton("Kokoro (free and offline; downloads ~120 MB once)")
        group = QButtonGroup(self)
        for button in (self.edge, self.kokoro):
            group.addButton(button)
            layout.addWidget(button)
        (self.kokoro if getattr(config, "TTS_PROVIDER", "edge") == "kokoro" else self.edge).setChecked(True)
        layout.addStretch(1)


class SetupWizard(QWizard):
    def __init__(self, parent, settings: dict):
        super().__init__(parent)
        self.setWindowTitle(f"Set up {config.ASSISTANT_NAME}")
        self.setWizardStyle(QWizard.ClassicStyle)
        self.setButtonText(QWizard.CancelButton, "Later")
        self.setButtonText(QWizard.FinishButton, "Start")
        t = _theme(settings)
        self.setStyleSheet(
            f"QWizard, QWizardPage {{ background: {t['bg']}; }}"
            f"QLabel, QRadioButton {{ color: {t['text']}; font-size: 13px; }}"
            f"QRadioButton::indicator {{ width: 12px; height: 12px; border-radius: 8px;"
            f"  border: 2px solid {t['text']}; background: transparent; }}"
            f"QRadioButton::indicator:checked {{ background: {t['accent']}; border-color: {t['accent']}; }}"
            f"QProgressBar::chunk {{ background: {t['accent']}; }}"
        )
        self.setMinimumSize(520, 380)
        self.brain = _BrainPage()
        self.mic = _MicPage()
        self.voice = _VoicePage()
        for page in (self.brain, self.mic, self.voice):
            self.addPage(page)
        self.problems: list[str] = []

    def choices(self) -> dict:
        return {
            "provider": "gemini" if self.brain.gemini.isChecked() else "ollama",
            "gemini_key": self.brain.key.text(),
            "tts": "kokoro" if self.voice.kokoro.isChecked() else "edge",
            "microphone": self.mic.combo.currentData(),
        }

    def done(self, result):
        self.mic.stop_meter()
        if result == QWizard.Accepted:
            self.problems = save_setup(self.choices())
        super().done(result)
