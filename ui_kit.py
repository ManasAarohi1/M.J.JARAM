"""Building blocks for the M.J.JARAM window: theme, sidebar, pages, setting rows, search, autosave.

Pages create their controls with the attribute names the app logic already reads (self.<name>), and lay
them out with Page / Section / row() from here. Nothing in this module knows about accounts or Roblox.
"""
from __future__ import annotations

import weakref
from typing import Callable, Dict, List, Optional, Tuple, TypeVar

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPoint, QPointF, QRect, QRectF, QSize, QStringListModel, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QCompleter, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QLayout, QLineEdit,
    QPushButton, QRadioButton, QScrollArea, QSizePolicy, QStackedWidget, QVBoxLayout, QWidget,
)

# Weather-station dark palette (same tokens as the prototype).
T = {
    "app": "#10161C", "sheet": "#161D25", "raised": "#1C252F", "rule": "#2C3742", "rule2": "#232D37",
    "ink": "#E6EBF0", "ink2": "#A9B4BF", "line": "#4B5866", "focus": "#8AB4FF", "warn": "#F4A26B",
    "chip": "#232D37", "sel": "#24303C", "track": "#3A4653", "knob": "#C9D1D9",
}
FONT = "'Segoe UI', 'Atkinson Hyperlegible Next', sans-serif"


def _icon_png(name: str, draw: Callable[[QPainter], None], size: int = 30) -> str:
    """Qt style sheets cannot draw a triangle or a tick, so those come from small PNGs in the temp folder (works in the one-file exe)."""
    import os
    import tempfile
    path = os.path.join(tempfile.gettempdir(), f"jaram_{name}_{T['ink2'][1:]}{T['app'][1:]}.png")
    try:
        if not os.path.exists(path):
            img = QImage(size, size, QImage.Format.Format_ARGB32)
            img.fill(0)
            p = QPainter(img)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            draw(p)
            p.end()
            img.save(path)
        return path.replace("\\", "/")
    except Exception:
        return ""


def _draw_arrow(p: QPainter) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(T["ink2"]))
    p.drawPolygon(QPolygonF([QPointF(7, 10), QPointF(23, 10), QPointF(15, 20)]))


def _draw_tick(p: QPainter) -> None:
    pen = QPen(QColor(T["app"]), 3.2)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.drawPolyline(QPolygonF([QPointF(7, 15.5), QPointF(12.5, 21), QPointF(23, 9)]))


