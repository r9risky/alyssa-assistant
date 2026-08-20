import math
import sys
import threading

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QPainterPath, QLinearGradient, QIcon,
)
from PySide6.QtWidgets import (
    QApplication, QWidget, QMenu, QSystemTrayIcon,
)

_QW = QWidget

import config

from .rendering import load_overlay_settings
from .theming import _THEME_LIGHT, _build_menu_style, _theme
from .widgets import Bridge, ChatInputBar, CompanionWindow, SpeechBubble

def _build_app_icon(size: int = 64, theme: dict = None) -> "QIcon":
    """A small on-brand app/tray icon, painted to match the current color
    theme's header gradient (Settings header, primary buttons) instead of
    a generic system icon - a soft circular badge with a simple sparkle,
    so Alyssa is recognizable at a glance in the taskbar/tray without
    needing a bundled image asset. Defaults to the light preset when no
    theme is given, same as before this became theme-aware."""
    t = theme or _THEME_LIGHT
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    painter = QPainter(pm)
    painter.setRenderHint(QPainter.Antialiasing)

    margin = size * 0.04
    circle_rect = QRect(int(margin), int(margin), int(size - 2 * margin), int(size - 2 * margin))
    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0.0, QColor(t["header_grad_start"]))
    gradient.setColorAt(1.0, QColor(t["header_grad_end"]))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawEllipse(circle_rect)

    # A small four-point sparkle/star, off-white, centered slightly high -
    # echoes the little heart/sparkle accents on the bundled chibi art
    # without reproducing it.
    cx, cy = size / 2, size * 0.47
    r_out, r_in = size * 0.24, size * 0.09
    star = QPainterPath()
    for i in range(8):
        angle = math.pi / 4 * i - math.pi / 2
        r = r_out if i % 2 == 0 else r_in
        x, y = cx + r * math.cos(angle), cy + r * math.sin(angle)
        if i == 0:
            star.moveTo(x, y)
        else:
            star.lineTo(x, y)
    star.closeSubpath()
    painter.setBrush(QColor("#FFFFFF"))
    painter.drawPath(star)

    # A tiny pink dot "gem" beneath the sparkle for a touch of the
    # accent2 pink used throughout the rest of the palette.
    dot_r = size * 0.055
    painter.setBrush(QColor(t["accent2"]))
    painter.drawEllipse(QPoint(int(cx), int(size * 0.74)), int(dot_r), int(dot_r))

    painter.end()
    return QIcon(pm)


def _build_tray_icon(window: CompanionWindow, app: QApplication) -> QSystemTrayIcon:
    icon = _build_app_icon(theme=_theme(window.settings))
    tray = QSystemTrayIcon(icon)
    tray.setToolTip(config.ASSISTANT_NAME)

    menu = QMenu()
    menu.setAttribute(Qt.WA_TranslucentBackground)
    menu.setStyleSheet(_build_menu_style(window.settings))
    menu.aboutToShow.connect(lambda: menu.setStyleSheet(_build_menu_style(window.settings)))
    show_action = menu.addAction("Show / Hide")
    settings_action = menu.addAction("Settings…")
    menu.addSeparator()
    quit_action = menu.addAction("Quit")

    def _toggle():
        window.setVisible(not window.isVisible())

    show_action.triggered.connect(_toggle)
    settings_action.triggered.connect(window.open_settings)
    quit_action.triggered.connect(app.quit)

    tray.setContextMenu(menu)
    tray.activated.connect(lambda reason: _toggle() if reason == QSystemTrayIcon.Trigger else None)
    tray.show()
    return tray


_console_window_handle = None


def _set_console_visible(visible: bool):
    """On Windows, shows or hides the console window this process is
    attached to - the cmd.exe window opened by scripts\\start_alyssa.bat (or
    whatever terminal launched it) - controlled by HIDE_CONSOLE_WINDOW in
    config.py (or the matching Settings -> Assistant checkbox).

    Unlike _close_parent_console() below, this doesn't close that window
    or end the .bat script waiting on it - it only toggles whether it's
    drawn on screen, so it can be shown again later (e.g. by unchecking
    "Show command prompt" in Settings) to see startup errors,
    tracebacks, or DEBUG_PRINT_TRANSCRIPTS output. No-op (silently) on
    any platform other than Windows, or if there's no console attached at
    all (e.g. launched via pythonw.exe or a frozen windowed .exe with no
    console) - both cases where there's nothing to show or hide."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        global _console_window_handle
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        kernel32.GetConsoleWindow.restype = wintypes.HWND
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.IsWindow.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]

        console_hwnd = kernel32.GetConsoleWindow()
        if not console_hwnd:
            return

        if _console_window_handle and not user32.IsWindow(_console_window_handle):
            _console_window_handle = None
        if _console_window_handle is None:
            if user32.IsWindowVisible(console_hwnd):
                _console_window_handle = console_hwnd
            else:
                foreground = user32.GetForegroundWindow()
                class_name = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(foreground, class_name, len(class_name))
                if class_name.value in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}:
                    _console_window_handle = foreground

        hwnd = _console_window_handle or console_hwnd
        if hwnd:
            user32.ShowWindowAsync(hwnd, 9 if visible else 0)  # SW_RESTORE / SW_HIDE
    except Exception:
        pass  # best-effort - never let this stop Alyssa from starting


def _close_parent_console():
    """On Windows, asks the console window this process is attached to -
    the cmd.exe window opened by scripts\\start_alyssa.bat (or whatever terminal
    launched it) - to close itself too.

    Quitting Alyssa only ends the Python process; the console window she
    was launched from stays open behind her (that's just how a console
    process works - the shell that ran the script doesn't close itself
    just because the script exited). GetConsoleWindow() finds that
    console's window handle, and posting it WM_CLOSE asks it to close the
    same way clicking its own [x] button would - including terminating
    the .bat script/cmd.exe that was waiting on this process, since they
    share one console. No-op (silently) on any platform other than
    Windows, or if there's no console attached at all (e.g. launched via
    pythonw.exe or a frozen windowed .exe with no console)."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            WM_CLOSE = 0x0010
            ctypes.windll.user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    except Exception:
        pass  # best-effort - never let this stop Alyssa from quitting


