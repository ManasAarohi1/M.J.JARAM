"""Run > Overview (tile wall over the multiscope data) and Run > Activity log (capped, newest first).

Replaces the old Dashboard, Logs and Multiscope tabs plus the Extras "All-time found" counter.
"""
from __future__ import annotations

import re
import time
from collections import deque
from datetime import datetime
from itertools import islice
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import (
    QAbstractListModel, QModelIndex, QPoint, QRect, QRectF, QRegularExpression, QSize,
    QSortFilterProxyModel, Qt, QTimer,
)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractItemView, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLayout,
    QListView, QMessageBox, QScrollArea, QStyle, QStyledItemDelegate, QVBoxLayout, QWidget,
)

import ui_kit as K
from biomes import biome_duration, biome_meta

T = K.T
NORMAL_BG = "#1F2832"   # prototype --normal
TILE_INK = "#18212B"    # dark text on light biome colours
RARE = {"GLITCHED", "DREAMSPACE", "CYBERSPACE", "SINGULARITY"}  # same set as multiscope.PLAYER_TRACKER_DELAY_BIOMES
# (min tile width, tile height) by density; density comes from the account count
DENSITY = {"large": (160, 120), "medium": (128, 108), "compact": (112, 102)}
RARE_PX = {"large": 24, "medium": 22, "compact": 20}  # the rare tile's biome name, by density
GAP = 6
MERCHANT_SHOW_S = 30 * 60  # ponytail: a merchant tag older than 30 min is noise; tune here

CATEGORIES = ["Biomes", "Merchants", "Items", "Accounts", "Webhooks", "Anti-AFK", "Chat", "Action rows",
              "Hardware", "Discord", "Manager"]
SOURCE_WORD = {"Biomes": "Biome", "Merchants": "Merchant", "Items": "Item", "Accounts": "Account",
               "Webhooks": "Webhook", "Action rows": "Action row"}
LOG_CAP = 5000
CAT_ROLE = Qt.ItemDataRole.UserRole + 1

# Page ids of the other builders' pages (prototype ids), used by the Automations aside and the attention strip.
PAGE_IDS = {"afk": "afk", "scbr": "scbr", "rows": "rows", "ocr": "ocr", "bes": "bes", "discord": "discord",
            "settings": "settings", "accounts": "accounts"}


# ---------------------------------------------------------------- log classification

_TAG_RE = re.compile(r"^\[([^\]]{1,40})\]\s*")
_TIME_RE = re.compile(r"^\[(\d{2}:\d{2}:\d{2})\]\s?(.*)$", re.S)
_ITEM_RE = re.compile(r"strange controller|biome randomizer|item used|item-used", re.I)
_MERCHANT_RE = re.compile(r"\b(merchant|jester|mari|rin|sinister)\b", re.I)
_ACCOUNT_RE = re.compile(r"\b(launch\w*|restart\w*|offline|captcha|cookie|handoff|spare|relaunch\w*|disconnect\w*)\b", re.I)
_TAGS = {
    "webhook": "Webhooks", "discord control": "Discord", "discord": "Discord",
    "anti-afk": "Anti-AFK", "antiafk": "Anti-AFK",
    "auto-actions": "Action rows", "auto actions": "Action rows", "auto-actions ocr": "Action rows",
    "auto-item": "Items", "auto sc + br": "Items",
    "ocr": "Chat", "ocr text": "Chat", "ocr->foundstats": "Merchants", "merchantfix": "Merchants", "merchant": "Merchants",
    "bes": "Hardware", "trimmer": "Hardware", "ram": "Hardware", "cpu": "Hardware",
    "manuallaunch": "Accounts", "manualrestart": "Accounts", "cap": "Accounts", "cookie": "Accounts",
    "browser": "Accounts", "disconnect": "Accounts", "dedup": "Accounts", "pid dead": "Accounts",
    "windowcheck": "Accounts", "relaunch": "Accounts", "serverlabel": "Accounts", "tempblock": "Accounts",
    "rampup": "Accounts", "verification": "Accounts", "private server link": "Accounts", "scan-trace": "Accounts",
    "pools": "Accounts", "resume": "Accounts", "sync": "Accounts", "popup": "Accounts",
}


_DIGITS_RE = re.compile(r"(\d+)")


def natural_key(name: str) -> list:
    """Number-aware sort key: alt9 < alt10 < alt100 (plain text puts alt100 before alt11)."""
    return [int(t) if t.isdigit() else t.lower() for t in _DIGITS_RE.split(str(name or ""))]


def classify(msg: str) -> str:
    """Activity chip for one main-log line (message without the time stamp)."""
    m = _TAG_RE.match(msg)
    tag = m.group(1).strip().lower() if m else ""
    if _ITEM_RE.search(msg):
        return "Items"
    if tag in _TAGS:
        return _TAGS[tag]
    if tag.startswith(("launch", "pidwait", "winpos")):
        return "Accounts"
    if _MERCHANT_RE.search(msg):
        return "Merchants"
    if "biome" in msg.lower():
        return "Biomes"
    if "webhook" in msg.lower() or "rate limit" in msg.lower():
        return "Webhooks"
    if _ACCOUNT_RE.search(msg):
        return "Accounts"
    return "Manager"


def _entry(line: str, category: Optional[str] = None) -> tuple:
    line = str(line or "")
    m = _TIME_RE.match(line)
    t, msg = (m.group(1), m.group(2)) if m else (datetime.now().strftime("%H:%M:%S"), line)
    raw = line if m else f"[{t}] {line}"
    return (t, category or classify(msg), msg, raw)