def stylesheet() -> str:
    t = T
    def url(path: str) -> str:
        return 'url("%s")' % path if path else "none"
    arrow_css = url(_icon_png("arrow", _draw_arrow))
    tick_css = url(_icon_png("tick", _draw_tick))
    return f"""
    QWidget {{ background: {t['sheet']}; color: {t['ink']}; font-family: {FONT}; font-size: 14px; }}
    QMainWindow, QDialog {{ background: {t['sheet']}; }}
    QToolTip {{ background: {t['raised']}; color: {t['ink']}; border: 1px solid {t['line']}; padding: 4px; }}
    QLabel {{ background: transparent; }}
    QWidget#clear, QWidget#seg {{ background: transparent; }}
    QWidget#navlist, #sidebar QScrollArea {{ background: {t['app']}; }}
    QWidget#main {{ background: {t['sheet']}; }}
    QLabel[role="title"] {{ font-size: 28px; font-weight: 700; }}
    QLabel[role="lead"], QLabel[role="help"], QLabel[role="hint"] {{ color: {t['ink2']}; }}
    QLabel[role="lead"] {{ font-size: 15px; }}
    QLabel[role="help"], QLabel[role="hint"] {{ font-size: 13px; }}
    QLabel[role="label"] {{ font-size: 15px; font-weight: 600; }}
    QLabel[role="h2"] {{ font-size: 19px; font-weight: 700; }}
    QLabel[role="warn"] {{ color: {t['warn']}; font-weight: 600; }}

    QWidget#sidebar {{ background: {t['app']}; border-right: 1px solid {t['rule']}; }}
    QLabel[role="brand"] {{ font-size: 19px; font-weight: 700; }}
    QLabel[role="group"] {{ color: {t['ink2']}; font-size: 13px; font-weight: 600; padding: 6px 10px 2px; }}
    QPushButton[nav="true"] {{ text-align: left; border: 0; border-radius: 6px; padding: 0 10px; min-height: 38px;
        background: transparent; font-size: 15px; font-weight: 500; }}
    QPushButton[nav="true"]:hover, QPushButton[nav="true"]:checked {{ background: {t['raised']}; }}
    QPushButton[nav="true"]:checked {{ font-weight: 700; }}
    QLabel[role="navstate"] {{ color: {t['ink2']}; font-size: 13px; background: transparent; }}
    QLabel[role="navstate"][warn="true"] {{ color: {t['warn']}; }}

    QPushButton {{ min-height: 38px; padding: 0 14px; border-radius: 6px; border: 1px solid {t['line']};
        background: {t['raised']}; color: {t['ink']}; font-weight: 600; }}
    QPushButton:hover {{ border-color: {t['ink2']}; }}
    QPushButton:disabled {{ color: {t['line']}; border-color: {t['rule']}; }}
    QPushButton:focus {{ outline: none; border: 2px solid {t['focus']}; }}
    QPushButton[kind="ink"] {{ background: {t['ink']}; color: {t['app']}; border-color: {t['ink']}; }}
    QPushButton[kind="warn"] {{ color: {t['warn']}; }}
    QPushButton[kind="discord"] {{ background: #5865F2; color: #FFFFFF; border-color: #5865F2; }}
    QPushButton[kind="discord"]:hover {{ background: #4752C4; border-color: #4752C4; }}
    QPushButton[kind="ink"]:disabled {{ background: {t['raised']}; color: {t['line']}; border-color: {t['rule']}; }}
    QPushButton[size="sm"] {{ min-height: 32px; padding: 0 11px; font-size: 13px; }}
    QPushButton[chip="true"] {{ min-height: 30px; padding: 0 12px; border-radius: 15px; border-color: {t['rule']}; font-size: 13px; }}
    QPushButton[chip="true"]:checked {{ background: {t['ink']}; color: {t['app']}; border-color: {t['ink']}; }}

    QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox, QPlainTextEdit, QTextEdit, QKeySequenceEdit QLineEdit {{
        min-height: 36px; padding: 0 10px; border-radius: 6px; border: 1px solid {t['line']};
        background: {t['app']}; color: {t['ink']}; selection-background-color: {t['focus']}; selection-color: {t['app']}; }}
    QPlainTextEdit, QTextEdit {{ padding: 6px 8px; }}
    QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {{
        color: {t['line']}; background: transparent; border-color: {t['rule']}; }}
    QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border: 2px solid {t['focus']}; padding: 0 9px; }}
    QPlainTextEdit:focus, QTextEdit:focus {{ border: 2px solid {t['focus']}; padding: 5px 7px; }}
    QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{ width: 0; border: 0; }}
    QComboBox::drop-down {{ border: 0; width: 24px; }}
    QComboBox::down-arrow {{ image: {arrow_css}; width: 16px; height: 16px; margin-right: 4px; }}
    QComboBox QAbstractItemView {{ background: {t['raised']}; border: 1px solid {t['line']}; selection-background-color: {t['sel']}; }}

    QCheckBox {{ background: transparent; spacing: 8px; }}
    QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {t['line']}; border-radius: 4px; background: {t['app']}; }}
    QCheckBox::indicator:checked {{ background: {t['ink']}; border-color: {t['ink']}; image: {tick_css}; }}
    QCheckBox::indicator:disabled {{ border-color: {t['rule']}; background: transparent; }}
    QCheckBox::indicator:checked:disabled {{ background: {t['line']}; }}
    QAbstractItemView::indicator {{ width: 14px; height: 14px; border: 1px solid {t['ink2']}; border-radius: 4px; background: {t['app']}; }}
    QAbstractItemView::indicator:checked {{ background: {t['ink']}; border-color: {t['ink']}; image: {tick_css}; }}
    QAbstractItemView::indicator:indeterminate {{ background: {t['line']}; }}
    QRadioButton {{ background: transparent; }}

    QTabBar {{ background: transparent; }}
    QTabBar::tab {{ background: transparent; color: {t['ink2']}; padding: 0 14px; min-height: 40px; font-weight: 600;
        border: 0; border-bottom: 3px solid transparent; }}
    QTabBar::tab:selected {{ color: {t['ink']}; border-bottom-color: {t['ink']}; }}
    QTabBar::tab:hover {{ color: {t['ink']}; }}
    QPushButton[sectab="true"] {{ background: transparent; color: {t['ink2']}; border: 0; border-radius: 0;
        border-bottom: 3px solid transparent; padding: 0 14px; min-height: 38px; }}
    QPushButton[sectab="true"]:hover {{ color: {t['ink']}; }}
    QPushButton[sectab="true"]:checked {{ color: {t['ink']}; border-bottom-color: {t['ink']}; }}
    QPushButton[sectab="true"]:focus {{ border: 0; border-bottom: 3px solid {t['focus']}; }}
    QTabWidget::pane {{ border: 0; border-top: 1px solid {t['rule']}; }}

    QFrame[role="row"] {{ background: transparent; border: 0; border-top: 1px solid {t['rule2']}; }}
    QFrame[role="row"][hit="true"] {{ background: {t['raised']}; }}
    QFrame[role="card"] {{ background: {t['raised']}; border: 1px solid {t['rule']}; border-radius: 8px; }}
    QFrame[role="card"] QLabel {{ background: transparent; }}

    QTableView, QTableWidget, QListView, QListWidget, QTreeView, QTreeWidget {{ background: {t['sheet']};
        alternate-background-color: {t['sheet']}; border: 1px solid {t['rule']}; border-radius: 8px;
        gridline-color: {t['rule2']}; selection-background-color: {t['sel']}; selection-color: {t['ink']}; outline: 0; }}
    QHeaderView::section {{ background: {t['raised']}; color: {t['ink2']}; border: 0; border-bottom: 1px solid {t['rule']};
        padding: 6px 10px; font-weight: 700; font-size: 13px; }}
    QTableCornerButton::section {{ background: {t['raised']}; border: 0; }}

    QScrollArea {{ border: 0; background: transparent; }}
    QScrollArea > QWidget > QWidget {{ background: transparent; }}
    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 0; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
    QScrollBar::handle {{ background: {t['line']}; border-radius: 5px; min-height: 30px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: 0; }}

    QGroupBox {{ border: 1px solid {t['rule']}; border-radius: 8px; margin-top: 18px; padding: 12px 10px 10px; font-weight: 700; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 4px; color: {t['ink']}; }}
    QMenu {{ background: {t['raised']}; border: 1px solid {t['line']}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 4px; }}
    QMenu::item:selected {{ background: {t['sel']}; }}
    QMenuBar {{ background: {t['app']}; }}
    QSlider::groove:horizontal {{ height: 4px; background: {t['track']}; border-radius: 2px; }}
    QSlider::handle:horizontal {{ width: 16px; margin: -6px 0; border-radius: 8px; background: {t['ink']}; }}
    QProgressBar {{ border: 1px solid {t['rule']}; border-radius: 6px; background: {t['app']}; text-align: center; }}
    QProgressBar::chunk {{ background: {t['ink']}; border-radius: 5px; }}
    """