def run_with_assistant(assistant_loop_fn):
    """Starts the Qt GUI on the main thread and runs assistant_loop_fn(bridge)
    on a background thread. assistant_loop_fn should behave like main.py's
    run_assistant_loop(bridge): do its own listening/thinking/speaking loop,
    and call bridge.speak_signal.emit(text) (instead of / in addition to
    printing) whenever it wants something shown in the speech bubble."""
    # Kick off the Whisper model load now, before window/QApplication
    # setup - it's a plain CPU/GPU-bound background thread with no
    # dependency on Qt. This is intentionally redundant with the load
    # main.run_assistant_loop() itself kicks off shortly after; whichever
    # gets there first does the real loading and the other is a no-op.
    import transcribe
    threading.Thread(target=transcribe.preload, daemon=True).start()

    # Hide the cmd.exe console window behind her by default (see
    # HIDE_CONSOLE_WINDOW in config.py).
    _set_console_visible(not getattr(config, "HIDE_CONSOLE_WINDOW", False))

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(_build_app_icon())

    settings = load_overlay_settings()
    bridge = Bridge()
    window = CompanionWindow(settings, bridge)
    bubble = SpeechBubble(window, settings)
    window.bubble = bubble
    chatbar = ChatInputBar(window, settings, bridge)
    window.chatbar = chatbar

    bridge.speak_signal.connect(bubble.show_message)
    bridge.thinking_signal.connect(bubble.show_thinking)
    # NOTE: connect to bound methods here, not bare lambdas.
    # speak_signal/talk_*_signal/error_signal are emitted from the
    # background assistant thread - PySide6 only auto-queues a signal to
    # the GUI thread when the slot is a bound method of a QObject (it
    # reads the receiver's thread affinity). A bare lambda has no such
    # receiver, so it falls back to a direct same-thread call, meaning
    # set_talking()/QMessageBox.critical() would run on the background
    # thread, touching Qt widgets/timers unsafely - causing mouth-
    # animation glitches and occasional crashes.
    bridge.error_signal.connect(window.show_error)
    bridge.gemini_key_needed.connect(window.prompt_gemini_key_setup)
    bridge.reply_pending_signal.connect(bubble.on_reply_pending)
    bridge.talk_start_signal.connect(window.start_talking)
    bridge.talk_end_signal.connect(window.stop_talking)
    # Keeps the speech bubble's fade-out timed to when she actually
    # finishes speaking, rather than a fixed guess made when the text
    # first appeared (see SpeechBubble.on_talk_start/on_talk_end).
    bridge.talk_start_signal.connect(bubble.on_talk_start)
    bridge.talk_end_signal.connect(bubble.on_talk_end)
    if QSystemTrayIcon.isSystemTrayAvailable():
        window.tray = _build_tray_icon(window, app)

    window.show()
    chatbar.apply_enabled()

    # Qt can reinitialize the Windows console during application/window setup.
    # Re-apply the configured visibility after the GUI is fully initialized so
    # the Settings checkbox and config.py value reliably control the console.
    _set_console_visible(not getattr(config, "HIDE_CONSOLE_WINDOW", False))
    # The startup greeting is spoken by run_assistant_loop() itself (via
    # speak(), once preflight checks pass) - not shown here directly,
    # since showing it here too would flash a silent, unvoiced duplicate
    # before the real greeting replaces it a moment later.

    window._worker_thread = None

    def _run_worker_safely():
        """Runs assistant_loop_fn and, if it ever exits via an unhandled
        exception, reports it through the GUI instead of letting the
        thread just vanish. Without this, any crash here (a bug, or a
        None sys.stdout/stderr when launched via pythonw.exe with no
        console - see main.py) leaves the window looking totally normal
        while nothing is actually listening, with no way to tell why."""
        try:
            assistant_loop_fn(bridge)
        except Exception as e:
            import traceback
            traceback.print_exc()
            bridge.error_signal.emit(
                f"Alyssa's listening loop crashed and stopped:\n{e}\n\n"
                "Check the console (Settings -> Show command prompt) for "
                "the full error, or restart Alyssa."
            )

    def _start_worker():
        """(Re)starts the background listening loop if it isn't already
        running. Safe to call any number of times - a no-op while the loop
        is already alive. This is what makes "paste API key -> Save" in
        Settings actually start listening immediately: the loop exits
        right away on first launch if run_preflight_checks() fails (e.g.
        no key yet), so without this, nothing would ever restart it short
        of fully relaunching the app."""
        if window._worker_thread is not None and window._worker_thread.is_alive():
            return
        window._worker_thread = threading.Thread(
            target=_run_worker_safely, daemon=True
        )
        window._worker_thread.start()

    window.start_assistant_worker = _start_worker
    _start_worker()

    # app.exec() only returns once app.quit() is called - and since
    # setQuitOnLastWindowClosed(False) is set above, only the "Quit"
    # actions in the right-click/tray menus do that (see
    # _show_context_menu and _build_tray_icon). So this is the reliable
    # spot to also close the console window she was launched from.
    exit_code = app.exec()
    _close_parent_console()
    sys.exit(exit_code)
