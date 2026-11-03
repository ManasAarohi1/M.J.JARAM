"""System pages: Settings, Tools, About and credits.

Ports gui.setup_settings_tab (the groups this module owns), the Multi-Instance / Log Cleanup / Cookie Encryption
menu dialogs, utilities_tab.build_utilities_widget, and _build_credits_widget into the new design. Widgets keep the
old attribute names so save_settings / load_settings_tab and every handler keep working.
"""
from __future__ import annotations

import importlib
import os
import re
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QObject, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox,
    QSpinBox, QStyledItemDelegate, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import ui_kit as K
from launch_priority import (
    DEFAULT_LAUNCH_PRIORITY, MAX_LAUNCH_PRIORITY, MIN_LAUNCH_PRIORITY, coerce_launch_priority,
    sort_user_items_by_launch_priority,
)

# Same value as gui._LAUNCH_PRIORITY_STATUS_ROLE (the status delegate reads it).
_STATUS_ROLE = int(Qt.ItemDataRole.UserRole) + 51

# One resolution list everywhere (spec). "Keep size" is the Shrink switch being off here.
SIZES = ["640 x 480", "800 x 600", "1024 x 768", "1280 x 720", "1366 x 768", "1600 x 900", "1920 x 1080", "Custom"]

LINKS = {
    "manas": "https://discord.gg/oppression",
    "source": "https://github.com/Jirach-1/J.JARAM",
    "license": "https://www.gnu.org/licenses/agpl-3.0.html",
    "bes": "https://mion.yosei.fi/BES/",
    "ram": "https://github.com/0vm/RAM-Limiter",
    "msfix": "https://github.com/ManasAarohi1/MultiScope-Fix",
    "scope": "https://discord.gg/6cuCu6ymkX",
    "jaram": "https://discord.gg/TheGlitchCore",
}
DEVS = [  # (name, local file next to gui.py, fallback download the old credits widget used)
    ("ManasAarohi", "manas.png",
     "https://cdn.discordapp.com/avatars/561923311530016790/5ec41258a398f8aec742d74e6f1978ec.png?size=4096"),
    ("Jirach1", "jirachi.gif", "https://media.tenor.com/7c8Mqt2ciZgAAAAi/jirachi.gif"),
    ("cresqnt", "cresqnt.gif", "https://media1.tenor.com/m/CNBGgG2DU10AAAAd/nyan-cat-poptart.gif"),
]


# ── small widgets ────────────────────────────────────────────────────────────
def _spin(lo: int, hi: int, suffix: str = "", width: int = 120) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    if suffix:
        s.setSuffix(suffix)
    s.setFixedWidth(width)
    return s


class _InvertedSwitch(K.Switch):
    """Draws ON when unchecked. For keys stored the other way round (misc.disable_manager_bad_marking)."""

    inverted = True


class _PriorityDelegate(QStyledItemDelegate):
    """Launch order tier column: a -3..+3 spin box editor, shown as +2 / 0 / -1."""

    def createEditor(self, parent, _option, _index):  # noqa: N802
        sb = QSpinBox(parent)
        sb.setRange(MIN_LAUNCH_PRIORITY, MAX_LAUNCH_PRIORITY)
        sb.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return sb

    def displayText(self, value, _locale):  # noqa: N802
        v = coerce_launch_priority(value)
        return f"{v:+d}" if v else "0"

    def initStyleOption(self, option, index):  # noqa: N802
        super().initStyleOption(option, index)
        option.displayAlignment = Qt.AlignmentFlag.AlignCenter


def _open_path(win, path) -> None:
    try:
        os.startfile(str(path))  # noqa: S606 (Windows app)
    except Exception as e:
        QMessageBox.warning(win, "Open folder", f"Could not open the folder:\n{path}\n\n{e}")


# ── REPLACES: launch order table without one QSpinBox widget per account ───────
def _populate_launch_priority_table(self, users_config=None) -> None:
    table = getattr(self, "launch_priority_table", None)
    if table is None:
        return
    current_priorities = self._collect_launch_priorities_from_ui()
    if not isinstance(users_config, dict):
        try:
            users_config = self.config_manager.load_users() or {}
        except Exception:
            users_config = {}
    if not isinstance(users_config, dict):
        users_config = {}
    try:
        query = str(self.launch_priority_search_input.text() or "").strip().lower()
    except Exception:
        query = ""
    rows = [
        (str(uid), info)
        for uid, info in users_config.items()
        if isinstance(info, dict)
        and (not query or query in str(uid).lower()
             or query in str(info.get("username", f"User_{uid}") or "").lower())
    ]
    for uid, info in rows:
        if uid in current_priorities:
            info["launch_priority"] = current_priorities[uid]
    rows = sort_user_items_by_launch_priority(rows)

    table.blockSignals(True)  # itemChanged = a user edit (saves users.json); filling the table is not
    table.setUpdatesEnabled(False)
    try:
        table.setRowCount(len(rows))
        for row, (uid, info) in enumerate(rows):
            uid_item = QTableWidgetItem(uid)
            uid_item.setData(Qt.ItemDataRole.UserRole, uid)
            name_item = QTableWidgetItem(str(info.get("username", f"User_{uid}") or f"User_{uid}"))
            segments = self._launch_priority_status_segments(info)
            status_item = QTableWidgetItem(" / ".join(segments))
            status_item.setData(_STATUS_ROLE, segments)
            prio_item = QTableWidgetItem()
            prio_item.setData(Qt.ItemDataRole.EditRole,
                              coerce_launch_priority(info.get("launch_priority", DEFAULT_LAUNCH_PRIORITY)))
            for item in (uid_item, name_item, status_item):
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            table.setItem(row, 0, uid_item)
            table.setItem(row, 1, name_item)
            table.setItem(row, 2, prio_item)
            table.setItem(row, 3, status_item)
    finally:
        table.setUpdatesEnabled(True)
        table.blockSignals(False)