_W = TypeVar("_W", bound=QWidget)


def _prop(w: _W, **props) -> _W:
    for k, v in props.items():
        w.setProperty(k, v)
    return w


def label(text: str, role: str = "", wrap: bool = False) -> QLabel:
    lbl = QLabel(text)
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    return lbl


def button(text: str, on_click: Optional[Callable] = None, kind: str = "", size: str = "") -> QPushButton:
    b = QPushButton(text)
    if kind:
        b.setProperty("kind", kind)
    if size:
        b.setProperty("size", size)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_click is not None:
        b.clicked.connect(lambda _=False: on_click())
    return b


def chip(text: str, checked: bool = False, on_toggle: Optional[Callable[[bool], None]] = None) -> QPushButton:
    b = _prop(QPushButton(text), chip=True)
    b.setCheckable(True)
    b.setChecked(checked)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    if on_toggle is not None:
        b.toggled.connect(on_toggle)
    return b


def hbox(*widgets, spacing: int = 8, stretch_end: bool = False) -> QWidget:
    w = QWidget()
    w.setObjectName("clear")
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(spacing)
    for x in widgets:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, QWidget):
            lay.addWidget(x)
        else:
            lay.addLayout(x)
    if stretch_end:
        lay.addStretch(1)
    return w


class _KeyboardFocusOnly(QObject):
    """Buttons take the focus from the keyboard (Tab) only, so a mouse click does not leave a focus ring (and a 2px border) on."""

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Polish and isinstance(obj, (QPushButton, QCheckBox, QRadioButton))                 and obj.focusPolicy() == Qt.FocusPolicy.StrongFocus:
            obj.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        return False


