"""Automate pages: Auto SC + BR, Anti-AFK, Action rows.

Ports setup_auto_item_tab (live copy) and setup_antiafk_tab onto ui_kit pages. Widgets keep the old attribute
names so the logic in gui.py (save/load/reset, handlers, engine) keeps working untouched.
"""
from __future__ import annotations

import json
import sys
import uuid

from PySide6.QtCore import QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDoubleSpinBox, QFileDialog, QHeaderView, QKeySequenceEdit, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QPushButton, QSpinBox, QTableWidget, QWidget, QHBoxLayout,
)

import ui_kit as K

# One resolution list everywhere (spec). The SC + BR setup dialog reads win._SOL_RESOLUTIONS.
# "Custom" needs width/height fields in _sol_setup_dialog: left to the integrator (gui.py).
SOL_RESOLUTIONS = (
    ("Keep size", None),
    ("640 x 480", (640, 480)),
    ("800 x 600", (800, 600)),
    ("1024 x 768", (1024, 768)),
    ("1280 x 720", (1280, 720)),
    ("1366 x 768", (1366, 768)),
    ("1600 x 900", (1600, 900)),
    ("1920 x 1080", (1920, 1080)),
)

# Anti-AFK actions: (plain name, backend value the engine and settings.json use).
AFK_ACTIONS = (
    ("Jump", "space"),
    ("Walk forward and back", "ws"),
    ("Zoom the camera", "zoom"),
    ("Auto reconnect", "AutoReconnect"),
)

_SEARCH_ROLE = Qt.ItemDataRole.UserRole + 1


def _g(win):
    """gui module of the window (lazy, avoids a circular import)."""
    return sys.modules[type(win).__module__]


# ---------------------------------------------------------------- small widgets

class _ValueCombo(QComboBox):
    """Shows plain names; currentText() / setCurrentText() speak the stored values, which is what the logic reads."""

    def currentText(self) -> str:  # noqa: N802
        data = self.currentData()
        return str(data) if data is not None else super().currentText()

    def setCurrentText(self, value: str) -> None:  # noqa: N802
        i = self.findData(value)
        if i >= 0:
            self.setCurrentIndex(i)


class _NameEdit(QLineEdit):
    """Row name: reads as text, double-click to rename; Enter or leaving the field finishes."""

    def __init__(self, text: str = ""):
        super().__init__(text)
        self.select_row = None  # set by the table loader: click selects the row
        self.setPlaceholderText("Double-click to name this row")
        self.setReadOnly(True)
        self.editingFinished.connect(lambda: self.setReadOnly(True))

    def setReadOnly(self, on: bool) -> None:  # noqa: N802
        super().setReadOnly(on)
        self.setProperty("naming", not on)
        self.style().unpolish(self)
        self.style().polish(self)
        if on:
            self.deselect()
            self.setCursorPosition(0)

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self.isReadOnly() and self.select_row:
            self.select_row(e.modifiers())
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e) -> None:  # noqa: N802
        if self.isReadOnly():
            self.setReadOnly(False)
            self.setFocus()
            self.selectAll()
            return
        super().mouseDoubleClickEvent(e)


class _UserCheck(QObject):
    """Stands in for the old per-account QCheckBox (auto_item_user_checks values) over a QListWidget item."""

    toggled = Signal(bool)

    def __init__(self, item: QListWidgetItem):
        super().__init__()
        self._item = item
        self._on = self.isChecked()

    def isChecked(self) -> bool:  # noqa: N802
        return self._item.checkState() == Qt.CheckState.Checked

    def setChecked(self, on: bool) -> None:  # noqa: N802
        self._item.setCheckState(Qt.CheckState.Checked if on else Qt.CheckState.Unchecked)

    def text(self) -> str:
        return self._item.text()

    def property(self, name: str):
        if name == "search_text":
            return self._item.data(_SEARCH_ROLE)
        if name == "user_id":
            return self._item.data(Qt.ItemDataRole.UserRole)
        return None

    def setVisible(self, on: bool) -> None:  # noqa: N802
        self._item.setHidden(not on)

    def isHidden(self) -> bool:  # noqa: N802
        return self._item.isHidden()

    def changed(self) -> None:
        on = self.isChecked()
        if on != self._on:
            self._on = on
            self.toggled.emit(on)


def _spin(cls, lo, hi, value, suffix="", step=None, decimals=None, width=130):
    s = cls()
    if decimals is not None:
        s.setDecimals(decimals)
    s.setRange(lo, hi)
    if step is not None:
        s.setSingleStep(step)
    s.setValue(value)
    s.setSuffix(suffix)
    s.setFixedWidth(width)
    return s


def _wide(btn: QPushButton) -> QPushButton:
    btn.setMinimumWidth(100)  # the right-hand buttons of one section line up
    return btn


def _go(shell, page_id: str, section: int = 0) -> None:
    for i in range(shell.stack.count()):
        w = shell.stack.widget(i)
        if getattr(w, "page_id", "") == page_id:
            shell.go(w)
            w.show_section(section)
            return