def _collect_launch_priorities_from_ui(self) -> dict:
    try:
        users_config = self.config_manager.load_users() or {}
    except Exception:
        users_config = {}
    priorities = {
        str(uid): coerce_launch_priority(info.get("launch_priority", DEFAULT_LAUNCH_PRIORITY))
        for uid, info in (users_config or {}).items()
        if isinstance(info, dict)
    }
    table = getattr(self, "launch_priority_table", None)
    if table is None:
        return priorities
    for row in range(table.rowCount()):
        uid_item, prio_item = table.item(row, 0), table.item(row, 2)
        if uid_item is None:
            continue
        uid = str(uid_item.data(Qt.ItemDataRole.UserRole) or uid_item.text() or "").strip()
        if uid:
            priorities[uid] = coerce_launch_priority(
                prio_item.data(Qt.ItemDataRole.EditRole) if prio_item is not None else DEFAULT_LAUNCH_PRIORITY)
    return priorities


def _reset_launch_priorities_ui(self) -> None:
    table = getattr(self, "launch_priority_table", None)
    if table is None:
        return
    table.blockSignals(True)  # one save below, not one per row
    try:
        for row in range(table.rowCount()):
            item = table.item(row, 2)
            if item is not None:
                item.setData(Qt.ItemDataRole.EditRole, DEFAULT_LAUNCH_PRIORITY)
    finally:
        table.blockSignals(False)
    table.viewport().update()
    self._save_launch_priorities_from_ui()


def show_multi_instance_window(self) -> None:
    """Old: opened the Multi-Instance dialog. Now: shows Settings > Many Roblox windows (start_manager and the
    repair prompt call this). `_multi_instance_dialog` is that Section."""
    self._refresh_multi_instance_process_snapshot()
    self._update_multi_instance_dialog()
    sec = getattr(self, "_multi_instance_dialog", None)
    page = getattr(sec, "page", None)
    if page is None:
        return
    stack = page.parentWidget()
    if hasattr(stack, "setCurrentWidget"):
        stack.setCurrentWidget(page)
    page.show_section(page.sections.index(sec))
    try:
        self.raise_()
        self.activateWindow()
    except Exception:
        pass


REPLACES = {
    "_populate_launch_priority_table": _populate_launch_priority_table,
    "_collect_launch_priorities_from_ui": _collect_launch_priorities_from_ui,
    "_reset_launch_priorities_ui": _reset_launch_priorities_ui,
    "show_multi_instance_window": show_multi_instance_window,
}


# ── public helpers for the integrator / other pages ─────────────────────────────
def open_launch_order(win) -> None:
    """Accounts > Overview > Launch priority "Change" can call this too."""
    dlg = getattr(win, "_launch_order_dialog", None)
    if dlg is None:
        return
    win._populate_launch_priority_table()
    dlg.show()
    dlg.raise_()
    dlg.activateWindow()


def set_update_available(win, url: str, tag: str = "") -> None:
    """Call from _on_update_found: the About > Updates row switches to "Update available"."""
    from version import __version__
    win._update_url = url
    row, btn = getattr(win, "_updates_row", (None, None))
    if row is None or not url:
        return
    row.name_label.setText("Update available")
    row.help_label.setText(f"{tag or 'A newer version'} is out. You have {__version__}.")
    btn.setText("Open release page")


