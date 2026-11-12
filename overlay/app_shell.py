import math
import os
import sys
import threading

from PySide6.QtCore import Qt, QPoint, QRect
from PySide6.QtGui import (
    QPixmap, QPainter, QColor, QPainterPath, QLinearGradient, QIcon,
)
from PySide6.QtWidgets import (
    QApplication, QMenu, QSystemTrayIcon,
)

import config

from .rendering import load_overlay_settings
from .theming import _THEME_LIGHT, _build_menu_style, _theme
from .widgets import Bridge, ChatInputBar, CompanionWindow, SpeechBubble

def _build_app_icon(size: int = 64, theme: dict = None) -> "QIcon":
    """Circular tray/app icon from the theme header gradient. Defaults to light if `theme` is None."""
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


def _find_windows_terminal_hwnd():
    """Visible Windows Terminal hwnd hosting this process, or None.

    - WT ConPTY: GetConsoleWindow() is an invisible conhost — show/hide it is a no-op.
    - Walk ancestors (python → cmd → OpenConsole → WindowsTerminal.exe), then
      EnumWindows for class CASCADIA_HOSTING_WINDOW_CLASS.
    """
    try:
        import psutil

        proc = psutil.Process(os.getpid())
        wt_pid = None
        for _ in range(10):  # bounded walk - never loop indefinitely
            parent = proc.parent()
            if parent is None:
                break
            if (parent.name() or "").lower() == "windowsterminal.exe":
                wt_pid = parent.pid
                break
            proc = parent
        if wt_pid is None:
            return None

        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd, _lparam):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == wt_pid:
                class_name = ctypes.create_unicode_buffer(64)
                user32.GetClassNameW(hwnd, class_name, len(class_name))
                if class_name.value == "CASCADIA_HOSTING_WINDOW_CLASS":
                    found.append(hwnd)
            return True  # keep enumerating

        user32.EnumWindows(_enum_proc, 0)
        return found[0] if found else None
    except Exception:
        return None  # fall back to conhost / foreground heuristic


