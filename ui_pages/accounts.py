"""Accounts page: the account list (model/view), the bulk bar, the detail panel, and the Add account,
Delete in bulk and Import from Account Manager dialogs.

Replaces the old Users tab, Accounts tab, Manage Users dialog and RAM Export dialog. The list view is also
`win.users_table` and `win.accounts_list`: it answers the QTableWidget calls the old logic makes
(item(row, 0).text(), currentRow(), rowCount(), selectionModel().selectedRows()), so _get_selected_user_ids,
show_selected_user_window, kill_selected_user, disable_selected_users... keep working untouched.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime
from typing import Dict, List, Optional

from PySide6.QtCore import (
    QAbstractTableModel, QItemSelectionModel, QModelIndex, QObject, QRectF, QSortFilterProxyModel, QStringListModel, Qt, Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
    QLineEdit, QListView, QMenu, QMessageBox, QRadioButton, QScrollArea, QSpinBox, QSplitter, QStyle,
    QStyledItemDelegate, QStyleOptionViewItem, QTableView, QTabWidget, QVBoxLayout, QWidget,
)

import ui_kit as K
from biomes import biome_duration, biome_meta
from discord_account_control import format_discord_ids, normalize_discord_ids
from launch_priority import DEFAULT_LAUNCH_PRIORITY, coerce_launch_priority, launch_queue_sort_key
from ui_pages.overview import natural_key

try:
    from roblox_cookie_utils import (
        extract_roblosecurity_from_selenium_driver, normalize_roblosecurity_cookie_value, persist_updated_cookie,
    )
except Exception:  # same optional import as gui.py
    extract_roblosecurity_from_selenium_driver = normalize_roblosecurity_cookie_value = persist_updated_cookie = None

UID_ROLE = int(Qt.ItemDataRole.UserRole) + 1
SUB_ROLE = int(Qt.ItemDataRole.UserRole) + 2
DEFAULT_PLACE_ID = "15532962292"  # Sol's RNG (UserManagementDialog._default_place_id)
_YES, _NO = QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.No

# status key -> (glyph, chip). Glyph shapes follow the prototype: filled = in game, ring = menu, square = offline.
STATUS = {
    "ok": ("\u25cf", "ok"), "spare": ("\u25c6", "offline"), "menu": ("\u25cb", "menu"), "offline": ("\u25a1", "offline"),
    "captcha": ("\u25b3", "flag"), "bad": ("\u25b3", "flag"), "disabled": ("\u2013", "disabled"),
}
CHIPS = [("all", "All"), ("ok", "In game"), ("menu", "In menu"), ("offline", "Offline"),
         ("flag", "Flagged"), ("disabled", "Disabled")]
SOL = (("sol_sc", "Strange Controller", "SC"), ("sol_br", "Biome Randomizer", "BR"))


def _G(win):
    """gui.py's module (it may run as __main__): its private helpers live there."""
    return sys.modules[type(win).__module__]


def _dur(s) -> str:
    if s is None:
        return ""
    s = max(0, int(s))
    if s < 60:
        return f"{s} s"
    if s < 3600:
        return f"{s // 60} min"
    if s < 2 * 86400:
        return f"{s // 3600} h {s % 3600 // 60} min"
    return f"{s // 86400} days"


def _when(ts: float) -> str:
    if not ts or ts <= 0:
        return "Never"
    d = datetime.fromtimestamp(ts)
    day = "Today" if d.date() == datetime.now().date() else d.strftime("%d %b %Y")
    return f"{day} at {d.strftime('%H:%M')} ({_dur(time.time() - ts)} ago)"


def _clock_ts(hms, now: float) -> float:
    """'HH:MM:SS' (process_data 'created') -> the latest past timestamp with that clock time."""
    try:
        t = datetime.combine(datetime.fromtimestamp(now).date(), datetime.strptime(str(hms), "%H:%M:%S").time())
    except Exception:
        return 0.0
    ts = t.timestamp()
    return ts - 86400 if ts > now + 60 else ts


def _manager_running(win) -> bool:
    wt = getattr(win, "worker_thread", None)
    try:
        return bool(wt and wt.isRunning())
    except Exception:
        return False


def _status(cfg: dict, rt: dict, in_menu, running: bool) -> tuple:
    """Same order and words as the Overview tiles (overview.OverviewPage._tiles)."""
    if cfg.get("cap"):
        return "captcha", "Needs a captcha"
    if cfg.get("bad"):
        return "bad", "Bad cookie"
    if cfg.get("backup"):  # with-solver: reserve account, held out of launch
        return "disabled", "Backup"
    if cfg.get("disabled"):
        return "disabled", "Disabled"
    if not running:  # user_data keeps the last states after a stop
        return "offline", "Stopped"
    raw = str(rt.get("status") or "Offline")
    if raw.startswith("Standby"):
        return "spare", "Spare, waiting"
    if "Restarting" in raw:
        return "offline", "Offline, restarting"
    if raw == "Active" and not rt.get("needs_restart"):
        return ("menu", "In menu") if in_menu else ("ok", "In game")
    return "offline", "Offline"


def _row_has(row: dict, uid: str) -> bool:
    users = row.get("users")
    if not row.get("users_explicit") or not isinstance(users, (list, tuple)):
        return True
    hit = uid in {str(u).strip() for u in users}
    return (not hit) if str(row.get("user_filter_mode", "whitelist")).lower() == "blacklist" else hit


# ---------------------------------------------------------------- model / view

class AccountsModel(QAbstractTableModel):
    HEAD = ("", "Account", "Biome", "In game", "Anti-AFK", "Automations")

    def __init__(self):
        super().__init__()
        self.rows: List[dict] = []
        self._glyph_font = QFont()
        self._glyph_font.setPixelSize(16)

    def rowCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QModelIndex()):  # noqa: N802
        return 0 if parent.isValid() else len(self.HEAD)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):  # noqa: N802
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return self.HEAD[section]
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r, c = self.rows[index.row()], index.column()
        if role == UID_ROLE:
            return r["uid"]
        if role == Qt.ItemDataRole.DisplayRole:
            return (STATUS[r["key"]][0], r["name"], r["biome"], _dur(r["in_game"]),
                    _dur(r["afk"]) + " ago" if r["afk"] is not None else "", r["autos"])[c]
        if role == SUB_ROLE:
            return r["status"]
        if role == Qt.ItemDataRole.ForegroundRole:
            if c == 0 and r["key"] in ("captcha", "bad"):
                return QColor(K.T["warn"])
            return QColor(K.T["ink2"] if c in (0, 5) else K.T["ink"])
        if role == Qt.ItemDataRole.FontRole and c == 0:
            return self._glyph_font
        if role == Qt.ItemDataRole.TextAlignmentRole and c == 0:
            return int(Qt.AlignmentFlag.AlignCenter)
        return None

    def set_rows(self, rows: List[dict]) -> bool:
        """Same accounts in the same order: one dataChanged. Otherwise a reset (returns True)."""
        if [r["uid"] for r in rows] == [r["uid"] for r in self.rows]:
            self.rows = rows
            if rows:
                self.dataChanged.emit(self.index(0, 0), self.index(len(rows) - 1, len(self.HEAD) - 1))
            return False
        self.beginResetModel()
        self.rows = rows
        self.endResetModel()
        return True


class AccountsFilter(QSortFilterProxyModel):
    def __init__(self):
        super().__init__()
        self.chip = "all"
        self.query = ""

    def set(self, chip: Optional[str] = None, query: Optional[str] = None) -> None:
        if chip is not None:
            self.chip = chip
        if query is not None:
            self.query = query.strip().lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, _parent):  # noqa: N802
        r = self.sourceModel().rows[row]
        if self.chip != "all" and STATUS[r["key"]][1] != self.chip:
            return False
        q = self.query
        return not q or q in r["name"].lower() or q in r["uid"] or q in r["biome"].lower()


class _Item:
    """What old code gets from table.item(row, 0): the account id."""

    def __init__(self, uid: str):
        self._uid = uid

    def text(self) -> str:
        return self._uid

    def data(self, _role=None) -> str:
        return self._uid


class AccountsView(QTableView):
    """The account list. Also answers the QTableWidget calls the old logic makes on users_table / accounts_list."""

    # (column, viewport width needed to show it): narrow windows drop the extras instead of crushing the name
    _NEED = ((5, 602), (4, 496), (3, 396))

    def resizeEvent(self, e):  # noqa: N802
        super().resizeEvent(e)
        w = self.viewport().width()
        for col, need in self._NEED:
            self.setColumnHidden(col, w < need)

    def item(self, row: int, _col: int = 0) -> Optional[_Item]:
        m = self.model()
        if m is None or not 0 <= row < m.rowCount():
            return None
        return _Item(str(m.index(row, 0).data(UID_ROLE)))

    def currentRow(self) -> int:  # noqa: N802
        return self.currentIndex().row()

    def rowCount(self) -> int:  # noqa: N802
        return self.model().rowCount() if self.model() is not None else 0

    def selected_uids(self) -> List[str]:
        rows = sorted(i.row() for i in self.selectionModel().selectedRows())
        return [str(self.model().index(r, 0).data(UID_ROLE)) for r in rows]

    def shown_uids(self) -> List[str]:
        m = self.model()
        return [str(m.index(r, 0).data(UID_ROLE)) for r in range(m.rowCount())]