class _WidePopup(QObject):
    """The search suggestions are 'Label  (Page > Section)': open the popup wider than the search box, right-aligned to it."""

    def __init__(self, box: QWidget, popup: QWidget, width: int = 460):
        super().__init__(popup)
        self._box, self._width = box, width
        popup.installEventFilter(self)

    def eventFilter(self, obj, event):  # noqa: N802
        # QCompleter re-sizes the popup to the box's width on every keystroke, shown or not, so fix it on resize/move too.
        if event.type() in (QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.Move) and obj.width() < self._width:
            right = self._box.mapToGlobal(QPoint(self._box.width(), 0)).x()
            obj.setGeometry(right - self._width, obj.y(), self._width, obj.height())
        return False


class Switch(QCheckBox):
    """An on/off switch. It is a QCheckBox, so isChecked / setChecked / toggled / stateChanged all work."""

    inverted = False  # True: drawn ON when unchecked (for settings stored the other way round)

    def __init__(self, checked: bool = False, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setChecked(checked)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)  # a mouse click must not leave the focus ring on
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self._pos = 1.0 if checked else 0.0  # knob: 0 = off, 1 = on
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(150)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self.toggled.connect(self._slide)

    def _step(self, v) -> None:
        self._pos = float(v)
        self.update()

    def _slide(self, on: bool) -> None:
        if not self.isVisible():  # loading settings into a hidden page: no animation
            self._anim.stop()
            self._step(1.0 if on else 0.0)
            return
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self) -> QSize:
        return QSize(44, 26)

    def hitButton(self, pos) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, 40, 22)
        t = 1.0 - self._pos if self.inverted else self._pos

        def mix(a: str, b: str) -> QColor:  # off colour -> on colour as the knob slides
            ca, cb = QColor(a), QColor(b)
            return QColor(round(ca.red() + (cb.red() - ca.red()) * t), round(ca.green() + (cb.green() - ca.green()) * t),
                          round(ca.blue() + (cb.blue() - ca.blue()) * t))
        p.setPen(QColor(T["focus"]) if self.hasFocus() else Qt.PenStyle.NoPen)
        p.setBrush(mix(T["track"], T["ink"]))
        if not self.isEnabled():
            p.setOpacity(0.4)
        p.drawRoundedRect(r, 11, 11)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(mix(T["knob"], T["app"]))
        p.drawEllipse(QRectF(5 + 16 * t, 5, 16, 16))


