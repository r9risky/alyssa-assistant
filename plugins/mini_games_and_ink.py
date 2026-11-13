"""Mini-Games (Tic-Tac-Toe, Connect Four, Rock Paper Scissors, Chess) & Screen Ink Overlay.

Inspired by Capri Desktop's `Minis.cs` and `Ink.cs`:
- `draw_on_screen`: Draw neon pink/cyan annotations (`line`, `arrow`, `circle`, `box`, `path`, `label`)
  directly onto a transparent desktop overlay (`action="replace" | "add" | "clear"`).
- `play_mini_game`: Play Tic-Tac-Toe, Connect Four, Rock Paper Scissors, or Chess against Alyssa
  by voice/chat or in an interactive GUI window!
"""

from __future__ import annotations

import math
import random
import re
import threading


_ink_lock = threading.RLock()
_ink_shapes: list[dict] = []
_ink_overlay_window = None
_active_game_dialog = None


# ===========================================================================
# 1. Screen ink: parser and overlay
# ===========================================================================

def parse_ink_shapes(shapes_text: str) -> list[dict]:
    """Parse ink commands, one per line or separated by semicolons (screen pixels).

    - `circle CX CY RADIUS`          ring around a centre point
    - `box X Y WIDTH HEIGHT`         rectangle from its top-left corner
    - `line X1 Y1 X2 Y2` / `arrow X1 Y1 X2 Y2` (arrowhead at the second point)
    - `path X1 Y1 X2 Y2 ...`         freehand stroke through 2+ points
    - `label X Y some words`         text at a point

    Every shape becomes {"kind", "points", "text"}; circles and boxes are stored as their
    two bounding corners so the overlay draws them the same way.
    """
    number = r"[+-]?\d+(?:\.\d+)?"
    parsed: list[dict] = []
    for raw_row in re.split(r"[;\r\n]+", shapes_text or ""):
        words = raw_row.strip().split(None, 1)
        if not words:
            continue
        kind, body = words[0].lower(), (words[1] if len(words) > 1 else "")
        if kind == "label":
            m = re.match(rf"^({number})[,\s]+({number})\s+(.+)$", body.strip())
            if m and m.group(3).strip("\"' "):
                parsed.append({
                    "kind": "text",
                    "points": [(float(m.group(1)), float(m.group(2)))],
                    "text": m.group(3).strip().strip("\"'"),
                })
            continue
        nums = [float(n) for n in re.findall(number, body)]
        if kind == "circle" and len(nums) == 3 and nums[2] > 0:
            cx, cy, r = nums
            parsed.append({"kind": "circle", "points": [(cx - r, cy - r), (cx + r, cy + r)], "text": None})
        elif kind == "box" and len(nums) == 4 and nums[2] > 0 and nums[3] > 0:
            x, y, w, h = nums
            parsed.append({"kind": "box", "points": [(x, y), (x + w, y + h)], "text": None})
        elif kind in ("line", "arrow") and len(nums) == 4:
            parsed.append({"kind": kind, "points": [(nums[0], nums[1]), (nums[2], nums[3])], "text": None})
        elif kind == "path" and len(nums) >= 4 and len(nums) % 2 == 0:
            pts = [(nums[i], nums[i + 1]) for i in range(0, len(nums), 2)]
            parsed.append({"kind": "path", "points": pts, "text": None})
    return parsed


def get_ink_shapes() -> list[dict]:
    with _ink_lock:
        return [dict(s) for s in _ink_shapes]