def _cell(*widgets, center: bool = False) -> QWidget:
    holder = QWidget()
    holder.setObjectName("clear")
    lay = QHBoxLayout(holder)
    lay.setContentsMargins(6, 0, 6, 0)
    lay.setSpacing(6)
    if center:
        lay.addStretch(1)
    for i, w in enumerate(x for x in widgets if x is not None):
        lay.addWidget(w, 1 if (i == 0 and not center) else 0, Qt.AlignmentFlag.AlignVCenter)
    if center:
        lay.addStretch(1)
    return holder


def _row_of(table: QTableWidget, holder: QWidget, col: int) -> int:
    return next((r for r in range(table.rowCount()) if table.cellWidget(r, col) is holder), -1)


class _AccountsBtn(QPushButton):
    """Accounts cell: _update_users_btn_text (gui.py) writes "N users" / "Blacklist empty"; show plain wording."""

    def setText(self, text: str) -> None:  # noqa: N802
        t = str(text or "")
        n = t.split(" ", 1)[0]
        if t in ("All users", "Blacklist empty"):
            t = "All accounts"
        elif t == "No users":
            t = "No accounts"
        elif t.endswith(" blocked") and n.isdigit():
            t = f"All but {n}"
        elif t.endswith((" user", " users")) and n.isdigit():
            t = "1 account" if n == "1" else f"{n} accounts"
        super().setText(t)


def _cell_btn(cls=QPushButton) -> QPushButton:
    b = cls()
    b.setProperty("cell", True)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def _users_prop(item: dict):
    """Row 'users' as the Users button stores it: None = all, list = explicit (same parsing as the old loader)."""
    raw = item.get("users", None)
    explicit = bool(item.get("users_explicit", False))
    if raw is None or isinstance(raw, dict):
        return None
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = [p.strip() for p in raw.replace("\r", "\n").replace("\n", ",").split(",") if p.strip()]
        if not isinstance(raw, (list, tuple, set)):
            raw = [raw] if str(raw).strip() else []
    try:
        users = [str(u).strip() for u in list(raw) if str(u).strip()]
    except Exception:
        return None
    return users if users or explicit else None


# ---------------------------------------------------------------- REPLACES (integrator moves these into gui.py)

def _load_auto_item_items_table(self, items):
    """Same data contract as before (cells 0-5 carry the row; buttons hold the properties); new look, a
    double-click rename, an Edit steps button and a row menu (Delete, Save as preset, Move up / down) in col 6."""
    G = _g(self)
    t = self.auto_item_table
    t.setRowCount(0)
    sol_ids = set(self._SOL_ROW_IDS.values())

    def _select(holder, col, mods):
        r = _row_of(t, holder, col)
        if r < 0:
            return
        sm = t.selectionModel()
        if mods & Qt.KeyboardModifier.ControlModifier:
            sm.select(t.model().index(r, 0), sm.SelectionFlag.Toggle | sm.SelectionFlag.Rows)
            sm.setCurrentIndex(t.model().index(r, 1), sm.SelectionFlag.NoUpdate)  # setCurrentCell would reset the selection
        else:
            t.selectRow(r)
            t.setCurrentCell(r, 1)

    def _menu_do(holder, what):
        r = _row_of(t, holder, 6)
        if r < 0:
            return
        t.clearSelection()
        t.selectRow(r)
        t.setCurrentCell(r, 1)
        if what == "up":
            self._auto_item_move_selected(-1)
        elif what == "down":
            self._auto_item_move_selected(1)
        elif what == "preset":
            self._auto_item_save_selected_rows_as_presets()
        elif what == "delete":
            name = self._auto_item_unwrap_cell_widget(t.cellWidget(r, 1), QLineEdit)
            label = (name.text().strip() if name else "") or "this row"
            if QMessageBox.question(self, "Delete row", f"Delete {label}? This cannot be undone.") \
                    == QMessageBox.StandardButton.Yes:
                self._auto_item_remove_selected_items()

    for item in (items or []):
        row_id = str(item.get("id") or item.get("row_id") or "").strip() or f"row_{uuid.uuid4().hex}"
        row = t.rowCount()
        t.insertRow(row)
        t.setRowHeight(row, 52)

        enabled = K.Switch(bool(item.get("enabled", True)))
        enabled.toggled.connect(self._on_auto_item_ui_changed)
        t.setCellWidget(row, 0, _cell(enabled, center=True))

        name = _NameEdit(str(item.get("name") or ""))
        name.textChanged.connect(self._on_auto_item_ui_changed)
        tag = None
        if row_id in sol_ids:
            tag = K.label("Auto SC + BR", "help")
            tag.setStyleSheet(f"border: 1px solid {K.T['line']}; border-radius: 10px; padding: 1px 8px; font-size: 12px;")
        name_cell = _cell(name, tag)
        name.select_row = lambda mods, h=name_cell: _select(h, 1, mods)
        t.setCellWidget(row, 1, name_cell)

        actions_btn = _cell_btn()
        actions_btn.setProperty("row_id", row_id)
        actions = []
        for idx, raw in enumerate(item.get("actions") or []):
            action = self._normalize_auto_item_action(raw, fallback_name=f"Action {idx + 1}")
            if action is not None:
                actions.append(action)
        actions_btn.setProperty("actions", actions)
        self._update_actions_btn_text(actions_btn)
        actions_btn.clicked.connect(lambda _=False, b=actions_btn: self._edit_item_actions(b))
        t.setCellWidget(row, 2, _cell(actions_btn))

        behavior_btn = _cell_btn()
        behavior_btn.setProperty("behavior", self._normalize_auto_item_behavior(item.get("behavior"), legacy=item))
        self._update_behavior_btn_text(behavior_btn)
        behavior_btn.clicked.connect(lambda _=False, b=behavior_btn: self._edit_item_behavior(b))
        t.setCellWidget(row, 3, _cell(behavior_btn))

        users_btn = _cell_btn(_AccountsBtn)
        users_btn.setProperty("users", _users_prop(item))
        users_btn.setProperty("user_filter_mode", G._normalize_user_filter_mode(item.get("user_filter_mode", "whitelist")))
        users_btn.setProperty("discord_user_edit_enabled", bool(item.get("discord_user_edit_enabled", False)))
        self._update_users_btn_text(users_btn)
        users_btn.clicked.connect(lambda _=False, b=users_btn: self._edit_item_users(b))
        t.setCellWidget(row, 4, _cell(users_btn))

        alert_btn = _cell_btn()
        alert_btn.setProperty("alert_enabled", bool(item.get("alert_enabled", False)))
        alert_btn.setProperty("alert_webhook", str(item.get("alert_webhook") or "").strip())
        alert_btn.setProperty("alert_message", str(item.get("alert_message") or ""))
        alert_btn.setProperty("alert_lead_s", float(item.get("alert_lead_s", 15.0) or 15.0))
        self._update_alert_btn_text(alert_btn)
        alert_btn.clicked.connect(lambda _=False, b=alert_btn: self._edit_item_alert(b))
        t.setCellWidget(row, 5, _cell(alert_btn))

        edit = K.button("Edit steps", lambda b=actions_btn: self._edit_item_actions(b), size="sm")
        more = K.button("More", size="sm")
        tools = _cell(edit, more, center=True)
        menu = QMenu(more)
        for text, what in (("Move up", "up"), ("Move down", "down"), ("Save as preset", "preset"), ("Delete", "delete")):
            menu.addAction(text, lambda h=tools, w=what: _menu_do(h, w))
        more.setMenu(menu)
        t.setCellWidget(row, 6, tools)
    self._sync_sol_toggles()