class AccountsDelegate(QStyledItemDelegate):
    """Account column: bold name over a grey status line. Biome column: a coloured chip."""

    def paint(self, p: QPainter, opt, index):
        col = index.column()
        if col not in (1, 2):
            return super().paint(p, opt, index)
        o = QStyleOptionViewItem(opt)
        self.initStyleOption(o, index)
        o.text = ""
        o.widget.style().drawControl(QStyle.ControlElement.CE_ItemViewItem, o, p, o.widget)
        r = opt.rect.adjusted(10, 0, -6, 0)
        text = str(index.data() or "")
        shown = text
        p.save()
        if col == 1:
            f = QFont(opt.font)
            f.setBold(True)
            p.setFont(f)
            p.setPen(QColor(K.T["ink"]))
            half = r.height() // 2
            p.drawText(r.adjusted(0, 0, 0, -half), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom),
                       p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, r.width()))
            f.setBold(False)
            f.setPixelSize(12)
            p.setFont(f)
            p.setPen(QColor(K.T["ink2"]))
            p.drawText(r.adjusted(0, half + 2, 0, 0), int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop),
                       p.fontMetrics().elidedText(str(index.data(SUB_ROLE) or ""), Qt.TextElideMode.ElideRight, r.width()))
        elif text:
            f = QFont(opt.font)
            f.setPixelSize(12)
            f.setBold(True)
            p.setFont(f)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            shown = p.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, r.width() - 16)
            w = min(r.width(), p.fontMetrics().horizontalAdvance(shown) + 16)
            chip = QRectF(r.left(), r.center().y() - 11, w, 22)
            if text == "NORMAL":
                p.setPen(QColor(K.T["rule"]))
                p.setBrush(Qt.BrushStyle.NoBrush)
                fg = QColor(K.T["ink2"])
            else:
                bg = QColor(f"#{biome_meta(text)[0] & 0xFFFFFF:06x}")
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(bg)
                fg = QColor("#10161C" if bg.lightness() > 140 else "#FFFFFF")
            p.drawRoundedRect(chip, 4, 4)
            p.setPen(fg)
            p.drawText(chip, int(Qt.AlignmentFlag.AlignCenter), shown)
        p.restore()
        return None


class _Bridge(QObject):
    """Background threads (manual launch, browser) talk to the UI through this."""
    log = Signal(str)
    launch_done = Signal()


# ---------------------------------------------------------------- the page