def _refresh_ink_overlay():
    global _ink_overlay_window
    try:
        from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
        from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath, QPen
        from PySide6.QtWidgets import QApplication, QWidget
    except ImportError:
        return

    if QApplication.instance() is None:
        return

    class _InkOverlayWidget(QWidget):
        def __init__(self):
            flags = (
                Qt.FramelessWindowHint
                | Qt.Tool
                | Qt.WindowStaysOnTopHint
                | Qt.WindowTransparentForInput
                | Qt.NoDropShadowWindowHint
            )
            super().__init__(None, flags)
            self.setAttribute(Qt.WA_TranslucentBackground)
            self.setAttribute(Qt.WA_TransparentForMouseEvents)
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                self.setGeometry(screen.geometry())
            else:
                self.setGeometry(0, 0, 1920, 1080)

        def paintEvent(self, _event):
            shapes = get_ink_shapes()
            if not shapes:
                return
            painter = QPainter(self)
            painter.setRenderHint(QPainter.Antialiasing)
            pen = QPen(QColor(255, 64, 160, 235), 4.0, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
            painter.setPen(pen)
            font = QFont("Segoe UI", 18, QFont.Bold)
            painter.setFont(font)

            for shape in shapes:
                kind = shape["kind"]
                pts = shape["points"]
                if kind == "line" and len(pts) == 2:
                    painter.drawLine(QPointF(*pts[0]), QPointF(*pts[1]))
                elif kind == "arrow" and len(pts) == 2:
                    p1, p2 = QPointF(*pts[0]), QPointF(*pts[1])
                    painter.drawLine(p1, p2)
                    angle = math.atan2(p2.y() - p1.y(), p2.x() - p1.x())
                    head_len = 16.0
                    a1 = QPointF(
                        p2.x() - head_len * math.cos(angle - math.pi / 6),
                        p2.y() - head_len * math.sin(angle - math.pi / 6),
                    )
                    a2 = QPointF(
                        p2.x() - head_len * math.cos(angle + math.pi / 6),
                        p2.y() - head_len * math.sin(angle + math.pi / 6),
                    )
                    painter.drawLine(p2, a1)
                    painter.drawLine(p2, a2)
                elif kind in ("circle", "box") and len(pts) == 2:
                    left = min(pts[0][0], pts[1][0])
                    top = min(pts[0][1], pts[1][1])
                    w = abs(pts[1][0] - pts[0][0])
                    h = abs(pts[1][1] - pts[0][1])
                    rect = QRectF(left, top, w, h)
                    if kind == "circle":
                        painter.drawEllipse(rect)
                    else:
                        painter.drawRoundedRect(rect, 6.0, 6.0)
                elif kind == "path" and len(pts) >= 2:
                    path = QPainterPath(QPointF(*pts[0]))
                    for pt in pts[1:]:
                        path.lineTo(QPointF(*pt))
                    painter.drawPath(path)
                elif kind == "text" and pts and shape.get("text"):
                    painter.drawText(QPointF(pts[0][0], pts[0][1] + 24), str(shape["text"]))

    def _update_ui():
        global _ink_overlay_window
        shapes = get_ink_shapes()
        if not shapes:
            if _ink_overlay_window is not None:
                _ink_overlay_window.hide()
            return
        if _ink_overlay_window is None:
            _ink_overlay_window = _InkOverlayWidget()
        _ink_overlay_window.show()
        _ink_overlay_window.raise_()
        _ink_overlay_window.update()

    from overlay.widgets import run_on_gui_thread
    run_on_gui_thread(_update_ui)


def draw_on_screen(
    shapes: str = "",
    action: str = "replace",
    description: str = "",
) -> str:
    """Draw or clear pink/cyan screen ink annotations directly on the user's screen."""
    act = (action or "replace").strip().lower()
    with _ink_lock:
        if act in ("clear", "erase", "remove"):
            _ink_shapes.clear()
            _refresh_ink_overlay()
            return "Cleared my drawing from your screen."

        parsed = parse_ink_shapes(shapes)
        if not parsed and description.strip():
            # Generate a helpful default diagram/art shape when description is given without coordinates
            label = description.strip()[:30]
            parsed = [
                {"kind": "box", "points": [(560.0, 320.0), (960.0, 520.0)], "text": None},
                {"kind": "text", "points": [(600.0, 400.0)], "text": label},
            ]
        if not parsed:
            return "Give me shapes (e.g. 'circle 500 400 100; label 420 260 Here') or action='clear'."

        if act == "add":
            _ink_shapes.extend(parsed)
            verb = "Added to my drawing on your screen"
        else:
            _ink_shapes[:] = parsed
            verb = "Drew on your screen"

    _refresh_ink_overlay()
    labels = [s["text"] for s in parsed if s.get("kind") == "text" and s.get("text")]
    if labels:
        return f"{verb}: {', '.join(labels)}."
    return f"{verb} ({len(parsed)} shape(s))."


# ===========================================================================
# 2. Mini-Games Engine (Tic-Tac-Toe, Connect Four, Rock Paper Scissors, Chess)
# ===========================================================================

class TicTacToeGame:
    WIN_LINES = [
        (0, 1, 2), (3, 4, 5), (6, 7, 8),
        (0, 3, 6), (1, 4, 7), (2, 5, 8),
        (0, 4, 8), (2, 4, 6),
    ]

    def __init__(self):
        self.reset()

    def reset(self):
        self.board = [" "] * 9
        self.winner = None

    def check_winner(self) -> str | None:
        for a, b, c in self.WIN_LINES:
            if self.board[a] != " " and self.board[a] == self.board[b] == self.board[c]:
                return self.board[a]
        if " " not in self.board:
            return "draw"
        return None

    def _pick_ai_move(self) -> int:
        # 1. Win if possible
        for idx in range(9):
            if self.board[idx] == " ":
                self.board[idx] = "O"
                if self.check_winner() == "O":
                    self.board[idx] = " "
                    return idx
                self.board[idx] = " "
        # 2. Block X win
        for idx in range(9):
            if self.board[idx] == " ":
                self.board[idx] = "X"
                if self.check_winner() == "X":
                    self.board[idx] = " "
                    return idx
                self.board[idx] = " "
        if self.board[4] == " ":
            return 4
        corners = [i for i in (0, 2, 6, 8) if self.board[i] == " "]
        if corners:
            return random.choice(corners)
        avail = [i for i in range(9) if self.board[i] == " "]
        return random.choice(avail)

    def play_move(self, cell_1_to_9: int) -> str:
        if self.winner:
            self.reset()
        idx = cell_1_to_9 - 1
        if not (0 <= idx < 9):
            return "Pick a square from 1 to 9."
        if self.board[idx] != " ":
            return f"Square {cell_1_to_9} is already taken."
        self.board[idx] = "X"
        self.winner = self.check_winner()
        if self.winner == "X":
            return f"You played {cell_1_to_9} and won! Well played.\n{self.format_board()}"
        if self.winner == "draw":
            return f"It's a draw!\n{self.format_board()}"
        ai_idx = self._pick_ai_move()
        self.board[ai_idx] = "O"
        self.winner = self.check_winner()
        if self.winner == "O":
            return f"You played {cell_1_to_9}; I played {ai_idx + 1} and won!\n{self.format_board()}"
        if self.winner == "draw":
            return f"You played {cell_1_to_9}; I played {ai_idx + 1}. It's a draw!\n{self.format_board()}"
        return f"You played {cell_1_to_9}; I played {ai_idx + 1}. Your turn!\n{self.format_board()}"

    def format_board(self) -> str:
        cells = [self.board[i] if self.board[i] != " " else str(i + 1) for i in range(9)]
        return (
            f"{cells[0]} | {cells[1]} | {cells[2]}\n"
            f"{cells[3]} | {cells[4]} | {cells[5]}\n"
            f"{cells[6]} | {cells[7]} | {cells[8]}"
        )


class ConnectFourGame:
    ROWS = 6
    COLS = 7

    def __init__(self):
        self.reset()

    def reset(self):
        self.grid = [["."] * self.COLS for _ in range(self.ROWS)]
        self.winner = None

    def _drop(self, col: int, token: str) -> int | None:
        for r in range(self.ROWS - 1, -1, -1):
            if self.grid[r][col] == ".":
                self.grid[r][col] = token
                return r
        return None

    def _undo(self, col: int):
        for r in range(self.ROWS):
            if self.grid[r][col] != ".":
                self.grid[r][col] = "."
                return

    def check_winner(self) -> str | None:
        for r in range(self.ROWS):
            for c in range(self.COLS):
                tok = self.grid[r][c]
                if tok == ".":
                    continue
                for dr, dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
                    if all(
                        0 <= r + k * dr < self.ROWS
                        and 0 <= c + k * dc < self.COLS
                        and self.grid[r + k * dr][c + k * dc] == tok
                        for k in range(4)
                    ):
                        return tok
        if all(self.grid[0][c] != "." for c in range(self.COLS)):
            return "draw"
        return None

    def _pick_ai_col(self) -> int:
        valid = [c for c in range(self.COLS) if self.grid[0][c] == "."]
        for c in valid:
            self._drop(c, "O")
            if self.check_winner() == "O":
                self._undo(c)
                return c
            self._undo(c)
        for c in valid:
            self._drop(c, "X")
            if self.check_winner() == "X":
                self._undo(c)
                return c
            self._undo(c)
        if 3 in valid:
            return 3
        return random.choice(valid)

    def play_move(self, col_1_to_7: int) -> str:
        if self.winner:
            self.reset()
        c = col_1_to_7 - 1
        if not (0 <= c < self.COLS):
            return "Choose a column from 1 to 7."
        if self._drop(c, "X") is None:
            return f"Column {col_1_to_7} is full."
        self.winner = self.check_winner()
        if self.winner == "X":
            return f"You dropped in column {col_1_to_7} and connected four! You win!"
        if self.winner == "draw":
            return "The Connect Four board is full — it's a draw!"
        ai_c = self._pick_ai_col()
        self._drop(ai_c, "O")
        self.winner = self.check_winner()
        if self.winner == "O":
            return f"You dropped in column {col_1_to_7}; I dropped in column {ai_c + 1} and connected four!"
        return f"You dropped in column {col_1_to_7}; I dropped in column {ai_c + 1}. Your turn!"


class RockPaperScissorsGame:
    BEATS = {"rock": "scissors", "paper": "rock", "scissors": "paper"}

    def __init__(self):
        self.user_score = 0
        self.ai_score = 0

    def play_round(self, choice: str) -> str:
        user = (choice or "").strip().lower()
        if user not in self.BEATS:
            return "Pick rock, paper, or scissors!"
        ai = random.choice(["rock", "paper", "scissors"])
        if user == ai:
            return f"We both picked {user} — tie! (Score: You {self.user_score} - {self.ai_score} Alyssa)"
        if self.BEATS[user] == ai:
            self.user_score += 1
            return f"You picked {user}, I picked {ai} — you win this round! (Score: You {self.user_score} - {self.ai_score} Alyssa)"
        self.ai_score += 1
        return f"You picked {user}, I picked {ai} — my point! (Score: You {self.user_score} - {self.ai_score} Alyssa)"


class SimpleChessGame:
    """Lightweight playable 8x8 Chess board with coordinate moves (e.g. e2e4)."""

    def __init__(self):
        self.reset()

    def reset(self):
        self.board = [
            list("rnbqkbnr"),
            list("pppppppp"),
            list("........"),
            list("........"),
            list("........"),
            list("........"),
            list("PPPPPPPP"),
            list("RNBQKBNR"),
        ]
        self.move_history: list[str] = []

    @staticmethod
    def _parse_sq(sq: str) -> tuple[int, int] | None:
        if len(sq) != 2:
            return None
        col = ord(sq[0].lower()) - ord("a")
        row = 8 - int(sq[1]) if sq[1].isdigit() else -1
        if 0 <= row < 8 and 0 <= col < 8:
            return row, col
        return None

    @staticmethod
    def _sq_name(r: int, c: int) -> str:
        return f"{chr(ord('a') + c)}{8 - r}"

    def play_move(self, move_str: str) -> str:
        clean = re.sub(r"[^a-h1-8]", "", (move_str or "").lower())
        if len(clean) < 4:
            return "Give your chess move in coordinate format like 'e2e4' or 'g1f3'."
        src = self._parse_sq(clean[:2])
        dst = self._parse_sq(clean[2:4])
        if src is None or dst is None or src == dst:
            return "Invalid chess coordinates. Use a1 through h8 (e.g., 'e2e4')."
        sr, sc = src
        dr, dc = dst
        piece = self.board[sr][sc]
        if piece == "." or not piece.isupper():
            return f"There isn't one of your white pieces on {clean[:2]}."
        target = self.board[dr][dc]
        if target != "." and target.isupper():
            return f"You already have a piece on {clean[2:4]}."
        # Execute user move (auto-queen promotion on rank 8)
        self.board[sr][sc] = "."
        self.board[dr][dc] = "Q" if (piece == "P" and dr == 0) else piece
        self.move_history.append(clean[:4])
        if target == "k":
            return f"You played {clean[:4]} and captured my King — checkmate, you win!"

        # AI black reply
        ai_move = self._pick_black_move()
        if ai_move is None:
            return f"You played {clean[:4]} — I have no moves left, you win!"
        (asr, asc), (adr, adc) = ai_move
        ai_piece = self.board[asr][asc]
        captured = self.board[adr][adc]
        self.board[asr][asc] = "."
        self.board[adr][adc] = "q" if (ai_piece == "p" and adr == 7) else ai_piece
        ai_str = f"{self._sq_name(asr, asc)}{self._sq_name(adr, adc)}"
        self.move_history.append(ai_str)
        if captured == "K":
            return f"You played {clean[:4]}; I played {ai_str} and captured your King!"
        return f"You played {clean[:4]}; I replied with {ai_str}."

    def _pick_black_move(self):
        candidates = []
        for r in range(8):
            for c in range(8):
                p = self.board[r][c]
                if not p.islower():
                    continue
                if p == "p":
                    if r + 1 < 8 and self.board[r + 1][c] == ".":
                        candidates.append((0, (r, c), (r + 1, c)))
                        if r == 1 and self.board[r + 2][c] == ".":
                            candidates.append((0, (r, c), (r + 2, c)))
                    for dc in (-1, 1):
                        if r + 1 < 8 and 0 <= c + dc < 8 and self.board[r + 1][c + dc].isupper():
                            candidates.append((10, (r, c), (r + 1, c + dc)))
                elif p == "n":
                    for dr, dc in ((-2, -1), (-2, 1), (-1, -2), (-1, 2), (1, -2), (1, 2), (2, -1), (2, 1)):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < 8 and 0 <= nc < 8 and not self.board[nr][nc].islower():
                            score = 12 if self.board[nr][nc].isupper() else 1
                            candidates.append((score, (r, c), (nr, nc)))
                else:
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)):
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < 8 and 0 <= nc < 8 and not self.board[nr][nc].islower():
                            score = 8 if self.board[nr][nc].isupper() else 0
                            candidates.append((score, (r, c), (nr, nc)))
        if not candidates:
            return None
        candidates.sort(key=lambda x: x[0], reverse=True)
        top_score = candidates[0][0]
        best = [m for m in candidates if m[0] == top_score]
        _, src, dst = random.choice(best)
        return src, dst


