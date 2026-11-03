"""Notify pages: Webhooks and Discord bot.

`win.webhooks_table` stays a QTableWidget and stays the source of truth (save_settings, load_settings_tab and
test_selected_webhook read it), but it is now the visible list: Webhook | (URL, hidden) | Accounts | Biomes.
Per-biome modes live in item data (MODES_ROLE) on column 0 instead of one QComboBox per cell, so the only
REPLACES entry for it is `_collect_webhooks_from_ui`. One editor panel under the list edits the selected row.
"""
from __future__ import annotations

import re
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QGridLayout, QHBoxLayout,
    QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
    QScrollArea, QSpinBox, QTableWidget, QTableWidgetItem, QTextEdit, QVBoxLayout, QWidget,
)

import biomes as B
import ui_kit as K

MODES_ROLE = int(Qt.ItemDataRole.UserRole) + 4   # {BIOME: "None"|"Message"|"Everyone"} on column 0
USERS_ROLE = int(Qt.ItemDataRole.UserRole)       # same roles the old table used
RARE = {"GLITCHED", "DREAMSPACE", "CYBERSPACE", "SINGULARITY"}
NEXT_MODE = {"None": "Message", "Message": "Everyone", "Everyone": "None"}
CHIP_WORD = {"None": "Off", "Message": "Post", "Everyone": "Ping"}
CHIP_V = {"None": "off", "Message": "post", "Everyone": "ping"}
PING_TYPES = ["None", "User ID", "Role ID", "@everyone"]  # save_settings' _mk_ping keys on these texts
FIELD_W = 380


def _gm(win):
    """The module that defines RobloxManagerGUI (`gui`, or `__main__` when gui.py is run directly)."""
    return sys.modules[type(win).__module__]


def _users(win) -> dict:
    cm = win.config_manager
    try:
        users = cm.peek_users() if hasattr(cm, "peek_users") else cm.load_users()
    except Exception:
        users = {}
    return users if isinstance(users, dict) else {}


def _field(placeholder: str = "", secret: bool = False, width: int = FIELD_W) -> QLineEdit:
    w = QLineEdit()
    w.setPlaceholderText(placeholder)
    if secret:
        w.setEchoMode(QLineEdit.EchoMode.Password)
    w.setFixedWidth(width)
    return w


# ----------------------------------------------------------------------------------------------- REPLACES

def _collect_webhooks_from_ui(self) -> list:
    """Same output as before; biome modes now come from column-0 item data instead of cell combos."""
    gm = sys.modules[type(self).__module__]
    all_user_ids = {str(uid) for uid in _users(self)}
    webhooks = []
    table = self.webhooks_table
    for r in range(table.rowCount()):
        name_item = table.item(r, 0)
        url_item = table.item(r, 1)
        name = name_item.text().strip() if name_item else ""
        url = url_item.text().strip() if url_item else ""
        if not url:
            continue
        data_item = name_item or url_item
        user_filter = data_item.data(USERS_ROLE)
        user_filter_mode = gm._normalize_user_filter_mode(data_item.data(gm.WEBHOOK_USER_FILTER_MODE_ROLE))
        selected_users: list = []
        explicit_users = False
        if isinstance(user_filter, (list, tuple, set)):
            explicit_users = True
            selected_users = sorted({str(u).strip() for u in user_filter if str(u).strip()})
            if user_filter_mode == "whitelist" and all_user_ids and set(selected_users) == all_user_ids:
                explicit_users = False
                selected_users = []
        if user_filter_mode == "blacklist":
            explicit_users = True

        stored = data_item.data(MODES_ROLE) or {}
        allowed, biome_modes = [], {}
        for biome_name in gm.GUI_BIOME_NAMES:
            key = str(biome_name).upper()
            mode = stored.get(key, "None")
            if key in gm.HARD_EVERYONE_BIOMES and gm._bm_lock_enforced():
                mode = "Everyone"
            biome_modes[key] = mode
            if mode in ("Message", "Everyone"):
                allowed.append(key)

        entry = {"name": name, "url": url, "biomes": allowed}
        if biome_modes:
            entry["biome_modes"] = biome_modes
        role_pings = self._normalize_webhook_role_pings_config(data_item.data(gm.WEBHOOK_ROLE_PINGS_ROLE))
        if role_pings:
            entry["biome_role_pings"] = role_pings
        if explicit_users:
            entry["users"] = selected_users
            entry["users_explicit"] = True
        if user_filter_mode == "blacklist":
            entry["user_filter_mode"] = "blacklist"
        webhooks.append(entry)
    return webhooks


def _on_discord_control_state_changed(self, state: str, message: str) -> None:
    """Plain wording and theme colours for the bot status; Start stays off when discord.py is missing."""
    key = str(state or "").strip().lower()
    label = getattr(self, "discord_control_status_label", None)
    if label is not None:
        label.setStyleSheet("")
        label.setText({"running": "Running", "starting": "Connecting", "error": "Needs attention"}.get(key, "Stopped"))
        label.setProperty("role", "warn" if key == "error" else "label")
        label.style().unpolish(label)
        label.style().polish(label)
    detail = getattr(self, "discord_control_status_detail_label", None)
    if detail is not None:
        detail.setText(str(message or "Discord bot is stopped."))
        detail.setVisible(True)
    running_like = key in {"running", "starting"}
    missing_dep = False
    try:
        missing_dep = bool(self.discord_control_service and self.discord_control_service.dependency_error())
    except Exception:
        pass
    start_btn = getattr(self, "discord_control_start_btn", None)
    stop_btn = getattr(self, "discord_control_stop_btn", None)
    if start_btn is not None:
        start_btn.setEnabled(not running_like and not missing_dep)
    if stop_btn is not None:
        stop_btn.setEnabled(running_like)