def _set_console_visible(visible: bool):
    """Show/hide the attached Windows console (HIDE_CONSOLE_WINDOW / Settings checkbox).

    - Hide only (ShowWindowAsync SW_RESTORE/SW_HIDE); does not close the window or launching .bat.
    - Cached hwnd: WT ConPTY via _find_windows_terminal_hwnd; else focused
      ConsoleWindowClass / CASCADIA_HOSTING_WINDOW_CLASS; else GetConsoleWindow().
    - No-op on non-Windows or if no console (pythonw / windowed exe).
    """
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
                # Invisible hwnd = likely WT ConPTY; resolve via ancestry, not foreground.
                _console_window_handle = _find_windows_terminal_hwnd()
            if _console_window_handle is None:
                # Fallback: focused ConsoleWindowClass / CASCADIA_HOSTING_WINDOW_CLASS.
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
    """Post WM_CLOSE to the attached Windows console so the launching cmd/.bat exits with the app.

    Unlike _set_console_visible, this closes the window. No-op on non-Windows or if no console.
    """
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
    """Qt GUI on the main thread; assistant_loop_fn(bridge) on a daemon worker.

    `assistant_loop_fn` should match main.run_assistant_loop: emit bridge.speak_signal for the bubble.
    """
    # Preload Whisper before Qt; redundant with run_assistant_loop — first load wins.
    import transcribe
    threading.Thread(target=transcribe.preload, daemon=True).start()

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
    from .widgets import HoverPill, MiniPlayerWidget
    from .call_panel import CallPanelWidget
    from .game_hud import GameHudWidget
    hover_pill = HoverPill(window, settings)
    window.hover_pill = hover_pill
    mini_player = MiniPlayerWidget(window, settings)
    window.mini_player = mini_player
    call_panel = CallPanelWidget(window, settings)
    window.call_panel = call_panel
    game_hud = GameHudWidget(window, settings)
    window.game_hud = game_hud

    bridge.speak_signal.connect(bubble.show_message)
    bridge.sources_signal.connect(bubble.show_sources)
    bridge.steps_signal.connect(bubble.show_steps)
    bridge.fondness_signal.connect(hover_pill.refresh_state)
    bridge.thinking_signal.connect(bubble.show_thinking)
    bridge.thinking_signal.connect(lambda: window.set_mood("thinking"))
    bridge.mood_signal.connect(window.set_mood)
    # Bound QObject methods only (not lambdas): background emits auto-queue to the GUI thread.
    bridge.error_signal.connect(window.show_error)
    bridge.gemini_key_needed.connect(window.prompt_gemini_key_setup)
    bridge.reply_pending_signal.connect(bubble.on_reply_pending)
    bridge.talk_start_signal.connect(window.start_talking)
    bridge.talk_end_signal.connect(window.stop_talking)
    bridge.talk_end_signal.connect(lambda: window.set_mood("neutral"))
    # Bubble linger/fade follows talk_start/talk_end, not a guess at show time.
    bridge.talk_start_signal.connect(bubble.on_talk_start)
    bridge.talk_end_signal.connect(bubble.on_talk_end)
    bridge.spoken_signal.connect(bubble.on_chunk_spoken)
    if QSystemTrayIcon.isSystemTrayAvailable():
        window.tray = _build_tray_icon(window, app)

    window.show()
    chatbar.apply_enabled()
    mini_player.apply_enabled()

    try:
        from hotkeys import GlobalHotkeyManager
        import actions
        import recorder
        from .widgets import run_on_gui_thread
        gui_callbacks = {
            "talk": lambda: (recorder.request_push_to_talk(), window.wake_up()),
            "mute": lambda: actions.toggle_mute(),
            "type": lambda: (chatbar.show(), chatbar.raise_(), chatbar.activateWindow(), chatbar.edit.setFocus()),
            "hide": lambda: window.setVisible(not window.isVisible()),
            "quiet": lambda: (setattr(config, "QUIET_MODE", not getattr(config, "QUIET_MODE", False)), hover_pill.refresh_state()),
            "hop_off": lambda: window.stop_cursor_ride(),
        }
        # The Win32 poller fires on its own thread; widgets may only be touched on the GUI thread.
        hotkey_mgr = GlobalHotkeyManager(
            {name: (lambda fn=fn: run_on_gui_thread(fn)) for name, fn in gui_callbacks.items()}
        )
        hotkey_mgr.start()
        if hotkey_mgr._thread is None:
            # No global poller (non-Windows): fall back to in-app shortcuts. Binding both
            # made every chord fire twice, so "hide" toggled off and straight back on.
            hotkey_mgr.bind_qt_shortcuts(window)
        window._hotkey_manager = hotkey_mgr
    except Exception as e:
        print(f"[hotkeys] Failed to initialize global hotkeys: {e}")

    # Qt may reinit the Windows console during setup — re-apply after the GUI is up.
    _set_console_visible(not getattr(config, "HIDE_CONSOLE_WINDOW", False))
    # Greeting is spoken by run_assistant_loop after preflight; showing it here would flash a silent duplicate.

    window._worker_thread = None

    def _run_worker_safely():
        """Run assistant_loop_fn; on unhandled exception, emit via the GUI so a dead listener is visible (pythonw has no console)."""
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
        """Start the listener thread if it isn't alive. Idempotent.

        After Settings saves a key: first launch can exit immediately if preflight fails; this restarts without a full relaunch.
        """
        if window._worker_thread is not None and window._worker_thread.is_alive():
            return
        window._worker_thread = threading.Thread(
            target=_run_worker_safely, daemon=True
        )
        window._worker_thread.start()

    window.start_assistant_worker = _start_worker
    _start_worker()

    # exec() returns only after Quit (last-window-close is off); close the parent console here.
    exit_code = app.exec()
    _close_parent_console()
    sys.exit(exit_code)