# ── Settings ──────────────────────────────────────────────────────────────────
def _build_settings(win, shell, autosave):
    page = K.Page("settings", "Settings", "Everything that applies to all accounts. Changes save as you make them.",
                  shell.search)
    watch = autosave.watch

    # Many Roblox windows (was Extras > Roblox Multi-Instance)
    sec = page.section("Many Roblox windows", "Lets Roblox open more than one window at a time.")
    win._multi_instance_checkbox = K.Switch(bool(getattr(win, "_multi_instance_enabled", False)))
    win._multi_instance_checkbox.toggled.connect(win._set_multi_instance_enabled)  # saves itself (antiafk block)
    sec.row("Allow many Roblox windows", "Holds Roblox's one-window guard so every account can open",
            win._multi_instance_checkbox)
    win._multi_instance_guard_label = K.label("", "help")
    win._multi_instance_process_label = K.label("", "help")
    win._multi_instance_refresh_btn = K.button("Check again", win._refresh_multi_instance_clicked)
    st = sec.row("Status", "", win._multi_instance_guard_label, win._multi_instance_process_label,
                 win._multi_instance_refresh_btn)
    win._multi_instance_status_label = st.help_label
    win._multi_instance_issue_label = K.label("", "warn", wrap=True)
    win._multi_instance_issue_label.hide()
    sec.add(win._multi_instance_issue_label)
    win._multi_instance_fix_btn = K.button("Fix it", win._fix_multi_instance, kind="warn")
    sec.row("Not working?", "Closes every Roblox window and rebuilds the guard. A running manager pauses and "
            "resumes by itself", win._multi_instance_fix_btn)
    sec.action("More than 15 windows", "Raises the Windows desktop heap to 128 MB. Needs admin and a PC restart",
               "Raise limit", win._apply_desktop_heap)
    win._multi_instance_dialog = sec  # show_multi_instance_window (REPLACES) navigates here
    try:
        win._update_multi_instance_dialog()
    except Exception:
        pass

    # Accounts (Basic Settings + Launch Priority)
    sec = page.section("Accounts")
    win.settings_window_limit_input = _spin(1, 999)
    sec.row("Windows per Roblox process", "A Roblox process with more windows than this is closed",
            win.settings_window_limit_input)
    win.spares_mode_chk = K.Switch()
    sec.row("Spare accounts", "Launch only part of your good accounts. The rest swap in before a session ends",
            win.spares_mode_chk)
    win.spares_split_cmb = QComboBox()
    win.spares_split_cmb.addItems(["1/2", "3/4", "4/5"])
    win.spares_split_cmb.setEnabled(win.spares_mode_chk.isChecked())
    win.spares_mode_chk.toggled.connect(win.spares_split_cmb.setEnabled)
    sec.row("Spare share", "How many good accounts launch at the start", win.spares_split_cmb)
    sec.action("Launch order", "Which accounts start first", "Edit order", lambda: open_launch_order(win))
    watch(win.settings_window_limit_input, win.spares_mode_chk, win.spares_split_cmb)
    _build_launch_order_dialog(win)

    # Roblox windows (Roblox Resizer + Set Roblox Window Geometry)
    sec = page.section("Roblox windows", "Smaller windows use less GPU. Auto SC + BR clicks work at any size.")
    win.resizer_enable_chk = K.Switch()
    sec.row("Shrink Roblox windows", "Renders every window small to save GPU, CPU and RAM. Removes the title bar too",
            win.resizer_enable_chk)
    win.resizer_width_input = _spin(1, 4096, " px", 100)
    win.resizer_height_input = _spin(1, 4096, " px", 100)
    win.resizer_size_combo = QComboBox()
    win.resizer_size_combo.addItems(SIZES)
    times = K.label("x", "help")
    sec.row("Size", "Pick Custom to type any width and height", win.resizer_size_combo, win.resizer_width_input,
            times, win.resizer_height_input)
    custom_widgets = (win.resizer_width_input, times, win.resizer_height_input)

    def _sync_size(*_):
        key = f"{win.resizer_width_input.value()} x {win.resizer_height_input.value()}"
        idx = win.resizer_size_combo.findText(key)
        if idx < 0:
            idx = SIZES.index("Custom")
        win.resizer_size_combo.blockSignals(True)
        win.resizer_size_combo.setCurrentIndex(idx)
        win.resizer_size_combo.blockSignals(False)
        for w in custom_widgets:
            w.setVisible(SIZES[idx] == "Custom")

    def _size_picked(idx: int):
        text = SIZES[idx]
        if text == "Custom":
            for w in custom_widgets:
                w.setVisible(True)
            return
        w, h = (int(x) for x in text.split(" x "))
        for spin, v in ((win.resizer_width_input, w), (win.resizer_height_input, h)):
            spin.blockSignals(True)
            spin.setValue(v)
            spin.blockSignals(False)
        _sync_size()
        win._on_resizer_settings_changed()
        autosave.poke()

    win.resizer_size_combo.currentIndexChanged.connect(_size_picked)
    for w in (win.resizer_enable_chk, win.resizer_width_input, win.resizer_height_input):
        (w.toggled if w is win.resizer_enable_chk else w.valueChanged).connect(win._on_resizer_settings_changed)
    win.resizer_width_input.valueChanged.connect(_sync_size)
    win.resizer_height_input.valueChanged.connect(_sync_size)
    # NOTE: save_settings() does not write roblox_resizer yet (integrator fix); these save once it does.
    watch(win.resizer_enable_chk, win.resizer_width_input, win.resizer_height_input)
    _sync_size()

    win.rbwin_geom_enforce_chk = K.Switch()
    sec.row("Put new windows in one place", "Moves and sizes each new window to the saved position. Retries once "
            "after 5 s", win.rbwin_geom_enforce_chk)
    watch(win.rbwin_geom_enforce_chk)

    def _record():
        before = dict(getattr(win, "_roblox_window_geometry", {}) or {})
        win._record_roblox_window_geometry()
        if dict(getattr(win, "_roblox_window_geometry", {}) or {}) != before:
            autosave.poke()

    win.rbwin_geom_record_btn = K.button("Use current window", _record)
    geom_row = sec.row("Saved position", "", win.rbwin_geom_record_btn)
    win.rbwin_geom_status_lbl = geom_row.help_label  # _refresh_rbwin_geom_status writes "Recorded: x=.."
    win.rbwin_geom_status_lbl.setText("Recorded: none")
    win.rbwin_geom_status_lbl.show()

    # Timing (Timing Settings + strap limit)
    sec = page.section("Timing", "How long the manager waits before acting.")
    win.settings_initial_delay_input = _spin(5, 999999, " s")
    win.settings_offline_threshold_input = _spin(10, 120, " s")
    win.settings_launch_delay_input = _spin(1, 999999, " s")
    win.settings_strap_threshold_input = _spin(1, 200)
    win.handoff_lead_input = _spin(5, 300, " s")
    win.early_join_window_input = _spin(10, 600, " s")
    sec.row("Before the first launch", "", win.settings_initial_delay_input)
    sec.row("Offline after", "No log activity for this long, then the account restarts",
            win.settings_offline_threshold_input)
    sec.row("Between launches", "", win.settings_launch_delay_input)
    sec.row("Bootstrapper helpers kept", "Extra strap.exe helper processes above this are closed",
            win.settings_strap_threshold_input)
    sec.row("Hand-off lead", "Start the spare this early before the old session is closed", win.handoff_lead_input)
    sec.row("Early join window", "Give spares to sessions that end within this time, to avoid a backlog",
            win.early_join_window_input)
    watch(win.settings_initial_delay_input, win.settings_offline_threshold_input, win.settings_launch_delay_input,
          win.settings_strap_threshold_input, win.handoff_lead_input, win.early_join_window_input)

    # Automation timing: the Automate builder owns these widgets (auto_item_*).
    sec = page.section("Automation timing", "Shared by Auto SC + BR, action rows and Anti-AFK.")
    _page_hook(win, sec, "automation_timing_rows", ("automate",))

    def _go_rows():
        for i in range(shell.stack.count()):
            if getattr(shell.stack.widget(i), "page_id", "") == "rows":
                shell.stack.setCurrentIndex(i)
    sec.action("Lowest FPS, hotkey and mouse lock", "Set on Action rows, under Running rows",
               "Open Action rows", _go_rows)

    # Idle and stuck accounts (Shutdown Settings + CAP Watchdog + auto-BAD)
    sec = page.section("Idle and stuck accounts")
    win.kill_after_enable_chk = K.Switch(False)
    win.kill_timeout_input = _spin(60, 2_147_483_647, " s", 140)
    win.poll_interval_input = _spin(1, 120, " s")
    win.kill_after_enable_chk.toggled.connect(win.kill_timeout_input.setEnabled)
    win.kill_timeout_input.setEnabled(False)
    sec.row("Close Roblox windows on a timer", "Each window is closed this long after it opened, then relaunched",
            win.kill_after_enable_chk)
    sec.row("Close after", "", win.kill_timeout_input)
    sec.row("Check every", "How often the timer and the blackout alert are checked", win.poll_interval_input)
    win.missing_username_cap_count_chk = K.Switch()
    win.missing_username_timeout_input = _spin(1, 86_400, " s")
    win.in_menu_none_cap_count_chk = K.Switch()
    win.in_menu_none_timeout_input = _spin(1, 86_400, " s")
    win.cap_counter_limit_input = _spin(1, 100)
    sec.row("Missing username counts as captcha", "The window is restarted either way. On also adds to its "
            "captcha count", win.missing_username_cap_count_chk)
    sec.row("Wait for the username", "Before restarting a window whose log has no username",
            win.missing_username_timeout_input)
    sec.row("Unknown menu state counts as captcha", "The window is restarted either way. On also adds to its "
            "captcha count", win.in_menu_none_cap_count_chk)
    sec.row("Wait on an unknown menu state", "Before restarting the window", win.in_menu_none_timeout_input)
    sec.row("Mark as captcha after this many", "Counted restarts before the account gets the captcha flag",
            win.cap_counter_limit_input)
    win.misc_disable_manager_bad_marking_chk = _InvertedSwitch()  # checked = key true = auto-BAD off
    win.misc_disable_manager_bad_marking_chk.toggled.connect(win._sync_misc_disable_bad_marking_warning)
    win.misc_disable_manager_bad_marking_chk.clicked.connect(win._warn_misc_disable_bad_marking_enabled)
    sec.row("Mark bad accounts automatically", "When a launch fails to sign in", win.misc_disable_manager_bad_marking_chk)
    win.misc_disable_manager_bad_marking_warn_lbl = K.label(
        "Off: bad cookies keep retrying Roblox sign-in and can get you rate limited.", "warn", wrap=True)
    win.misc_disable_manager_bad_marking_warn_lbl.setVisible(False)
    sec.add(win.misc_disable_manager_bad_marking_warn_lbl)
    watch(win.kill_after_enable_chk, win.kill_timeout_input, win.poll_interval_input,
          win.missing_username_cap_count_chk, win.missing_username_timeout_input, win.in_menu_none_cap_count_chk,
          win.in_menu_none_timeout_input, win.cap_counter_limit_input, win.misc_disable_manager_bad_marking_chk)

    # Roblox logs (was Extras > Roblox Log Cleanup; saves itself like the old dialog)
    from roblox_log_cleanup import get_roblox_logs_dir
    _page_hook(win, page, "captcha_solver_section", ("solver",))  # with-solver build: Captcha solver

    sec = page.section("Roblox logs", "Old log files slow detection down.")
    cfg = win._get_roblox_log_cleanup_config()
    win._roblox_log_cleanup_config = cfg
    win._roblox_log_cleanup_enabled_checkbox = K.Switch(bool(cfg.get("enabled", False)))
    sec.row("Delete old Roblox logs", "Checks the log folder once a minute", win._roblox_log_cleanup_enabled_checkbox)
    win._roblox_log_cleanup_age_spin = _spin(1, 9999, "", 90)
    win._roblox_log_cleanup_age_spin.setValue(int(cfg.get("inactive_age", 24)))
    win._roblox_log_cleanup_unit_combo = QComboBox()
    for text, value in (("Minutes", "minutes"), ("Hours", "hours"), ("Days", "days")):
        win._roblox_log_cleanup_unit_combo.addItem(text, value)
    win._roblox_log_cleanup_unit_combo.setCurrentIndex(
        max(0, win._roblox_log_cleanup_unit_combo.findData(str(cfg.get("inactive_unit", "hours")))))
    sec.row("Delete logs older than", "Files not changed for this long. Other files and folders are left alone",
            win._roblox_log_cleanup_age_spin, win._roblox_log_cleanup_unit_combo)
    win._roblox_log_cleanup_run_button = K.button("Clean now", lambda: win._run_roblox_log_cleanup(force=True))
    win._roblox_log_cleanup_run_button.setEnabled(not win._roblox_log_cleanup_in_progress)
    clean_row = sec.row("Clean now", "", win._roblox_log_cleanup_run_button)
    win._roblox_log_cleanup_status_label = clean_row.help_label
    clean_row.help_label.show()
    logs_dir = get_roblox_logs_dir()
    sec.action("Log folder", os.path.abspath(os.fspath(logs_dir)), "Open folder", lambda: _open_path(win, logs_dir))
    win._roblox_log_cleanup_enabled_checkbox.toggled.connect(win._save_roblox_log_cleanup_settings)
    page._logs_autosave = K.Autosave(win._save_roblox_log_cleanup_settings)  # was editingFinished only: a typed
    page._logs_autosave.watch(win._roblox_log_cleanup_age_spin)              # age was lost if the app closed first
    win._roblox_log_cleanup_unit_combo.currentIndexChanged.connect(win._save_roblox_log_cleanup_settings)
    win._roblox_log_cleanup_dialog = sec  # keeps show_roblox_log_cleanup_window from building a second copy
    win._update_roblox_log_cleanup_status()

    # Cookie security (File > Cookie Encryption...; the dialog stays as it is)
    sec = page.section("Cookie security", "Encrypts the cookies saved in users.json with a password.")
    main_btn = K.button("Manage", lambda: (win.show_cookie_encryption_dialog(), _cookie_refresh()))
    main_row = sec.row("Encrypt cookies", "", main_btn)
    main_row.help_label.show()
    unlock_btn = K.button("Unlock", lambda: (win.show_cookie_encryption_dialog(), _cookie_refresh()))
    unlock_row = sec.row("Unlock cookies", "Enter your password so the manager can use the saved cookies", unlock_btn)

    def _cookie_refresh():
        cm = win.config_manager
        try:
            if not cm.cookie_encryption_available():
                state = "na"
            elif not bool(cm._get_cookie_encryption_settings().get("enabled", False)):
                state = "off"
            else:
                state = "unlocked" if cm.is_cookie_unlocked() else "locked"
        except Exception:
            state = "na"
        main_row.help_label.setText({
            "na": "Not available on this PC.",
            "off": "Off. Cookies are saved as plain text.",
            "unlocked": "On and unlocked. Manage changes the password, decrypts, resets or encrypts old backups.",
            "locked": "On and locked. Unlock before launching accounts.",
        }[state])
        main_btn.setText("Turn on" if state == "off" else "Manage")
        main_btn.setEnabled(state != "na")
        unlock_row.setVisible(state == "locked")

    _cookie_refresh()
    shell.stack.currentChanged.connect(lambda i: _cookie_refresh() if shell.stack.widget(i) is page else None)

    # Troubleshooting: log-view switches owned by the Overview / Automate builders (not saved).
    sec = page.section("Troubleshooting", "Extra detail in the activity log. Leave off unless something is wrong.")
    _page_hook(win, sec, "troubleshooting_rows", ("overview", "automate"))

    # Advanced (rest of Misc)
    sec = page.section("Advanced")
    win.misc_log_confirmed_launch_mode_chk = K.Switch()
    win.misc_close_bootstrapper_error_popups_chk = K.Switch(True)
    win.misc_bes_wait_for_username_logs_chk = K.Switch(True)
    win.ui_show_tutorial_menu_chk = K.Switch()
    win.misc_hide_discord_tabs_chk = K.Switch(True)
    sec.row("Launch the next account after its log confirms", "Launch delays become minimums: the next account "
            "waits until the last one's username shows in its log", win.misc_log_confirmed_launch_mode_chk)
    sec.row("Close bootstrapper error popups", "Dismisses the Fishstrap / Bloxstrap connectivity error (403) so "
            "launches don't wait on it", win.misc_close_bootstrapper_error_popups_chk)
    sec.row("Wait for the username in logs before limiting CPU", "New windows stay unlimited until their account "
            "shows in the log", win.misc_bes_wait_for_username_logs_chk)
    sec.row("Show the tutorial", "Adds a Tutorial row to About and credits", win.ui_show_tutorial_menu_chk)
    sec.row("Hide the Discord bot page", "For when you never use the bot", win.misc_hide_discord_tabs_chk)
    watch(win.misc_log_confirmed_launch_mode_chk, win.misc_close_bootstrapper_error_popups_chk,
          win.misc_bes_wait_for_username_logs_chk, win.ui_show_tutorial_menu_chk, win.misc_hide_discord_tabs_chk)

    # Your settings (Save / Reset / Export / Import + File > Show Config Location)
    sec = page.section("Your settings", "Settings save as you change them.")
    cfg_dir = win.config_manager.get_config_info()["config_dir"]
    sec.action("Settings folder", cfg_dir, "Open folder", lambda: _open_path(win, cfg_dir))
    sec.action("Export", "Save every setting to a file. You choose whether webhooks go in it", "Export",
               win.export_config)
    sec.action("Import", "Load settings from a file. Accounts are not touched", "Import", win.import_config)

    def _reset():
        if QMessageBox.question(
                win, "Reset settings",
                "Put Settings and Merchant detection back to their defaults?\n\nAccounts and webhooks are kept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            win.reset_settings()

    sec.action("Reset settings", "Settings, Merchant detection and the alert messages go back to the defaults. "
               "Accounts and webhooks stay",
               "Restore defaults", _reset, kind="warn")
    return page


def _page_hook(win, sec, fn_name: str, modules) -> bool:
    """Rows another page builder owns: calls ui_pages.<module>.<fn_name>(win, sec) for each module that has it.
    PLACEHOLDER (guarded by ImportError) for builds where that module does not exist yet."""
    found = False
    for name in modules:
        try:
            mod = importlib.import_module(f"ui_pages.{name}")
        except ImportError:
            continue
        fn = getattr(mod, fn_name, None)
        if callable(fn):
            fn(win, sec)
            found = True
    return found


def _build_launch_order_dialog(win) -> None:
    """Old Settings > Launch Priority group, now a dialog. Same attributes; tiers -3..+3."""
    dlg = QDialog(win)
    dlg.setWindowTitle("Launch order")
    dlg.resize(760, 600)
    v = QVBoxLayout(dlg)
    v.setContentsMargins(20, 18, 20, 16)
    v.setSpacing(12)
    v.addWidget(K.label("Launch order", "h2"))
    v.addWidget(K.label("Higher numbers launch first, from +3 down to -3. Accounts with the same number keep this "
                        "order. Double-click a number to change it.", "help", wrap=True))
    win.launch_priority_search_input = QLineEdit()
    win.launch_priority_search_input.setPlaceholderText("Find by username or user id")
    win.launch_priority_search_input.setClearButtonEnabled(True)
    win.launch_priority_search_input.textChanged.connect(lambda _t: win._populate_launch_priority_table())
    v.addWidget(win.launch_priority_search_input)

    t = win.launch_priority_table = QTableWidget(0, 4)
    t.setObjectName("launchPriorityTable")
    t.setHorizontalHeaderLabels(["User id", "Username", "Priority", "Status"])
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
                      | QAbstractItemView.EditTrigger.SelectedClicked)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(36)
    t.setShowGrid(False)
    win._launch_priority_priority_delegate = _PriorityDelegate(t)
    t.setItemDelegateForColumn(2, win._launch_priority_priority_delegate)
    G = sys.modules[type(win).__module__]
    win._launch_priority_status_delegate = G._LaunchPriorityStatusDelegate(t)
    t.setItemDelegateForColumn(3, win._launch_priority_status_delegate)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
    hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
    t.setColumnWidth(2, 100)
    hh.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
    # A tier edit writes users.json at once (like save_settings' first step), so filtering the list never drops it.
    t.itemChanged.connect(lambda item: win._save_launch_priorities_from_ui() if item.column() == 2 else None)
    v.addWidget(t, 1)
    v.addWidget(K.hbox(K.button("Refresh", win._populate_launch_priority_table),
                       K.button("Reset all to 0", win._reset_launch_priorities_ui), None,
                       K.button("Close", dlg.close, kind="ink")))
    win._launch_order_dialog = dlg