class AccountsUI:
    def __init__(self, win, shell):
        self.win = win
        self.shell = shell
        self.uid: Optional[str] = None  # account shown in the detail panel
        self._loading = False
        self.bridge = _Bridge()
        self.bridge.log.connect(win.add_log)
        self.bridge.launch_done.connect(lambda: win.launch_roblox_btn.setEnabled(True))
        self._sol_cache: dict = {}
        self.page = K.Page("accounts", "Accounts", "Every account, what it is doing, and its settings.", shell.search)
        self._build_top()
        self._build_body()
        self._build_add_dialog()
        self._build_bulk_dialog()
        self._build_import_dialog()
        lay = self.page.layout()
        lay.setStretch(0, 1)  # the custom area (list + detail) fills the page; there are no sections
        lay.setStretch(2, 0)
        shell.stack.currentChanged.connect(self._on_page_changed)

    # ---------- layout
    def _build_top(self) -> None:
        win = self.win
        win.accounts_search_input = QLineEdit()
        win.accounts_search_input.setPlaceholderText("Find by name, user id or biome")
        win.accounts_search_input.setClearButtonEnabled(True)
        win.accounts_search_input.setFixedWidth(260)
        win.accounts_search_input.textChanged.connect(lambda t: (self.proxy.set(query=t), self._update_count()))
        self.chip_group = QButtonGroup(self.page)
        self.chip_group.setExclusive(True)
        self.chips: Dict[str, object] = {}
        chip_row = []
        for key, text in CHIPS:
            c = K.chip(text, key == "all")
            c.toggled.connect(lambda on, k=key: on and (self.proxy.set(chip=k), self._update_count()))
            self.chip_group.addButton(c)
            self.chips[key] = c
            chip_row.append(c)
        add_btn = K.button("Add account", self.open_add_dialog, kind="ink")
        self.page.custom(K.hbox(win.accounts_search_input, *chip_row, None, add_btn))

        sm = "sm"
        clear_menu = QMenu(self.page)
        clear_menu.addAction("All flags", lambda: self.clear_flags(("bad", "cap")))
        clear_menu.addAction("Captcha lock only", lambda: self.clear_flags(("cap",)))
        clear_menu.addAction("Bad cookie only", lambda: self.clear_flags(("bad",)))
        win.clear_all_flags_btn = K.button("Clear flags", size=sm)
        win.clear_all_flags_btn.setMenu(clear_menu)
        more = QMenu(self.page)
        more.addAction("Delete in bulk\u2026", self.open_bulk_dialog)
        more.addAction("Import from Account Manager\u2026", self.open_import_dialog)
        from ui_pages.system import _page_hook
        _page_hook(win, more, "account_menu_actions", ("solver",))  # with-solver build: solver and backup actions
        more_btn = K.button("More", size=sm)
        more_btn.setMenu(more)
        self.page.custom(K.hbox(
            K.button("Launch", lambda: self.launch(self.targets()), size=sm),
            K.button("Restart", lambda: self.restart(self.targets()), size=sm),
            K.button("Close Roblox", lambda: self.close_roblox(self.targets()), size=sm),
            K.button("Show windows", lambda: self.show_windows(self.targets()), size=sm),
            K.button("Hide windows", lambda: self.hide_windows(self.targets()), size=sm),
            K.button("Enable", lambda: self.set_disabled(self.targets(), False), size=sm),
            K.button("Disable", lambda: self.set_disabled(self.targets(), True), size=sm),
            win.clear_all_flags_btn,
            K.button("Delete", lambda: self.delete(self.targets()), kind="warn", size=sm),
            more_btn, None, spacing=6))

    def _build_body(self) -> None:
        win = self.win
        self.model = AccountsModel()
        self.proxy = AccountsFilter()
        self.proxy.setSourceModel(self.model)
        view = AccountsView()
        view.setModel(self.proxy)
        view.setItemDelegate(AccountsDelegate(view))
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setShowGrid(False)
        view.setWordWrap(False)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.verticalHeader().setVisible(False)
        view.verticalHeader().setDefaultSectionSize(50)
        view.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
        hh = view.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        for col, width in enumerate((30, 150, 120, 96, 100, 106)):
            view.setColumnWidth(col, width)
        hh.setMinimumSectionSize(30)
        hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        view.selectionModel().selectionChanged.connect(lambda *_: self._update_count())
        view.selectionModel().currentRowChanged.connect(self._on_current)
        self.view = view
        win.users_table = view      # old Users tab table
        win.accounts_list = view    # old Accounts tab table

        self.count_label = K.label("", "hint", wrap=True)
        self.clear_sel_btn = K.button("Clear selection", view.clearSelection, size="sm")
        left = QWidget()
        left.setObjectName("clear")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(6)
        ll.addWidget(view, 1)
        foot = QHBoxLayout()
        foot.addWidget(self.count_label, 1)
        foot.addWidget(self.clear_sel_btn, 0, Qt.AlignmentFlag.AlignTop)
        foot_w = QWidget()
        foot_w.setObjectName("clear")
        foot_w.setLayout(foot)
        foot.setContentsMargins(0, 0, 0, 0)
        foot_w.setMinimumHeight(self.clear_sel_btn.sizeHint().height())  # the list does not jump when the button shows
        ll.addWidget(foot_w)

        split = QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        split.setHandleWidth(14)
        split.addWidget(left)
        split.addWidget(self._build_detail())
        split.setStretchFactor(0, 1)
        split.setSizes([500, 540])
        self.page.custom(split, 1)

    def _section(self, tabs: QTabWidget, title: str) -> K.Section:
        sec = K.Section(self.page, title)
        sec.show_title(False)
        holder = QWidget()
        holder.setObjectName("clear")
        hl = QVBoxLayout(holder)
        hl.setContentsMargins(16, 0, 16, 12)
        hl.addWidget(sec)
        hl.addStretch(1)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setWidget(holder)
        tabs.addTab(scroll, title)
        return sec

    def _info(self, sec: K.Section, text: str) -> QFrame:
        fr = sec.row(text, " ", searchable=False)
        fr.help_label.setVisible(True)
        return fr

    def _build_detail(self) -> QWidget:
        win = self.win
        panel = QFrame()
        panel.setObjectName("accDetail")
        panel.setStyleSheet(f"QFrame#accDetail {{ border: 1px solid {K.T['rule']}; border-radius: 8px; }}"
                            "QTabBar::tab { padding: 0 7px; font-size: 13px; }")
        panel.setMinimumWidth(520)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 16, 0, 0)
        lay.setSpacing(4)
        self.d_name = K.label("No account selected", "h2")
        self.d_line = K.label("", "help", wrap=True)
        self.d_disable = K.button("Disable account", lambda: self.uid and self.set_disabled(
            [self.uid], not bool(self._cfg(self.uid).get("disabled"))), kind="warn", size="sm")
        head = QVBoxLayout()
        head.setContentsMargins(16, 0, 16, 8)
        head.setSpacing(4)
        head.addWidget(K.hbox(self.d_name, None, self.d_disable))
        head.addWidget(self.d_line)
        head.addSpacing(6)
        self.d_actions = K.hbox(
            K.button("Restart", lambda: self.uid and self.restart([self.uid]), size="sm"),
            K.button("Show window", lambda: self.uid and self.show_windows([self.uid]), size="sm"),
            K.button("Close Roblox", lambda: self.uid and self.close_roblox([self.uid]), size="sm"),
            None, spacing=6)
        head.addWidget(self.d_actions)
        head.addStretch(1)  # with no account shown the text stays at the top instead of spreading over the panel
        lay.addLayout(head)
        tabs = QTabWidget()
        lay.addWidget(tabs, 1)
        self.d_body = tabs

        # Overview
        s = self._section(tabs, "Overview")
        self.o = {k: self._info(s, k) for k in (
            "Biome now", "In game for", "Anti-AFK last ran", "Roblox processes", "Time to live", "Last active",
            "Inactive for", "Server", "Events", "Last merchant", "Last launched")}
        self.prio_spin = QSpinBox()
        self.prio_spin.setRange(-3, 3)
        self.prio_spin.setFixedWidth(80)
        self.prio_spin.valueChanged.connect(self._on_priority)
        s.row("Launch priority", "Higher tiers launch first, from -3 to +3", self.prio_spin, searchable=False)

        # Automations
        s = self._section(tabs, "Automations")
        self.sol_switch = {}
        for pid, name, _short in SOL:
            sw = K.Switch()
            sw.toggled.connect(lambda on, p=pid: self._on_sol(p, on))
            self.sol_switch[pid] = sw
            self.sol_switch[pid + "_row"] = s.row(name, "", sw, searchable=False)
        self.a_afk = self._info(s, "Anti-AFK")
        self.a_ocr = s.row("Merchant detection (chat)", " ",
                           K.button("Choose accounts", self._open_ocr_accounts, size="sm"), searchable=False)
        self.exempt_switch = K.Switch()
        self.exempt_switch.toggled.connect(self._on_exempt)
        self.a_exempt = s.row("Hardware limiting exempt", "On keeps CPU and RAM limits off this account (3 at most)",
                              self.exempt_switch, searchable=False)

        # Webhooks
        s = self._section(tabs, "Webhooks")
        self.w_biome = self._info(s, "Biome alerts")
        self.w_items = self._info(s, "Item-used alerts")

        # Server and launch
        s = self._section(tabs, "Server and launch")
        win.private_server_input = QLineEdit()
        win.private_server_input.setPlaceholderText("https://www.roblox.com/share?code=...")
        win.place_input = QLineEdit()
        win.place_input.setPlaceholderText("Public server of this place")
        for w in (win.private_server_input, win.place_input):
            w.setFixedWidth(210)
            w.editingFinished.connect(self._on_server_fields)
        s.row("Private server link", "Its own server. Leave empty to use the place id", win.private_server_input,
              searchable=False)
        s.row("Or a place id", "Public server of this place. Empty means Sol's RNG", win.place_input, searchable=False)
        self.reconnect_switch = K.Switch()
        self.reconnect_switch.toggled.connect(
            lambda on: self._save(self.uid, skip_reconnect_on_log_disconnect=not on))
        s.row("Reconnect after a log disconnect", "Turn off for accounts you watch by hand", self.reconnect_switch,
              searchable=False)
        win.launch_server_link_input = QLineEdit()
        win.launch_server_link_input.setPlaceholderText("Server link")
        win.launch_place_id_input = QLineEdit()
        win.launch_place_id_input.setPlaceholderText(f"Place id ({DEFAULT_PLACE_ID})")
        win.launch_roblox_btn = K.button("Launch", self._launch_once, size="sm")
        s.row("Join another server once", "Uses this link, or this place id, without saving it. With several "
              "accounts selected, launches all of them", win.launch_roblox_btn, searchable=False)
        s.add(K.hbox(win.launch_server_link_input, win.launch_place_id_input))
        win.browser_any_link_input = QLineEdit()
        win.browser_any_link_input.setPlaceholderText("Any link, or empty for Roblox home")
        win.open_browser_link_btn = K.button("Open", self._open_browser, size="sm")
        s.row("Open in a browser", "Signed in with this account's cookie. With several accounts selected, opens "
              "one browser each", win.open_browser_link_btn, searchable=False)
        s.add(win.browser_any_link_input)

        # Details and flags
        s = self._section(tabs, "Details and flags")
        win.username_input = QLineEdit()
        win.username_input.setFixedWidth(210)
        win.username_input.editingFinished.connect(self._on_username)
        self.e_user = s.row("Username", " ", win.username_input, searchable=False)
        win.browser_login_btn = K.button("Log in with a browser", self._login_browser, size="sm")
        self.e_cookie = s.row("Cookie", " ", win.browser_login_btn,
                              K.button("Paste cookie", self._paste_cookie, size="sm"), searchable=False)
        win.launch_description_input = QLineEdit()
        win.launch_description_input.setPlaceholderText("Only you see this")
        win.launch_description_input.setFixedWidth(210)
        win.launch_description_input.editingFinished.connect(self._on_note)
        s.row("Note", "", win.launch_description_input, searchable=False)
        win.discord_ids_input = QLineEdit()
        win.discord_ids_input.setPlaceholderText("123456789012345678")
        win.discord_ids_input.setFixedWidth(210)
        win.discord_ids_input.editingFinished.connect(self._on_discord_ids)
        s.row("Discord user ids", "These Discord users own this account in the bot and are pinged by "
              "{discord_ping}. Separate several with commas", win.discord_ids_input, searchable=False)
        win.bad_chk = K.Switch()
        win.bad_chk.toggled.connect(lambda on: self._save(self.uid, bad=bool(on)))
        s.row("Bad cookie", "Set when the cookie stops working. The account is skipped until cleared", win.bad_chk,
              searchable=False)
        win.cap_chk = K.Switch()
        win.cap_chk.toggled.connect(lambda on: self._save(self.uid, cap=bool(on)))
        s.row("Captcha lock", "Set when Roblox wants a captcha", win.cap_chk, searchable=False)
        win.alternate_chk = K.Switch()
        win.alternate_chk.toggled.connect(self._on_alternate)
        s.row("Alternate", "Launches without a cookie through roblox://. Only one account can have it",
              win.alternate_chk, searchable=False)
        s.row("Clear flags", "Clears bad cookie and captcha lock",
              K.button("Clear", lambda: self.uid and self.clear_flags(("bad", "cap"), [self.uid]), size="sm"),
              searchable=False)
        s.row("Delete account", "Removes it and its saved cookie",
              K.button("Delete", lambda: self.uid and self.delete([self.uid]), kind="warn", size="sm"),
              searchable=False)
        return panel

    # ---------- dialogs
    def _dialog(self, title: str, lead: str, width: int = 560) -> tuple:
        dlg = QDialog(self.page)  # the page's theme, centred on the window
        dlg.setWindowTitle(title)
        dlg.setMinimumWidth(width)
        lay = QVBoxLayout(dlg)
        lay.setContentsMargins(24, 20, 24, 20)
        lay.setSpacing(10)
        lay.addWidget(K.label(title, "h2"))
        lay.addWidget(K.label(lead, "lead", wrap=True))
        return dlg, lay

    def _build_add_dialog(self) -> None:
        win = self.win
        dlg, lay = self._dialog("Add an account", "Log in once; the cookie is saved on this PC.", 620)
        self.add_dialog = dlg
        win.account_browser_login_btn = K.button("Log in with a browser", win.extract_account_cookie_from_browser,
                                                 kind="ink")
        lay.addWidget(win.account_browser_login_btn)
        lay.addWidget(K.label("Or paste the details yourself", "hint"))
        win.account_cookie = QLineEdit()
        win.account_cookie.setEchoMode(QLineEdit.EchoMode.Password)
        win.account_cookie.setPlaceholderText(".ROBLOSECURITY cookie")
        win.account_username = QLineEdit()
        win.account_username.setPlaceholderText("PlayerName")
        win.account_user_id = QLineEdit()
        win.account_user_id.setPlaceholderText("123456789")
        win.account_private_link = QLineEdit()
        win.account_private_link.setPlaceholderText("https://www.roblox.com/share?code=...")
        win.account_place_id = QLineEdit()
        win.account_place_id.setPlaceholderText("Public server of this place")
        grid = QGridLayout()  # username and user id share a line: the dialog fits a 720 px high screen
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for text, w, row, col, span in (
                ("Cookie", win.account_cookie, 0, 0, 2), ("Username", win.account_username, 2, 0, 1),
                ("User id", win.account_user_id, 2, 1, 1), ("Private server link", win.account_private_link, 4, 0, 2),
                ("Or a place id", win.account_place_id, 6, 0, 2)):
            lb = K.label(text, "label")
            lb.setContentsMargins(0, 8 if row else 0, 0, 0)
            grid.addWidget(lb, row, col, 1, span)
            grid.addWidget(w, row + 1, col, 1, span)
        lay.addLayout(grid)
        win.account_alternate_launch = K.Switch()
        win.account_alternate_launch.toggled.connect(win._on_account_alternate_launch_toggled)
        sec = K.Section(self.page, "")
        sec.show_title(False)
        sec.row("Alternate", "No cookie needed: launches through roblox://. Only one account can have it",
                win.account_alternate_launch, searchable=False)
        lay.addWidget(sec)
        # Not shown: the type follows which field is filled; disabled / log reconnects are set after adding.
        win.account_private_radio = QRadioButton(dlg)
        win.account_public_radio = QRadioButton(dlg)
        win.account_private_radio.setChecked(True)
        win.account_disabled = QCheckBox(dlg)
        win.account_skip_reconnect_on_log_disconnect = QCheckBox(dlg)
        for w in (win.account_private_radio, win.account_public_radio, win.account_disabled,
                  win.account_skip_reconnect_on_log_disconnect):
            w.hide()
        win.account_private_radio.toggled.connect(win.on_account_server_type_changed)

        def _sync_type(*_):
            public = bool(win.account_place_id.text().strip()) and not win.account_private_link.text().strip()
            (win.account_public_radio if public else win.account_private_radio).setChecked(True)
        win.account_private_link.textChanged.connect(_sync_type)
        win.account_place_id.textChanged.connect(_sync_type)

        win.clear_form_btn = K.button("Cancel", lambda: (win.clear_account_form(), dlg.reject()))
        win.add_account_btn = K.button("Add account", self._confirm_add, kind="ink")
        lay.addSpacing(6)
        lay.addWidget(K.hbox(None, win.clear_form_btn, win.add_account_btn))

    def _build_bulk_dialog(self) -> None:
        win = self.win
        G = _G(win)
        dlg, lay = self._dialog("Delete accounts in bulk", "Deletes every account that matches. Their saved "
                                "cookies go too.", 640)
        self.bulk_dialog = dlg
        sec = K.Section(self.page, "")
        sec.show_title(False)
        win.bulk_use_inactivity_chk = K.Switch(True)
        win.bulk_inactive_days_spin = QSpinBox()
        win.bulk_inactive_days_spin.setRange(1, 36500)
        win.bulk_inactive_days_spin.setValue(30)
        win.bulk_inactive_days_spin.setSuffix(" days")
        win.bulk_inactive_days_spin.setFixedWidth(110)
        sec.row("Not launched for at least", "Turn off to match by flags alone", win.bulk_inactive_days_spin,
                win.bulk_use_inactivity_chk, searchable=False)
        win.bulk_include_never_chk = K.Switch(False)
        sec.row("Include accounts that never launched", "", win.bulk_include_never_chk, searchable=False)
        win.bulk_flag_checks = {}
        for key, text in G._BULK_DELETE_FLAG_LABELS.items():
            win.bulk_flag_checks[key] = K.chip(text[:1] + text[1:].lower())
        win.bulk_flag_match_combo = K.Segmented(["Any", "All"], "Any")
        sec.row("Flags", "None picked: flags are ignored. Any: one is enough. All: needs each",
                win.bulk_flag_match_combo, searchable=False)
        sec.add(K.hbox(*win.bulk_flag_checks.values(), spacing=6, stretch_end=True))
        lay.addWidget(sec)
        win.bulk_preview_label = K.label("", "label")
        lay.addWidget(win.bulk_preview_label)
        self.bulk_preview_model = QStringListModel()
        win.bulk_preview_list = QListView()
        win.bulk_preview_list.setModel(self.bulk_preview_model)
        win.bulk_preview_list.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        win.bulk_preview_list.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        win.bulk_preview_list.setMinimumHeight(140)
        lay.addWidget(win.bulk_preview_list, 1)
        win.bulk_delete_all_btn = K.button("Delete all accounts", self._delete_all, kind="warn")
        win.bulk_delete_matching_btn = K.button("Delete matching accounts", self._delete_matching, kind="ink")
        lay.addWidget(K.hbox(win.bulk_delete_all_btn, None, K.button("Cancel", dlg.reject),
                             win.bulk_delete_matching_btn))

        def _controls(*_):
            on = win.bulk_use_inactivity_chk.isChecked()
            win.bulk_inactive_days_spin.setEnabled(on)
            win.bulk_include_never_chk.setEnabled(on)
            self._bulk_preview()
        win.bulk_use_inactivity_chk.toggled.connect(_controls)
        win.bulk_inactive_days_spin.valueChanged.connect(self._bulk_preview)
        win.bulk_include_never_chk.toggled.connect(self._bulk_preview)
        win.bulk_flag_match_combo.changed.connect(self._bulk_preview)
        for c in win.bulk_flag_checks.values():
            c.toggled.connect(self._bulk_preview)

    def _build_import_dialog(self) -> None:
        win = self.win
        dlg, lay = self._dialog("Import from Account Manager", "Reads accounts from Roblox Account Manager "
                                "running on this PC. Turn on \u201cAllow external connections\u201d there first.")
        self.import_dialog = dlg
        sec = K.Section(self.page, "")
        sec.show_title(False)
        win.ram_port_input = QLineEdit("7963")
        win.ram_group_input = QLineEdit()
        win.ram_group_input.setPlaceholderText("All groups")
        win.ram_pwd_input = QLineEdit()
        win.ram_pwd_input.setEchoMode(QLineEdit.EchoMode.Password)
        for w in (win.ram_port_input, win.ram_group_input, win.ram_pwd_input):
            w.setFixedWidth(200)
        sec.row("Port", "Restart Account Manager after changing its port", win.ram_port_input, searchable=False)
        sec.row("Group", "Leave empty to import every group", win.ram_group_input, searchable=False)
        sec.row("Password", "Only if Account Manager has one set", win.ram_pwd_input, searchable=False)
        win.merge_chk = K.Switch(True)
        win.replace_cookie_chk = K.Switch(False)
        win.replace_ps_chk = K.Switch(False)
        sec.row("Merge with my accounts", "Off replaces every account with the imported ones", win.merge_chk,
                searchable=False)
        sec.row("Overwrite cookies", "Also clears bad cookie and captcha lock on those accounts",
                win.replace_cookie_chk, searchable=False)
        sec.row("Overwrite private servers", "Takes the private server link and place id from Account Manager",
                win.replace_ps_chk, searchable=False)

        def _merge_toggled(checked: bool):
            win.replace_cookie_chk.setEnabled(checked)
            win.replace_ps_chk.setEnabled(checked)
        win.merge_chk.toggled.connect(_merge_toggled)
        _merge_toggled(win.merge_chk.isChecked())
        lay.addWidget(sec)

        def _run():
            win.execute_ram_import()
            self.refresh(full=True)
        lay.addWidget(K.hbox(None, K.button("Cancel", dlg.reject), K.button("Fetch and import", _run, kind="ink")))

    def open_add_dialog(self) -> None:
        self.win.clear_account_form()
        self.add_dialog.exec()

    def open_bulk_dialog(self) -> None:
        self._bulk_preview()
        self.bulk_dialog.exec()

    def open_import_dialog(self) -> None:
        self.import_dialog.exec()

    def _confirm_add(self) -> None:
        win = self.win
        uid = win.account_user_id.text().strip()
        existed = uid in (win.config_manager.peek_users() or {})
        win.add_account()
        if uid and not existed and uid in (win.config_manager.peek_users() or {}):
            self.add_dialog.accept()
            self.select(uid)

    # ---------- bulk delete
    def _bulk_ids(self) -> List[str]:
        win = self.win
        G = _G(win)
        users = win.config_manager.peek_users() or {}
        flags = [k for k, c in win.bulk_flag_checks.items() if c.isChecked()]
        now = time.time()
        ids = [str(uid) for uid, info in users.items() if G._matches_bulk_user_delete_filters(
            info, use_inactivity=win.bulk_use_inactivity_chk.isChecked(),
            inactive_days=win.bulk_inactive_days_spin.value(),
            include_never_launched=win.bulk_include_never_chk.isChecked(), selected_flags=flags,
            match_all_flags=win.bulk_flag_match_combo.currentText() == "All", now=now)]
        return sorted(ids, key=lambda u: (natural_key((users.get(u) or {}).get("username") or u), u))

    def _bulk_preview(self, *_):
        win = self.win
        G = _G(win)
        users = win.config_manager.peek_users() or {}
        ids = self._bulk_ids()
        lines = []
        for uid in ids:
            info = users.get(uid) or {}
            ts = G._coerce_activity_timestamp(info.get("last_launch", 0.0))
            flags = [t[:1] + t[1:].lower() for k, t in G._BULK_DELETE_FLAG_LABELS.items() if info.get(k)]  # as the chips
            lines.append(f"{info.get('username') or uid}  ({uid})   last launched: {_when(ts)}"
                         + (f"   flags: {', '.join(flags)}" if flags else ""))
        self.bulk_preview_model.setStringList(lines)
        win.bulk_preview_label.setText(f"{len(ids)} of {len(users)} accounts match")
        win.bulk_delete_matching_btn.setText(f"Delete {len(ids)} matching")
        win.bulk_delete_matching_btn.setEnabled(bool(ids))
        win.bulk_delete_all_btn.setEnabled(bool(users))

    def _delete_matching(self) -> None:
        ids = self._bulk_ids()
        if ids and self.delete(ids):
            self.bulk_dialog.accept()

    def _delete_all(self) -> None:
        ids = list(self.win.config_manager.peek_users() or {})
        if ids and self.delete(ids, f"Delete ALL {len(ids)} accounts? This ignores the filters and cannot be undone."):
            self.bulk_dialog.accept()

    # ---------- data
    def _cfg(self, uid: Optional[str]) -> dict:
        info = (self.win.config_manager.peek_users() or {}).get(str(uid or ""))
        return info if isinstance(info, dict) else {}

    def _sol_rows(self) -> dict:
        try:
            return self.win._sol_rows(self.win._current_auto_item_items())
        except Exception:
            return {}

    def refresh(self, full: bool = False) -> None:
        """Rebuild the rows from users.json + the worker's runtime data. full: also reload the detail fields."""
        win = self.win
        users = win.config_manager.peek_users() or {}
        runtime = {str(k): v for k, v in (getattr(win, "user_data", None) or {}).items()}
        uids = [str(u) for u in users] + [u for u in runtime if u not in users]
        uids.sort(key=lambda u: (natural_key((users.get(u) or {}).get("username") or u), u))  # as the Overview "Name" sort
        biomes = dict(getattr(win, "_ms_biome_by_uid", {}) or {})
        menus = dict(getattr(win, "_ms_in_menu_by_uid", {}) or {})
        try:
            with win._antiafk_touch_lock:
                touch = dict(win._antiafk_last_touch_by_uid)
        except Exception:
            touch = {}
        try:
            created = dict(win.worker_thread.manager.process_tracker.creation_timestamps or {})
        except Exception:
            created = {}
        procs = getattr(win, "process_data", None) or {}
        sol = self._sol_rows()
        afk_on = bool(getattr(win, "antiafk_enable_chk", None) and win.antiafk_enable_chk.isChecked())
        running = _manager_running(win)
        now = time.time()
        rows = []
        for uid in uids:
            cfg = users.get(uid) if isinstance(users.get(uid), dict) else {}
            rt = runtime.get(uid) or {}
            key, status = _status(cfg, rt, menus.get(uid), running)
            online = key in ("ok", "menu")  # a spare waits in the pool: not in game
            pids = rt.get("pids") or []
            pids = pids if isinstance(pids, (list, tuple)) else [pids]
            starts = [float(created[p]) for p in pids if p in created]
            if online and not starts:  # no tracker (resumed state): the "created" clock time from process_data
                starts = [t for t in (_clock_ts((procs.get(p) or procs.get(str(p)) or {}).get("created"), now)
                                      for p in pids) if t]
            autos = [short for pid, _n, short in SOL
                     if (sol.get(win._SOL_ROW_IDS[pid]) or {}).get("enabled") and _row_has(sol[win._SOL_ROW_IDS[pid]], uid)]
            if afk_on:
                autos.append("AFK")
            biome = str(biomes.get(uid) or "").strip()
            biome = biome.upper()  # the game's names, as on the Overview
            rows.append({
                "uid": uid, "name": str(cfg.get("username") or uid), "key": key, "status": status,
                "biome": (biome or "NORMAL") if online else "",
                "in_game": now - min(starts) if online and starts else None,
                "afk": now - float(touch[uid]) if online and uid in touch else None,
                "autos": ", ".join(autos) or "None",
            })
        keep = self.view.selected_uids()
        self._sol_cache = sol
        if self.model.set_rows(rows):
            sel = self.view.selectionModel()
            for uid in keep:
                r = self._proxy_row(uid)
                if r >= 0:
                    sel.select(self.proxy.index(r, 0), sel.SelectionFlag.Select | sel.SelectionFlag.Rows)
            r = self._proxy_row(self.uid or "")
            if r >= 0:
                sel.setCurrentIndex(self.proxy.index(r, 1), sel.SelectionFlag.NoUpdate)
        counts = {k: 0 for k, _ in CHIPS}
        counts["all"] = len(rows)
        for r in rows:
            counts[STATUS[r["key"]][1]] += 1
        for k, text in CHIPS:
            text = "Stopped" if k == "offline" and not running else text
            if self.chips[k].text() != f"{text} {counts[k]}":
                self.chips[k].setText(f"{text} {counts[k]}")
        self._update_count()
        if self.uid not in users:
            self.uid = None
            if self.proxy.rowCount():  # current (shown in the panel) but not selected: the bulk bar still acts on all
                self.view.selectionModel().setCurrentIndex(self.proxy.index(0, 1), QItemSelectionModel.SelectionFlag.NoUpdate)
            else:
                self.show(None)
        elif full:
            self.show(self.uid)
        else:
            self.fill()

    def _proxy_row(self, uid: str) -> int:
        for r in range(self.proxy.rowCount()):
            if self.proxy.index(r, 0).data(UID_ROLE) == uid:
                return r
        return -1

    def select(self, uid: Optional[str]) -> None:
        if not uid:
            return
        if self._proxy_row(uid) < 0:  # hidden by a filter: show everything
            self.win.accounts_search_input.clear()
            self.chips["all"].setChecked(True)
        if self._proxy_row(uid) < 0:
            self.refresh()
        r = self._proxy_row(uid)
        if r >= 0:
            idx = self.proxy.index(r, 1)
            sm = self.view.selectionModel()
            sm.setCurrentIndex(idx, sm.SelectionFlag.ClearAndSelect | sm.SelectionFlag.Rows)
            self.view.scrollTo(idx)

    def targets(self) -> List[str]:
        """The selected accounts, or every shown account when none is selected."""
        return self.view.selected_uids() or self.view.shown_uids()

    def _sel_or_current(self) -> List[str]:
        sel = self.view.selected_uids()
        return sel if len(sel) > 1 else ([self.uid] if self.uid else [])

    def _update_count(self) -> None:
        shown, total, sel = self.proxy.rowCount(), self.model.rowCount(), len(self.view.selected_uids())
        if not shown:
            self.count_label.setText(f"Showing 0 of {total} accounts. Nothing matches the filter or the search.")
            self.clear_sel_btn.setVisible(False)
            return
        what = ("1 selected; the buttons act on it." if sel == 1 else f"{sel} selected; the buttons act on them." if sel else
                f"None selected, so the buttons act on all {shown} shown. Ctrl or Shift + click picks several.")
        self.count_label.setText(f"Showing {shown} of {total} accounts. {what}")
        self.clear_sel_btn.setVisible(bool(sel))

    def _on_current(self, cur, _prev) -> None:
        uid = cur.data(UID_ROLE) if cur.isValid() else None
        if uid != self.uid:
            self.show(uid)

    def _on_page_changed(self, _index: int) -> None:
        if self.shell.stack.currentWidget() is self.page:
            self.refresh(full=True)

    # ---------- detail panel
    def show(self, uid: Optional[str]) -> None:
        """Load the account into the detail panel (editable fields included)."""
        win = self.win
        other = uid != self.uid
        self.uid = uid
        cfg = self._cfg(uid)
        for w in (self.d_body, self.d_actions, self.d_disable):
            w.setVisible(bool(uid))
        self._loading = True
        try:
            fields = [(win.username_input, cfg.get("username") or ""),
                      (win.private_server_input, cfg.get("private_server_link") or ""),
                      (win.place_input, cfg.get("place") or cfg.get("place_id") or ""),
                      (win.launch_description_input, cfg.get("description") or ""),
                      (win.discord_ids_input, format_discord_ids(cfg.get("discord_user_ids")))]
            if other:  # one-off launch target / browser link: kept while the same account stays shown
                fields += [(win.launch_server_link_input, ""), (win.launch_place_id_input, ""),
                           (win.browser_any_link_input, "")]
            for w, v in fields:
                if not w.hasFocus():
                    w.setText(str(v))
                    w.setCursorPosition(0)  # long links show their start
        finally:
            self._loading = False
        self.fill()

    def _set_switch(self, sw, on: bool) -> None:
        if sw.isChecked() != bool(on):
            sw.blockSignals(True)
            sw.setChecked(bool(on))
            sw.blockSignals(False)
            sw.update()

    def fill(self) -> None:
        """Refresh the read-only parts of the detail panel for the shown account."""
        win = self.win
        uid = self.uid
        if not uid:
            self.d_name.setText("No account selected")
            self.d_line.setText("Pick an account in the list, or add one.")
            return
        cfg = self._cfg(uid)
        row = next((r for r in self.model.rows if r["uid"] == uid), None) or {}
        rt = (getattr(win, "user_data", None) or {}).get(uid) or {}
        self.d_name.setText(str(cfg.get("username") or uid))
        psl = str(cfg.get("private_server_link") or "").strip()
        place = str(cfg.get("place") or cfg.get("place_id") or "").strip()
        if cfg.get("alternate_launch"):
            server = "alternate launch (no cookie)"
        elif psl:
            server = "share link" if "roblox.com/share" in psl else "own private server"
        else:
            server = f"public server of place {place}" if place else "Sol's RNG public server"
        biome = row.get("biome")
        self.d_line.setText(f"{row.get('status', 'Offline')}, {server}" + (f", biome {biome}" if biome else "")
                            + (", captcha solver off" if cfg.get("solver_enabled") is False else ""))
        self.d_disable.setText("Enable account" if cfg.get("disabled") else "Disable account")

        # Overview
        G = _G(win)
        ms = next((r for r in (getattr(win, "_last_multiscope_rows", None) or [])
                   if isinstance(r, dict) and uid in {str(u) for u in (r.get("users") or [])}), {})
        pids = rt.get("pids") or []
        pids = pids if isinstance(pids, (list, tuple)) else [pids]
        procs = getattr(win, "process_data", None) or {}
        created = [str((procs.get(p) or procs.get(str(p)) or {}).get("created") or "") for p in pids]
        biome_text = "Not known yet"
        if biome:
            age = ms.get("biome_age")
            dur = biome_duration(biome) if biome != "NORMAL" else None
            biome_text = biome + (f", {_dur(max(0, dur - int(age)))} left" if dur and age is not None else "")
        server_label = str(rt.get("server") or "")
        shared = [n for n in (str(self._cfg(u).get("username") or u) for u, r in
                  (getattr(win, "user_data", None) or {}).items()
                  if str(u) != uid and server_label and str((r or {}).get("server") or "") == server_label)]
        ttl = rt.get("ttl") or []
        ttl = ttl if isinstance(ttl, (list, tuple)) else [ttl]
        try:
            last_active = float(rt.get("last_active") or 0)
        except Exception:
            last_active = 0.0
        try:
            inactive = float(rt.get("inactive_since") or 0)
        except Exception:
            inactive = 0.0
        merchant = str(ms.get("last_merchant", ms.get("merchant", "")) or "")
        vals = {
            "Biome now": biome_text,
            "In game for": _dur(row.get("in_game")) or "Not in game",
            "Anti-AFK last ran": (_dur(row["afk"]) + " ago") if row.get("afk") is not None else "Not yet",
            "Roblox processes": ", ".join(f"pid {p}" + (f" (started {c})" if c else "") for p, c in zip(pids, created))
                                or "None",
            "Time to live": ", ".join(f"{t} s" for t in ttl) or "Not running",
            "Last active": datetime.fromtimestamp(last_active).strftime("%H:%M:%S") if last_active > 0 else "Never",
            "Inactive for": _dur(time.time() - inactive) if inactive > 0 else "Not inactive",
            "Server": (server_label or "None") + (f", shared with {', '.join(shared)}" if shared else ""),
            "Events": str(ms.get("events", 0) or 0),
            "Last merchant": (merchant + (f", {_dur(ms.get('merchant_age'))} ago" if ms.get("merchant_age") is not None
                                          else "")) if merchant else "None seen yet",
            "Last launched": _when(G._coerce_activity_timestamp(cfg.get("last_launch", 0.0))),
        }
        for k, v in vals.items():
            self.o[k].help_label.setText(v)
        self._set_spin(coerce_launch_priority(cfg.get("launch_priority", DEFAULT_LAUNCH_PRIORITY)))

        # Automations
        sol = self._sol_cache
        for pid, name, _short in SOL:
            r = sol.get(win._SOL_ROW_IDS[pid]) or {}
            self._set_switch(self.sol_switch[pid], bool(r) and _row_has(r, uid))
            self.sol_switch[pid + "_row"].help_label.setText(
                ("Item is on" if r.get("enabled") else "Item is off on the Auto SC + BR page") + ". This account "
                + ("uses it" if self.sol_switch[pid].isChecked() else "does not use it"))
            self.sol_switch[pid + "_row"].help_label.setVisible(True)
        afk = bool(getattr(win, "antiafk_enable_chk", None) and win.antiafk_enable_chk.isChecked())
        self.a_afk.help_label.setText("On for every account (shared Anti-AFK settings)" if afk else
                                      "Off. Turn it on from the Anti-AFK page")
        self.a_ocr.help_label.setText(self._ocr_text(uid))
        self._set_switch(self.exempt_switch, uid in self._exempt_uids())

        # Webhooks
        hooks = self._biome_hooks(uid)
        self.w_biome.help_label.setText(", ".join(hooks) if hooks else "No biome webhook includes this account")
        items = [n for pid, n, _s in SOL if (sol.get(win._SOL_ROW_IDS[pid]) or {}).get("alert_enabled")
                 and (sol.get(win._SOL_ROW_IDS[pid]) or {}).get("enabled") and self.sol_switch[pid].isChecked()]
        where = self._hook_names(win._account_webhook_urls(uid)) if items else []
        self.w_items.help_label.setText(f"{' and '.join(items)}, posted to {', '.join(where) or 'the alert channel'}"
                                        if items else "None")

        # Server and launch / Details and flags
        self._set_switch(self.reconnect_switch, not cfg.get("skip_reconnect_on_log_disconnect", False))
        self._set_switch(win.bad_chk, cfg.get("bad", False))
        self._set_switch(win.cap_chk, cfg.get("cap", False))
        self._set_switch(win.alternate_chk, cfg.get("alternate_launch", False))
        self.e_user.help_label.setText(f"User id {uid}")
        cm = win.config_manager
        if cfg.get("alternate_launch"):
            cookie = "Not used (alternate launch)"
        elif cm.cookie_encryption_enabled() and not cm.is_cookie_unlocked():
            cookie = "Saved and locked. Unlock cookies in Settings > Cookie security to change it"
        else:
            cookie = ("Saved" + (" and encrypted" if cm.cookie_encryption_enabled() else "")) if cfg.get("cookie") \
                else "None saved"
        self.e_cookie.help_label.setText(cookie)
        alt = bool(cfg.get("alternate_launch"))
        win.browser_login_btn.setEnabled(not alt)
        win.open_browser_link_btn.setEnabled(not alt)

    def _set_spin(self, v: int) -> None:
        if self.prio_spin.value() != v and not self.prio_spin.hasFocus():
            self.prio_spin.blockSignals(True)
            self.prio_spin.setValue(v)
            self.prio_spin.blockSignals(False)

    def _hook_names(self, urls: List[str]) -> List[str]:
        settings = self.win.config_manager.peek_settings() or {}
        names = {str(w.get("url") or "").strip(): str(w.get("name") or "").strip()
                 for w in settings.get("webhooks") or [] if isinstance(w, dict)}
        return [names.get(u) or "the alert channel" for u in urls]

    def _biome_hooks(self, uid: str) -> List[str]:
        win = self.win
        settings = win.config_manager.peek_settings() or {}
        uname = str(self._cfg(uid).get("username") or "").strip().lower()
        out = []
        for i, wh in enumerate(settings.get("webhooks") or []):
            if not isinstance(wh, dict) or not str(wh.get("url") or "").strip():
                continue
            allowed = wh.get("users")
            if bool(wh.get("users_explicit")) and isinstance(allowed, (list, tuple, set)):
                names = {str(u).strip() for u in allowed}
                hit = uid in names or (bool(uname) and uname in {n.lower() for n in names})
                if _G(win)._normalize_user_filter_mode(wh.get("user_filter_mode", "whitelist")) == "blacklist":
                    hit = not hit
            else:
                hit = True
            if hit:
                out.append(str(wh.get("name") or f"Webhook {i + 1}"))
        return out

    def _ocr_text(self, uid: str) -> str:
        win = self.win
        table = getattr(win, "ocr_filter_table", None)
        if table is None:
            return "Not available"
        total = hit = 0
        for row in range(table.rowCount()):
            spec = win._ocr_filter_row_data(row) or {}
            total += 1
            ids = win._normalize_ocr_filter_user_ids(spec.get("user_ids", None))
            black = _G(win)._normalize_user_filter_mode(spec.get("user_filter_mode", "whitelist")) == "blacklist"
            inside = True if ids is None else ((uid not in ids) if black else (uid in ids))
            hit += inside
        on = bool(getattr(win, "ocr_enable_chk", None) and win.ocr_enable_chk.isChecked())
        state = "Read chat is on" if on else "Read chat is off"
        return f"{state}. This account is in {hit} of {total} chat filters" if total else f"{state}. No chat filters yet"

    def _exempt_uids(self) -> List[str]:
        out = []
        for c in getattr(self.win, "bes_exempt_combos", None) or []:
            try:
                out.append(str(c.currentData() or "").strip())
            except Exception:
                out.append("")
        return out

    # ---------- detail handlers
    def _save(self, uid: Optional[str], **fields) -> bool:
        """Write fields into users.json for one account (immediately)."""
        if not uid or self._loading:
            return False
        win = self.win
        users = win.config_manager.load_users()
        info = users.get(uid)
        if not isinstance(info, dict):
            return False
        info.update(fields)
        if win.config_manager.save_users(users):
            self.after_change()
            return True
        QMessageBox.critical(win, "Could not save", win.config_manager.get_cookie_error() or "Failed to save users.json.")
        self.refresh(full=True)  # put the controls back
        return False

    def after_change(self) -> None:
        self.win.refresh_accounts_list()

    def _on_username(self) -> None:
        name = self.win.username_input.text().strip() or f"User_{self.uid}"
        if self.uid and name != self._cfg(self.uid).get("username"):
            self._save(self.uid, username=name)

    def _on_server_fields(self) -> None:
        win, cfg = self.win, self._cfg(self.uid)
        link, place = win.private_server_input.text().strip(), win.place_input.text().strip()
        if self.uid and (link, place) != (str(cfg.get("private_server_link") or ""), str(cfg.get("place") or "")):
            self._save(self.uid, private_server_link=link, place=place,
                       server_type="private" if link or not place else "public")

    def _on_note(self) -> None:
        text = self.win.launch_description_input.text().strip()
        if self.uid and text != str(self._cfg(self.uid).get("description") or ""):
            self._save(self.uid, description=text)

    def _on_discord_ids(self) -> None:
        ids = normalize_discord_ids(self.win.discord_ids_input.text())
        self.win.discord_ids_input.setText(format_discord_ids(ids))
        if self.uid and ids != normalize_discord_ids(self._cfg(self.uid).get("discord_user_ids")):
            self._save(self.uid, discord_user_ids=ids)

    def _on_alternate(self, on: bool) -> None:
        win, uid = self.win, self.uid
        if not uid or self._loading:
            return
        users = win.config_manager.load_users()
        if on and not win._confirm_account_alternate_switch(users, uid):
            self._set_switch(win.alternate_chk, False)
            return
        if on:
            for other, info in users.items():
                if isinstance(info, dict) and other != uid:
                    info["alternate_launch"] = False
        if isinstance(users.get(uid), dict):
            users[uid]["alternate_launch"] = bool(on)
        if win.config_manager.save_users(users):
            self.after_change()
        else:
            QMessageBox.critical(win, "Could not save", win.config_manager.get_cookie_error() or "Failed to save.")
            self.refresh(full=True)

    def _on_priority(self, value: int) -> None:
        """Through the Launch order table when it lists the account (so a later settings save keeps it)."""
        win, uid = self.win, self.uid
        if not uid:
            return
        table = getattr(win, "launch_priority_table", None)
        if table is not None:
            for row in range(table.rowCount()):
                it = table.item(row, 0)
                if it is not None and str(it.data(Qt.ItemDataRole.UserRole) or it.text() or "").strip() == uid:
                    prio = table.item(row, 2)  # the tier lives in the item (system.py launch order table)
                    if prio is not None:
                        table.blockSignals(True)
                        prio.setData(Qt.ItemDataRole.EditRole, int(value))
                        table.blockSignals(False)
                        win._save_launch_priorities_from_ui()
                        return
        self._save(uid, launch_priority=coerce_launch_priority(value))

    def _cookie_for(self, cookie: str) -> None:
        """Save a new cookie for the shown account (clears bad cookie), after checking whose it is."""
        win, uid = self.win, self.uid
        cookie = str(cookie or "").strip()
        if normalize_roblosecurity_cookie_value is not None:
            try:
                cookie = normalize_roblosecurity_cookie_value(cookie)
            except Exception:
                pass
        if not uid or not cookie:
            return
        info, cookie = win._fetch_authenticated_user(cookie)
        if info and str(info.get("id")) != uid:
            if QMessageBox.question(win, "Different account", f"This cookie signs in as {info.get('name')} "
                                    f"({info.get('id')}), not {self._cfg(uid).get('username') or uid}. Save it anyway?",
                                    _YES | _NO, _NO) != _YES:
                return
        if self._save(uid, cookie=cookie, bad=False):
            win.add_log(f"[UI] Cookie updated for {uid}.")

    def _login_browser(self) -> None:
        win = self.win
        if getattr(win, "cookie_extractor", None) is None:
            from cookie_extractor import CookieExtractor
            win.cookie_extractor = CookieExtractor(win)
        win.browser_login_btn.setEnabled(False)

        def _done(cookie):
            win.browser_login_btn.setEnabled(True)
            if cookie:
                self._cookie_for(cookie)
            else:
                QMessageBox.information(win, "Log in", "The browser login was cancelled or failed.")
        try:
            win.cookie_extractor.extract_cookie_async(callback=_done, parent_widget=win)
        except Exception as e:
            win.browser_login_btn.setEnabled(True)
            QMessageBox.critical(win, "Log in", f"Could not open the browser: {e}")

    def _paste_cookie(self) -> None:
        if not self.uid:
            return
        text, ok = QInputDialog.getText(self.win, "Paste cookie", "The .ROBLOSECURITY cookie for this account:",
                                        QLineEdit.EchoMode.Password)
        if ok and text.strip():
            self._cookie_for(text)

    def _on_sol(self, preset_id: str, on: bool) -> None:
        """Add or remove the shown account from the Strange Controller / Biome Randomizer row."""
        win, uid = self.win, self.uid
        if not uid or self._loading:
            return
        items = win._current_auto_item_items()
        row = win._sol_rows(items).get(win._SOL_ROW_IDS[preset_id]) or {}
        if row.get("users_explicit") and isinstance(row.get("users"), list):
            users = [str(u) for u in row["users"]]
        else:  # same default as the Auto SC + BR dialog: the accounts ticked for Action rows, else all
            checks = getattr(win, "auto_item_user_checks", {}) or {}
            users = [u for u, cb in checks.items() if cb.isChecked()] or [u for u, _ in win._auto_item_user_catalog()]
        users = [u for u in users if u != uid] + ([uid] if on else [])
        win._sol_apply_accounts(items, {preset_id: users})
        win._load_auto_item_items_table(items)
        win._on_auto_item_ui_changed()
        self.refresh()

    def _on_exempt(self, on: bool) -> None:
        win, uid = self.win, self.uid
        combos = getattr(win, "bes_exempt_combos", None) or []
        if not uid or not combos:
            return
        cur = self._exempt_uids()
        if on and uid not in cur:
            if "" not in cur:
                QMessageBox.information(win, "Hardware limiting", "All three exempt slots are taken. Free one on the "
                                        "Hardware limiting page first.")
                self._set_switch(self.exempt_switch, False)
                return
            combo = combos[cur.index("")]
            if combo.findData(uid) < 0:
                combo.addItem(f"{self._cfg(uid).get('username') or uid} ({uid})", uid)
            combo.setCurrentIndex(combo.findData(uid))
        elif not on:
            for c, v in zip(combos, cur):
                if v == uid:
                    c.setCurrentIndex(max(0, c.findData("")))
        win._on_bes_ui_changed()

    def _open_ocr_accounts(self) -> None:
        self.win._open_ocr_filter_user_assignments_dialog()
        self.fill()

    def _launch_once(self) -> None:
        win = self.win
        self.launch(self._sel_or_current(), link=win.launch_server_link_input.text().strip(),
                    place=win.launch_place_id_input.text().strip() or DEFAULT_PLACE_ID)

    def _open_browser(self) -> None:
        win = self.win
        link = win.browser_any_link_input.text().strip()
        if link:
            if link.startswith("//"):
                link = "https:" + link
            elif not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", link):
                link = "https://" + link
            win.browser_any_link_input.setText(link)
        users = win.config_manager.peek_users() or {}
        skipped = []
        for uid in self._sel_or_current():
            info = users.get(uid) or {}
            cookie = str(info.get("cookie") or "").strip()
            if info.get("alternate_launch") or not cookie:
                skipped.append(str(info.get("username") or uid))
                continue
            _open_logged_in_browser(win, self.bridge, uid, cookie, link or "https://www.roblox.com/home")
        if skipped:
            QMessageBox.information(win, "Open in a browser", "Skipped (no cookie, or alternate launch): "
                                    + ", ".join(skipped[:12]) + (" ..." if len(skipped) > 12 else ""))

    # ---------- actions (bulk bar and detail header)
    def _names(self, uids: List[str]) -> str:
        names = [str(self._cfg(u).get("username") or u) for u in uids]
        return names[0] if len(names) == 1 else f"{len(names)} accounts"

    def launch(self, uids: List[str], link: Optional[str] = None, place: Optional[str] = None) -> None:
        """Launch with each account's saved server, or once into link / place. Works with the manager stopped."""
        win = self.win
        users = win.config_manager.peek_users() or {}
        skipped: Dict[str, int] = {}
        todo = []
        for uid in uids:
            info = users.get(uid)
            if not isinstance(info, dict):
                continue
            reason = ("already online" if _online(win, uid) else "disabled" if info.get("disabled") else
                      "flagged" if info.get("bad") or info.get("cap") else
                      "no cookie" if not info.get("cookie") and not info.get("alternate_launch") else "")
            if reason:
                skipped[reason] = skipped.get(reason, 0) + 1
            else:
                todo.append(uid)
        states = getattr(getattr(win, "worker_thread", None), "user_states", {}) or {}
        todo = [u for _i, u in sorted(enumerate(todo), key=lambda p: launch_queue_sort_key(
            p[1], users.get(p[1]) or {}, states.get(p[1], {}), p[0]))]
        note = ", ".join(f"{n} {k}" for k, n in skipped.items())
        if not todo:
            QMessageBox.information(win, "Nothing to launch", "None of these accounts can launch"
                                    + (f" ({note})." if note else "."))
            return
        if len(todo) > 1 and QMessageBox.question(win, "Launch", f"Launch Roblox for {len(todo)} accounts?"
                                                  + (f"\n\nSkipped: {note}." if note else ""), _YES | _NO) != _YES:
            return

        def _info(uid):
            info = dict(users.get(uid) or {})
            if link is not None:  # one-off target (UserManagementDialog.launch_roblox_for_selected)
                info["private_server_link"] = link
                info["server_type"] = "private" if link else "public"
                info["place"] = place or DEFAULT_PLACE_ID
            return info

        if _manager_running(win):
            for uid in todo:
                try:
                    win.worker_thread.submit_action("launch_user_session_custom", uid, _info(uid), skip_cleanup=False)
                except Exception as e:
                    win.add_log(f"[ManualLaunch] uid={uid} failed: {e}")
            return
        win.launch_roblox_btn.setEnabled(False)
        infos = [(u, _info(u)) for u in todo]
        bridge = self.bridge

        def _run():
            try:
                launcher = _manual_launcher(win, bridge)
                delay = max(0.0, float(getattr(launcher, "launch_delay", 0) or 0))
                for i, (uid, info) in enumerate(infos):
                    bridge.log.emit(f"[ManualLaunch] launching uid={uid}")
                    try:
                        launcher.start_game_session(uid, str(info.get("cookie") or "").strip(), info,
                                                    skip_cleanup=False)
                    except Exception as e:
                        bridge.log.emit(f"[ManualLaunch] uid={uid} error: {e}")
                    if i < len(infos) - 1 and delay > 0:
                        time.sleep(delay)
                bridge.log.emit(f"[ManualLaunch] done ({len(infos)} users)" + (f"; skipped {note}" if note else ""))
            finally:
                bridge.launch_done.emit()
        threading.Thread(target=_run, daemon=True).start()

    def restart(self, uids: List[str]) -> None:
        win = self.win
        if not _manager_running(win):
            QMessageBox.warning(win, "Manager not running", "Start the manager first.")
            return
        if set(uids) >= set(win.config_manager.peek_users() or {}):  # everyone: the staggered restart-all
            win.restart_all_sessions()
            return
        if len(uids) > 1 and QMessageBox.question(win, "Restart", f"Restart {len(uids)} accounts?",
                                                  _YES | _NO) != _YES:
            return
        for uid in uids:
            if win._queue_worker_action("restart_user_session", uid):
                win.add_log(f"[UI] Queued restart for user {uid}.")

    def close_roblox(self, uids: List[str]) -> None:
        win = self.win
        if not _manager_running(win):
            if set(uids) >= set(win.config_manager.peek_users() or {}):
                win.kill_all_processes()  # works with the manager stopped
            else:
                QMessageBox.information(win, "Close Roblox", "With the manager stopped, only every Roblox window can "
                                        "be closed at once. Clear the selection and filters, or start the manager.")
            return
        if QMessageBox.question(win, "Close Roblox", f"Close Roblox for {self._names(uids)}?", _YES | _NO) != _YES:
            return
        for uid in uids:
            win._queue_worker_action("kill_user_processes", uid)

    def show_windows(self, uids: List[str]) -> None:
        win = self.win
        if len(uids) == 1 and win._ui_show_selected_sets_bes_exempt_slot1_enabled():
            win._set_bes_exempt_slot1_to_user(uids[0])
        missing = []
        for uid in uids:
            hwnd = win._find_roblox_hwnd_for_user(uid)
            if hwnd is None or not win._bring_hwnd_to_foreground(hwnd):
                missing.append(str(self._cfg(uid).get("username") or uid))
        if missing and len(missing) == len(uids):
            more = f" and {len(missing) - 12} more" if len(missing) > 12 else ""
            QMessageBox.information(win, "Show windows", f"No Roblox window found for {', '.join(missing[:12])}{more}.")

    def hide_windows(self, uids: List[str]) -> None:
        win = self.win
        try:
            import win32con
            import win32gui
        except Exception:
            return
        for uid in uids:
            hwnd = win._find_roblox_hwnd_for_user(uid)
            if hwnd:
                try:
                    win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)
                except Exception:
                    pass

    def set_disabled(self, uids: List[str], disabled: bool) -> None:
        win = self.win
        users = win.config_manager.load_users()
        changed = [u for u in uids if isinstance(users.get(u), dict) and bool(users[u].get("disabled")) != disabled]
        if not changed:
            return
        for u in changed:
            users[u]["disabled"] = disabled
        if not win.config_manager.save_users(users):
            QMessageBox.critical(win, "Could not save", "Failed to save account configuration.")
            return
        if disabled and _manager_running(win):
            for u in changed:
                win._queue_worker_action("kill_user_processes", u)
        self.after_change()

    def clear_flags(self, flags: tuple, uids: Optional[List[str]] = None) -> None:
        win = self.win
        uids = uids if uids is not None else self.targets()
        users = win.config_manager.load_users()
        hit = [u for u in uids if isinstance(users.get(u), dict) and any(users[u].get(f) for f in flags)]
        label = {("bad", "cap"): "bad cookie and captcha", ("cap",): "captcha", ("bad",): "bad cookie"}[tuple(flags)]
        if not hit:
            QMessageBox.information(win, "Clear flags", f"No {label} flags to clear.")
            return
        if len(hit) > 1 and QMessageBox.question(win, "Clear flags", f"Clear {label} flags on {len(hit)} accounts?",
                                                 _YES | _NO) != _YES:
            return
        for u in hit:
            for f in flags:
                users[u][f] = False
        if win.config_manager.save_users(users):
            self.after_change()
        else:
            QMessageBox.critical(win, "Could not save", win.config_manager.get_cookie_error() or "Failed to save.")

    def delete(self, uids: List[str], question: str = "") -> bool:
        win = self.win
        if not uids:
            return False
        question = question or f"Delete {self._names(uids)}? Their saved cookies go too. This cannot be undone."
        if QMessageBox.warning(win, "Delete accounts", question, _YES | _NO, _NO) != _YES:
            return False
        users = win.config_manager.load_users()
        gone = [u for u in uids if users.pop(u, None) is not None]
        if not gone:
            return False
        if not win.config_manager.save_users(users):
            QMessageBox.critical(win, "Could not save", win.config_manager.get_cookie_error() or "Failed to save.")
            return False
        if _manager_running(win):
            for u in gone:
                win._queue_worker_action("kill_user_processes", u)
        win.add_log(f"[UI] Deleted {len(gone)} account(s).")
        self.after_change()
        return True