def _auto_item_refresh_users(self):
    """Global 'Who it runs on' list: a checkable QListWidget; auto_item_user_checks keeps the old dict contract."""
    try:
        users = self.config_manager.load_users() or {}
    except Exception:
        users = {}
    selected = [uid for uid, cb in (getattr(self, "auto_item_user_checks", {}) or {}).items() if cb.isChecked()]
    if not bool(getattr(self, "_auto_item_user_list_initialized", False)):
        try:
            selected = list(self._get_auto_item_cfg_from_disk().get("users", []) or [])
        except Exception:
            selected = []

    lst = self.auto_item_user_list
    lst.blockSignals(True)
    lst.clear()
    self.auto_item_user_checks = {}
    for uid in sorted(users.keys(), key=lambda u: str((users.get(u, {}) or {}).get("username") or u)):
        uname = (users.get(uid, {}) or {}).get("username", uid)
        it = QListWidgetItem(f"{uname} ({uid})")
        it.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable)
        it.setCheckState(Qt.CheckState.Unchecked)
        it.setData(Qt.ItemDataRole.UserRole, str(uid))
        it.setData(_SEARCH_ROLE, f"{uname} {uid}".lower())
        lst.addItem(it)
        cb = _UserCheck(it)
        cb.toggled.connect(self._on_auto_item_user_selection_changed)
        self.auto_item_user_checks[str(uid)] = cb
    lst.blockSignals(False)
    self._auto_item_user_list_initialized = True

    prev = bool(getattr(self, "_loading_autoitem_settings", False))
    self._loading_autoitem_settings = True
    try:
        self._apply_auto_item_users_to_ui(selected)
    finally:
        self._loading_autoitem_settings = prev

    self._auto_item_filter_users()
    self._auto_item_update_users_hint()
    try:
        self._auto_item_last_users_mtime = float(self.config_manager.get_users_mtime())
    except Exception:
        self._auto_item_last_users_mtime = None


def _auto_item_update_users_hint(self) -> None:
    """Who it runs on help text, in plain words (was "N of M users are enabled. Action-row user filters ...")."""
    hint = getattr(self, "auto_item_users_hint", None)
    if hint is None:
        return
    checks = getattr(self, "auto_item_user_checks", {}) or {}
    total = len(checks)
    ticked = sum(1 for cb in checks.values() if cb.isChecked())
    combo = getattr(self, "auto_item_user_filter_mode_combo", None)
    if _g(self)._normalize_user_filter_mode(combo.currentData() if combo is not None else "whitelist") == "blacklist":
        text = f"Runs on {total - ticked} of {total} accounts, all except the ones ticked below"
    else:
        text = f"Runs on {ticked} of {total} accounts, only the ones ticked below"
    query = self.auto_item_user_search_input.text().strip() if hasattr(self, "auto_item_user_search_input") else ""
    if query:
        shown = sum(1 for cb in checks.values() if not cb.isHidden())
        text += f". Showing {shown} that match"
    hint.setText(text + ". Each row can narrow this further")