# ── Tools (utilities_tab.py) ──────────────────────────────────────────────────
def _build_tools(win, shell):
    import utilities_tab

    # The old builder makes every widget and wires all its closures; this page only re-lays them out.
    # ponytail: the old (now empty) container stays alive on win._utilities_widget because its closures hold
    # references into it; nothing shows it any more.
    win._utilities_widget = utilities_tab.build_utilities_widget(win)
    save_btn, block_btn, unblock_btn, psl_btn, accounts_btn = win._util_buttons

    # Switches instead of check boxes. The closures read these attributes at call time; the old response-log
    # check box keeps its toggled -> show/hide log connection, so the switch drives it.
    old_log = win.utilities_response_log_chk
    win.utilities_response_log_chk = K.Switch(False)
    win.utilities_response_log_chk.toggled.connect(old_log.setChecked)
    win.utilities_force_chk = K.Switch(False)
    win.psl_only_missing_chk = K.Switch(True)

    page = K.Page("tools", "Tools", "Jobs that act on your accounts' Roblox profiles. One job runs at a time.",
                  shell.search)
    accounts_btn.setText("Choose accounts")
    old_label = win.utilities_accounts_label  # the closures write "Accounts: All (n)" into whatever this attribute is
    win.utilities_accounts_label = _RunsOnLabel()
    win.utilities_accounts_label.setText(old_label.text())
    win.utilities_accounts_label.setProperty("role", "label")
    bar = K.hbox(K.label("Runs on", "help"), win.utilities_accounts_label, None, accounts_btn)
    bar.setMaximumWidth(968)  # lines up with the sections below (980 minus their right margin)
    page.custom(bar)
    # Double-click "enlarge" hid the old tab's top row and scroll area; in this layout it only hid the account picker.
    try:
        win.utilities_api_log_box.doubleClicked.disconnect()
    except (RuntimeError, TypeError):
        pass

    sec = page.section("Private servers", "Creates a private server for each account and saves its link. Costs "
                       "Robux when the server is not free.")
    for w in (win.psl_place_id, win.psl_game_slug, win.psl_name_tmpl, win.psl_expected_price):
        w.setFixedWidth(300)
    sec.row("Place id", "Pick a recent one or type a new one", win.psl_place_id)
    sec.row("Game slug", "The part of the game link after the place id. Blank works", win.psl_game_slug)
    sec.row("Server name", "Use {username}, {uid} and {ts}", win.psl_name_tmpl)
    sec.row("Server price", "Robux. 0 for free private servers", win.psl_expected_price)
    sec.row("Only accounts without a link", "Skip accounts that already have a private server link",
            win.psl_only_missing_chk)
    psl_btn.setText("Get private servers")
    psl_btn.setProperty("kind", "ink")
    sec.row("Get private servers", "Runs on the accounts chosen above", psl_btn)
    sec.row("Show API responses", "Roblox's replies while the job runs",
            win.utilities_response_log_chk)
    win.utilities_api_log_label.setText("API responses")
    win.utilities_api_log_box.setPlaceholderText("Roblox's replies show here while a job runs.")
    win.utilities_api_log_label.setProperty("role", "help")
    sec.add(win.utilities_api_log_label)
    sec.add(win.utilities_api_log_box)

    sec = page.section("Block players", "Every chosen account blocks the players on the list.")
    save_btn.setText("Save list")
    sec.row("Block list", "One username or user id per line. Kept between runs", save_btn)
    sec.add(win.block_persistent_box)
    block_btn.setText("Start blocking")
    block_btn.setProperty("kind", "ink")
    sec.row("Block everyone on the list", "Saves the list first", block_btn)
    sec.row("Ignore the block log", "Block and unblock again even when the log says it is already done",
            win.utilities_force_chk)

    sec = page.section("Unblock players", "Every chosen account unblocks the players on the list.")
    sec.row("Unblock list", "One username or user id per line. Unblocked players leave the block list too")
    sec.add(win.unblock_box)
    unblock_btn.setText("Start unblocking")
    unblock_btn.setProperty("kind", "ink")
    sec.row("Unblock everyone on the list", "", unblock_btn)
    return page