# ---------------------------------------------------------------- launching / browser (from UserManagementDialog)

def _online(win, uid: str) -> bool:
    """Already has a live Roblox (manager tracker, or the state kept after a stop)."""
    wt = getattr(win, "worker_thread", None)
    try:
        pids = (wt.manager.process_tracker.user_processes or {}).get(uid, []) or []
        if any(wt.process_mgr.verify_process_active(p) for p in pids):
            return True
    except Exception:
        pass
    state = getattr(win, "_stopped_worker_state", None)
    try:
        import psutil
        pids = ((state or {}).get("process_tracker") or {}).get("user_processes", {}).get(uid, []) or []
        for pid in pids if isinstance(pids, (list, tuple, set)) else [pids]:
            try:
                if psutil.Process(int(pid)).name().lower() == "robloxplayerbeta.exe":
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def _manual_launcher(win, bridge: _Bridge):
    launcher = getattr(win, "_accounts_manual_launcher", None)
    if launcher is not None:
        return launcher
    from main import GameLauncher, ProcessManager, RobloxManager
    mgr = RobloxManager(config_manager=win.config_manager)
    pm = ProcessManager(getattr(mgr, "excluded_pid", 0) or 0)
    timeouts = getattr(mgr, "timeouts", {}) or {}
    launcher = GameLauncher(
        getattr(mgr, "target_place", DEFAULT_PLACE_ID), pm, getattr(mgr, "auth_handler", None),
        getattr(mgr, "process_tracker", None), getattr(mgr, "config_manager", win.config_manager),
        launch_delay=int(timeouts.get("launch_delay", 4) or 4), initial_delay=int(timeouts.get("initial_delay", 4) or 4),
        log_fn=bridge.log.emit,
    )
    win._accounts_manual_launcher = launcher
    return launcher