REPLACES = {
    "_load_auto_item_items_table": _load_auto_item_items_table,
    "_auto_item_refresh_users": _auto_item_refresh_users,
    "_auto_item_update_users_hint": _auto_item_update_users_hint,
}


# ---------------------------------------------------------------- Settings > Troubleshooting

def _debug_timing_switch(win) -> K.Switch:
    chk = getattr(win, "antiafk_debug_timing_chk", None)
    if not isinstance(chk, K.Switch):
        chk = win.antiafk_debug_timing_chk = K.Switch()
        cfg = (win.antiafk.config or {}) if getattr(win, "antiafk", None) else {}
        chk.setChecked(bool(cfg.get("antiafk_dev_mode", False)))
        chk.toggled.connect(win._on_antiafk_ui_changed)  # saved with the Anti-AFK settings, as before
    return chk


def _timing_spins(win) -> None:
    """Check rows every / pause between steps: the Action rows engine reads them; Settings > Automation timing shows them."""
    if getattr(win, "auto_item_tick_spin", None) is None:
        win.auto_item_tick_spin = QDoubleSpinBox()
        win.auto_item_tick_spin.setRange(0.2, 60.0)
        win.auto_item_tick_spin.setDecimals(2)
        win.auto_item_tick_spin.setSingleStep(0.1)
        win.auto_item_tick_spin.setValue(1.0)
        win.auto_item_tick_spin.setSuffix(" s")
        win.auto_item_delay_spin = QDoubleSpinBox()
        win.auto_item_delay_spin.setRange(0.01, 2.0)
        win.auto_item_delay_spin.setDecimals(2)
        win.auto_item_delay_spin.setSingleStep(0.05)
        win.auto_item_delay_spin.setValue(0.2)
        win.auto_item_delay_spin.setSuffix(" s")
        win.auto_item_tick_spin.setFixedWidth(130)  # same width as every other number field
        win.auto_item_delay_spin.setFixedWidth(130)


def automation_timing_rows(win, section) -> None:
    """Settings > Automation timing."""
    _timing_spins(win)
    section.row("Check rows every", "How often action rows look for work", win.auto_item_tick_spin)
    section.row("Pause between steps", "After each click, key or paste", win.auto_item_delay_spin)


def troubleshooting_rows(win, section) -> None:
    """Settings > Troubleshooting calls this (overview + automate only) to place the Anti-AFK debug timings switch
    and Merchant detection's two log switches (either build order works). These three are saved."""
    section.row("Log Anti-AFK timings", "How long each pass, batch and step takes. Stays on after a restart",
                _debug_timing_switch(win))
    from ui_pages import detect_hw
    text_chk, loop_chk = detect_hw.ocr_debug_switches(win)
    section.row("Log the chat text read", "Every line merchant detection reads from chat. Stays on after a restart",
                text_chk)
    section.row("Log each detection pass", "One line per chat-reading pass. Stays on after a restart", loop_chk)


# ---------------------------------------------------------------- pages

def build(win, shell):
    _timing_spins(win)  # before the rows page loads auto_item settings into them
    scbr, refresh_scbr = _build_scbr(win, shell)
    afk = _build_afk(win, shell)
    rows = _build_rows(win, shell)
    refresh_scbr()
    win.auto_item_enable_chk.toggled.connect(lambda _on: refresh_scbr())
    win.auto_item_tick_spin.valueChanged.connect(win._on_auto_item_ui_changed)
    win.auto_item_delay_spin.valueChanged.connect(win._on_auto_item_ui_changed)
    return [("Auto SC + BR", scbr), ("Anti-AFK", afk), ("Action rows", rows)]