_TTT = TicTacToeGame()
_C4 = ConnectFourGame()
_RPS = RockPaperScissorsGame()
_CHESS = SimpleChessGame()


def play_mini_game(
    game: str = "tic_tac_toe",
    move: str = "",
    action: str = "play",
) -> str:
    """Play or open a mini-game (tic_tac_toe, connect_four, rps, or chess) with Alyssa."""
    g = (game or "tic_tac_toe").strip().lower().replace("-", "_").replace(" ", "_")
    act = (action or "play").strip().lower()

    if act == "reset":
        if g in ("tic_tac_toe", "ttt"):
            _TTT.reset()
            return "Reset Tic-Tac-Toe! Pick a square from 1 to 9.\n" + _TTT.format_board()
        if g in ("connect_four", "c4"):
            _C4.reset()
            return "Reset Connect Four! Pick a column from 1 to 7."
        if g in ("rps", "rock_paper_scissors"):
            _RPS.user_score = 0
            _RPS.ai_score = 0
            return "Reset Rock Paper Scissors scores!"
        if g == "chess":
            _CHESS.reset()
            return "Reset the Chess board! Make your opening move (e.g., 'e2e4')."

    if act == "open_gui" or not move.strip():
        open_mini_game_window(g)
        if g in ("tic_tac_toe", "ttt"):
            return "Opened Tic-Tac-Toe! You can click a square or tell me a number 1–9.\n" + _TTT.format_board()
        if g in ("connect_four", "c4"):
            return "Opened Connect Four! Click a column or tell me a column 1–7."
        if g in ("rps", "rock_paper_scissors"):
            return "Opened Rock Paper Scissors! Pick rock, paper, or scissors."
        if g == "chess":
            return "Opened Chess! Enter a move like 'e2e4'."

    if g in ("tic_tac_toe", "ttt"):
        m = re.search(r"[1-9]", move)
        if not m:
            return "Tell me a square number from 1 to 9."
        return _TTT.play_move(int(m.group(0)))

    if g in ("connect_four", "c4"):
        m = re.search(r"[1-7]", move)
        if not m:
            return "Tell me a column number from 1 to 7."
        return _C4.play_move(int(m.group(0)))

    if g in ("rps", "rock_paper_scissors"):
        return _RPS.play_round(move)

    if g == "chess":
        return _CHESS.play_move(move)

    return f"Unknown game '{game}'. Choose tic_tac_toe, connect_four, rps, or chess."


