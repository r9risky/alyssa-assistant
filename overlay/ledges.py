"""Window ledge detection and companion wandering physics (inspired by Capri's Ledges.cs)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import random

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QGuiApplication


def get_window_ledges(exclude_hwnds: set[int] | None = None) -> list[dict]:
    """Return a list of horizontal ledges from visible top-level windows and screen floors.

    Each ledge dict has: {"left": int, "right": int, "y": int, "kind": "window" | "floor"}.
    """
    exclude = exclude_hwnds or set()
    ledges: list[dict] = []

    if os.name == "nt" and hasattr(ctypes, "windll") and getattr(ctypes, "windll", None) is not None:
        try:
            user32 = ctypes.windll.user32
            WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

            def _enum_cb(hwnd, _lparam):
                if int(hwnd) in exclude:
                    return True
                if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
                    return True
                rect = wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                width = rect.right - rect.left
                height = rect.bottom - rect.top
                if width >= 180 and height >= 100 and rect.top > 40:
                    ledges.append(
                        {
                            "left": int(rect.left),
                            "right": int(rect.right),
                            "y": int(rect.top),
                            "kind": "window",
                        }
                    )
                return True

            user32.EnumWindows(WNDENUMPROC(_enum_cb), 0)
        except Exception:
            pass

    for screen in QGuiApplication.screens():
        avail = screen.availableGeometry()
        if avail.width() > 0 and avail.height() > 0:
            ledges.append(
                {
                    "left": avail.left(),
                    "right": avail.right(),
                    "y": avail.bottom(),
                    "kind": "floor",
                }
            )
    return ledges


def snap_to_nearest_ledge(
    x: int,
    y: int,
    width: int,
    height: int,
    max_distance: int = 28,
    exclude_hwnds: set[int] | None = None,
) -> tuple[int, int, dict | None]:
    """If the companion's feet (`y + height`) are within `max_distance` px of a ledge, snap to it."""
    foot_y = y + height
    center_x = x + width // 2
    best_ledge = None
    best_dist = max_distance + 1

    for ledge in get_window_ledges(exclude_hwnds=exclude_hwnds):
        if ledge["right"] - ledge["left"] < width // 2:
            continue
        if center_x < ledge["left"] or center_x > ledge["right"]:
            continue
        dist = abs(foot_y - ledge["y"])
        if dist <= max_distance and dist < best_dist:
            best_dist = dist
            best_ledge = ledge

    if best_ledge is None:
        return x, y, None

    clamped_x = max(best_ledge["left"], min(x, best_ledge["right"] - width))
    snapped_y = best_ledge["y"] - height
    return clamped_x, snapped_y, best_ledge


def pick_wander_destination(
    current_x: int,
    current_y: int,
    width: int,
    height: int,
    perch_on_windows: bool = True,
    exclude_hwnds: set[int] | None = None,
) -> QPoint:
    """Pick a target QPoint for the companion to wander or hop to."""
    ledges = get_window_ledges(exclude_hwnds=exclude_hwnds)
    if not perch_on_windows:
        ledges = [l for l in ledges if l.get("kind") == "floor"]
    valid = [l for l in ledges if (l["right"] - l["left"]) >= width + 20]
    if not valid:
        return QPoint(current_x, current_y)
    chosen = random.choice(valid)
    min_x = chosen["left"] + 8
    max_x = max(min_x, chosen["right"] - width - 8)
    target_x = random.randint(min_x, max_x)
    target_y = chosen["y"] - height
    # Clamp within screen bounds
    screen = QGuiApplication.screenAt(QPoint(target_x, max(0, target_y))) or QGuiApplication.primaryScreen()
    if screen is not None:
        avail = screen.availableGeometry()
        target_x = max(avail.left(), min(target_x, avail.right() - width))
        target_y = max(avail.top(), min(target_y, avail.bottom() - height))
    return QPoint(target_x, target_y)