def _build_scbr(win, shell):
    page = K.Page("scbr", "Auto SC + BR", "", shell.search)
    sec = page.section(
        "What to use",
        "Uses Strange Controllers and Biome Randomizers as each one runs out. It runs as two action rows, "
        "so Action rows must be on and the manager running.",
    )
    win.sol_sc_chk = K.Switch()
    win.sol_br_chk = K.Switch()
    sec.row("Strange Controller", "One every 20 min 15 s on its accounts", win.sol_sc_chk)
    sec.row("Biome Randomizer", "One every 35 min 15 s on its accounts", win.sol_br_chk)
    acc_row = sec.row("Accounts", " ", _wide(K.button("Choose", lambda: (win._sol_edit_setup(), refresh()), size="sm")))
    size_row = sec.row("Window size", " ", _wide(K.button("Change", lambda: (win._sol_edit_setup(), refresh()), size="sm")))
    fps_row = sec.row("Lowest Roblox FPS", " ", _wide(K.button("Change", lambda: _go(shell, "rows", 1), size="sm")))
    sec.row("Item used alert", "Posted to each account's webhook",
            _wide(K.button("Webhooks", lambda: _go(shell, "webhooks"), size="sm")))

    def refresh() -> None:
        try:
            rows = win._sol_rows(win._current_auto_item_items())
            total = len(win._auto_item_user_catalog())
        except Exception:
            return

        def _count(row_id):
            r = rows.get(row_id)
            if not r:
                return 0
            return len(r.get("users") or []) if r.get("users_explicit") else total

        sc, br = _count("row_sol_sc"), _count("row_sol_br")
        acc_row.help_label.setText(f"Strange Controller on {sc}, Biome Randomizer on {br} of {total} accounts")
        try:
            rz = (win.config_manager.peek_settings() or {}).get("roblox_resizer") or {}
        except Exception:
            rz = {}
        size_row.help_label.setText(
            f"{int(rz.get('width') or 0)} x {int(rz.get('height') or 0)}. Buttons are found at any size"
            if rz.get("enabled") else "Each window keeps its size"
        )
        fps = getattr(win, "auto_item_min_fps_spin", None)
        if fps is not None:
            fps_row.help_label.setText(f"{fps.value()} FPS. Lower means slower, surer clicks")
        on_sc, on_br = win.sol_sc_chk.isChecked(), win.sol_br_chk.isChecked()
        rows_on = getattr(win, "auto_item_enable_chk", None)
        n_sc, n_br = sc if on_sc else 0, br if on_br else 0
        if not (on_sc or on_br):
            lead = "Off. Turn one on to choose the accounts and window size."
        elif rows_on is not None and not rows_on.isChecked():
            lead = f"Strange Controllers on {n_sc} accounts, Biome Randomizers on {n_br}. Action rows is off, so nothing runs."
        else:
            lead = f"Uses Strange Controllers on {n_sc} accounts and Biome Randomizers on {n_br} as each one runs out."
        shell.set_lead(page, lead)

    for chk, preset_id in ((win.sol_sc_chk, "sol_sc"), (win.sol_br_chk, "sol_br")):
        chk.toggled.connect(lambda on, p=preset_id: win._sol_toggle_changed(p, bool(on)))
        chk.toggled.connect(lambda _on: refresh())
    shell.stack.currentChanged.connect(lambda i: refresh() if shell.stack.widget(i) is page else None)
    return page, refresh