def open_mini_game_window(game_key: str = "tic_tac_toe", parent=None):
    """Open the interactive Mini-Games window on the GUI thread."""
    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return None
    if QApplication.instance() is None:
        return None

    def _show():
        global _active_game_dialog
        panel = build_gui(parent=parent, initial_game=game_key, as_window=True)
        _active_game_dialog = panel
        panel.show()
        panel.raise_()
        panel.activateWindow()

    from overlay.widgets import run_on_gui_thread
    run_on_gui_thread(_show)
    return True


def build_gui(parent=None, initial_game: str = "tic_tac_toe", as_window: bool = False):
    """Build the Mini-Games & Screen Ink widget (used both as a Settings tab and standalone game window)."""
    from PySide6.QtWidgets import (
        QGridLayout,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )

    root = QWidget(parent)
    if as_window:
        root.setWindowTitle("Alyssa — Mini-Games & Screen Ink")
        root.resize(420, 440)

    layout = QVBoxLayout(root)
    layout.setContentsMargins(10, 10, 10, 10)
    tabs = QTabWidget(root)
    layout.addWidget(tabs)

    # 1. Tic-Tac-Toe Tab
    ttt_tab = QWidget()
    ttt_layout = QVBoxLayout(ttt_tab)
    ttt_status = QLabel("Your turn (X)! Click any square:")
    ttt_layout.addWidget(ttt_status)
    ttt_grid = QGridLayout()
    ttt_buttons: list[QPushButton] = []

    def _sync_ttt():
        for idx, btn in enumerate(ttt_buttons):
            val = _TTT.board[idx]
            btn.setText("" if val == " " else val)

    def _click_ttt(cell_num: int):
        msg = _TTT.play_move(cell_num)
        ttt_status.setText(msg.splitlines()[0])
        _sync_ttt()

    for i in range(9):
        btn = QPushButton("")
        btn.setFixedSize(64, 64)
        btn.clicked.connect(lambda _checked=False, n=i + 1: _click_ttt(n))
        ttt_buttons.append(btn)
        ttt_grid.addWidget(btn, i // 3, i % 3)
    ttt_layout.addLayout(ttt_grid)
    ttt_reset = QPushButton("New Game")
    ttt_reset.clicked.connect(lambda: (_TTT.reset(), _sync_ttt(), ttt_status.setText("New game! Your turn (X).")))
    ttt_layout.addWidget(ttt_reset)
    _sync_ttt()
    tabs.addTab(ttt_tab, "Tic-Tac-Toe")

    # 2. Connect Four Tab
    c4_tab = QWidget()
    c4_layout = QVBoxLayout(c4_tab)
    c4_status = QLabel("Drop a disc in columns 1–7:")
    c4_layout.addWidget(c4_status)
    c4_col_row = QHBoxLayout()
    c4_board_label = QLabel()
    c4_board_label.setStyleSheet("font-family: monospace; font-size: 14px;")

    def _sync_c4():
        rows_txt = [" ".join(r) for r in _C4.grid]
        c4_board_label.setText("\n".join(rows_txt))

    for col in range(1, 8):
        col_btn = QPushButton(str(col))
        col_btn.clicked.connect(lambda _c=False, c_num=col: (c4_status.setText(_C4.play_move(c_num)), _sync_c4()))
        c4_col_row.addWidget(col_btn)
    c4_layout.addLayout(c4_col_row)
    c4_layout.addWidget(c4_board_label)
    c4_reset = QPushButton("Reset Connect Four")
    c4_reset.clicked.connect(lambda: (_C4.reset(), _sync_c4(), c4_status.setText("Reset! Pick a column 1–7.")))
    c4_layout.addWidget(c4_reset)
    _sync_c4()
    tabs.addTab(c4_tab, "Connect Four")

    # 3. Rock Paper Scissors Tab
    rps_tab = QWidget()
    rps_layout = QVBoxLayout(rps_tab)
    rps_status = QLabel("Choose Rock, Paper, or Scissors:")
    rps_layout.addWidget(rps_status)
    rps_row = QHBoxLayout()
    for move_name in ("Rock", "Paper", "Scissors"):
        b = QPushButton(move_name)
        b.clicked.connect(lambda _c=False, m=move_name.lower(): rps_status.setText(_RPS.play_round(m)))
        rps_row.addWidget(b)
    rps_layout.addLayout(rps_row)
    tabs.addTab(rps_tab, "RPS")

    # 4. Chess Tab
    chess_tab = QWidget()
    chess_layout = QVBoxLayout(chess_tab)
    chess_status = QLabel("Enter move (e.g. e2e4):")
    chess_board_lbl = QLabel()
    chess_board_lbl.setStyleSheet("font-family: monospace; font-size: 13px;")

    def _sync_chess():
        lines = [f"{8 - r}  " + " ".join(_CHESS.board[r]) for r in range(8)]
        lines.append("   a b c d e f g h")
        chess_board_lbl.setText("\n".join(lines))

    chess_input_row = QHBoxLayout()
    chess_edit = QLineEdit()
    chess_edit.setPlaceholderText("e2e4")
    chess_move_btn = QPushButton("Play Move")
    chess_reset_btn = QPushButton("Reset")
    chess_input_row.addWidget(chess_edit, 1)
    chess_input_row.addWidget(chess_move_btn)
    chess_input_row.addWidget(chess_reset_btn)

    def _on_chess_move():
        mv = chess_edit.text().strip()
        if mv:
            chess_status.setText(_CHESS.play_move(mv))
            chess_edit.clear()
            _sync_chess()

    chess_move_btn.clicked.connect(_on_chess_move)
    chess_reset_btn.clicked.connect(lambda: (_CHESS.reset(), _sync_chess(), chess_status.setText("Board reset.")))
    chess_layout.addWidget(chess_status)
    chess_layout.addWidget(chess_board_lbl)
    chess_layout.addLayout(chess_input_row)
    _sync_chess()
    tabs.addTab(chess_tab, "Chess")

    # 5. Screen Ink Tab
    ink_tab = QWidget()
    ink_layout = QVBoxLayout(ink_tab)
    ink_layout.addWidget(QLabel("Draw on screen (e.g. `circle 400 300 100; label 320 170 Look here`):"))
    ink_edit = QLineEdit()
    ink_edit.setPlaceholderText("circle 520 350 120; arrow 300 200 400 250")
    ink_btn_row = QHBoxLayout()
    draw_btn = QPushButton("Draw on Screen")
    clear_ink_btn = QPushButton("Clear Screen Ink")
    ink_btn_row.addWidget(draw_btn)
    ink_btn_row.addWidget(clear_ink_btn)
    draw_btn.clicked.connect(lambda: draw_on_screen(shapes=ink_edit.text(), action="replace"))
    clear_ink_btn.clicked.connect(lambda: draw_on_screen(action="clear"))
    ink_layout.addWidget(ink_edit)
    ink_layout.addLayout(ink_btn_row)
    ink_layout.addStretch(1)
    tabs.addTab(ink_tab, "Screen Ink")

    index_map = {"tic_tac_toe": 0, "connect_four": 1, "rps": 2, "chess": 3, "ink": 4}
    tabs.setCurrentIndex(index_map.get(initial_game, 0))
    return root


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "draw_on_screen",
            "description": (
                "Sketch on top of the user's screen (it doesn't touch the mouse), e.g. to point something out. "
                "Coordinates are screen pixels. Commands, separated by ';': 'circle CX CY RADIUS', "
                "'box X Y WIDTH HEIGHT', 'line X1 Y1 X2 Y2', 'arrow X1 Y1 X2 Y2', 'path X1 Y1 X2 Y2 ...', "
                "'label X Y words'. Example: 'circle 640 360 80; arrow 400 200 580 320; label 380 180 This one'. "
                "action='clear' wipes the sketch."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "shapes": {
                        "type": "string",
                        "description": "Drawing commands separated by ';' (circle, box, line, arrow, path, label).",
                    },
                    "action": {
                        "type": "string",
                        "description": "'replace' draws fresh (default), 'add' keeps what's there, 'clear' removes everything.",
                        "enum": ["replace", "add", "clear"],
                    },
                    "description": {
                        "type": "string",
                        "description": "If you have no coordinates, a short caption to show in a box mid-screen.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "play_mini_game",
            "description": (
                "Play a built-in mini-game with the user (Tic-Tac-Toe, Connect Four, Rock Paper Scissors, or Chess) "
                "or open the interactive Mini-Game window."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "game": {
                        "type": "string",
                        "description": "Game to play: 'tic_tac_toe', 'connect_four', 'rps', or 'chess'.",
                        "enum": ["tic_tac_toe", "connect_four", "rps", "chess"],
                    },
                    "move": {
                        "type": "string",
                        "description": "The user's move (1-9 for Tic-Tac-Toe, 1-7 for Connect Four, rock/paper/scissors for RPS, or e2e4 for Chess).",
                    },
                    "action": {
                        "type": "string",
                        "description": "'play' (default), 'open_gui', or 'reset'.",
                        "enum": ["play", "open_gui", "reset"],
                    },
                },
                "required": ["game"],
            },
        },
    },
]

FUNCTIONS = {
    "draw_on_screen": draw_on_screen,
    "play_mini_game": play_mini_game,
}