_DRIVERS: list = []  # keeps Selenium drivers alive so the Chrome windows stay open


def _find_chrome() -> Optional[str]:
    env = str(os.environ.get("CHROME_PATH", "") or "").strip()
    cands = [env, r"C:\Program Files\Google\Chrome\Application\chrome.exe",
             r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
             os.path.join(os.environ.get("LOCALAPPDATA") or "", "Google", "Chrome", "Application", "chrome.exe")]
    for p in cands:
        if p and os.path.isfile(p):
            return p
    for name in ("chrome.exe", "chrome", "google-chrome"):
        p = shutil.which(name)
        if p:
            return p
    return None


def _open_logged_in_browser(win, bridge: _Bridge, uid: str, cookie: str, url: str) -> None:
    """Real Chrome on a throwaway profile, .ROBLOSECURITY injected over remote debugging; cookie rotations are saved.
    Port of UserManagementDialog._open_logged_in_browser."""
    log = bridge.log.emit
    if normalize_roblosecurity_cookie_value is not None:
        try:
            cookie = normalize_roblosecurity_cookie_value(cookie)
        except Exception:
            pass

    def _worker():
        import socket
        import subprocess
        from urllib.request import urlopen
        try:
            chrome = _find_chrome()
            if not chrome:
                log("[Browser] Chrome not found. Set CHROME_PATH or install Google Chrome.")
                return
            profile = tempfile.mkdtemp(prefix=f"jaram_chrome_{uid}_")
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
            s.close()
            proc = subprocess.Popen([chrome, f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                                     "--no-first-run", "--no-default-browser-check", "--new-window",
                                     "https://www.roblox.com/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            threading.Thread(target=lambda: (proc.wait(), shutil.rmtree(profile, ignore_errors=True)),
                             daemon=True).start()
            deadline = time.time() + 8.0
            while True:
                try:
                    with urlopen(f"http://127.0.0.1:{port}/json/version", timeout=0.5):
                        break
                except Exception:
                    if time.time() > deadline:
                        log(f"[Browser] uid={uid} failed: Chrome remote debug not reachable (port={port}).")
                        return
                    time.sleep(0.2)
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.webdriver import WebDriver
            opts = Options()
            opts.add_experimental_option("debuggerAddress", f"127.0.0.1:{port}")
            driver = WebDriver(options=opts)
            driver.get("https://www.roblox.com/")
            obj = {"name": ".ROBLOSECURITY", "value": cookie, "domain": ".roblox.com", "path": "/", "secure": True,
                   "httpOnly": True}
            try:
                driver.delete_cookie(".ROBLOSECURITY")
                driver.add_cookie(obj)
            except Exception:
                obj["domain"] = "roblox.com"
                driver.add_cookie(obj)
            driver.get(url or "https://www.roblox.com/home")
            _DRIVERS.append(driver)
            log(f"[Browser] uid={uid} opened (profile=temp)")
            last = cookie
            while extract_roblosecurity_from_selenium_driver is not None and persist_updated_cookie is not None:
                time.sleep(5.0)
                try:
                    cur = extract_roblosecurity_from_selenium_driver(driver)
                except Exception:
                    break  # browser closed
                if cur and cur != last:
                    last = cur
                    ok = persist_updated_cookie(win.config_manager, user_id=uid, new_cookie=cur)
                    log(f"[Cookie] uid={uid} updated from browser." if ok else
                        f"[Cookie] uid={uid} updated in browser, but failed to save (cookies locked?).")
        except Exception as e:
            log(f"[Browser] uid={uid} failed: {type(e).__name__}: {e}")
    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------- entry point + replaced methods

def build(win, shell):
    ui = AccountsUI(win, shell)
    win._accounts_ui = ui

    def _open_account_page(uid=None):
        """Show the Accounts page; with uid, select that account."""
        shell.go(ui.page)
        ui.select(str(uid) if uid else None)
    win._open_account_page = _open_account_page
    ui.refresh(full=True)
    return [("Accounts", ui.page)]


def refresh_users(self, *_args, force_full: bool = False):
    ui = getattr(self, "_accounts_ui", None)
    if ui is not None:
        ui.refresh(full=force_full)


def refresh_accounts_list(self):
    ui = getattr(self, "_accounts_ui", None)
    if ui is not None:
        ui.refresh(full=True)
    try:
        self._accounts_list_users_mtime = float(self.config_manager.get_users_mtime())
    except Exception:
        self._accounts_list_users_mtime = 0.0
    try:
        self._refresh_discord_control_accounts_table()
    except Exception:
        pass


def _refresh_users_antiafk_age_column(self) -> None:
    refresh_users(self)


def _refresh_users_created_column(self) -> None:
    refresh_users(self)


def open_user_management(self):
    """File > Manage Users: the Manage Users dialog is now the Accounts page."""
    self._open_account_page(None)


def on_account_server_type_changed(self):
    """Add account dialog: both fields stay visible; the server type follows which one is filled."""


def clear_account_form(self):
    """Reset the Add account dialog. (The old version also re-wired add_account_btn for its Edit mode, which is gone.)"""
    for w in (self.account_user_id, self.account_username, self.account_private_link, self.account_place_id,
              self.account_cookie):
        w.clear()
    for w in (self.account_disabled, self.account_alternate_launch, self.account_skip_reconnect_on_log_disconnect):
        w.setChecked(False)
    self.account_private_radio.setChecked(True)
    self._on_account_alternate_launch_toggled(False)
    self._reset_account_browser_button()


def _reset_account_browser_button(self):
    btn = getattr(self, "account_browser_login_btn", None)
    if btn is not None:
        btn.setEnabled(not self.account_alternate_launch.isChecked())
        btn.setText("Log in with a browser")


REPLACES = {
    "refresh_users": refresh_users,
    "refresh_accounts_list": refresh_accounts_list,
    "_refresh_users_antiafk_age_column": _refresh_users_antiafk_age_column,
    "_refresh_users_created_column": _refresh_users_created_column,
    "open_user_management": open_user_management,
    "on_account_server_type_changed": on_account_server_type_changed,
    "clear_account_form": clear_account_form,
    "_reset_account_browser_button": _reset_account_browser_button,
}