# ── About and credits (_build_credits_widget + show_about + Help menu) ─────────
class _RunsOnLabel(QLabel):
    """utilities_tab writes "Accounts: All (12)" / "Accounts: 3/12" / "Accounts: None"; after "Runs on" say it plainly."""

    def setText(self, text: str) -> None:  # noqa: N802
        t = str(text or "")
        t = t[len("Accounts: "):] if t.startswith("Accounts: ") else t
        m_all, m_some = re.fullmatch(r"All \((\d+)\)", t), re.fullmatch(r"(\d+)/(\d+)", t)
        if t == "None":
            t = "no accounts (none has a working cookie)"
        elif m_all:
            t = f"all {m_all.group(1)} accounts with a working cookie"
        elif m_some:
            t = f"{m_some.group(1)} of {m_some.group(2)} accounts"
        super().setText(t)


class _AvatarFetch(QObject):
    got = Signal(int, bytes)


def _build_about(win, shell):
    from version import __version__
    page = K.Page("about", "About and credits", "", shell.search)
    open_url = win.open_url

    sec = page.section("About")
    sec.action("Manas Biome Hunt", "Support server on Discord", "Join", lambda: open_url(LINKS["manas"]))
    sec.row("M.J.JARAM", f"Jirach1's Just Another Roblox Account Manager, version {__version__}")
    sec.action("Source code", "github.com/Jirach-1/J.JARAM", "Open", lambda: open_url(LINKS["source"]))
    sec.action("License", "AGPL-3.0. © 2025 cresqnt", "Read license", lambda: open_url(LINKS["license"]))
    tut_btn = K.button("Open tutorial", win.open_help_link)
    tut_row = sec.row("Tutorial", "How to set up M.J.JARAM", tut_btn)
    upd_btn = K.button("Check for updates", lambda: win._open_update_url() if getattr(win, "_update_url", "")
                       else win.check_for_updates_manual())
    upd_row = sec.row("Updates", f"You have version {__version__}", upd_btn)
    upd_row.setVisible(hasattr(win, "check_for_updates_manual"))  # the with-solver build has no update check
    win._updates_row = (upd_row, upd_btn)

    # Tutorial row follows ui.show_tutorial_menu through the logic's QAction (_apply_tutorial_menu_visibility).
    act = getattr(win, "_tutorial_menu_action", None)
    if act is not None:
        tut_row.setVisible(act.isVisible())
        act.changed.connect(lambda: tut_row.setVisible(act.isVisible()))
    else:
        tut_row.setVisible(win.ui_show_tutorial_menu_chk.isChecked())
        win.ui_show_tutorial_menu_chk.toggled.connect(tut_row.setVisible)
    if getattr(win, "_update_url", ""):
        set_update_available(win, win._update_url)

    sec = page.section("Developers")
    holder = QWidget()
    holder.setObjectName("clear")
    lay = QHBoxLayout(holder)
    lay.setSpacing(40)
    lay.addStretch(1)
    here = Path(sys.modules[type(win).__module__].__file__).parent
    cards, pending = [], []
    for i, (name, fname, url) in enumerate(DEVS):
        try:
            data = (here / fname).read_bytes()
        except Exception:
            data = b""
            pending.append((i, url))
        cards.append(win._make_dev_card(name, data, fallback=name[:1]))
        lay.addWidget(cards[-1])
    lay.addStretch(1)
    sec.add(holder)

    # The old widget downloaded missing avatars on the UI thread; here it happens in the background, once,
    # the first time About is opened.
    fetch = page._avatar_fetch = _AvatarFetch()

    def _got(i: int, data: bytes):
        new = win._make_dev_card(DEVS[i][0], data, fallback=DEVS[i][0][:1])
        lay.replaceWidget(cards[i], new)
        cards[i].deleteLater()
        cards[i] = new

    fetch.got.connect(_got)

    def _download():
        from urllib.request import Request, urlopen
        for i, url in pending:
            try:
                fetch.got.emit(i, urlopen(Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=8).read())
            except Exception:
                pass

    def _on_page(i):
        if shell.stack.widget(i) is page and pending and not getattr(page, "_avatars_started", False):
            page._avatars_started = True
            threading.Thread(target=_download, daemon=True).start()

    shell.stack.currentChanged.connect(_on_page)

    sec = page.section("Credits")
    sec.action("CPU limiter", "BES, Battle Encoder Shirase", "Open site", lambda: open_url(LINKS["bes"]))
    sec.action("RAM limiter", "RAM-Limiter, the trimmer's inspiration", "Open site", lambda: open_url(LINKS["ram"]))
    sec.action("Merchant fix", "MultiScope merchant fix", "Open site", lambda: open_url(LINKS["msfix"]))
    sec.action("Scope Development", "Discord server", "Join", lambda: open_url(LINKS["scope"]))
    sec.action("J.JARAM Discord", "The official J.JARAM Discord server", "Join", lambda: open_url(LINKS["jaram"]))
    return page


def build(win, shell):
    autosave = getattr(shell, "autosave_settings", None)
    if autosave is None:
        autosave = shell.autosave_settings = K.Autosave(lambda: win.save_settings())

    settings_page = _build_settings(win, shell, autosave)
    tools_page = _build_tools(win, shell)
    about_page = _build_about(win, shell)

    # setup_settings_tab ended with load_settings_tab(); this module is built last, so every page's widgets exist.
    autosave.paused = True
    try:
        win.load_settings_tab()
    except Exception as e:  # a page that load_settings_tab reads (webhooks, OCR) is missing
        try:
            win.add_log(f"[Settings] Could not load every setting into the new pages: {e}")
        except Exception:
            pass
    finally:
        autosave.paused = False
    return [("Settings", settings_page), ("Tools", tools_page), ("About and credits", about_page)]
