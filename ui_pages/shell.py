"""The M.J.JARAM window: sidebar, header (Start / Pause / Stop, search), pages in nav order."""
from __future__ import annotations

import importlib
import os

from PySide6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import QApplication, QLabel, QPushButton

import ui_kit as K

SUPPORT_URL = "https://discord.gg/oppression"  # Manas Biome Hunt

# (group, [page_id, ...]) in sidebar order; page modules return their pages by page_id.
NAV = [
    ("Run", ["overview", "accounts", "bloxgen", "activity"]),
    ("Automate", ["scbr", "afk", "rows", "ocr", "bes", "autoreplace"]),
    ("Notify", ["webhooks", "discord"]),
    ("System", ["settings", "tools", "about"]),
]
# "solver" (Account generator, Auto replace) exists only in the with-solver build; _modules() skips it elsewhere.
MODULES = ["overview", "accounts", "automate", "detect_hw", "notify", "solver", "system"]


def _modules():
    for name in MODULES:
        try:
            yield importlib.import_module(f"ui_pages.{name}")
        except ModuleNotFoundError as e:
            if e.name != "ui_pages.solver":  # only the optional module may be missing
                raise


class _ShowWhenEnabled(QObject):
    """Header buttons: the app logic enables/disables Start, Pause and Stop; the header shows only the enabled ones."""

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.EnabledChange:
            obj.setVisible(obj.isEnabled())
        return False


class _FlushOnClose(QObject):
    """Closing the window saves a settings change made in the last moment (autosave waits 400 ms)."""

    def eventFilter(self, obj, event):  # noqa: N802
        if event.type() == QEvent.Type.Close:
            K.Autosave.flush_all()
        return False


class _PauseButton(QPushButton):
    """The logic writes "Pause Manager" / "Resume Manager"; the header says Pause / Resume."""

    def setText(self, text: str) -> None:  # noqa: N802
        super().setText({"Pause Manager": "Pause", "Resume Manager": "Resume"}.get(text, text))


def setup_ui(win, icon_path: str = "") -> None:
    win.setWindowTitle("M.J.JARAM")
    win.resize(1360, 860)
    if icon_path and os.path.exists(icon_path):
        win.setWindowIcon(QIcon(icon_path))
    app = QApplication.instance()
    if isinstance(app, QApplication):
        app.setStyleSheet(K.stylesheet())  # app-wide, so every dialog gets the theme too

    shell = K.Shell("M.J.JARAM")
    shell.autosave_settings = K.Autosave(lambda: win.save_settings())
    win.shell = shell
    win.tab_widget = shell.stack  # the logic's tab API (addTab / currentIndex / currentChanged) lives on the page stack
    win.setCentralWidget(shell)
    QShortcut(QKeySequence.StandardKey.Find, win, activated=lambda: shell.search_box.setFocus())
    win._flush_on_close = _FlushOnClose(win)
    win.installEventFilter(win._flush_on_close)

    # Header: Start when stopped; Pause / Resume + Stop while running.
    win.start_btn = K.button("Start manager", win.start_manager, kind="ink")
    win.pause_btn = _PauseButton("Pause")
    win.pause_btn.setCursor(Qt.CursorShape.PointingHandCursor)
    win.pause_btn.clicked.connect(lambda _=False: win.toggle_pause_manager())
    win.stop_btn = K.button("Stop manager", win.stop_manager)
    win._show_when_enabled = _ShowWhenEnabled(win)
    for b in (win.pause_btn, win.stop_btn, win.start_btn):
        b.installEventFilter(win._show_when_enabled)
        shell.header_actions.addWidget(b)
    win.pause_btn.setEnabled(False)
    win.stop_btn.setEnabled(False)
    win.pause_btn.setVisible(False)
    win.stop_btn.setVisible(False)

    # Sidebar footer: manager status + support server.
    # The logic still writes manager status/uptime here; they are not shown (the header buttons say the state).
    win.status_label = QLabel("Status: Stopped")
    win.uptime_label = QLabel()
    shell.sidebar.footer.addWidget(K.button("Join support server", lambda: QDesktopServices.openUrl(QUrl(SUPPORT_URL)),
                                            kind="discord"))

    _menu_actions(win)

    pages = {}
    for mod in _modules():
        for nav_text, page in mod.build(win, shell):
            pages[page.page_id] = (nav_text, page)
    index = {}
    for group, ids in NAV:
        shell.sidebar.group(group)
        for pid in ids:
            if pid in pages:
                nav_text, page = pages[pid]
                index[pid] = shell.add_page(page, nav_text)
    shell.sidebar.finish()
    win.page_index = index
    # Tab indices the logic compares against currentIndex().
    win.dashboard_tab_index = index.get("overview")
    win.multiscope_tab_index = index.get("overview")
    win.users_tab_index = index.get("accounts")
    win.accounts_tab_index = index.get("accounts")
    win.settings_tab_index = index.get("settings")
    win.discord_control_tab_index = index.get("discord")
    win._apply_discord_tabs_visibility()  # load_settings_tab ran inside system.build, before the pages had indices

    win.start_time = None
    win.user_data = {}
    refresh_nav(win)


def refresh_nav(win) -> None:
    """Sidebar state text (once a second, from update_ui; skipped while minimised)."""
    sb, idx = win.shell.sidebar, getattr(win, "page_index", {})
    page = getattr(win, "_overview_page", None)
    for key, (get, is_on, _state) in (getattr(page, "auto_specs", None) or {}).items():  # key == page id
        try:
            target = get()
            on = bool(target is not None and is_on(target))
            text = ("Running" if on else "Stopped") if key == "discord" else ("On" if on else "Off")
            sb.set_state(idx.get(key, -1), text)
        except Exception:
            pass
    try:
        users = win.config_manager.peek_users() or {}
        wt = getattr(win, "worker_thread", None)
        running = wt is not None and wt.isRunning()  # user_data keeps the last run's rows after a stop
        active = sum(1 for d in (win.user_data or {}).values()
                     if d.get("status") == "Active" and not d.get("needs_restart")) if running else 0
        sb.set_state(idx.get("accounts", -1), f"{active} in game" if active else f"{len(users)}")
    except Exception:
        pass
    table = getattr(win, "webhooks_table", None)
    if table is not None:
        sb.set_state(idx.get("webhooks", -1), str(table.rowCount()))
    try:
        sb.count_label.setText(f"{len(win.config_manager.peek_users() or {})} accounts")
    except Exception:
        pass


def install(cls) -> None:
    """Put the page modules' methods (their REPLACES) on the window class; gui.py calls this once at import."""
    for mod in _modules():
        for meth, fn in getattr(mod, "REPLACES", {}).items():
            setattr(cls, meth, fn)
    cls._SOL_RESOLUTIONS = importlib.import_module("ui_pages.automate").SOL_RESOLUTIONS


def _menu_actions(win) -> None:
    """The old menu bar is gone, but the logic keeps state on these QActions; pages trigger them."""
    win._tutorial_menu_action = QAction("Tutorial", win)
    win._tutorial_menu_action.triggered.connect(win.open_help_link)
    win._tutorial_menu_sep = QAction(win)
    win._update_url = ""
    win._update_thread = None
    if hasattr(win, "check_for_updates_startup"):  # the with-solver build has no update check
        QTimer.singleShot(3000, win.check_for_updates_startup)  # silent; About shows it when something newer exists