class ActivityModel(QAbstractListModel):
    """Newest first. Holds at most LOG_CAP entries (t, category, message, raw line)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._d: deque = deque(maxlen=LOG_CAP)

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self._d)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid() or idx.row() >= len(self._d):
            return None
        e = self._d[-1 - idx.row()]
        if role == Qt.ItemDataRole.UserRole:
            return e
        if role == CAT_ROLE:
            return e[1]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.AccessibleTextRole):
            return f"{e[0]} {SOURCE_WORD.get(e[1], e[1])}: {e[2]}"
        if role == Qt.ItemDataRole.ToolTipRole:
            return e[2]
        return None

    def add(self, entries: List[tuple]) -> None:
        entries = list(entries)[-LOG_CAP:]
        if not entries:
            return
        over = len(self._d) + len(entries) - LOG_CAP
        if over > 0:
            n = len(self._d)
            self.beginRemoveRows(QModelIndex(), n - over, n - 1)
            for _ in range(over):
                self._d.popleft()
            self.endRemoveRows()
        self.beginInsertRows(QModelIndex(), 0, len(entries) - 1)
        self._d.extend(entries)
        self.endInsertRows()

    def add_lines(self, lines, category: Optional[str] = None) -> None:
        self.add([_entry(s, category) for s in lines])

    def clear(self) -> None:
        self.beginResetModel()
        self._d.clear()
        self.endResetModel()

    def lines(self) -> List[str]:
        return [e[3] for e in self._d]


class _LogDelegate(QStyledItemDelegate):
    def __init__(self, compact: bool, parent=None):
        super().__init__(parent)
        self.compact = compact

    def sizeHint(self, _opt, _idx):  # noqa: N802
        return QSize(100, 36 if self.compact else 40)

    def paint(self, p: QPainter, opt, idx):
        e = idx.data(Qt.ItemDataRole.UserRole)
        if not e:
            return
        t, cat, msg, _raw = e
        src = SOURCE_WORD.get(cat, cat)
        m = _TAG_RE.match(msg)
        if m and m.group(1).lower().startswith(src.lower()):  # "[Webhook] x" under the Webhook column -> "x"
            msg = msg[m.end():]
        r = opt.rect
        p.save()
        p.setPen(QColor(T["rule2"]))
        p.drawLine(r.bottomLeft(), r.bottomRight())
        f = QFont(opt.font)
        f.setPixelSize(14)
        bold = QFont(f)
        bold.setWeight(QFont.Weight.DemiBold)
        fm, bfm = QFontMetrics(f), QFontMetrics(bold)
        pad = 0 if self.compact else 14
        x = r.left() + pad
        time_w = 48 if self.compact else 72
        p.setFont(f)
        p.setPen(QColor(T["ink2"]))
        p.drawText(QRect(x, r.top(), time_w, r.height()), Qt.AlignmentFlag.AlignVCenter, t[:5] if self.compact else t)
        x += time_w
        p.setFont(bold)
        p.setPen(QColor(T["ink"]))
        src_w = bfm.horizontalAdvance(src) + 8 if self.compact else 104
        p.drawText(QRect(x, r.top(), src_w, r.height()), Qt.AlignmentFlag.AlignVCenter, bfm.elidedText(src, Qt.TextElideMode.ElideRight, src_w - 6))
        x += src_w
        p.setFont(f)
        w = r.right() - pad - x
        p.drawText(QRect(x, r.top(), w, r.height()), Qt.AlignmentFlag.AlignVCenter,
                   fm.elidedText(msg.replace("\n", " "), Qt.TextElideMode.ElideRight, w))
        p.restore()


# ---------------------------------------------------------------- small layout helper

class _Flow(QLayout):
    """Left-to-right layout that wraps (chips, attention items)."""

    def __init__(self, parent=None, spacing: int = 6):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):  # noqa: N802
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):  # noqa: N802
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):  # noqa: N802
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self):  # noqa: N802
        return True

    def heightForWidth(self, w):  # noqa: N802
        m = self.contentsMargins()
        return self._do(QRect(0, 0, w, 0), True) + m.top() + m.bottom()

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._do(rect, False)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        m = self.contentsMargins()
        return s + QSize(m.left() + m.right(), m.top() + m.bottom())

    def _do(self, rect, test):
        m = self.contentsMargins()
        r = rect.adjusted(m.left(), m.top(), -m.right(), -m.bottom())
        x, y, line, sp = r.x(), r.y(), 0, self.spacing()
        for it in self._items:
            if it.isEmpty():
                continue
            h = it.sizeHint()
            if x + h.width() > r.right() + 1 and line > 0:
                x, y, line = r.x(), y + line + sp, 0
            if not test:
                it.setGeometry(QRect(QPoint(x, y), h))
            x += h.width() + sp
            line = max(line, h.height())
        return y + line - r.y()

    def clear(self):
        while self._items:
            w = self._items.pop().widget()
            if w is not None:
                w.hide()
                w.deleteLater()


def _flow_box(spacing=6, margins=(0, 0, 0, 0)):
    w = QWidget()
    w.setObjectName("clear")
    lay = _Flow(w, spacing)
    lay.setContentsMargins(*margins)
    return w, lay


# ---------------------------------------------------------------- tile wall

def _text_on(c: QColor) -> QColor:
    def lum(col):
        out = []
        for v in (col.redF(), col.greenF(), col.blueF()):
            out.append(v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4)
        return 0.2126 * out[0] + 0.7152 * out[1] + 0.0722 * out[2]
    L = lum(c)
    return QColor(TILE_INK) if (L + 0.05) / 0.0651 >= 1.05 / (L + 0.05) else QColor("#FFFFFF")


def _dense(items: List[dict], cols: int) -> List[dict]:
    """Like CSS grid-auto-flow: dense. A 2-wide rare tile that does not fit at the end of a row lets the next
    1-wide tile go first, so rows have no holes."""
    out, pending, col = [], deque(items), 0
    while pending:
        span = 2 if (pending[0]["rare"] and cols >= 2) else 1
        if col + span <= cols:
            it = pending.popleft()
        else:
            j = next((k for k, x in enumerate(pending) if not x["rare"]), None)
            if j is None:
                col = 0
                continue
            it, span = pending[j], 1
            del pending[j]
        out.append(it)
        col = (col + span) % cols
    return out


def _fmt_left(s: int) -> str:
    return f"{s // 60}m {s % 60:02d}s left" if s >= 60 else f"{s}s left"


class TileModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items: List[dict] = []

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.items)

    def data(self, idx, role=Qt.ItemDataRole.DisplayRole):
        if not idx.isValid() or idx.row() >= len(self.items):
            return None
        it = self.items[idx.row()]
        if role == Qt.ItemDataRole.UserRole:
            return it
        if role == Qt.ItemDataRole.DisplayRole:
            return it["name"]
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{it['name']}, {it['label']}. Open account"
        return None

    def set_items(self, items: List[dict]) -> None:
        if [(i["uid"], i["rare"]) for i in items] == [(i["uid"], i["rare"]) for i in self.items]:
            self.items = items
            if items:
                self.dataChanged.emit(self.index(0), self.index(len(items) - 1))
            return
        self.beginResetModel()
        self.items = items
        self.endResetModel()


class _TileDelegate(QStyledItemDelegate):
    def __init__(self, wall: "_Wall"):
        super().__init__(wall)
        self.wall = wall
        self._need_cache = {}

    def _need(self, font, dens: str) -> int:
        """Height the text lines need with the real font (the widest case: a rare tile), so nothing is cut off."""
        key = (dens, font.family(), font.pointSizeF())
        if key not in self._need_cache:
            h = 10 + 8 + 8  # top pad, the time bar, bottom pad
            for px, gap in ((13, 2), (RARE_PX[dens], 2), (12, 2), (11, 0)):
                f = QFont(font)
                f.setPixelSize(px)
                f.setWeight(QFont.Weight.Bold)
                h += QFontMetrics(f).height() + gap
            self._need_cache[key] = h
        return self._need_cache[key]

    def sizeHint(self, opt, idx):  # noqa: N802
        it = idx.data(Qt.ItemDataRole.UserRole) or {}
        w = self.wall.tile_w
        dens = self.wall.density
        return QSize(w * 2 + self.wall.spacing() if it.get("rare") else w, max(DENSITY[dens][1], self._need(opt.font, dens)))

    def paint(self, p: QPainter, opt, idx):
        it = idx.data(Qt.ItemDataRole.UserRole)
        if not it:
            return
        dens = self.wall.density
        r = QRectF(opt.rect)
        p.save()
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        if it["kind"] == "bad":
            fg = QColor(T["warn"])
            p.setBrush(QColor(T["sheet"]))
            p.setPen(QPen(fg, 2, Qt.PenStyle.DashLine))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)
        else:
            bg = it["color"] or QColor(NORMAL_BG)
            fg = it["fg"] or QColor(T["ink2"])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(r, 8, 8)
        if opt.state & (QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus):
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor(T["focus"]), 2))
            p.drawRoundedRect(r.adjusted(1, 1, -1, -1), 8, 8)

        x, y, w = r.left() + 12, r.top() + 10, r.width() - 24

        def line(text, px, weight, gap=2):
            nonlocal y
            f = QFont(opt.font)
            f.setPixelSize(px)
            f.setWeight(weight)
            fm = QFontMetrics(f)
            p.setFont(f)
            p.setPen(fg)
            p.drawText(QRectF(x, y, w, fm.height()), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       fm.elidedText(text, Qt.TextElideMode.ElideRight, int(w)))
            y += fm.height() + gap

        line(it["name"], 13, QFont.Weight.Bold)
        line(it["label"], RARE_PX[dens] if it["rare"] else 14 if dens == "compact" else 16, QFont.Weight.Bold)
        line(_fmt_left(it["left"]) if it["left"] else "", 12, QFont.Weight.Medium)
        if it["dur"]:
            dark = fg.name().lower() == TILE_INK.lower()
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(24, 33, 43, 56) if dark else QColor(255, 255, 255, 77))
            p.drawRoundedRect(QRectF(x, y + 1, w, 4), 2, 2)
            p.setBrush(fg)
            p.drawRoundedRect(QRectF(x, y + 1, w * min(1.0, it["left"] / it["dur"]), 4), 2, 2)
        y += 8
        extra = it["merchant"]
        if extra and dens == "compact":
            extra = extra.split(" ")[0]
        line(extra, 11, QFont.Weight.DemiBold)
        p.restore()


class _Wall(QListView):
    """One tile per account; tile width fills the row like a CSS auto-fill grid."""

    def __init__(self):
        super().__init__()
        self.density = "large"
        self.tile_w = DENSITY["large"][0]
        self.cols = 1
        self.on_cols = None  # called when the column count changes (the page re-packs the order)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(False)
        self.setSpacing(GAP // 2)
        self.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.setMouseTracking(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet("QListView { background: transparent; border: 0; }")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setItemDelegate(_TileDelegate(self))

    def set_density(self, density: str) -> None:
        if density != self.density:
            self.density = density
            self._relayout()

    def _relayout(self) -> None:
        min_w = DENSITY[self.density][0]
        s = self.spacing()
        vw = max(min_w, self.viewport().width() - 2)
        cols = max(1, (vw - s) // (min_w + 2 * s))
        # QListView puts `spacing` around every item: cols * (w + 2s) <= vw
        self.tile_w = max(min_w, vw // cols - 2 * s)
        if cols != self.cols:
            self.cols = cols
            if self.on_cols:
                self.on_cols()
        self.scheduleDelayedItemsLayout()

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        self._relayout()

    def keyPressEvent(self, e):  # noqa: N802
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space) and self.currentIndex().isValid():
            self.clicked.emit(self.currentIndex())
            return
        super().keyPressEvent(e)


# ---------------------------------------------------------------- helpers shared with REPLACES

def _running(win) -> bool:
    wt = getattr(win, "worker_thread", None)
    try:
        return bool(wt is not None and wt.isRunning())
    except Exception:
        return False


def _shown(page) -> bool:
    try:
        return bool(page is not None and page.isVisible() and not page.window().isMinimized())
    except Exception:
        return False


def _go_page(shell, page_id: str) -> bool:
    stack = shell.stack
    for i in range(stack.count()):
        w = stack.widget(i)
        if getattr(w, "page_id", None) == page_id:
            shell.go(w)
            return True
    return False


def _btn(win, name) -> Optional[QAbstractButton]:
    w = getattr(win, name, None)
    return w if isinstance(w, QAbstractButton) else None


def _ensure_debug_toggles(win) -> None:
    """The log-view filters add_log reads. Session only (never saved), like the old Logs tab."""
    if not isinstance(getattr(win, "watch_hit_chk", None), K.Switch):
        win.watch_hit_chk = K.Switch(False)
        win.scan_trace_chk = win.watch_hit_chk  # old alias: one switch drives both filters
    if not isinstance(getattr(win, "multiscope_biome_chk", None), K.Switch):
        win.multiscope_biome_chk = K.Switch(False)
    if not isinstance(getattr(win, "launch_debug_chk", None), K.Switch):
        win.launch_debug_chk = K.Switch(False)


def troubleshooting_rows(win, section) -> None:
    """Settings > Troubleshooting calls this to place the log-view switches (not saved)."""
    _ensure_debug_toggles(win)
    section.row("Log detection hits", "Watch hits and scan traces. Off again after a restart.", win.watch_hit_chk)
    section.row("Log every biome read", "Each biome read and each skipped webhook. Off again after a restart.",
                win.multiscope_biome_chk)
    section.row("Log launch steps", "Launch, process and window checks. Off again after a restart.",
                win.launch_debug_chk)


# ---------------------------------------------------------------- Overview page

class OverviewPage(K.Page):
    def __init__(self, win, shell):
        super().__init__("overview", "Overview", "", shell.search)
        self.win, self.shell = win, shell
        self.rows: list = []
        self.rows_ts = 0.0
        self.biome_filter: Optional[str] = None
        self.sort = "rare"
        self._chip_sig = None
        self._attn_sig = None
        self._ticks = 0
        self.activity_page = None

        body = QWidget()
        body.setObjectName("clear")
        hb = QHBoxLayout(body)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(28)

        left = QVBoxLayout()
        left.setSpacing(12)
        self.attn = QFrame()
        self.attn.setObjectName("attn")
        self.attn.setStyleSheet(f"QFrame#attn {{ border: 2px dashed {T['warn']}; border-radius: 8px; background: transparent; }}")
        al = QVBoxLayout(self.attn)
        al.setContentsMargins(12, 10, 12, 10)
        self.attn_box, self.attn_flow = _flow_box(8)
        al.addWidget(self.attn_box)
        self.attn.setVisible(False)
        left.addWidget(self.attn)

        self.chip_box, self.chip_flow = _flow_box(6)
        left.addWidget(self.chip_box)

        self.wall_title = K.label("", "h2")
        self.sort_seg = K.Segmented(["Rare first", "Name"], "Rare first")
        self.sort_seg.changed.connect(self._on_sort)
        left.addWidget(K.hbox(self.wall_title, None, self.sort_seg))
        self.empty = K.label("No accounts yet. Add one on the Accounts page.", "help", wrap=True)
        self.empty.setVisible(False)
        left.addWidget(self.empty, 1, Qt.AlignmentFlag.AlignTop)  # takes the wall's space while there are no accounts

        self.tiles = TileModel(self)
        self.wall = _Wall()
        self.wall.setModel(self.tiles)
        self.wall.clicked.connect(self._open_tile)
        self.wall.on_cols = self.refresh
        left.addWidget(self.wall, 1)
        hb.addLayout(left, 1)

        aside = QWidget()
        aside.setObjectName("clear")
        av = QVBoxLayout(aside)
        av.setContentsMargins(0, 0, 12, 0)
        av.setSpacing(24)
        av.addWidget(self._build_autos())
        av.addWidget(self._build_recent())
        av.addWidget(self._build_found())
        av.addStretch(1)
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.Shape.NoFrame)
        sc.setWidget(aside)
        sc.setFixedWidth(360)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        hb.addWidget(sc)

        self.custom(body, 1)
        # no sections: give the custom area the page's spare height instead of the empty section stack
        self.layout().setStretch(0, 1)
        self.layout().setStretch(2, 0)

    # ---- aside
    def _build_autos(self) -> QWidget:
        w = QWidget()
        w.setObjectName("clear")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(K.label("Automations", "h2"))
        v.addSpacing(8)
        win = self.win
        self.autos = []

        def chk(name):
            return lambda: _btn(win, name)

        def set_chk(name):
            def _set(on):
                b = _btn(win, name)
                if b is not None and b.isChecked() != on:
                    b.setChecked(on)
            return _set

        def afk_state(on):
            if not on:
                return "Off"
            spin = getattr(win, "antiafk_interval_spin", None)
            try:
                s = int(spin.value())
                return f"Every {s // 60} min" if s % 60 == 0 else f"Every {s} s"
            except Exception:
                return "On"

        def scbr_targets():
            return [b for b in (_btn(win, "sol_sc_chk"), _btn(win, "sol_br_chk")) if b is not None]

        def scbr_state(_on):
            sc, br = _btn(win, "sol_sc_chk"), _btn(win, "sol_br_chk")
            parts = [n for n, b in (("SC", sc), ("BR", br)) if b is not None and b.isChecked()]
            return " and ".join(parts) + " on" if parts else "Off"

        def hw_targets():
            trim = getattr(getattr(win, "trimmer_tab", None), "enabled_chk", None)
            return [b for b in (_btn(win, "bes_enable_chk"), trim if isinstance(trim, QAbstractButton) else None) if b is not None]

        def hw_state(_on):
            t = hw_targets()
            cpu = bool(_btn(win, "bes_enable_chk") and _btn(win, "bes_enable_chk").isChecked())
            ram = any(b.isChecked() for b in t if b is not _btn(win, "bes_enable_chk"))
            return {(True, True): "CPU limited, RAM trimmed", (True, False): "CPU limited",
                    (False, True): "RAM trimmed", (False, False): "Off"}[(cpu, ram)]

        def many_get(targets):
            def _get():
                t = targets()
                return t or None
            return _get

        def many_set(targets, page_key):
            def _set(on):
                if on:  # which one to turn on is a choice: open the page
                    _go_page(self.shell, PAGE_IDS[page_key])
                    return
                for b in targets():
                    if b.isChecked():
                        b.setChecked(False)
            return _set

        def bot():
            return getattr(win, "discord_control_service", None)

        def bot_set(on):
            fn = getattr(win, "_start_discord_control_bot_from_ui" if on else "_stop_discord_control_bot_from_ui", None)
            if callable(fn):
                fn()

        def bot_state(on):
            lbl = getattr(win, "discord_control_status_label", None)
            txt = lbl.text().strip() if isinstance(lbl, QLabel) else ""
            return txt or ("Running" if on else "Stopped")

        def single(name):  # (get target, is_on)
            return chk(name), lambda b: b.isChecked()

        specs = [
            ("Anti-AFK", "afk", *single("antiafk_enable_chk"), set_chk("antiafk_enable_chk"), afk_state),
            ("Auto SC + BR", "scbr", many_get(scbr_targets), lambda t: any(b.isChecked() for b in t),
             many_set(scbr_targets, "scbr"), scbr_state),
            ("Action rows", "rows", *single("auto_item_enable_chk"), set_chk("auto_item_enable_chk"),
             lambda on: "On" if on else "Off"),
            ("Merchant detection", "ocr", *single("ocr_enable_chk"), set_chk("ocr_enable_chk"),
             lambda on: "Logs and chat" if on else "Logs only"),
            ("Hardware limiting", "bes", many_get(hw_targets), lambda t: any(b.isChecked() for b in t),
             many_set(hw_targets, "bes"), hw_state),
            ("Discord bot", "discord", bot, lambda s: bool(s.is_running()), bot_set, bot_state),
        ]
        self.auto_specs = {}  # page key -> (get, is_on, state): the sidebar shows the same state text
        for name, key, get, is_on, setter, state in specs:
            sw = K.Switch(False)
            st = K.label("", "help")
            fr = QFrame()
            fr.setProperty("role", "row")
            h = QHBoxLayout(fr)
            h.setContentsMargins(0, 10, 0, 10)
            h.setSpacing(14)
            h.addWidget(sw)
            col = QVBoxLayout()
            col.setSpacing(2)
            col.addWidget(K.label(name, "label"))
            col.addWidget(st)
            h.addLayout(col, 1)
            h.addWidget(K.button("Open", lambda k=key: _go_page(self.shell, PAGE_IDS[k]), size="sm"))
            v.addWidget(fr)
            sw.clicked.connect(lambda on, s=setter: (s(bool(on)), QTimer.singleShot(0, self.refresh_autos)))
            self.autos.append((sw, st, get, is_on, state))
            self.auto_specs[key] = (get, is_on, state)
        return w

    def refresh_autos(self) -> None:
        for sw, st, get, is_on, state in self.autos:
            try:
                target = get()
                on = bool(target is not None and is_on(target))
                text = state(on) if target is not None else "Not available"
            except Exception:
                on, text = False, "Not available"
            sw.setEnabled(target is not None)
            if sw.isChecked() != on:
                sw.setChecked(on)
            if st.text() != text:
                st.setText(text)

    def _build_recent(self) -> QWidget:
        w = QWidget()
        w.setObjectName("clear")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        self.full_log_btn = K.button("Full log", lambda: self.activity_page and self.shell.go(self.activity_page), size="sm")
        v.addWidget(K.hbox(K.label("Activity", "h2"), None, self.full_log_btn))
        self.recent = QListView()
        self.recent.setModel(self.win.activity_model)
        self.recent.setItemDelegate(_LogDelegate(True, self.recent))
        self.recent.setUniformItemSizes(True)
        self.recent.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.recent.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.recent.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.recent.setFrameShape(QFrame.Shape.NoFrame)
        self.recent.setStyleSheet(f"QListView {{ background: transparent; border: 0; border-top: 1px solid {T['rule2']}; }}")
        v.addWidget(self.recent)
        self.recent_empty = K.label("Nothing logged yet.", "help")
        v.addWidget(self.recent_empty)
        m = self.win.activity_model
        for sig in (m.rowsInserted, m.rowsRemoved, m.modelReset):
            sig.connect(self._recent_changed)
        self._recent_changed()
        return w

    def _recent_changed(self, *_a) -> None:
        n = min(6, self.win.activity_model.rowCount())  # the last 6 entries
        self.recent_empty.setVisible(n == 0)
        self.recent.setVisible(n > 0)
        self.recent.setFixedHeight(n * 36 + 2)
        self.recent.scrollToTop()

    def _build_found(self) -> QWidget:
        w = QWidget()
        w.setObjectName("clear")
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(8)
        v.addWidget(K.label("All-time found", "h2"))
        self.win._extras_found_counter_action = K.label("", "lead", wrap=True)
        v.addWidget(self.win._extras_found_counter_action)
        v.addWidget(K.hbox(K.button("Found stats", lambda: self.win.show_found_stats_window()), stretch_end=True))
        return w

    # ---- wall
    def _on_sort(self, text: str) -> None:
        self.sort = "rare" if text == "Rare first" else "name"
        self.refresh()

    def _open_tile(self, idx) -> None:
        it = idx.data(Qt.ItemDataRole.UserRole)
        if it:
            self._open_account(it["uid"])

    def _open_account(self, uid: str) -> None:
        hook = getattr(self.win, "_open_account_page", None)
        if callable(hook):
            hook(uid)
        else:
            _go_page(self.shell, PAGE_IDS["accounts"])

    def set_rows(self, rows, ts: float) -> None:
        self.rows = list(rows or [])
        self.rows_ts = float(ts or time.time())

    def _tiles(self):
        win = self.win
        try:
            users = win.config_manager.peek_users() or {}
        except Exception:
            users = {}
        runtime = getattr(win, "user_data", None) or {}
        running = _running(win)
        now = time.time()
        elapsed = max(0.0, now - self.rows_ts) if self.rows_ts else 0.0
        ms = {}
        for row in self.rows:
            if not isinstance(row, dict):
                continue
            uids = [str(u).strip() for u in (row.get("users") or []) if str(u).strip()]
            per_uid = row.get("in_menu_by_uid") if isinstance(row.get("in_menu_by_uid"), dict) else {}
            for uid in uids:
                in_menu = per_uid.get(uid) if uid in per_uid else (row.get("in_menu") if len(uids) == 1 else None)
                ms[uid] = (row, in_menu)
        items, procs, active, pending = [], 0, 0, 0
        for uid, info in users.items():
            uid = str(uid)
            info = info if isinstance(info, dict) else {}
            rt = runtime.get(uid) or {}
            raw = str(rt.get("status", "") or "")
            pids = rt.get("pids") or []
            if running:  # user_data keeps the last states after a stop
                procs += len(pids) if isinstance(pids, (list, tuple)) else 1
                pending += bool(rt.get("needs_restart"))
            it = {"uid": uid, "name": str(info.get("username") or uid), "label": "", "kind": "muted",
                  "color": None, "fg": None, "left": 0, "dur": 0, "merchant": "", "rare": False, "biome": "",
                  "attention": ""}
            if info.get("cap"):
                it.update(label="Captcha", kind="bad", attention="needs a captcha")
            elif info.get("bad"):
                it.update(label="Bad cookie", kind="bad", attention="needs a new cookie")
            elif info.get("backup"):  # with-solver: reserve account
                it.update(label="Backup")
            elif info.get("disabled"):
                it.update(label="Disabled")
            elif not running:
                it.update(label="Stopped")
            elif raw.startswith("Standby"):
                it.update(label="Spare")
            elif "Restarting" in raw:
                it.update(label="Restarting")
            elif "Active" not in raw or rt.get("needs_restart"):
                it.update(label="Offline", kind="bad", attention="offline")
            else:
                row, in_menu = ms.get(uid, ({}, None))
                active += not in_menu  # same count as the Accounts "In game" chip
                biome = str(row.get("last_biome", row.get("biome", "")) or "").strip().upper()
                if in_menu:
                    it.update(label="In menu")
                elif biome and biome != "NORMAL":
                    color_int, _thumb = biome_meta(biome)
                    c = QColor(f"#{int(color_int) & 0xFFFFFF:06x}")
                    dur = biome_duration(biome) or 0
                    age = row.get("biome_age")
                    left = int(max(0, dur - (float(age) + elapsed))) if (dur and age is not None) else 0
                    it.update(label=biome, kind="biome", color=c, fg=_text_on(c), rare=biome in RARE, biome=biome,
                              left=left, dur=dur if left else 0)
                else:
                    it.update(label="Normal")
                merchant = str(row.get("last_merchant", row.get("merchant", "")) or "").strip().title()
                m_age = row.get("merchant_age")
                if merchant and m_age is not None:
                    m_age = int(float(m_age) + elapsed)
                    if m_age < MERCHANT_SHOW_S:
                        it["merchant"] = f"{merchant} {m_age // 60} min ago" if m_age >= 60 else f"{merchant} just now"
            items.append(it)
        items.sort(key=lambda it: natural_key(it["name"]))  # the attention strip and Restart these follow this order
        return items, {"total": len(users), "active": active, "procs": procs, "pending": pending, "running": running}

    def refresh(self) -> None:
        items, stats = self._tiles()
        total = stats["total"]
        self.wall.set_density("large" if total <= 24 else "medium" if total <= 60 else "compact")

        counts = {}
        for it in items:
            if it["biome"]:
                counts[it["biome"]] = counts.get(it["biome"], 0) + 1
        if self.biome_filter and self.biome_filter not in counts:
            self.biome_filter = None
        self._set_chips(total, counts)

        shown = [it for it in items if not self.biome_filter or it["biome"] == self.biome_filter]
        if self.sort == "rare":
            def rank(it):
                return 0 if it["kind"] == "bad" else 1 if it["rare"] else 2 if it["biome"] else 3
            shown.sort(key=rank)  # stable: names stay in natural order within a rank
        self.tiles.set_items(_dense(shown, self.wall.cols))
        self.wall_title.setText(f"{len(shown)} in {self.biome_filter}" if self.biome_filter else f"All {total} accounts")
        self.empty.setVisible(total == 0)
        self.chip_box.setVisible(total > 0)
        self.wall.setVisible(total > 0)

        self._set_attention(items, stats)

        rare_up = sorted({it["biome"] for it in items if it["rare"]})
        lead = f"{stats['active']} of {total} accounts are in game, {stats['procs']} Roblox processes." if total else ""
        if rare_up:
            lead += " " + (" and ".join(rare_up) if len(rare_up) <= 2 else ", ".join(rare_up)) + \
                    (" is" if len(rare_up) == 1 else " are") + " up right now."
        if self.lead != lead:
            self.shell.set_lead(self, lead)
        self.refresh_autos()

    def _set_chips(self, total: int, counts: dict) -> None:
        order = sorted(counts, key=lambda b: (b not in RARE, counts[b], b))
        sig = (total, tuple((b, counts[b]) for b in order), self.biome_filter)
        if sig == self._chip_sig:
            return
        self._chip_sig = sig
        self.chip_flow.clear()
        for b, text in [(None, f"All {total}")] + [(b, f"{b} {counts[b]}") for b in order]:
            c = K.chip(text, checked=(b == self.biome_filter))
            c.clicked.connect(lambda _=False, b=b: self._pick_biome(b))
            self.chip_flow.addWidget(c)
        self.chip_box.updateGeometry()

    def _pick_biome(self, biome: Optional[str]) -> None:
        self.biome_filter = None if biome == self.biome_filter else biome
        self.refresh()

    def _set_attention(self, items, stats) -> None:
        win = self.win
        entries = [(f"{it['name']} {it['attention']}", ("acc", it["uid"])) for it in items if it["attention"]]
        try:
            cm = win.config_manager
            if cm.cookie_encryption_enabled() and not cm.is_cookie_unlocked():
                entries.append(("Cookies locked", ("page", "settings")))
        except Exception:
            pass
        if getattr(win, "_multi_instance_needs_repair", False):
            entries.append(("Many Roblox windows is not working", ("page", "settings")))
        if getattr(win, "_update_url", ""):
            entries.append(("Update available", ("update", "")))
        offline = [it["uid"] for it in items if it["attention"] == "offline"]
        sig = (tuple(e[0] for e in entries), bool(offline))
        if sig == self._attn_sig:
            return
        self._attn_sig = sig
        self.attn_flow.clear()
        self.attn.setVisible(bool(entries))
        if not entries:
            return
        self.attn_flow.addWidget(K.label("Needs attention", "warn"))
        shown = entries[:5]  # ponytail: 100 broken accounts would be 100 buttons; the wall shows them all anyway. Few, so the wall keeps its height at 1280x720
        for text, (kind, arg) in shown:
            def act(kind=kind, arg=arg):
                if kind == "acc":
                    self._open_account(arg)
                elif kind == "page":
                    _go_page(self.shell, PAGE_IDS[arg])
                else:
                    win._open_update_url()
            self.attn_flow.addWidget(K.button(text, act, size="sm"))
        if len(entries) > len(shown):
            self.attn_flow.addWidget(K.label(f"and {len(entries) - len(shown)} more", "help"))
        if offline:
            self.attn_flow.addWidget(K.button("Restart these", self._restart_offline, kind="ink", size="sm"))
        self.attn_box.updateGeometry()

    def _restart_offline(self) -> None:
        items, _stats = self._tiles()
        for it in items:
            if it["attention"] == "offline":
                self.win.restart_user_session(it["uid"])

    def showEvent(self, e):  # noqa: N802
        super().showEvent(e)
        win = self.win
        try:
            win._schedule_multiscope_table_refresh(immediate=True)
        except Exception:
            pass
        self.refresh()
        try:
            win._refresh_extras_found_counters()
        except Exception:
            pass


# ---------------------------------------------------------------- Activity log page

def _build_activity(win, shell) -> K.Page:
    page = K.Page("activity", "Activity log", "What the manager did, newest first.", shell.search)
    body = QWidget()
    body.setObjectName("clear")
    v = QVBoxLayout(body)
    v.setContentsMargins(0, 0, 0, 0)
    v.setSpacing(12)

    proxy = QSortFilterProxyModel(page)
    proxy.setSourceModel(win.activity_model)
    proxy.setFilterRole(CAT_ROLE)
    page.proxy = proxy

    chips_w, flow = _flow_box(6)
    group = QButtonGroup(page)
    group.setExclusive(True)
    page.chips = {}
    for name in ["All"] + CATEGORIES:
        c = K.chip(name, checked=(name == "All"))
        group.addButton(c)
        flow.addWidget(c)
        page.chips[name] = c

        def pick(on, name=name):
            if on:
                proxy.setFilterRegularExpression(
                    QRegularExpression("" if name == "All" else f"^{QRegularExpression.escape(name)}$"))
                _empty()
        c.toggled.connect(pick)
    top = QHBoxLayout()
    top.setSpacing(8)
    top.addWidget(chips_w, 1)
    top.addWidget(K.button("Save log", lambda: win.save_logs(), size="sm"), 0, Qt.AlignmentFlag.AlignTop)
    top.addWidget(K.button("Clear", lambda: win.clear_logs(), size="sm"), 0, Qt.AlignmentFlag.AlignTop)
    v.addLayout(top)

    view = QListView()
    view.setModel(proxy)
    view.setItemDelegate(_LogDelegate(False, view))
    view.setUniformItemSizes(True)
    view.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    win.activity_view = view
    v.addWidget(view, 1)
    empty = K.label("", "help", wrap=True)
    v.addWidget(empty, 1, Qt.AlignmentFlag.AlignTop)  # takes the list's space while it is hidden

    def _empty(*_a):
        empty.setText("Nothing logged yet. New activity shows up here as it happens."
                      if win.activity_model.rowCount() == 0 else "Nothing in this group yet.")
        empty.setVisible(proxy.rowCount() == 0)
        view.setVisible(proxy.rowCount() > 0)
    for sig in (proxy.rowsInserted, proxy.rowsRemoved, proxy.modelReset, proxy.layoutChanged):
        sig.connect(_empty)
    _empty()

    page.custom(body, 1)
    page.layout().setStretch(0, 1)
    page.layout().setStretch(2, 0)
    return page


# ---------------------------------------------------------------- build

def build(win, shell):
    win.activity_model = ActivityModel(win)
    _ensure_debug_toggles(win)
    overview = OverviewPage(win, shell)
    win._overview_page = overview
    activity = _build_activity(win, shell)
    overview.activity_page = activity
    return [("Overview", overview), ("Activity log", activity)]


# ---------------------------------------------------------------- REPLACES (the integrator moves these into gui.py)

def _schedule_multiscope_table_refresh(self, *, immediate: bool = False) -> None:
    rows = getattr(self, "_multiscope_table_latest_rows", None)
    if rows is not getattr(self, "_ov_rows_obj", None):  # update_multiscope stores a new list per update
        self._ov_rows_obj = rows
        self._ov_rows_ts = time.time()
    self._multiscope_table_dirty = True
    if self._multiscope_table_refresh_pending:
        return
    if not _shown(getattr(self, "_overview_page", None)):
        return
    self._multiscope_table_refresh_pending = True
    QTimer.singleShot(0 if immediate else 150, self._flush_multiscope_table_refresh)


def _flush_multiscope_table_refresh(self) -> None:
    self._multiscope_table_refresh_pending = False
    if not self._multiscope_table_dirty:
        return
    if not _shown(getattr(self, "_overview_page", None)):
        return
    rows = self._multiscope_table_latest_rows or []
    self._multiscope_table_dirty = False
    self._render_multiscope_table(rows)


def _render_multiscope_table(self, rows: list) -> None:
    page = getattr(self, "_overview_page", None)
    if page is None:
        return
    page.set_rows(rows, getattr(self, "_ov_rows_ts", 0.0) or time.time())
    page.refresh()


_SUBLOGS = (  # (queue, old box, old flush, activity chip)
    ("_ocr_log_queue", "ocr_log_box", "_flush_ocr_log_queue", "Chat"),
    ("_antiafk_log_queue", "antiafk_status_box", "_flush_antiafk_log_queue", "Anti-AFK"),
    ("_autoitem_log_queue", "autoitem_status_box", "_flush_autoitem_log_queue", "Action rows"),
    ("_bes_log_queue", "bes_log_box", "_flush_bes_log_queue", "Hardware"),
)


def _flush_log_queue(self) -> None:
    model = getattr(self, "activity_model", None)
    try:
        q = getattr(self, "_log_queue", None)
        if q and model is not None:
            batch = [q.popleft() for _ in range(min(250, len(q)))]
            model.add_lines(batch)
    except Exception:
        pass
    finally:
        # The page logs (OCR, Anti-AFK, Auto-Actions, BES) also land in the activity log. When a page still has its
        # own log box, its old flush pops the same <=250 lines we peek here; otherwise we drain the queue ourselves.
        for qname, box, flush, cat in _SUBLOGS:
            try:
                q = getattr(self, qname, None)
                if not q:
                    continue
                if getattr(self, box, None) is not None:
                    batch = list(islice(q, 250))
                    getattr(self, flush)()
                else:
                    batch = [q.popleft() for _ in range(min(250, len(q)))]
                if model is not None and batch:
                    if cat == "Action rows":
                        model.add([_entry(s, "Items" if _ITEM_RE.search(s) else cat) for s in batch])
                    else:
                        model.add_lines(batch, cat)
            except Exception:
                pass


def clear_logs(self):
    try:
        self._log_queue.clear()
        self._activity_recent.clear()
    except Exception:
        pass
    self._last_ocr_log = None  # a repeated chat line shows again after Clear
    model = getattr(self, "activity_model", None)
    if model is not None:
        model.clear()


def save_logs(self):
    try:
        try:
            self._flush_log_queue()
        except Exception:
            pass
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            base_dir = Path(self.config_manager.config_dir)
        except Exception:
            base_dir = Path.cwd()
        logs_dir = base_dir / "logs"
        try:
            logs_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        file_path = logs_dir / f"jaram_logs_{timestamp}.txt"

        def _box_text(box) -> str:
            if box is None:
                return ""
            try:
                return str(box.toPlainText() or "")
            except Exception:
                return ""

        model = getattr(self, "activity_model", None)
        sections = [
            ("Main Logs", "\n".join(model.lines()) if model is not None else ""),
            ("OCR Log", _box_text(getattr(self, "ocr_log_box", None))),
            ("Anti-AFK Log", _box_text(getattr(self, "antiafk_status_box", None))),
            ("Auto-Item Log", _box_text(getattr(self, "autoitem_status_box", None))),
            ("BES Log", _box_text(getattr(self, "bes_log_box", None))),
        ]
        with open(file_path, "w", encoding="utf-8") as f:
            f.write("JARAM Logs Export\n")
            f.write(f"Generated: {timestamp}\n\n")
            for title, body in sections:
                f.write(f"===== {title} =====\n")
                if body.strip():
                    f.write(body.rstrip() + "\n")
                f.write("\n")
        QMessageBox.information(self, "Log saved", f"Saved to {file_path}")
    except Exception as e:
        QMessageBox.critical(self, "Could not save the log", f"{e}")


def _refresh_extras_found_counters(self) -> None:
    lbl = getattr(self, "_extras_found_counter_action", None)
    if not lbl:
        return
    try:
        snap = self._get_found_stats_snapshot()
        b = int(snap.get("biomes_total_count", 0) or 0)
        m = int(snap.get("merchants_total_count", 0) or 0)
        lbl.setText(f"{b:,} biomes and {m:,} merchants")
    except Exception:
        pass


def update_ui(self):
    # Old dashboard labels are gone; the counts feed the hourly report and the Overview lead.
    user_data = self.user_data or {}
    active_users = sum(1 for data in user_data.values() if data.get('status') == 'Active')
    users_cfg = self.config_manager.peek_users()
    try:
        good_count = sum(1 for i in users_cfg.values()
                         if not (i.get("bad") or i.get("cap")) and not i.get("disabled") and not i.get("backup"))
    except Exception:
        good_count = 0
    self._maybe_send_hourly_users_report(good_count, active_users)
    if hasattr(self, "_maintain_account_count"):  # with-solver: Auto replace holds the healthy headcount
        self._maintain_account_count(good_count)
    try:
        if self.users_tab_index is not None and self.tab_widget.currentIndex() == self.users_tab_index:
            wt = getattr(self, "worker_thread", None)
            if wt is not None and wt.isRunning():
                self._refresh_users_antiafk_age_column()
    except Exception:
        pass
    try:
        if getattr(self, "accounts_tab_index", None) is not None and self.tab_widget.currentIndex() == self.accounts_tab_index:
            users_mtime = float(self.config_manager.get_users_mtime())
            if users_mtime and users_mtime != float(getattr(self, "_accounts_list_users_mtime", -1.0)):
                self.refresh_accounts_list()
    except Exception:
        pass
    try:
        self._check_multi_instance_process_state()
    except Exception:
        pass
    page = getattr(self, "_overview_page", None)
    if not self.isMinimized():
        try:
            from ui_pages import shell as _shell
            _shell.refresh_nav(self)
        except Exception:
            pass
    if _shown(page):  # only the visible page refreshes; nothing while minimised
        try:
            page.refresh()  # time left counts down, statuses, attention strip, automations
            page._ticks += 1
            if page._ticks % 15 == 0:
                self._refresh_extras_found_counters()
        except Exception:
            pass


REPLACES = {
    "_schedule_multiscope_table_refresh": _schedule_multiscope_table_refresh,
    "_flush_multiscope_table_refresh": _flush_multiscope_table_refresh,
    "_render_multiscope_table": _render_multiscope_table,
    "_flush_log_queue": _flush_log_queue,
    "clear_logs": clear_logs,
    "save_logs": save_logs,
    "_refresh_extras_found_counters": _refresh_extras_found_counters,
    "update_ui": update_ui,
}