def _build_afk(win, shell):
    G = _g(win)
    page = K.Page("afk", "Anti-AFK", "Keeps accounts from being kicked for idling.", shell.search)

    main = page.section("Anti-AFK", "Sends a short input to every Roblox window on a timer. "
                                    "It may need M.J.JARAM to run as administrator.")
    win.antiafk_enable_chk = K.Switch()
    main.row("Anti-AFK", "Runs while the manager is running", win.antiafk_enable_chk)
    win.antiafk_interval_spin = _spin(QSpinBox, 5, 3600, 120, " s")
    chips = {}

    def _sync_chips(value) -> None:
        for secs, b in chips.items():
            b.setChecked(int(value) == secs)

    def _pick(secs) -> None:
        win.antiafk_interval_spin.setValue(secs)
        _sync_chips(secs)

    for secs, text in ((120, "2 min"), (300, "5 min"), (600, "10 min")):
        b = K.chip(text)
        b.clicked.connect(lambda _=False, s=secs: _pick(s))
        chips[secs] = b
    win.antiafk_interval_spin.valueChanged.connect(_sync_chips)
    main.row("Every", "Time between passes", K.hbox(*chips.values(), win.antiafk_interval_spin))
    win.antiafk_action_combo = _ValueCombo()
    for text, value in AFK_ACTIONS:
        win.antiafk_action_combo.addItem(text, value)
    win.antiafk_action_combo.setFixedWidth(220)
    main.row("Action", "What it does in game", win.antiafk_action_combo)
    win.antiafk_alt_delay_spin = _spin(QSpinBox, 10, 5000, 400, " ms", step=10)
    main.row("Key hold", "How long each key is held", win.antiafk_alt_delay_spin)
    win.antiafk_menu_autoreconnect_chk = K.Switch()
    main.row("Reconnect from the main menu", "Uses auto reconnect when an account sits in the menu",
             win.antiafk_menu_autoreconnect_chk)

    thr = page.section("With hardware limiting",
                       "Only when Limit CPU is on. Lifts the limit briefly so actions land on slow windows.")
    win.antiafk_unthrottle_chk = K.Switch()
    thr.row("Unthrottle first", "Just before each pass", win.antiafk_unthrottle_chk)
    win.antiafk_unthrottle_batch_spin = _spin(QSpinBox, 1, 50, 5, "")
    thr.row("Windows at a time", "How many are unthrottled together", win.antiafk_unthrottle_batch_spin)
    win.antiafk_unthrottle_lead_spin = _spin(QDoubleSpinBox, 0.0, 15.0, 3.0, " s", step=0.5, decimals=1)
    thr.row("Lead time", "How early to unthrottle", win.antiafk_unthrottle_lead_spin)

    heads = page.section("Heads-up", "Warn you before Anti-AFK takes the mouse.")
    win.antiafk_alert_sound_chk = K.Switch()
    heads.row("Sound", "Plays before each pass", win.antiafk_alert_sound_chk)
    win.antiafk_alert_tooltip_chk = K.Switch()
    heads.row("Message", "Shows a note in the middle of the screen before each pass", win.antiafk_alert_tooltip_chk)
    win.antiafk_alert_lead_spin = _spin(QDoubleSpinBox, 0.0, 60.0, 3.0, " s", step=0.5, decimals=1)
    heads.row("Warn this early", "How long before each pass", win.antiafk_alert_lead_spin)
    win.antiafk_test_btn = heads.action("Try it now", "Runs one pass on every account", "Test pass",
                                        lambda: win._on_antiafk_test())
    win.antiafk_show_btn = K.button("Show all", lambda: win._on_antiafk_show())
    win.antiafk_hide_btn = K.button("Hide all", lambda: win._on_antiafk_hide())
    heads.row("Roblox windows", "Bring every window to the front, or hide them", win.antiafk_show_btn, win.antiafk_hide_btn)
    heads.action("Defaults", "Put Anti-AFK back how it came", "Restore defaults",
                 lambda: win._reset_antiafk_to_defaults())

    _debug_timing_switch(win)  # lives on Settings > Troubleshooting (troubleshooting_rows)

    created = getattr(win, "antiafk", None) is None
    if created:  # engine + callbacks, as the old builder did
        antiafk_cfg = (win.config_manager.load_settings() or {}).get("antiafk", {}) or {}
        win.antiafk = G.AntiAFK(parent=win, config=antiafk_cfg)
        win.antiafk.status_callback = win._emit_antiafk_status
        win.antiafk.button_state_callback = win._emit_antiafk_state
        win.antiafk.touch_callback = win._emit_antiafk_touch
        try:
            win.antiafk.pre_action_callback = win._emit_antiafk_pre_action
        except Exception:
            pass
        win.antiafk.is_pid_in_menu_callback = win._is_pid_in_menu
        win.antiafk.bes_hold_unthrottled_callback = win._antiafk_bes_hold_unthrottled
        win.antiafk.bes_release_hold_callback = win._antiafk_bes_release_hold

    win._loading_antiafk_settings = True
    try:
        cfg = win.antiafk.config or {}
        win._multi_instance_enabled = bool(cfg.get("multi_instance_enabled", False))
        win.antiafk_interval_spin.setValue(int(cfg.get("antiafk_interval", 120)))
        win.antiafk_action_combo.setCurrentText(cfg.get("antiafk_action", "space"))
        win.antiafk_alt_delay_spin.setValue(int(cfg.get("antiafk_alt_delay_ms", 400)))
        win.antiafk_menu_autoreconnect_chk.setChecked(bool(cfg.get("antiafk_menu_autoreconnect", False)))
        win.antiafk_debug_timing_chk.setChecked(bool(cfg.get("antiafk_dev_mode", False)))
        win.antiafk_enable_chk.setChecked(bool(cfg.get("antiafk_enabled", False)))
        win.antiafk_alert_sound_chk.setChecked(bool(cfg.get("antiafk_alert_sound", False)))
        win.antiafk_alert_tooltip_chk.setChecked(bool(cfg.get("antiafk_alert_tooltip", False)))
        try:
            win.antiafk_alert_lead_spin.setValue(float(cfg.get("antiafk_alert_lead_s", 3.0) or 0.0))
        except Exception:
            win.antiafk_alert_lead_spin.setValue(3.0)
        win.antiafk_unthrottle_chk.setChecked(bool(cfg.get("antiafk_unthrottle_enabled", False)))
        try:
            win.antiafk_unthrottle_batch_spin.setValue(int(cfg.get("antiafk_unthrottle_batch_size", 5) or 5))
        except Exception:
            win.antiafk_unthrottle_batch_spin.setValue(5)
        try:
            win.antiafk_unthrottle_lead_spin.setValue(float(cfg.get("antiafk_unthrottle_lead_s", 3.0) or 0.0))
        except Exception:
            win.antiafk_unthrottle_lead_spin.setValue(3.0)
    finally:
        win._loading_antiafk_settings = False
    _sync_chips(win.antiafk_interval_spin.value())

    for w in (win.antiafk_interval_spin, win.antiafk_alt_delay_spin, win.antiafk_alert_lead_spin,
              win.antiafk_unthrottle_batch_spin, win.antiafk_unthrottle_lead_spin):
        w.valueChanged.connect(win._on_antiafk_ui_changed)
    win.antiafk_action_combo.currentTextChanged.connect(win._on_antiafk_ui_changed)
    for w in (win.antiafk_menu_autoreconnect_chk, win.antiafk_alert_sound_chk, win.antiafk_alert_tooltip_chk,
              win.antiafk_unthrottle_chk, win.antiafk_enable_chk):
        w.toggled.connect(win._on_antiafk_ui_changed)

    win._on_antiafk_ui_changed()
    if created:
        QTimer.singleShot(0, win._restore_multi_instance_state)
    return page


def _import_presets(win) -> None:
    path, _ = QFileDialog.getOpenFileName(win, "Import presets", "", "JSON Files (*.json)")
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except Exception as e:
        QMessageBox.warning(win, "Import presets", f"Could not read that file.\n\n{e}")
        return
    presets = list(getattr(win, "_auto_item_presets", []) or [])
    added = 0
    for idx, raw in enumerate(payload if isinstance(payload, list) else [payload]):
        preset = win._normalize_auto_item_preset(raw, fallback_index=idx)
        if preset is None:
            continue
        if str(preset.get("id") or "") == "item":
            preset["builtin"] = True
        presets.append(preset)
        added += 1
    win._auto_item_presets = presets
    win._on_auto_item_ui_changed()
    QMessageBox.information(win, "Import presets", f"Added {added} preset{'' if added == 1 else 's'}.")