class Segmented(QWidget):
    """A row of joined choice buttons. currentText() / setCurrentText() / changed(str)."""

    changed = Signal(str)

    def __init__(self, options: List[str], current: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setStyleSheet(
            f"QWidget#seg > QPushButton {{ min-height: 34px; border-radius: 0; border: 1px solid {T['line']}; border-left-width: 0; font-size: 13px; }}"
            # Qt has no :first-child for widgets, so each button carries its position.
            f"QWidget#seg > QPushButton[pos=\"first\"] {{ border-left-width: 1px; border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}"
            f"QWidget#seg > QPushButton[pos=\"last\"] {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; }}"
            f"QWidget#seg > QPushButton[pos=\"only\"] {{ border-left-width: 1px; border-radius: 6px; }}"
            f"QWidget#seg > QPushButton:checked {{ background: {T['ink']}; color: {T['app']}; }}"
        )
        self.setObjectName("seg")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._btns: Dict[str, QPushButton] = {}
        for i, opt in enumerate(options):
            b = QPushButton(opt)
            b.setCheckable(True)
            b.setAutoExclusive(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            last = i == len(options) - 1
            b.setProperty("pos", "only" if i == 0 and last else "first" if i == 0 else "last" if last else "mid")
            b.clicked.connect(lambda _=False, o=opt: self.changed.emit(o))
            lay.addWidget(b)
            self._btns[opt] = b
        self.setCurrentText(current or (options[0] if options else ""))

    def currentText(self) -> str:
        return next((o for o, b in self._btns.items() if b.isChecked()), "")

    def setCurrentText(self, text: str) -> None:
        b = self._btns.get(text)
        if b is not None:
            b.setChecked(True)


class Autosave:
    """Debounced save: connect any number of controls; the save function runs once, 400 ms after the last change."""

    _live: "weakref.WeakSet[Autosave]" = weakref.WeakSet()

    def __init__(self, save: Callable[[], object], delay_ms: int = 400):
        self._save = save
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._run)
        self.paused = False
        Autosave._live.add(self)

    @classmethod
    def flush_all(cls) -> None:
        """Save every waiting change now (the window is closing)."""
        for a in list(cls._live):
            a.flush()

    def _run(self) -> None:
        if not self.paused:
            self._save()

    def flush(self) -> None:
        """Save now if a change is still waiting (before the manager starts, or the window closes)."""
        if self._timer.isActive():
            self._timer.stop()
            self._run()

    def poke(self, *_args) -> None:
        if not self.paused:
            self._timer.start()

    def watch(self, *widgets: QWidget) -> None:
        for w in widgets:
            for sig in ("toggled", "valueChanged", "textEdited", "currentIndexChanged", "changed", "keySequenceChanged"):
                s = getattr(w, sig, None)
                if s is not None and hasattr(s, "connect"):
                    s.connect(self.poke)
                    break


class FlowLayout(QLayout):
    """Lays widgets left to right and wraps to the next line (the Qt flow layout example, trimmed)."""

    def __init__(self, parent=None, spacing: int = 2):
        super().__init__(parent)
        self._items = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

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

    def heightForWidth(self, width):  # noqa: N802
        return self._place(QRect(0, 0, width, 0), move=False)

    def setGeometry(self, rect):  # noqa: N802
        super().setGeometry(rect)
        self._place(rect, move=True)

    def sizeHint(self):  # noqa: N802
        return self.minimumSize()

    def minimumSize(self):  # noqa: N802
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _place(self, rect, move: bool) -> int:
        x, y, line_h, gap = rect.x(), rect.y(), 0, self.spacing()
        for it in self._items:
            w = it.sizeHint()
            if x + w.width() > rect.right() and line_h > 0:
                x, y, line_h = rect.x(), y + line_h + gap, 0
            if move:
                it.setGeometry(QRect(QPoint(x, y), w))
            x += w.width() + gap
            line_h = max(line_h, w.height())
        return y + line_h - rect.y()


class SectionTabs(QWidget):
    """Section tabs that wrap onto a second line instead of scrolling. Same calls as the QTabBar it replaces."""

    currentChanged = Signal(int)

    def __init__(self):
        super().__init__()
        self.setObjectName("clear")
        self._flow = FlowLayout(self, spacing=0)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._group.idClicked.connect(self.currentChanged.emit)

    def addTab(self, title: str) -> int:  # noqa: N802
        b = _prop(QPushButton(title), sectab=True)
        b.setCheckable(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        i = len(self._group.buttons())
        self._group.addButton(b, i)
        self._flow.addWidget(b)
        if i == 0:
            b.setChecked(True)
        return i

    def setCurrentIndex(self, i: int) -> None:  # noqa: N802
        b = self._group.button(i)
        if b is not None and not b.isChecked():
            b.setChecked(True)
            self.currentChanged.emit(i)


class Section(QWidget):
    """One titled block of setting rows. Rows are label + help on the left, the control on the right."""

    def __init__(self, page: "Page", title: str, help_text: str = ""):
        super().__init__()
        self.page = page
        self.title = title
        self.setObjectName("clear")
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(0)
        self._title_lbl = label(title, "h2")
        self._lay.addWidget(self._title_lbl)
        self._help_lbl = label(help_text, "help", wrap=True)
        self._lay.addWidget(self._help_lbl)
        self._help_lbl.setVisible(bool(help_text))
        self._lay.addSpacing(8)
        self._body = self._lay

    def show_title(self, on: bool) -> None:
        self._title_lbl.setVisible(on)

    def row(self, text: str, help_text: str = "", *controls: QWidget, searchable: bool = True) -> QFrame:
        """Add a setting row. `controls` go on the right, in order. Returns the row frame."""
        fr = QFrame()
        fr.setProperty("role", "row")
        lay = QHBoxLayout(fr)
        lay.setContentsMargins(0, 10, 0, 10)
        lay.setSpacing(16)
        left = QVBoxLayout()
        left.setSpacing(2)
        name = label(text, "label", wrap=True)
        left.addWidget(name)
        hl = label(help_text, "help", wrap=True)
        if not help_text:  # hide only: showing a widget before it has a parent opens it as its own window
            hl.hide()
        left.addWidget(hl)
        lay.addLayout(left, 1)
        for c in controls:
            if c is not None:
                lay.addWidget(c, 0, Qt.AlignmentFlag.AlignVCenter)
        fr.setMinimumHeight(60)
        fr.help_label = hl  # type: ignore[attr-defined]  # pages update live help (status text)
        fr.name_label = name  # type: ignore[attr-defined]
        self._body.addWidget(fr)
        if searchable:
            self.page.register(text, help_text, self, fr)
        return fr

    def action(self, text: str, help_text: str, btn_text: str, on_click: Callable, kind: str = "") -> QPushButton:
        b = button(btn_text, on_click, kind=kind)
        self.row(text, help_text, b)
        return b

    def add(self, widget: QWidget, stretch: int = 0) -> QWidget:
        """A full-width custom widget (a table, a list, a toolbar) inside the section."""
        self._body.addWidget(widget, stretch)
        return widget

    def end(self) -> None:
        self._body.addStretch(1)


class Page(QWidget):
    """A page: optional custom content on top, then sections. More than one section -> section tabs."""

    def __init__(self, page_id: str, title: str, lead: str = "", search: Optional["SearchIndex"] = None):
        super().__init__()
        self.page_id = page_id
        self.title = title
        self.lead = lead
        self._search = search
        self.sections: List[Section] = []
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(12)
        self._top = QVBoxLayout()
        self._top.setSpacing(12)
        outer.addLayout(self._top)
        self._tabs = SectionTabs()
        self._tabs.setVisible(False)
        outer.addWidget(self._tabs)
        self._stack = QStackedWidget()
        self._stack.setObjectName("clear")
        outer.addWidget(self._stack, 1)
        self._tabs.currentChanged.connect(self._stack.setCurrentIndex)

    def custom(self, widget: QWidget, stretch: int = 0) -> QWidget:
        self._top.addWidget(widget, stretch)
        return widget

    def section(self, title: str, help_text: str = "") -> Section:
        sec = Section(self, title, help_text)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("clear")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(0, 4, 12, 24)
        hl.addWidget(sec)
        hl.addStretch(1)
        holder.setMaximumWidth(980)
        scroll.setWidget(holder)
        self.sections.append(sec)
        self._stack.addWidget(scroll)
        self._tabs.addTab(title)
        many = len(self.sections) > 1
        self._tabs.setVisible(many)
        for s in self.sections:
            s.show_title(not many)
        return sec

    def show_section(self, index: int) -> None:
        if 0 <= index < len(self.sections):
            self._tabs.setCurrentIndex(index)
            self._stack.setCurrentIndex(index)

    def register(self, text: str, help_text: str, sec: Section, row: QFrame) -> None:
        if self._search is not None:
            self._search.add(self, self.sections.index(sec) if sec in self.sections else 0, text, help_text, row)


class PageStack(QStackedWidget):
    """The page area. Keeps the QTabWidget calls the app logic uses: addTab, currentIndex, setCurrentIndex, currentChanged."""

    def addTab(self, widget: QWidget, _label: str = "") -> int:  # noqa: N802 (Qt naming)
        return self.addWidget(widget)

    def setTabVisible(self, index: int, visible: bool) -> None:  # noqa: N802
        nav = getattr(self, "nav", None)
        if nav is not None:
            nav.set_index_visible(index, visible)


class SearchIndex:
    """Every setting row, findable from the header search box (a QCompleter over 'Label  (Page > Section)')."""

    def __init__(self):
        self._entries: Dict[str, Tuple[Page, int, QFrame]] = {}
        self.model = QStringListModel()
        self.go: Optional[Callable[[Page], None]] = None

    def add(self, page: Page, sec_index: int, text: str, help_text: str, row: QFrame) -> None:
        sec_title = page.sections[sec_index].title if page.sections else ""
        key = f"{text}  ({page.title}{' > ' + sec_title if sec_title and sec_title != page.title else ''})"
        self._entries[key] = (page, sec_index, row)
        self.model.setStringList(list(self._entries))

    def open(self, key: str) -> None:
        hit = self._entries.get(key)
        if not hit:
            return
        page, sec_index, row = hit
        if self.go:
            self.go(page)
        page.show_section(sec_index)
        row.setProperty("hit", True)
        row.style().polish(row)

        def _clear():
            row.setProperty("hit", False)
            row.style().polish(row)
        QTimer.singleShot(1600, _clear)


class Sidebar(QWidget):
    navigate = Signal(int)

    def __init__(self, brand: str):
        super().__init__()
        self.setObjectName("sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(244)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 18, 12, 14)
        lay.setSpacing(2)
        top = QHBoxLayout()
        top.addWidget(label(brand, "brand"))
        top.addStretch(1)
        self.count_label = label("", "navstate")
        top.addWidget(self.count_label)
        lay.addLayout(top)
        lay.addSpacing(10)
        # The page list scrolls on short windows; the brand and the footer stay put.
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        items = QWidget()
        items.setObjectName("navlist")
        self._lay = QVBoxLayout(items)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(2)
        scroll.setWidget(items)
        lay.addWidget(scroll, 1)
        self._scroll = scroll
        self._outer = lay
        self._items: Dict[int, Tuple[QPushButton, QLabel]] = {}
        self.footer = QVBoxLayout()
        self.footer.setSpacing(8)

    def group(self, name: str) -> None:
        self._lay.addSpacing(8)
        self._lay.addWidget(label(name, "group"))

    def item(self, index: int, text: str) -> None:
        b = _prop(QPushButton(), nav=True)
        b.setCheckable(True)
        b.setAutoExclusive(True)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        row = QHBoxLayout(b)
        row.setContentsMargins(10, 0, 10, 0)
        name = label(text)
        name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        state = label("", "navstate")
        state.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        row.addWidget(name)
        row.addStretch(1)
        row.addWidget(state)
        b.clicked.connect(lambda _=False, i=index: self.navigate.emit(i))
        self._lay.addWidget(b)
        self._items[index] = (b, state)

    def finish(self) -> None:
        self._lay.addStretch(1)
        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {T['rule']};")
        self._outer.addWidget(line)
        self._outer.addSpacing(8)
        self._outer.addLayout(self.footer)

    def set_current(self, index: int) -> None:
        if index in self._items:
            btn = self._items[index][0]
            btn.setChecked(True)
            self._scroll.ensureWidgetVisible(btn, 0, 40)  # a short window scrolls the list to the current page

    def set_state(self, index: int, text: str, warn: bool = False) -> None:
        if index in self._items:
            st = self._items[index][1]
            if st.text() != text:
                st.setText(text)
            if bool(st.property("warn")) != warn:
                st.setProperty("warn", warn)
                st.style().polish(st)

    def set_index_visible(self, index: int, visible: bool) -> None:
        if index in self._items:
            self._items[index][0].setVisible(visible)


class Shell(QWidget):
    """Sidebar + header (title, lead, header buttons, search) + page stack."""

    def __init__(self, brand: str):
        super().__init__()
        self.search = SearchIndex()
        app = QApplication.instance()
        if app is not None:
            self._keyboard_focus = _KeyboardFocusOnly(self)
            app.installEventFilter(self._keyboard_focus)
        self.sidebar = Sidebar(brand)
        self.stack = PageStack()
        self.stack.nav = self.sidebar  # type: ignore[attr-defined]
        self.search.go = self.go

        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self.sidebar)
        main = QWidget()
        main.setObjectName("main")
        ml = QVBoxLayout(main)
        ml.setContentsMargins(32, 24, 32, 16)
        ml.setSpacing(16)
        head = QHBoxLayout()
        head.setSpacing(16)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        self.title = label("", "title")
        self.lead = label("", "lead", wrap=True)
        titles.addWidget(self.title)
        titles.addWidget(self.lead)
        head.addLayout(titles, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        head.addLayout(self.header_actions)
        head.setAlignment(self.header_actions, Qt.AlignmentFlag.AlignTop)  # same height on every page, whatever the lead's length
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Find any setting")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.setFixedWidth(280)
        self.search_box.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        comp = QCompleter(self.search.model, self.search_box)
        comp.setFilterMode(Qt.MatchFlag.MatchContains)
        comp.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        comp.setMaxVisibleItems(12)
        comp.activated.connect(self._open_hit)
        self.search_box.setCompleter(comp)
        self._wide_popup = _WidePopup(self.search_box, comp.popup())
        head.addWidget(self.search_box, 0, Qt.AlignmentFlag.AlignTop)
        ml.addLayout(head)
        ml.addWidget(self.stack, 1)
        root.addWidget(main, 1)

        self.sidebar.navigate.connect(self.stack.setCurrentIndex)
        self.stack.currentChanged.connect(self._on_page)

    def _open_hit(self, key: str) -> None:
        self.search.open(key)
        QTimer.singleShot(0, self.search_box.clear)

    def add_page(self, page: QWidget, nav_text: str = "") -> int:
        index = self.stack.addWidget(page)
        if nav_text:
            self.sidebar.item(index, nav_text)
        if self.stack.count() == 1:
            self._on_page(0)
        return index

    def go(self, page: QWidget) -> None:
        self.stack.setCurrentWidget(page)

    def _on_page(self, index: int) -> None:
        page = self.stack.widget(index)
        if page is not None and self.isVisible():
            fx = QGraphicsOpacityEffect(page)
            page.setGraphicsEffect(fx)
            fade = QVariantAnimation(fx)
            fade.setDuration(140)
            fade.setStartValue(0.0)
            fade.setEndValue(1.0)
            fade.valueChanged.connect(lambda v, e=fx: e.setOpacity(float(v)))
            fade.finished.connect(lambda pg=page: pg.setGraphicsEffect(None))
            fade.start()
        self.title.setText(getattr(page, "title", ""))
        lead = getattr(page, "lead", "")
        self.lead.setText(lead)
        self.lead.setVisible(bool(lead))
        self.sidebar.set_current(index)

    def set_lead(self, page: QWidget, text: str) -> None:
        page.lead = text  # type: ignore[attr-defined]
        if self.stack.currentWidget() is page:
            self.lead.setText(text)
            self.lead.setVisible(bool(text))