REPLACES = {
    "_collect_webhooks_from_ui": _collect_webhooks_from_ui,
    "_on_discord_control_state_changed": _on_discord_control_state_changed,
}


# ----------------------------------------------------------------------------------------------- Webhooks

def _build_biomes_section(win, page, save):
    gm = _gm(win)
    role_pings_role = gm.WEBHOOK_ROLE_PINGS_ROLE
    mode_role = gm.WEBHOOK_USER_FILTER_MODE_ROLE
    norm_mode = gm._normalize_user_filter_mode

    sec = page.section("Biomes", "")
    custom_btn = K.button("Custom biomes", size="sm")
    roles_btn = K.button("Role pings", size="sm")
    spread_btn = K.button("Spread accounts evenly", size="sm")
    add_btn = K.button("Add webhook", kind="ink", size="sm")
    head = sec.row("Biome webhooks", "", custom_btn, roles_btn, spread_btn, add_btn)

    t = win.webhooks_table = QTableWidget(0, 4)
    t.setHorizontalHeaderLabels(["Webhook", "URL", "Accounts", "Biomes"])
    t.setColumnHidden(1, True)
    t.verticalHeader().setVisible(False)
    t.verticalHeader().setDefaultSectionSize(40)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.setEditTriggers(QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed)
    t.setShowGrid(False)
    t.setWordWrap(False)
    hh = t.horizontalHeader()
    hh.setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    hh.setSectionResizeMode(2, QHeaderView.ResizeMode.Interactive)
    hh.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
    hh.setHighlightSections(False)
    hh.setDefaultAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    t.setColumnWidth(0, 220)
    t.setColumnWidth(2, 190)
    t.setMinimumHeight(250)
    sec.add(t)
    rename_hint = K.label("Double-click a name to rename it.", "hint")
    rename_hint.setContentsMargins(0, 6, 0, 10)
    sec.add(rename_hint)

    busy = [False]  # set while code (not the user) writes into the table

    # ---- row data helpers
    def cur() -> int:
        r = t.currentRow()
        return r if 0 <= r < t.rowCount() else -1

    def modes(r) -> dict:
        return dict(t.item(r, 0).data(MODES_ROLE) or {})

    def set_users(item, user_ids, mode, all_ids=None):
        """Port of the old _apply_user_filter_to_row (data only; summaries replace the tooltip)."""
        mode = norm_mode(mode)
        item.setData(mode_role, mode)
        if user_ids is None:
            item.setData(USERS_ROLE, None)
            return
        cleaned = [str(u).strip() for u in user_ids if str(u).strip()]
        if mode == "whitelist" and all_ids and set(cleaned) == set(all_ids):
            item.setData(USERS_ROLE, None)
        else:
            item.setData(USERS_ROLE, cleaned)

    def accounts_text(r, all_ids) -> str:
        it = t.item(r, 0)
        sel = it.data(USERS_ROLE)
        if norm_mode(it.data(mode_role)) == "blacklist":
            return f"All except {len(sel)}" if sel else "All accounts"
        if sel is None or (all_ids and set(sel) >= all_ids):
            return "All accounts"
        return f"{len(sel)} accounts" if sel else "No accounts (posts nothing)"

    def biomes_text(r) -> str:
        m = modes(r)
        names = [str(b).upper() for b in gm.GUI_BIOME_NAMES]
        posts = [b for b in names if m.get(b, "None") != "None"]
        if not posts:
            return "Posts nothing yet"
        pings = [b.title() for b in names if m.get(b) == "Everyone"]
        text = f"Posts {len(posts)} of {len(names)} biomes"
        if pings:
            text += "; pings on " + ", ".join(pings[:3]) + (f" and {len(pings) - 3} more" if len(pings) > 3 else "")
        roles = t.item(r, 0).data(role_pings_role) or {}
        if roles:
            text += f"; role pings on {len(roles)}"
        return text

    def refresh_row(r, all_ids=None):
        if all_ids is None:
            all_ids = {str(u) for u in _users(win)}
        busy[0] = True
        t.item(r, 2).setText(accounts_text(r, all_ids))
        t.item(r, 3).setText(biomes_text(r))
        busy[0] = False

    def refresh_count():
        n = t.rowCount()
        head.help_label.setText(f"{n} webhook{'' if n == 1 else 's'}. Select one to set its URL, accounts and biomes.")
        head.help_label.setVisible(True)

    def add_webhook_row(name="", url="", allowed_biomes=None, biome_modes=None, user_ids=None,
                        biome_role_pings=None, user_filter_mode="whitelist"):
        """Same signature as the old nested add_webhook_row (load_settings_tab calls win._add_webhook_row)."""
        allowed = {str(b).upper() for b in (allowed_biomes or [])}
        given = {str(k).upper(): v for k, v in (biome_modes or {}).items()}
        m = {}
        for b in gm.GUI_BIOME_NAMES:
            key = str(b).upper()
            v = given.get(key, "Message" if key in allowed else "None")
            m[key] = v if v in NEXT_MODE else "None"
        busy[0] = True
        r = t.rowCount()
        t.insertRow(r)
        it0 = QTableWidgetItem(name or "")
        it0.setData(MODES_ROLE, m)
        set_users(it0, user_ids, user_filter_mode)
        it0.setData(role_pings_role, win._normalize_webhook_role_pings_config(biome_role_pings or {}))
        t.setItem(r, 0, it0)
        for col, text in ((1, url), (2, ""), (3, "")):
            it = QTableWidgetItem(text)
            it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            t.setItem(r, col, it)
        busy[0] = False
        refresh_row(r)
        refresh_count()
        return r

    def clear_rows():
        t.setRowCount(0)
        refresh_count()
        show_editor()

    win._add_webhook_row = add_webhook_row
    win._clear_webhook_rows = clear_rows
    win.__dict__.pop("_apply_webhook_biome_column_visibility", None)  # Biome Columns is dropped

    # ---- editor panel (one for the selected row)
    panel = QFrame()
    panel.setProperty("role", "card")
    panel.setStyleSheet(
        f"QPushButton[bchip=\"true\"] {{ min-height: 30px; padding: 0 10px; border-radius: 15px; font-size: 13px;"
        f" text-align: left; border: 1px solid {K.T['rule']}; background: transparent; color: {K.T['ink2']}; }}"
        f"QPushButton[bchip=\"true\"][v=\"post\"] {{ background: {K.T['sel']}; color: {K.T['ink']}; border-color: {K.T['ink2']}; }}"
        f"QPushButton[bchip=\"true\"][v=\"ping\"] {{ background: {K.T['ink']}; color: {K.T['app']}; border-color: {K.T['ink']}; }}"
    )
    pl = QVBoxLayout(panel)
    pl.setContentsMargins(16, 14, 16, 16)
    pl.setSpacing(12)
    empty_lbl = K.label("Select a webhook above, or add one.", "help")
    pl.addWidget(empty_lbl)
    body = QWidget()
    body.setObjectName("clear")
    bl = QVBoxLayout(body)
    bl.setContentsMargins(0, 0, 0, 0)
    bl.setSpacing(12)
    pl.addWidget(body)

    title = K.label("", "h2")
    test_btn = K.button("Test", size="sm")
    acc_btn = K.button("Accounts", size="sm")
    del_btn = K.button("Delete", kind="warn", size="sm")
    bl.addWidget(K.hbox(title, None, test_btn, acc_btn, del_btn))
    url_edit = _field("https://discord.com/api/webhooks/...", secret=True, width=520)
    bl.addWidget(K.hbox(K.label("Webhook URL", "label"), None, url_edit))
    post_all_btn = K.button("Post all", size="sm")
    rare_btn = K.button("Rare only, with ping", size="sm")
    none_btn = K.button("None", size="sm")
    left = QVBoxLayout()
    left.setSpacing(2)
    left.addWidget(K.label("Biomes", "label"))
    left.addWidget(K.label("Click a biome to switch it between Off, Post and Ping (@everyone).", "help"))
    bl.addWidget(K.hbox(left, None, post_all_btn, rare_btn, none_btn))
    grid_host = QWidget()
    grid_host.setObjectName("clear")
    grid = QGridLayout(grid_host)
    grid.setContentsMargins(0, 0, 0, 0)
    grid.setSpacing(6)
    bl.addWidget(grid_host)
    sec.add(panel)
    chips: dict = {}

    def build_chips():
        while grid.count():
            w = grid.takeAt(0).widget()
            if w is not None:
                w.hide()  # gone now, not at the next event loop pass
                w.deleteLater()
        chips.clear()
        for i, b in enumerate(gm.GUI_BIOME_NAMES):
            key = str(b).upper()
            btn = QPushButton()
            btn.setProperty("bchip", True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            try:
                color = int(B.biome_meta(key)[0])
            except Exception:
                color = 0x3BA55D
            pm = QPixmap(12, 12)
            pm.fill(QColor(f"#{color & 0xFFFFFF:06x}"))
            btn.setIcon(QIcon(pm))
            btn.clicked.connect(lambda _=False, k=key: cycle(k))
            grid.addWidget(btn, i // 5, i % 5)
            chips[key] = btn

    def paint_chips():
        r = cur()
        if r < 0:
            return
        m = modes(r)
        for key, btn in chips.items():
            v = m.get(key, "None")
            btn.setText(f"{key.title()}  {CHIP_WORD[v]}")
            btn.setProperty("v", CHIP_V[v])
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def show_editor():
        r = cur()
        empty_lbl.setVisible(r < 0)
        body.setVisible(r >= 0)
        if r < 0:
            return
        title.setText(t.item(r, 0).text().strip() or f"Webhook {r + 1}")
        url_edit.setText(t.item(r, 1).text())
        paint_chips()

    def set_modes(r, m):
        busy[0] = True
        t.item(r, 0).setData(MODES_ROLE, m)
        busy[0] = False
        refresh_row(r)
        paint_chips()
        save.poke()

    def cycle(key):
        r = cur()
        if r >= 0:
            m = modes(r)
            m[key] = NEXT_MODE[m.get(key, "None")]
            set_modes(r, m)

    def set_all(pick):
        r = cur()
        if r >= 0:
            set_modes(r, {str(b).upper(): pick(str(b).upper()) for b in gm.GUI_BIOME_NAMES})

    def on_item_changed(item):
        if busy[0] or item.column() != 0:
            return
        if item.row() == cur():
            title.setText(item.text().strip() or f"Webhook {item.row() + 1}")
        save.poke()

    def on_url_edited(text):
        r = cur()
        if r >= 0:
            t.item(r, 1).setText(text.strip())
            save.poke()

    def test():
        r = cur()
        if r >= 0:
            t.selectRow(r)
            win.test_selected_webhook()

    def delete():
        r = cur()
        if r < 0:
            return
        name = t.item(r, 0).text().strip() or f"Webhook {r + 1}"
        if QMessageBox.question(win, "Delete webhook", f"Delete {name}? Its biomes and accounts are lost.") \
                != QMessageBox.StandardButton.Yes:
            return
        t.removeRow(r)
        refresh_count()
        show_editor()
        save.poke()

    def add():
        url, ok = QInputDialog.getText(win, "Add webhook", "Discord webhook URL:")
        url = (url or "").strip()
        if not ok or not url:
            return
        r = add_webhook_row(f"Webhook {t.rowCount() + 1}", url)
        t.selectRow(r)
        save.poke()

    # ---- accounts for one webhook (replaces the old multi-webhook "Webhook User Routing" dialog)
    def open_accounts():
        r = cur()
        if r < 0:
            return
        it = t.item(r, 0)
        users = _users(win)
        choices = []
        seen = set()
        for uid, info in users.items():
            cid = str(uid).strip()
            if cid and cid not in seen:
                seen.add(cid)
                choices.append((cid, str((info or {}).get("username", cid) if isinstance(info, dict) else cid).strip()))
        ids = [c[0] for c in choices]
        idset = set(ids)
        name_to_id = {n.lower(): cid for cid, n in choices if n}
        # Backup accounts are reserve, not routed: hidden from the list, but an existing pick on one is kept.
        backups = {c for c in ids if isinstance(users.get(c), dict) and users[c].get("backup")}
        choices = [c for c in choices if c[0] not in backups]
        shown = idset - backups

        def resolve(val: str) -> str:
            val = (val or "").strip()
            if val in idset:
                return val
            m = re.search(r"(\d+)", val)
            if m and m.group(1) in idset:
                return m.group(1)
            return name_to_id.get(val.lower(), "")

        mode = norm_mode(it.data(mode_role))
        raw = it.data(USERS_ROLE)
        selected, missing = set(), []
        if raw is None:
            selected = set(ids) if mode == "whitelist" else set()
        else:
            for v in raw:
                rid = resolve(str(v))
                if rid:
                    selected.add(rid)
                elif str(v).strip():
                    missing.append(str(v).strip())

        dlg = QDialog(win)
        name = it.text().strip() or f"Webhook {r + 1}"
        dlg.setWindowTitle(f"Accounts for {name}")
        dlg.resize(560, 600)
        v = QVBoxLayout(dlg)
        v.setContentsMargins(20, 20, 20, 16)
        v.setSpacing(12)
        v.addWidget(K.label(f"Accounts for {name}", "h2"))
        v.addWidget(K.label("Biome alerts from these accounts go to this webhook.", "help", wrap=True))
        seg = K.Segmented(["Only these", "All except these"], "All except these" if mode == "blacklist" else "Only these")
        lw = QListWidget()
        for cid, uname in choices:
            li = QListWidgetItem(f"{uname} ({cid})" if uname and uname != cid else cid)
            li.setData(USERS_ROLE, cid)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked if cid in selected else Qt.CheckState.Unchecked)
            lw.addItem(li)
        for mid in sorted(set(missing)):
            li = QListWidgetItem(f"{mid} (not in your accounts)")
            li.setData(USERS_ROLE, mid)
            li.setFlags(li.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            li.setCheckState(Qt.CheckState.Checked)
            lw.addItem(li)
        count = K.label("", "help")

        def items():
            return [lw.item(i) for i in range(lw.count())]

        def set_checks(pick):
            for i, li in enumerate(items()):
                li.setCheckState(Qt.CheckState.Checked if pick(i, li) else Qt.CheckState.Unchecked)

        def checked():
            return [li.data(USERS_ROLE) for li in items() if li.checkState() == Qt.CheckState.Checked]

        def update_count(*_):
            count.setText(f"{len(checked())} of {lw.count()} ticked")

        last_mode = [seg.currentText()]

        def mode_changed(text):  # old _mode_changed: flip all/none when switching
            if text == last_mode[0]:
                return
            sel = set(checked())
            if text == "All except these" and shown and sel >= shown:
                set_checks(lambda i, li: False)
            elif text == "Only these" and not sel:
                set_checks(lambda i, li: True)
            last_mode[0] = text

        seg.changed.connect(mode_changed)
        bulk = [("All", lambda i, li: True), ("None", lambda i, li: False),
                ("Every other one", lambda i, li: i % 2 == 0),
                ("Invert", lambda i, li: li.checkState() != Qt.CheckState.Checked)]
        bulk_btns = [K.button(txt, (lambda p=p: set_checks(p)), size="sm") for txt, p in bulk]
        v.addWidget(K.hbox(seg, None, *bulk_btns))
        v.addWidget(lw, 1)
        v.addWidget(count)
        lw.itemChanged.connect(update_count)
        update_count()
        box = QDialogButtonBox()
        ok_btn = box.addButton("Save accounts", QDialogButtonBox.ButtonRole.AcceptRole)
        ok_btn.setProperty("kind", "ink")
        box.addButton(QDialogButtonBox.StandardButton.Cancel)
        box.accepted.connect(dlg.accept)
        box.rejected.connect(dlg.reject)
        v.addWidget(box)
        if not dlg.exec():
            return
        new_mode = "blacklist" if seg.currentText() == "All except these" else "whitelist"
        busy[0] = True
        set_users(it, sorted(set(checked()) | (selected & backups)), new_mode, all_ids=idset)
        busy[0] = False
        refresh_row(r)
        save.poke()

    def spread():
        """Old _distribute_evenly: round-robin every account over every webhook (whitelist), keep unknown ids."""
        n = t.rowCount()
        users = _users(win)
        ids = [str(u).strip() for u, info in users.items() if not (isinstance(info, dict) and info.get("backup"))]
        if not n or not ids:
            QMessageBox.information(win, "Spread accounts evenly", "Add webhooks and accounts first.")
            return
        if QMessageBox.question(
                win, "Spread accounts evenly",
                f"Share {len(ids)} accounts across {n} webhooks? This replaces the accounts chosen for each webhook.") \
                != QMessageBox.StandardButton.Yes:
            return
        idset = set(ids)
        picks = [{u for u in (t.item(r, 0).data(USERS_ROLE) or []) if u not in idset} for r in range(n)]
        for i, uid in enumerate(ids):
            picks[i % n].add(uid)
        busy[0] = True
        for r in range(n):
            set_users(t.item(r, 0), sorted(picks[r]), "whitelist", all_ids=idset)
        busy[0] = False
        for r in range(n):
            refresh_row(r, idset)
        save.poke()

    # ---- Role pings (old _open_webhook_role_ping_dialog, unchanged apart from reading this table)
    def open_role_pings():
        rows = t.rowCount()
        if rows == 0:
            QMessageBox.information(win, "No webhooks", "Add a webhook before adding role pings.")
            return
        role_biomes = [str(b).strip().upper() for b in gm.GUI_BIOME_NAMES
                       if str(b).strip() and str(b).strip().upper() not in gm.HARD_EVERYONE_BIOMES]
        if not role_biomes:
            QMessageBox.information(win, "Role pings", "No eligible biomes are available.")
            return
        entries = []
        for r in range(rows):
            display = t.item(r, 0).text().strip() or t.item(r, 1).text().strip() or f"Webhook {r + 1}"
            entries.append({"row": r, "display": display,
                            "pings": dict(win._normalize_webhook_role_pings_config(t.item(r, 0).data(role_pings_role)))})

        dlg = QDialog(win)
        dlg.setWindowTitle("Role pings")
        dlg.resize(760, 560)
        h = QHBoxLayout(dlg)
        left_col = QVBoxLayout()
        webhook_list = QListWidget()
        webhook_list.setMinimumWidth(240)
        for entry in entries:
            webhook_list.addItem(QListWidgetItem(entry["display"]))
        left_col.addWidget(webhook_list)
        clear_all_btn = QPushButton("Clear all")
        left_col.addWidget(clear_all_btn)
        h.addLayout(left_col)
        right = QVBoxLayout()
        h.addLayout(right)
        right.addWidget(K.label("Discord role pinged when a biome is posted. Mentions like <@&123> work too.", "help", wrap=True))
        clear_current_btn = QPushButton("Clear this webhook")
        right.addWidget(K.hbox(clear_current_btn, None))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        fields_widget = QWidget()
        fields_form = QFormLayout(fields_widget)
        fields_form.setContentsMargins(6, 6, 6, 6)
        fields_form.setSpacing(8)
        scroll.setWidget(fields_widget)
        right.addWidget(scroll)
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        right.addWidget(btn_box)
        current_idx = [-1]
        editors: dict = {}

        def _clear_fields():
            while fields_form.count():
                item = fields_form.takeAt(0)
                w = item.widget() if item is not None else None
                if w is not None:
                    w.setParent(None)
                    w.deleteLater()

        def _save_current_fields():
            idx = current_idx[0]
            if 0 <= idx < len(entries):
                cleaned = {}
                for biome_key, edit in editors.items():
                    role_id = win._normalize_role_id_for_ping(edit.text())
                    if role_id:
                        cleaned[biome_key] = role_id
                entries[idx]["pings"] = cleaned

        def _build_role_fields(idx):
            _save_current_fields()
            editors.clear()
            _clear_fields()
            current_idx[0] = idx
            if idx < 0 or idx >= len(entries):
                fields_form.addRow(QLabel("Select a webhook to edit."))
                return
            pings = entries[idx].get("pings", {}) or {}
            for biome_key in role_biomes:
                edit = QLineEdit()
                edit.setPlaceholderText("Role id")
                edit.setText(str(pings.get(biome_key, "") or ""))
                editors[biome_key] = edit
                fields_form.addRow(biome_key.title(), edit)
            scroll.verticalScrollBar().setValue(0)

        def _clear_current():
            idx = current_idx[0]
            if 0 <= idx < len(entries):
                entries[idx]["pings"] = {}
            for edit in editors.values():
                edit.clear()

        def _clear_all():
            for entry in entries:
                entry["pings"] = {}
            for edit in editors.values():
                edit.clear()

        def _apply_and_close():
            _save_current_fields()
            busy[0] = True
            for entry in entries:
                t.item(entry["row"], 0).setData(role_pings_role,
                                                 win._normalize_webhook_role_pings_config(entry.get("pings") or {}))
            busy[0] = False
            for entry in entries:
                refresh_row(entry["row"])
            save.poke()
            dlg.accept()

        clear_current_btn.clicked.connect(_clear_current)
        clear_all_btn.clicked.connect(_clear_all)
        webhook_list.currentRowChanged.connect(_build_role_fields)
        btn_box.accepted.connect(_apply_and_close)
        btn_box.rejected.connect(dlg.reject)
        start = max(0, cur())
        webhook_list.setCurrentRow(start)
        if current_idx[0] != start:
            _build_role_fields(start)
        dlg.exec()

    # ---- Custom biomes (old _open_custom_biomes_dialog; the rebuild now refreshes chips and summaries)
    def biomes_changed():
        B.load_biomes_catalog()
        gm.GUI_BIOME_NAMES[:] = [b for b in B.biome_names() if str(b).upper() != "NORMAL"]
        build_chips()
        all_ids = {str(u) for u in _users(win)}
        for r in range(t.rowCount()):
            refresh_row(r, all_ids)
        show_editor()

    def open_custom_biomes():
        dlg = QDialog(win)
        dlg.setWindowTitle("Custom biomes")
        dlg.resize(520, 560)
        v = QVBoxLayout(dlg)
        v.addWidget(K.label(
            "Add your own biomes, or change a built-in one. The name must match the biome text Roblox reports "
            "(case does not matter). Removing only clears your own entry; built-in biomes come back.", "help", wrap=True))
        lw = QListWidget()
        v.addWidget(lw, 1)
        form = QFormLayout()
        name_edit = QLineEdit()
        name_edit.setPlaceholderText("INCINERATOR")
        color_edit = QLineEdit()
        color_edit.setPlaceholderText("0xff6a00 (optional)")
        timer_edit = QLineEdit()
        timer_edit.setPlaceholderText("Duration in seconds (optional)")
        thumb_edit = QLineEdit()
        thumb_edit.setPlaceholderText("Picture URL (optional)")
        form.addRow("Name", name_edit)
        form.addRow("Color", color_edit)
        form.addRow("Timer (s)", timer_edit)
        form.addRow("Picture", thumb_edit)
        v.addLayout(form)

        def _refresh():
            lw.clear()
            custom = {str(k).upper() for k in B.load_custom_biomes().keys()}
            for b in B.biome_names():
                item = QListWidgetItem(f"{b}  (custom)" if str(b).upper() in custom else str(b))
                item.setData(Qt.ItemDataRole.UserRole, str(b))
                lw.addItem(item)

        def _on_pick():
            item = lw.currentItem()
            if item is None:
                return
            b = str(item.data(Qt.ItemDataRole.UserRole))
            color, thumb = B.biome_meta(b)
            dur = B.biome_duration(b)
            name_edit.setText(b)
            color_edit.setText("0x%06x" % int(color))
            thumb_edit.setText(thumb)
            timer_edit.setText("" if dur is None else str(dur))

        def _save():
            try:
                saved_name = B.save_custom_biome(name_edit.text(), color_edit.text(), thumb_edit.text(),
                                                  timer_edit.text().strip() or None)
            except Exception as e:
                QMessageBox.warning(dlg, "Custom biomes", f"Could not save: {e}")
                return
            biomes_changed()
            _refresh()
            QMessageBox.information(dlg, "Custom biomes", f"Saved {saved_name}.")

        def _remove():
            item = lw.currentItem()
            if item is None:
                return
            b = str(item.data(Qt.ItemDataRole.UserRole))
            try:
                removed = B.delete_custom_biome(b)
            except Exception as e:
                QMessageBox.warning(dlg, "Custom biomes", f"Could not remove: {e}")
                return
            if not removed:
                QMessageBox.information(dlg, "Custom biomes", f"{b} is built in, so there is nothing of yours to remove.")
                return
            biomes_changed()
            _refresh()

        save_btn = QPushButton("Add or update")
        del_btn2 = QPushButton("Remove")
        v.addWidget(K.hbox(save_btn, del_btn2, None))
        btn_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        v.addWidget(btn_box)
        lw.currentRowChanged.connect(lambda _: _on_pick())
        save_btn.clicked.connect(_save)
        del_btn2.clicked.connect(_remove)
        btn_box.rejected.connect(dlg.reject)
        _refresh()
        dlg.exec()

    t.itemChanged.connect(on_item_changed)
    t.itemSelectionChanged.connect(show_editor)
    url_edit.textEdited.connect(on_url_edited)
    test_btn.clicked.connect(test)
    acc_btn.clicked.connect(open_accounts)
    del_btn.clicked.connect(delete)
    post_all_btn.clicked.connect(lambda: set_all(lambda k: "Message"))
    rare_btn.clicked.connect(lambda: set_all(lambda k: "Everyone" if k in RARE else "None"))
    none_btn.clicked.connect(lambda: set_all(lambda k: "None"))
    add_btn.clicked.connect(add)
    spread_btn.clicked.connect(spread)
    roles_btn.clicked.connect(open_role_pings)
    custom_btn.clicked.connect(open_custom_biomes)
    build_chips()
    refresh_count()
    show_editor()


def _wire_ping(combo: QComboBox, idf: QLineEdit) -> None:
    """Ping type + id. @everyone fills the id itself (save_settings keeps a raw "@everyone" id as-is);
    None clears it. Old configs that stored a raw mention under None are shown as User / Role / @everyone."""
    def sync(*_):
        typ = combo.currentText()
        if typ == "@everyone":
            if idf.text() != "@everyone":
                idf.setText("@everyone")
            idf.setEnabled(False)
            return
        if idf.text() == "@everyone":
            idf.clear()
        idf.setEnabled(typ != "None" or bool(idf.text()))

    def on_text(text):
        if combo.currentText() != "None":
            return
        s = text.strip()
        m_role, m_user = re.fullmatch(r"<@&(\d+)>", s), re.fullmatch(r"<@!?(\d+)>", s)
        if s == "@everyone":
            combo.setCurrentText("@everyone")
        elif m_role or m_user:
            combo.setCurrentText("Role ID" if m_role else "User ID")
            idf.setText((m_role or m_user).group(1))
        sync()

    combo.currentIndexChanged.connect(sync)
    combo.activated.connect(lambda _i: idf.clear() if combo.currentText() == "None" else None)
    idf.textChanged.connect(on_text)
    sync()


def _build_webhooks(win, shell):
    save = shell.autosave_settings
    page = K.Page("webhooks", "Webhooks",
                  "Where each alert goes. Sinister S. has its own webhook; item alerts go to each account's webhook.",
                  shell.search)
    _build_biomes_section(win, page, save)

    # ---- Merchants and bosses
    sec = page.section("Merchants and bosses", "Jester, Mari and Rin are posted to the merchant webhook.")
    for key, name in (("jester", "Jester"), ("mari", "Mari"), ("rin", "Rin")):
        sw = K.Switch()
        combo = QComboBox()
        combo.addItems(PING_TYPES)
        combo.setFixedWidth(130)
        idf = _field("Id", width=200)
        setattr(win, f"ms_enable_{key}", sw)
        setattr(win, f"ms_{key}_type", combo)
        setattr(win, f"ms_{key}_id", idf)
        _wire_ping(combo, idf)
        sec.row(name, "Alert when it arrives. Ping: nobody, a user, a role, or @everyone.", combo, idf, sw)
        save.watch(sw, combo, idf)
    sec.row("Sinister S.", "The boss. Always posted to the Sinister S. webhook (the merchant webhook when that is "
                           "blank), never pinged.")
    sec.action("Test merchant webhook", "Sends a test alert for each merchant that is on.", "Send test",
               win.test_merchant_pings)

    # ---- Alert channel
    sec = page.section("Alert channel", "Dead accounts, captchas and rate limits are posted here.")
    win.webhook_input = _field("https://discord.com/api/webhooks/...", secret=True)
    sec.row("Alert webhook", "Blackout, captcha, bad account and report alerts.", win.webhook_input)
    win.ratelimit_ping_input = _field("User id, or <@&role id>")
    sec.row("Rate-limit ping", "Pinged here when any webhook is rate limited; held alerts are resent. "
                               "Blank pings the Discord bot admins.", win.ratelimit_ping_input)
    win.blackout_ping_input = _field("Blank sends nothing")
    sec.row("Blackout message", "Sent when one or no Roblox windows are left running.", win.blackout_ping_input)
    win.cap_msg_input = _field("Blank sends nothing")
    sec.row("Captcha message", "Sent when an account needs a captcha. Use {username} and {uid}.", win.cap_msg_input)
    from ui_pages.system import _page_hook
    _page_hook(win, sec, "alert_channel_rows", ("solver",))  # with-solver build: Captcha solved message
    win.bad_msg_input = _field("Blank sends nothing")
    sec.row("Bad account message", "Sent when an account's cookie stops working. Use {username} and {uid}.",
            win.bad_msg_input)
    win.hourly_users_report_chk = K.Switch()
    sec.row("Accounts report", "Posts how many accounts there are and how many are running.",
            win.hourly_users_report_chk)
    sp = win.hourly_users_report_interval_spin = QSpinBox()
    sp.setRange(1, 168)
    sp.setSuffix(" h")
    sp.setValue(1)
    sp.setFixedWidth(100)
    sp.setEnabled(False)
    win.hourly_users_report_chk.toggled.connect(sp.setEnabled)
    sec.row("Report every", "Hours between accounts reports.", sp)
    save.watch(win.webhook_input, win.ratelimit_ping_input, win.blackout_ping_input, win.cap_msg_input,
               win.bad_msg_input, win.hourly_users_report_chk, sp)

    # ---- Other webhooks
    sec = page.section("Other webhooks")
    win.ms_merchant_webhook_input = _field("https://discord.com/api/webhooks/...", secret=True)
    sec.row("Merchant webhook", "Jester, Mari and Rin.", win.ms_merchant_webhook_input)
    win.ms_sinister_webhook_input = _field("Blank uses the merchant webhook", secret=True)
    sec.row("Sinister S. webhook", "The boss. Blank uses the merchant webhook.", win.ms_sinister_webhook_input)
    win.ms_player_tracker_input = _field("Optional", secret=True)
    sec.row("Player tracker webhook", "Who joins each server.", win.ms_player_tracker_input)
    win.misc_skip_unknown_webhook_chk = K.Switch()
    sec.row("Skip alerts with unknown context", "Posts nothing when the account's owner or private server is unknown.",
            win.misc_skip_unknown_webhook_chk)
    save.watch(win.ms_merchant_webhook_input, win.ms_sinister_webhook_input, win.ms_player_tracker_input,
               win.misc_skip_unknown_webhook_chk)
    return page


# ----------------------------------------------------------------------------------------------- Discord bot

def _build_discord(win, shell):
    win._ensure_discord_control_service()
    page = K.Page("discord", "Discord bot", "Run M.J.JARAM from Discord with slash commands.", shell.search)

    sec = page.section("Discord bot", "Control the manager from your Discord server.")
    win.discord_control_status_label = K.label("Stopped", "label")
    win.discord_control_start_btn = K.button("Start bot", win._start_discord_control_bot_from_ui, kind="ink")
    win.discord_control_stop_btn = K.button("Stop bot", win._stop_discord_control_bot_from_ui)
    status_row = sec.row("Bot status", "Discord bot is stopped.", win.discord_control_status_label,
                         win.discord_control_start_btn, win.discord_control_stop_btn)
    win.discord_control_status_detail_label = status_row.help_label
    try:
        dep_msg = win.discord_control_service.dependency_error() if win.discord_control_service else ""
    except Exception:
        dep_msg = ""
    if dep_msg:
        sec.add(K.label("The bot needs the discord.py package, which is not installed on this PC.", "warn", wrap=True))
    win.discord_control_token_input = _field("Discord bot token", secret=True)
    sec.row("Bot token", "Kept on this PC, encrypted when cookie encryption is on.", win.discord_control_token_input)
    win.discord_control_enabled_chk = K.Switch()
    sec.row("Start with M.J.JARAM", "Starts the bot when M.J.JARAM opens.", win.discord_control_enabled_chk)

    sec = page.section("Who can use it", "Admins can use every command.")
    win.discord_control_admin_users_input = QTextEdit()
    win.discord_control_admin_users_input.setPlaceholderText("1234567890")
    win.discord_control_admin_roles_input = QTextEdit()
    win.discord_control_admin_roles_input.setPlaceholderText("1234567890")
    for w in (win.discord_control_admin_users_input, win.discord_control_admin_roles_input):
        w.setAcceptRichText(False)
        w.setFixedSize(FIELD_W, 72)
    sec.row("Admin user ids", "Comma separated or one per line. Also pinged when a webhook is rate limited and the "
                              "rate-limit ping is blank.", win.discord_control_admin_users_input)
    sec.row("Admin role ids", "Comma separated or one per line.", win.discord_control_admin_roles_input)
    win.discord_control_allow_admin_perm_chk = K.Switch(True)
    sec.row("Allow server admins", "Members with Discord's Administrator permission.",
            win.discord_control_allow_admin_perm_chk)

    sec = page.section("Commands", "Everything the bot can do.")
    win.discord_control_help_box = QTextEdit()
    win.discord_control_help_box.setReadOnly(True)
    win.discord_control_help_box.setFont(QFont("Consolas", 10))
    win.discord_control_help_box.setStyleSheet("QTextEdit { min-height: 380px; }")  # the kit's min-height overrides setFixedHeight's minimum
    sec.add(win.discord_control_help_box)

    # Initial load (was at the end of setup_discord_control_tab).
    config = win._load_discord_control_config()
    token = str(config.get("token") or "")
    cm = win.config_manager
    try:
        raw_encrypted_token = str(getattr(cm, "_settings_raw_encrypted_discord_token", "") or "") \
            or cm._raw_encrypted_discord_token_from_disk()
    except Exception:
        raw_encrypted_token = ""
    win._discord_control_token_hidden_until_unlock = bool(
        not token and cm.cookie_encryption_enabled() and not cm.is_cookie_unlocked() and raw_encrypted_token)
    win.discord_control_enabled_chk.setChecked(bool(config.get("enabled", False)))
    win.discord_control_token_input.setText(token)
    win.discord_control_admin_users_input.setPlainText("\n".join(config.get("admin_user_ids", [])))
    win.discord_control_admin_roles_input.setPlainText("\n".join(config.get("admin_role_ids", [])))
    win.discord_control_allow_admin_perm_chk.setChecked(bool(config.get("allow_discord_admin_permission", True)))
    win._refresh_discord_control_command_docs()
    win._on_discord_control_state_changed("stopped", "Discord bot is stopped.")

    # Save Config button -> debounced autosave (connected after the load so startup does not save).
    def autosave():
        win._refresh_discord_control_token_after_cookie_unlock()
        cfg = win._discord_control_config_from_ui()
        if not win._save_discord_control_config(cfg):
            try:
                err = cm.get_cookie_error()
            except Exception:
                err = ""
            win.discord_control_status_detail_label.setText("Could not save the bot settings." + (f" {err}" if err else ""))
            return
        svc = win.discord_control_service
        if not (svc and svc.is_running()):
            return
        keys = ("token", "admin_user_ids", "admin_role_ids", "allow_discord_admin_permission")
        live = svc.current_config()
        if [cfg.get(k) for k in keys] == [live.get(k) for k in keys]:
            return
        ok, msg = svc.stop()  # same restart the old Save Config did, so changes reach the running bot
        if ok and str(cfg.get("token") or "").strip():
            ok, msg = svc.start(win._discord_control_runtime_config(cfg))
        if not ok:
            win.add_log(f"[Discord Control] {msg}")

    dc_save = page.dc_autosave = K.Autosave(autosave, delay_ms=1000)  # ponytail: kept on the page so it lives
    dc_save.watch(win.discord_control_enabled_chk, win.discord_control_token_input,
                  win.discord_control_allow_admin_perm_chk)
    win.discord_control_admin_users_input.textChanged.connect(dc_save.poke)
    win.discord_control_admin_roles_input.textChanged.connect(dc_save.poke)
    return page


def build(win, shell):
    if getattr(shell, "autosave_settings", None) is None:
        shell.autosave_settings = K.Autosave(lambda: win.save_settings())
    return [("Webhooks", _build_webhooks(win, shell)), ("Discord bot", _build_discord(win, shell))]