def _export_presets(win) -> None:
    presets = [p for p in (getattr(win, "_auto_item_presets", []) or []) if isinstance(p, dict) and not p.get("builtin")]
    if not presets:
        QMessageBox.information(win, "Export presets", "There are no saved presets yet. Save a row as a preset first.")
        return
    path, _ = QFileDialog.getSaveFileName(win, "Export presets", "action-presets.json", "JSON Files (*.json)")
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(presets, fh, indent=2)
    except Exception as e:
        QMessageBox.warning(win, "Export presets", f"Could not save the file.\n\n{e}")


def _build_rows(win, shell):
    G = _g(win)
    page = K.Page("rows", "Action rows", "Your own sequences of clicks, keys and webhooks.", shell.search)

    # ---- Rows
    sec = page.section("Rows", "Higher rows go first. Double-click a name to rename it; select rows to test them.")
    win.auto_item_test_btn = K.button("Test selected", lambda: win._auto_item_test_once())
    sec.add(K.hbox(
        K.button("Add row", lambda: win._auto_item_add_item(), kind="ink"),
        K.button("Presets", lambda: win._open_auto_item_presets_dialog()),
        win.auto_item_test_btn,
        K.button("Monitor", lambda: win._open_auto_item_monitor()),
        K.button("Import presets", lambda: _import_presets(win)),
        K.button("Export presets", lambda: _export_presets(win)),
        stretch_end=True,
    ))
    sec.layout().addSpacing(10)
    t = win.auto_item_table = QTableWidget()
    t.setColumnCount(7)
    t.setHorizontalHeaderLabels(["On", "Name", "Steps", "When it runs", "Accounts", "Alert", ""])
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setShowGrid(False)
    t.setWordWrap(False)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(52)
    t.setTextElideMode(Qt.TextElideMode.ElideRight)
    h = t.horizontalHeader()
    h.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    # "When it runs" reads like "Every 35 min 15 s, first after 10 s": cell buttons do not elide, so give it room.
    for col, mode, width in ((0, "fixed", 56), (1, "stretch", 0), (2, "fixed", 80), (3, "fixed", 250),
                             (4, "fixed", 110), (5, "fixed", 100), (6, "fixed", 190)):
        h.setSectionResizeMode(col, QHeaderView.ResizeMode.Stretch if mode == "stretch" else QHeaderView.ResizeMode.Fixed)
        if width:
            t.setColumnWidth(col, width)
    t.setColumnHidden(5, not G._bm_relaxed())
    t.setMinimumHeight(470)
    t.setStyleSheet(
        f"QTableWidget QPushButton[cell=\"true\"] {{ background: transparent; border: 0; color: {K.T['ink2']};"
        f" text-align: left; padding: 0 8px; min-height: 32px; font-weight: 500; }}"
        f"QTableWidget QPushButton[cell=\"true\"]:hover {{ color: {K.T['ink']}; background: {K.T['raised']}; }}"
        f"QTableWidget QLineEdit[naming=\"false\"] {{ border: 0; background: transparent; font-weight: 700; padding: 0 2px; }}"
    )
    sec.add(t)
    empty = K.label("No rows yet. Add row starts an empty one; Presets has ready-made ones.", "help")
    empty.setParent(t.viewport())
    empty.move(16, 16)
    empty.resize(empty.sizeHint())

    def _empty_state(*_args) -> None:
        try:
            empty.setVisible(t.rowCount() == 0)
        except RuntimeError:  # the table's own teardown removes its rows
            pass
    for sig in (t.model().rowsInserted, t.model().rowsRemoved, t.model().modelReset):
        sig.connect(_empty_state)
    _empty_state()

    # ---- Running rows
    run = page.section("Running rows", "Applies to every action row.")
    win.auto_item_enable_chk = K.Switch()
    run.row("Action rows", "Run while the manager is running. Auto SC + BR needs this on too", win.auto_item_enable_chk)
    win.auto_item_antiafk_overdue_minutes_spin = _spin(
        QDoubleSpinBox, 1.0, 15.0, G._ANTIAFK_AUTO_ACTION_OVERDUE_MIN_DEFAULT, " min", step=0.5, decimals=1)
    run.row("Let Anti-AFK through after", "Rows stop holding Anti-AFK back once an account has waited this long",
            win.auto_item_antiafk_overdue_minutes_spin)
    win.auto_item_disable_mouse_chk = K.Switch()
    run.row("Lock your mouse while a row runs", "Stops you knocking a click off target", win.auto_item_disable_mouse_chk)
    win.auto_item_min_fps_spin = _spin(QSpinBox, 1, 240, 60, " FPS")
    run.row("Lowest Roblox FPS", "Your slowest window, down to 1. Clicks wait for its frames. Also used by Auto SC + BR",
            win.auto_item_min_fps_spin)
    win.auto_item_hotkey_edit = QKeySequenceEdit()
    try:
        win.auto_item_hotkey_edit.setKeySequence(QKeySequence("Ctrl+Alt+Space"))
    except Exception:
        pass
    win.auto_item_hotkey_edit.setFixedWidth(220)
    hk_help = "Turns every action row on or off from anywhere. Click the box, then press the keys"
    hk_row = run.row("On and off hotkey", hk_help, win.auto_item_hotkey_edit)
    win.auto_item_menu_debug_chk = K.Switch()
    run.row("Log when an account is in the menu as a row starts", "Writes the window, log file, biome and menu state",
            win.auto_item_menu_debug_chk)

    win.auto_item_user_filter_mode_combo = QComboBox()
    win.auto_item_user_filter_mode_combo.addItem("Only these", "whitelist")
    win.auto_item_user_filter_mode_combo.addItem("All except these", "blacklist")
    win.auto_item_user_filter_mode_combo.setProperty("previous_mode", "whitelist")
    win.auto_item_user_filter_mode_combo.setFixedWidth(200)
    who = run.row("Who it runs on", " ", win.auto_item_user_filter_mode_combo)
    win.auto_item_users_hint = who.help_label
    win.auto_item_user_search_input = QLineEdit()
    win.auto_item_user_search_input.setPlaceholderText("Search username or user id")
    win.auto_item_user_search_input.setClearButtonEnabled(True)
    run.add(K.hbox(
        win.auto_item_user_search_input,
        K.button("Select all", lambda: win._auto_item_set_all_users(True), size="sm"),
        K.button("Select none", lambda: win._auto_item_set_all_users(False), size="sm"),
    ))
    run.layout().addSpacing(8)
    win.auto_item_user_list = QListWidget()
    win.auto_item_user_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    win.auto_item_user_list.setUniformItemSizes(True)
    win.auto_item_user_list.setFixedHeight(240)
    win.auto_item_user_list.setStyleSheet(
        f"QListWidget::item {{ min-height: 30px; padding-left: 4px; }}"
        f"QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid {K.T['line']}; border-radius: 4px;"
        f" background: {K.T['app']}; }}"
        f"QListWidget::indicator:checked {{ background: {K.T['ink']}; border-color: {K.T['ink']}; }}"
    )

    def _user_item_changed(item) -> None:
        cb = (getattr(win, "auto_item_user_checks", {}) or {}).get(str(item.data(Qt.ItemDataRole.UserRole)))
        if isinstance(cb, _UserCheck):
            cb.changed()

    win.auto_item_user_list.itemChanged.connect(_user_item_changed)
    run.add(win.auto_item_user_list)
    win.auto_item_user_checks = {}
    win._auto_item_user_list_initialized = False

    def _reset() -> None:
        if QMessageBox.question(
            win, "Restore defaults",
            "Put action rows back how they came? This removes your rows and saved presets.",
        ) == QMessageBox.StandardButton.Yes:
            win._reset_auto_item_to_defaults()

    run.action("Defaults", "Put action rows, presets and these settings back how they came", "Restore defaults", _reset)

    def _hotkey_status(*_args) -> None:
        if getattr(win, "_loading_autoitem_settings", False):
            # load / restore defaults register the hotkey after this signal: read the result once they finish
            QTimer.singleShot(0, _hotkey_status)
            return
        seq = win.auto_item_hotkey_edit.keySequence().toString().strip()
        if not seq:
            note = ". Off: no hotkey set"
        elif sys.platform == "win32" and not getattr(win, "_auto_item_hotkey_registered", False):
            note = ". Could not register it; another app may be using it"
        else:
            note = ""
        hk_row.help_label.setText(hk_help + note)

    # ---- state + wiring (as the old builder)
    win._auto_item_presets = []
    if not isinstance(getattr(win, "_auto_item_save_timer", None), QTimer):
        win._auto_item_save_timer = QTimer(win)
        win._auto_item_save_timer.setSingleShot(True)
        win._auto_item_save_timer.timeout.connect(win._save_auto_item_settings)
    if not isinstance(getattr(win, "_auto_item_users_refresh_timer", None), QTimer):
        win._auto_item_users_refresh_timer = QTimer(win)
        win._auto_item_users_refresh_timer.setInterval(1000)
        win._auto_item_users_refresh_timer.timeout.connect(win._auto_item_refresh_users_if_changed)

    win.auto_item_enable_chk.toggled.connect(win._on_auto_item_enabled_toggled)
    win.auto_item_min_fps_spin.valueChanged.connect(win._on_auto_item_ui_changed)
    win.auto_item_disable_mouse_chk.toggled.connect(win._on_auto_item_mouse_toggle_changed)
    win.auto_item_menu_debug_chk.toggled.connect(win._on_auto_item_ui_changed)
    win.auto_item_antiafk_overdue_minutes_spin.valueChanged.connect(win._on_auto_item_ui_changed)
    win.auto_item_hotkey_edit.keySequenceChanged.connect(win._on_auto_item_hotkey_changed)
    win.auto_item_hotkey_edit.keySequenceChanged.connect(_hotkey_status)
    win.auto_item_user_filter_mode_combo.currentIndexChanged.connect(win._on_auto_item_user_filter_mode_changed)
    win.auto_item_user_search_input.textChanged.connect(win._auto_item_filter_users)

    try:
        win._update_auto_item_disable_mouse_tooltip()
    except Exception:
        pass
    win._auto_item_refresh_users()
    win._load_auto_item_settings()
    win._ensure_auto_item_engine()
    win._auto_item_users_refresh_timer.start()
    _hotkey_status()
    return page
