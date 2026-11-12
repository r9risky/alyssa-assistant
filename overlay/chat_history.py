"""Chat history: scroll back through what you said and what she answered (with her sources).

Reads the same in-memory log the phone remote shows (phone_remote.messages_after),
so there is one record of the conversation, not two.
"""
import html

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QDialog, QTextBrowser, QVBoxLayout

import config
import phone_remote

from .theming import _theme


def render_html(messages: list, theme: dict) -> str:
    """The conversation as chat bubbles, newest at the bottom."""
    if not messages:
        return (
            f'<p style="color:{theme["subtext"]};">Nothing yet. Say hi, and your chat '
            "shows up here.</p>"
        )
    rows = []
    for m in messages:
        mine = m.get("who") == "you"
        who = "You" if mine else html.escape(config.ASSISTANT_NAME)
        bg = theme["accent"] if mine else theme["card"]
        fg = "#ffffff" if mine else theme["text"]
        align = "right" if mine else "left"
        text = html.escape(m.get("text", "")).replace("\n", "<br>")
        links = " · ".join(
            f'<a href="{html.escape(s["url"], quote=True)}" style="color:{fg};">{html.escape(s["site"])}</a>'
            for s in m.get("sources") or []
        )
        if links:
            text += f'<br><span style="font-size:8pt;">Sources: {links}</span>'
        rows.append(
            f'<table width="100%" cellspacing="0" cellpadding="0"><tr><td align="{align}">'
            f'<div style="color:{theme["subtext"]}; font-size:8pt;">{who} · {html.escape(m.get("time", ""))}</div>'
            f'<table cellpadding="8" style="background:{bg}; border-radius:10px;"><tr>'
            f'<td style="color:{fg};">{text}</td></tr></table></td></tr></table><br>'
        )
    return "".join(rows)


class ChatHistoryDialog(QDialog):
    def __init__(self, companion):
        super().__init__(None, Qt.Tool | Qt.WindowStaysOnTopHint)
        self.companion = companion
        self.setWindowTitle(f"Chat with {config.ASSISTANT_NAME}")
        self.resize(380, 520)
        self.view = QTextBrowser(self)
        self.view.setOpenExternalLinks(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.addWidget(self.view)
        self._last_id = None
        self._timer = QTimer(self)  # picks up new lines while it's open
        self._timer.timeout.connect(self.refresh)
        self.refresh()

    def refresh(self):
        messages = phone_remote.messages_after(0)
        last_id = messages[-1]["id"] if messages else 0
        last_sources = bool(messages and messages[-1].get("sources"))
        if (last_id, last_sources) == self._last_id:
            return
        self._last_id = (last_id, last_sources)
        theme = _theme(self.companion.settings)
        self.setStyleSheet(
            f"QDialog {{ background: {theme['bg']}; }}"
            f"QTextBrowser {{ background: {theme['panel']}; border: 1px solid {theme['border']};"
            f" border-radius: 10px; padding: 6px; }}"
        )
        self.view.setHtml(render_html(messages, theme))
        bar = self.view.verticalScrollBar()
        bar.setValue(bar.maximum())

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()
        self._timer.start(1000)

    def hideEvent(self, event):
        self._timer.stop()
        super().hideEvent(event)


def open_for(companion):
    """Show (or raise) the companion's chat history window."""
    dialog = getattr(companion, "_chat_history", None)
    if dialog is None:
        dialog = companion._chat_history = ChatHistoryDialog(companion)
    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    return dialog
