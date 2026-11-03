"""Merchant detection (ocr) and Hardware limiting (bes) pages.

Ports setup_ocr_tab, setup_bes_tab, setup_TRIMMER_tab and the Settings-tab controls the spec moves here
(misc_disable_log_merchants_when_ocr_active_chk, ui_show_selected_sets_bes_exempt_slot1_chk,
misc_msedgewebview2_limiter_enabled_chk) plus the Extras-menu Merchant Fix. Widgets keep the old attribute names.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDoubleSpinBox, QHeaderView, QLabel, QMessageBox,
    QSpinBox, QTableWidget, QVBoxLayout, QWidget,
)

import ui_kit as K
import trimmer as _trimmer

MISC_DISABLE_LOG_MERCHANTS_KEY = "disable_log_based_merchant_detection_when_ocr_merchants_enabled"


def _spin(lo, hi, suffix="", step=1, width=130):
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    s.setFixedWidth(width)
    return s


def _dspin(lo, hi, decimals, step, suffix="", width=130):
    s = QDoubleSpinBox()
    s.setRange(lo, hi)
    s.setDecimals(decimals)
    s.setSingleStep(step)
    if suffix:
        s.setSuffix(suffix)
    s.setFixedWidth(width)
    return s


def _column(*widgets) -> QWidget:
    w = QWidget()
    w.setObjectName("clear")
    lay = QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(6)
    for x in widgets:
        lay.addWidget(x)
    return w


class _RamLimiter(_trimmer.TrimmerTab):
    """TrimmerTab logic (worker, load/save, config snapshot) with the UI built by this page.

    The widget itself is never shown; its controls are placed in the RAM section rows.
    """

    def __init__(self, main_window):
        self._win = main_window
        super().__init__(getattr(main_window, "config_manager", None))

    def _build_ui(self):
        self.enabled_chk = K.Switch()
        self.interval_spin = _spin(1, 3600, " s")
        self.interval_spin.setValue(15)
        self.threshold_chk = K.Switch(True)
        self.threshold_spin = _dspin(1.0, 65536.0, 1, 64.0, " MB", width=150)
        self.threshold_spin.setValue(1024.0)
        self.status_lbl = QLabel()  # replaced by the Limit RAM row's help label in build()
        self.interval_spin.valueChanged.connect(self._on_ui_changed)
        self.threshold_chk.toggled.connect(self._on_ui_changed)
        self.threshold_spin.valueChanged.connect(self._on_ui_changed)
        self.enabled_chk.toggled.connect(self._on_enabled_toggled)

    def _queue_log(self, msg: str):
        try:
            self.log_queue.put(str(msg))  # add_log stamps the time
        except Exception:
            pass

    def _append_log(self, msg: str):
        try:
            self._win.add_log(f"[RAM] {msg}")
        except Exception:
            pass

    def _update_status(self):
        if _trimmer._native_trim_targets is None:
            text = "Not available on this build (the native RAM trimmer is missing)"
        elif self.enabled_chk.isChecked():
            text = "Trimming every Roblox window"
        else:
            text = "Off"
        try:
            self.status_lbl.setText(text)
        except Exception:
            pass


def build(win, shell):
    if getattr(shell, "autosave_settings", None) is None:
        shell.autosave_settings = K.Autosave(lambda: win.save_settings())
    try:
        settings = win.config_manager.load_settings() or {}
    except Exception:
        settings = {}
    defaults = win.config_manager.default_settings or {}
    return [("Merchant detection", _build_ocr(win, shell, settings, defaults)),
            ("Hardware limiting", _build_bes(win, shell, settings, defaults))]


# ----------------------------------------------------------------------------------------------
# Merchant detection
# ----------------------------------------------------------------------------------------------

def _build_ocr(win, shell, settings, defaults):
    page = K.Page("ocr", "Merchant detection",
                  "Finds Jester, Mari, Rin and Sinister S. on every account and sends the alert.", shell.search)

    # --- How merchants are found ---
    how = page.section("How merchants are found",
                       "Roblox logs are always read. Reading chat too catches what the logs miss.")
    win.ocr_enable_chk = K.Switch()
    win.ocr_enable_chk.toggled.connect(win._on_ocr_enabled_toggled)

    def _save_while_stopped(on):  # _on_ocr_enabled_toggled returns before saving when the manager is stopped
        if on and not win._loading_ocr_settings and not win._is_manager_running():
            win._on_ocr_settings_changed()
    win.ocr_enable_chk.toggled.connect(_save_while_stopped)
    win.ocr_status_label = how.row("Read chat too", "Uses more CPU", win.ocr_enable_chk).help_label

    win.misc_disable_log_merchants_when_ocr_active_chk = K.Switch()
    misc = settings.get("misc", {}) if isinstance(settings.get("misc"), dict) else {}
    win.misc_disable_log_merchants_when_ocr_active_chk.setChecked(bool(misc.get(
        MISC_DISABLE_LOG_MERCHANTS_KEY, (defaults.get("misc", {}) or {}).get(MISC_DISABLE_LOG_MERCHANTS_KEY, True))))
    shell.autosave_settings.watch(win.misc_disable_log_merchants_when_ocr_active_chk)
    how.row("When reading chat, ignore merchants in the logs",
            "Only while Read chat too is on and a merchant filter is on, so each merchant is posted once",
            win.misc_disable_log_merchants_when_ocr_active_chk)

    # Merchant fix was a checkable Extras-menu QAction; _toggle_merchant_fix only needs setChecked().
    win._merchant_fix_action = K.Switch()
    try:
        import merchant_fix
        win._merchant_fix_action.setChecked(bool(merchant_fix.is_enabled()))
    except Exception:
        pass
    win._merchant_fix_action.clicked.connect(win._toggle_merchant_fix)
    how.row("Merchant fix",
            "Blocks Roblox asset downloads so every merchant shows in the logs, and clears the asset cache. "
            "Needs admin; close Roblox first", win._merchant_fix_action)

    def _clear_asset_cache():
        try:
            import merchant_fix
            deleted, errors = merchant_fix.clear_roblox_cache()
        except Exception as e:
            QMessageBox.critical(win, "Roblox asset cache", f"Could not clear the cache: {e}")
            return
        msg = f"Cleared {deleted} cache file(s)."
        if errors:
            msg += (f"\n\n{len(errors)} file(s) could not be deleted (close Roblox and try again):\n"
                    + "\n".join(errors[:5]))
        QMessageBox.information(win, "Roblox asset cache", msg)
        try:
            win.add_log(f"[MerchantFix] Cleared {deleted} Roblox asset cache file(s).")
        except Exception:
            pass
    how.action("Roblox asset cache", "Clearing it makes merchants show again. Close Roblox first",
               "Clear cache", _clear_asset_cache)

    # --- Reading chat ---
    chat = page.section("Reading chat",
                        "Only applies when Read chat too is on. Pick the six lines of Roblox chat to read.")
    win.ocr_roi_label = chat.row("Chat area", "Not set yet",
                                 K.button("Set area", win.calibrate_ocr_roi)).help_label
    chat.action("Check it", "Shows what the reader sees in the chat area", "Preview chat", win.show_ocr_preview)
    chat.action("Test the skip setting",
                "Run it twice: it compares the chat now with the last run and says if the read would be skipped",
                "Compare chat", win.test_ocr_frame_compare)
    chat.action("Shared areas", "Reuse one screen area for many filters", "Edit areas",
                win._open_ocr_shared_areas_dialog)

    filters_dlg = _filters_dialog(win)
    chat.action("Filters", "The merchant filters and any other chat lines that should alert you",
                "Edit filters", lambda: (filters_dlg.show(), filters_dlg.raise_(), filters_dlg.activateWindow()))
    chat.action("Ready-made filters", "Common filters to start from", "Add presets",
                win._open_ocr_filter_presets_dialog)

    win.ocr_only_mapped_chk = K.Switch()  # new: ocr.only_mapped_pids had no control outside the Filter Users dialog

    def _on_only_mapped(checked):
        win.ocr_only_mapped_pids = bool(checked)
        win._on_ocr_settings_changed()
    win.ocr_only_mapped_chk.toggled.connect(_on_only_mapped)

    def _open_filter_users():
        win._open_ocr_filter_user_assignments_dialog()
        _sync_only_mapped(win)  # the dialog has its own copy of this checkbox
    chat.action("Accounts per filter", "Which accounts each filter reads", "Choose accounts", _open_filter_users)
    chat.row("Only read windows linked to an account", "Skips Roblox windows the manager did not launch",
             win.ocr_only_mapped_chk)

    # --- Speed and CPU ---
    speed = page.section("Speed and CPU",
                         "Only applies when Read chat too is on. More workers read chat faster but use more CPU.")
    win.ocr_workers_spin = _spin(1, 16)
    win.ocr_workers_spin.valueChanged.connect(win._on_ocr_settings_changed)
    speed.row("Workers", "How many read chat at once", win.ocr_workers_spin)

    win.ocr_device_combo = QComboBox()
    win.ocr_device_combo.setMinimumWidth(240)
    win.ocr_device_combo.currentIndexChanged.connect(win._on_ocr_settings_changed)
    win._load_ocr_device_choices()
    win.ocr_device_label = speed.row("Runs on", "", win.ocr_device_combo).help_label
    win.ocr_device_label.setVisible(True)  # status text, filled by _update_ocr_device_label

    win.ocr_batch_delay_spin = _dspin(0.0, 60.0, 2, 0.1, " s")
    win.ocr_batch_delay_spin.valueChanged.connect(win._on_ocr_settings_changed)
    speed.row("Wait between batches", "Lower reads more often", win.ocr_batch_delay_spin)

    win.ocr_max_caps_spin = _spin(1, 60)
    win.ocr_max_caps_spin.valueChanged.connect(win._on_ocr_settings_changed)
    speed.row("Captures per batch", "Most windows captured in one batch", win.ocr_max_caps_spin)

    win.ocr_frame_diff_tol_spin = _spin(0, 100, " %")
    win.ocr_frame_diff_tol_spin.valueChanged.connect(win._on_ocr_settings_changed)
    speed.row("Skip if chat barely changed", "Skips the read when the chat changed by this much or less. Saves CPU",
              win.ocr_frame_diff_tol_spin)

    win.ocr_preprocess_chk = K.Switch()
    win.ocr_preprocess_chk.toggled.connect(win._on_ocr_settings_changed)
    speed.row("Clean up the image first", "Helps on busy backgrounds", win.ocr_preprocess_chk)

    speed.action("Defaults", "Puts merchant detection back how it came, including the chat area, shared areas "
                 "and filters", "Restore defaults", win._reset_ocr_to_defaults, kind="warn")

    ocr_debug_switches(win)  # Settings > Troubleshooting places them (automate.troubleshooting_rows)

    win._apply_ocr_settings_to_ui(settings.get("ocr", {}) or {})
    _update_ocr_roi_label(win)
    _update_ocr_device_label(win)
    return page


def ocr_debug_switches(win):
    """ocr_log_text_chk / ocr_loop_logs_chk (ocr.log_ocr_text / ocr.log_loop), made once by whichever page asks first."""
    for attr in ("ocr_log_text_chk", "ocr_loop_logs_chk"):
        if not isinstance(getattr(win, attr, None), K.Switch):
            sw = K.Switch()
            sw.toggled.connect(win._on_ocr_settings_changed)
            setattr(win, attr, sw)
    return win.ocr_log_text_chk, win.ocr_loop_logs_chk


def _filters_dialog(win) -> QDialog:
    """The old Filters group (table + Add / Remove), now behind "Edit filters". Each row's Edit opens
    _open_ocr_filter_settings_dialog (wired by _add_filter_row)."""
    dlg = QDialog(win)
    dlg.setWindowTitle("Chat filters")
    dlg.resize(900, 560)
    lay = QVBoxLayout(dlg)
    lay.setContentsMargins(20, 18, 20, 18)
    lay.setSpacing(10)
    lay.addWidget(K.label("Each filter looks for text of one color in its area. The three merchant filters "
                          "cannot be renamed or removed. Changes save right away.", "help", wrap=True))

    t = QTableWidget()
    t.setColumnCount(7)
    t.setHorizontalHeaderLabels(["On", "Name", "R", "G", "B", "Tolerance", "Settings"])
    t.setShowGrid(False)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    hh.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    for col, w in ((0, 60), (2, 95), (3, 95), (4, 95), (5, 105), (6, 120)):
        t.setColumnWidth(col, w)
    vh = t.verticalHeader()
    vh.setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    vh.setDefaultSectionSize(62)
    vh.setMinimumSectionSize(62)
    win.ocr_filter_table = t
    lay.addWidget(t, 1)

    def _add():
        win._add_filter_row()
        win._on_ocr_settings_changed()

    def _remove():
        win._remove_selected_filter_rows()
        win._on_ocr_settings_changed()
    lay.addWidget(K.hbox(K.button("Add filter", _add), K.button("Remove selected", _remove), None,
                         K.button("Close", dlg.close)))
    return dlg


def _sync_only_mapped(win) -> None:
    sw = getattr(win, "ocr_only_mapped_chk", None)
    if sw is not None and sw.isChecked() != bool(getattr(win, "ocr_only_mapped_pids", False)):
        sw.blockSignals(True)
        sw.setChecked(bool(win.ocr_only_mapped_pids))
        sw.blockSignals(False)


# ----------------------------------------------------------------------------------------------
# Hardware limiting
# ----------------------------------------------------------------------------------------------

def _build_bes(win, shell, settings, defaults):
    page = K.Page("bes", "Hardware limiting", "Keeps CPU and RAM use down so more accounts fit on this PC.",
                  shell.search)
    available = win.bes_controller is not None

    # --- CPU ---
    cpu = page.section("CPU", "Slows Roblox down to save CPU (BES). Lifted briefly for Anti-AFK and actions.")
    win.bes_enable_chk = K.Switch()
    win.bes_status_label = cpu.row("Limit CPU", "Applies to every Roblox window", win.bes_enable_chk).help_label

    win.bes_mode_combo = QComboBox()
    win.bes_mode_combo.addItem("All threads", "all_threads")
    win.bes_mode_combo.addItem("Smart", "smart")
    win.bes_mode_combo.setMinimumWidth(160)
    cpu.row("Which threads", "Smart limits only the busiest threads of each Roblox window", win.bes_mode_combo)

    win.bes_smart_count_spin = _spin(1, 256)
    win.bes_smart_count_spin.setValue(4)
    cpu.row("Smart threads per window", "Only used with Smart", win.bes_smart_count_spin)

    # Old QSliders (0-99) -> number fields; the logic only uses value / setValue / valueChanged.
    win.bes_game_slider = _spin(0, 99, " %")
    win.bes_game_slider.setValue(50)
    cpu.row("Outside the menu", "CPU taken away while in game", win.bes_game_slider)
    win.bes_menu_slider = _spin(0, 99, " %")
    win.bes_menu_slider.setValue(85)
    cpu.row("In the menu", "CPU taken away while in the Roblox main menu", win.bes_menu_slider)
    # ponytail: the spin boxes show the value; these labels only exist because _load_bes_settings writes them.
    win.bes_menu_val = QLabel()
    win.bes_game_val = QLabel()

    win.bes_cycle_spin = _spin(10, 1000, " ms", step=5)
    win.bes_cycle_spin.setValue(50)
    cpu.row("Cycle", "How often it switches between running and paused", win.bes_cycle_spin)

    # --- RAM ---
    ram_sec = page.section("RAM", "Trims Roblox memory it is not using, so more accounts fit.")
    old = getattr(win, "trimmer_tab", None)
    if old is not None:  # only while the old tab is still built (harness): one worker at a time
        try:
            old.shutdown()
        except Exception:
            pass
    ram = _RamLimiter(win)
    win.trimmer_tab = ram
    ram.status_lbl = ram_sec.row("Limit RAM", "", ram.enabled_chk).help_label
    ram.status_lbl.setVisible(True)  # status text, filled by _update_status
    ram._update_status()
    ram_sec.row("Check every", "How often it trims", ram.interval_spin)
    ram_sec.row("Only trim big windows", "Skips windows under the size below", ram.threshold_chk)
    ram_sec.row("Trim above", "Per Roblox window", ram.threshold_spin)

    # --- Fine tuning ---
    fine = page.section("Fine tuning", "Timing around actions, and accounts that are never limited.")
    win.bes_auto_grace_spin = _dspin(0.0, 10.0, 2, 0.25, " s")
    win.bes_auto_grace_spin.setValue(1.0)
    fine.row("Wait after an action", "Before limiting the window again", win.bes_auto_grace_spin)
    win.bes_auto_lead_spin = _dspin(0.0, 15.0, 1, 0.5, " s")
    win.bes_auto_lead_spin.setValue(3.0)
    fine.row("Lift the limit this early", "Before action rows start clicking or typing", win.bes_auto_lead_spin)

    win.bes_exempt_combos = []
    for _ in range(3):
        c = QComboBox()
        c.setMinimumWidth(300)
        win.bes_exempt_combos.append(c)
    refresh = K.button("Refresh list", win._bes_refresh_user_list, size="sm")
    never = fine.row("Never limit", "Up to 3 accounts that are never slowed down",
                     _column(*win.bes_exempt_combos, refresh))
    never.layout().itemAt(0).layout().addStretch(1)  # keep label + help at the top of the tall row

    ui = settings.get("ui", {}) if isinstance(settings.get("ui"), dict) else {}
    misc = settings.get("misc", {}) if isinstance(settings.get("misc"), dict) else {}
    win.ui_show_selected_sets_bes_exempt_slot1_chk = K.Switch(bool(ui.get(
        "show_selected_sets_bes_exempt_slot1",
        (defaults.get("ui", {}) or {}).get("show_selected_sets_bes_exempt_slot1", False))))
    fine.row("Showing a window stops limiting it",
             "Showing an account's window puts it in the first Never limit slot",
             win.ui_show_selected_sets_bes_exempt_slot1_chk)
    win.misc_msedgewebview2_limiter_enabled_chk = K.Switch(bool(misc.get(
        "msedgewebview2_limiter_enabled",
        (defaults.get("misc", {}) or {}).get("msedgewebview2_limiter_enabled", True))))
    fine.row("Limit Edge WebView too",
             "Closes msedgewebview2.exe, Roblox's built-in browser helper, once each launch is confirmed. "
             "Can close Microsoft Edge pages too", win.misc_msedgewebview2_limiter_enabled_chk)
    shell.autosave_settings.watch(win.ui_show_selected_sets_bes_exempt_slot1_chk,
                                  win.misc_msedgewebview2_limiter_enabled_chk)

    def _restore_defaults():
        win._reset_bes_to_defaults()
        d = _trimmer.TrimmerTab.DEFAULTS  # no trimmer reset existed; the setters save through _on_ui_changed
        ram.enabled_chk.setChecked(bool(d["enabled"]))
        ram.interval_spin.setValue(int(d["interval_s"]))
        ram.threshold_chk.setChecked(bool(d["use_threshold"]))
        ram.threshold_spin.setValue(float(d["threshold_mb"]))
    fine.action("Defaults", "Puts CPU and RAM limiting back how they came", "Restore defaults", _restore_defaults,
                kind="warn")

    if not available:
        # Old tab showed only this message and created nothing; keep the BES controls inert.
        for w in (win.bes_enable_chk, win.bes_mode_combo, win.bes_smart_count_spin, win.bes_game_slider,
                  win.bes_menu_slider, win.bes_cycle_spin, win.bes_auto_grace_spin, win.bes_auto_lead_spin,
                  refresh, *win.bes_exempt_combos):
            w.setEnabled(False)
        win.bes_status_label.setText("CPU limiting is unavailable on this system or build")
        win.bes_status_label.setProperty("role", "warn")
        return page

    if win._bes_save_timer is None:
        win._bes_save_timer = QTimer(win)
        win._bes_save_timer.setSingleShot(True)
        win._bes_save_timer.timeout.connect(win._save_bes_settings)
    if win._bes_tick_timer is None:
        win._bes_tick_timer = QTimer(win)
        win._bes_tick_timer.timeout.connect(win._bes_tick)
        win._bes_tick_timer.setInterval(1000)

    win.bes_enable_chk.toggled.connect(win._on_bes_enabled_toggled)
    win.bes_cycle_spin.valueChanged.connect(win._on_bes_ui_changed)
    win.bes_mode_combo.currentIndexChanged.connect(win._on_bes_mode_changed)
    win.bes_smart_count_spin.valueChanged.connect(win._on_bes_ui_changed)
    win.bes_menu_slider.valueChanged.connect(win._on_bes_slider_changed)
    win.bes_game_slider.valueChanged.connect(win._on_bes_slider_changed)
    win.bes_auto_lead_spin.valueChanged.connect(win._on_bes_ui_changed)
    win.bes_auto_grace_spin.valueChanged.connect(win._on_bes_ui_changed)
    for combo in win.bes_exempt_combos:
        combo.currentIndexChanged.connect(win._on_bes_ui_changed)

    win._bes_refresh_user_list()
    win._load_bes_settings()
    return page


# ----------------------------------------------------------------------------------------------
# Replacements for old gui.py methods (label wording / theme). The integrator moves these into gui.py.
# ----------------------------------------------------------------------------------------------

def _update_ocr_roi_label(self):
    if self.ocr_roi:
        x, y, w, h = self.ocr_roi
        self.ocr_roi_label.setText(f"Set: starts {x:.0%} from the left and {y:.0%} from the top, "
                                   f"{w:.0%} wide and {h:.0%} tall")
    else:
        self.ocr_roi_label.setText("Not set yet")
    # ponytail: every path that loads ocr.only_mapped_pids (_apply_ocr_settings_to_ui, _reset_ocr_to_defaults)
    # refreshes this label right after, so the switch is synced here instead of in each of them.
    _sync_only_mapped(self)


def _update_ocr_device_label(self):
    try:
        from ocr_worker import get_ocr_device_summary
        summary = get_ocr_device_summary()
    except Exception:
        summary = "Unknown (error checking the device)"
    if summary.startswith("Unknown (OCR engine not initialized"):
        text = "Shows what it uses once chat reading starts"
    elif summary.startswith("Unavailable"):
        text = "Chat reading is not installed in this build"
    else:
        text = f"Now using: {summary}"
    self.ocr_device_label.setText(text)


def _handle_ocr_status(self, status: str):
    if (status or "").strip().lower() == "running":
        self.ocr_status_label.setText("Uses more CPU. Reading chat now")
        self._refresh_ocr_device_label()
    else:
        self._stop_ocr_filter_alert()
        self.ocr_status_label.setText("Uses more CPU. Not reading chat right now")


REPLACES = {
    "_update_ocr_roi_label": _update_ocr_roi_label,
    "_update_ocr_device_label": _update_ocr_device_label,
    "_handle_ocr_status": _handle_ocr_status,
}
