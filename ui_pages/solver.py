"""with-solver only: the Account generator (BloxGen) and Auto replace pages, plus the solver rows other pages place
through hooks: Settings > Captcha solver (system.py), Webhooks > Alert channel > Captcha solved message (notify.py),
and the Accounts > More solver / backup actions (accounts.py).

Ports setup_bloxgen_tab and setup_auto_replace_tab. The logic stays in gui.py; widgets keep the old attribute names.
"""
from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit, QSpinBox, QTextEdit, QVBoxLayout, QWidget

import ui_kit as K
from bloxgen import ACCOUNT_TYPES, PLANS

SOLS_RNG_UNIVERSE = "5361032378"
PROVIDERS = ["YesCaptcha", "OMOCaptcha"]  # captcha.RobloxCaptchaSolver names
FIELD_W = 380


def _spin(lo: int, hi: int, value: int, width: int = 120) -> QSpinBox:
    s = QSpinBox()
    s.setRange(lo, hi)
    s.setValue(value)
    s.setFixedWidth(width)
    return s


def _region_combo(saved: str) -> QComboBox:
    c = QComboBox()
    c.setEditable(True)
    c.addItem("Any")
    saved = str(saved or "").strip()
    if saved and saved.lower() != "any":
        c.addItem(saved)
        c.setCurrentText(saved)
    c.setFixedWidth(160)
    return c


def _type_checks(saved: list) -> tuple:
    """One QCheckBox per BloxGen account type (the logic rewrites their text with the stock state)."""
    box = QWidget()
    box.setObjectName("clear")
    lay = QVBoxLayout(box)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(4)
    checks = {}
    for t in ACCOUNT_TYPES:
        cb = QCheckBox(t)
        cb.setChecked(t in saved)
        lay.addWidget(cb)
        checks[t] = cb
    return box, checks


def _log() -> QTextEdit:
    log = QTextEdit()
    log.setReadOnly(True)
    log.setMinimumHeight(220)
    return log


# ── Account generator (old BloxGen tab) ───────────────────────────────────────
def _build_bloxgen(win, shell):
    cfg = win._bloxgen_load_config()
    win._bloxgen_workers = []
    win._bloxgen_query_workers = []
    win._bloxgen_saved_count = 0
    win._bloxgen_done_workers = 0
    win._bloxgen_total = 0
    page = K.Page("bloxgen", "Account generator",
                  "Makes new accounts with BloxGen, gives each its own private server, and adds them here.", shell.search)

    sec = page.section("Generate", "Uses the keys, plan and game from BloxGen account.")
    win.bloxgen_count = _spin(1, 1000, int(cfg.get("count", 5) or 5))
    sec.row("Accounts to make", "Split between your keys", win.bloxgen_count)
    win.bloxgen_disabled = K.Switch(bool(cfg.get("add_disabled", False)))
    sec.row("Add them disabled", "They are added but not launched until you enable them", win.bloxgen_disabled)
    win.bloxgen_globalize = K.Switch(bool(cfg.get("globalize_cookies", False)))
    win.bloxgen_globalize.toggled.connect(lambda _v: win._bloxgen_save_config())
    sec.row("Refresh each cookie first", "Saves a fresh, warning-free cookie for every new account. About 3 more "
            "requests per account", win.bloxgen_globalize)
    win.bloxgen_progress_label = K.label("Idle.", "help")
    win.bloxgen_start_btn = K.button("Start", win._bloxgen_start, kind="ink", size="sm")
    win.bloxgen_stop_btn = K.button("Stop", win._bloxgen_stop, size="sm")
    win.bloxgen_stop_btn.setEnabled(False)
    sec.row("Make accounts", "Each one is generated, gets its private server, then is added to your accounts",
            win.bloxgen_progress_label, win.bloxgen_start_btn, win.bloxgen_stop_btn)
    win.bloxgen_globalize_list_btn = K.button("Choose file", win._bloxgen_globalize_list, size="sm")
    sec.row("Refresh a cookie list", "A .txt file with one cookie per line. The working ones are written to "
            "<file>_globalized.txt", win.bloxgen_globalize_list_btn)
    win.bloxgen_log = _log()
    sec.add(win.bloxgen_log, 1)

    sec = page.section("BloxGen account", "Your keys and plan. Balance and stock results are written to the log on "
                       "Generate.")
    win.bloxgen_api_keys = QTextEdit()
    win.bloxgen_api_keys.setAcceptRichText(False)
    win.bloxgen_api_keys.setPlaceholderText("One BLOX-... key per line")
    win.bloxgen_api_keys.setFixedSize(FIELD_W, 90)
    saved_keys = cfg.get("api_keys")
    if isinstance(saved_keys, list):
        win.bloxgen_api_keys.setPlainText("\n".join(str(k) for k in saved_keys))
    elif cfg.get("api_key"):  # migrate old single-key config
        win.bloxgen_api_keys.setPlainText(str(cfg.get("api_key")))
    sec.row("API keys", "One per line. Several keys make accounts at the same time", win.bloxgen_api_keys)
    win.bloxgen_balance_label = K.label("Balance: unknown", "help")
    win.bloxgen_balance_btn = K.button("Check balance", win._bloxgen_check_balance, size="sm")
    sec.row("Balance", "Also sets the plan from your first key", win.bloxgen_balance_label, win.bloxgen_balance_btn)
    win.bloxgen_plan = QComboBox()
    plans = [p.capitalize() for p in PLANS]
    win.bloxgen_plan.addItems(plans)
    saved_plan = str(cfg.get("plan", "free")).capitalize()
    if saved_plan in plans:
        win.bloxgen_plan.setCurrentText(saved_plan)
    win.bloxgen_plan.setFixedWidth(160)
    win.bloxgen_plan.currentTextChanged.connect(lambda _t: win._bloxgen_apply_plan())
    sec.row("Plan", "Higher plans unlock more account types and picking a region", win.bloxgen_plan)
    saved_types = cfg.get("types")
    if not isinstance(saved_types, list):
        saved_types = [cfg.get("type")] if cfg.get("type") else ["alt"]
    types_box, win.bloxgen_type_checks = _type_checks(saved_types)
    win.bloxgen_stock_btn = K.button("Check stock", win._bloxgen_check_stock, size="sm")
    sec.row("Account types", "Types your plan can't make are locked. Check stock marks which are in stock",
            types_box, win.bloxgen_stock_btn)
    win.bloxgen_region = _region_combo(cfg.get("region", ""))
    win.bloxgen_regions_btn = K.button("Refresh regions", win._bloxgen_refresh_regions, size="sm")
    sec.row("Region", "Ultra plan only. A 2-letter code such as GB or US, or Any", win.bloxgen_region,
            win.bloxgen_regions_btn)
    win.bloxgen_universe = QLineEdit(cfg.get("universe_id", ""))
    win.bloxgen_universe.setPlaceholderText("For example 6432631261")
    win.bloxgen_universe.setFixedWidth(220)
    win.bloxgen_solsrng_btn = K.button("Sol's RNG", lambda: win.bloxgen_universe.setText(SOLS_RNG_UNIVERSE), size="sm")
    sec.row("Game universe id", "Not the place id. Each account gets a free private server here. Empty adds the "
            "cookie only", win.bloxgen_universe, win.bloxgen_solsrng_btn)

    win._bloxgen_stock = {}
    win._bloxgen_apply_plan()

    # Autosave (the old tab saved on Start). Connected after loading so startup writes nothing.
    page.autosave = save = K.Autosave(win._bloxgen_save_config)
    save.watch(win.bloxgen_count, win.bloxgen_disabled, win.bloxgen_plan, *win.bloxgen_type_checks.values())
    win.bloxgen_api_keys.textChanged.connect(save.poke)
    win.bloxgen_region.currentTextChanged.connect(save.poke)
    win.bloxgen_universe.textChanged.connect(save.poke)
    return page


# ── Auto replace (old Auto Replace tab) ───────────────────────────────────────
def _build_autoreplace(win, shell):
    cfg = win._autorep_load_config()
    win._auto_replace_workers = []
    win._auto_replaced_uids = set()
    win._auto_replace_count = 0
    win._bloxgen_backup_worker = None
    page = K.Page("autoreplace", "Auto replace",
                  "When an account is flagged bad or hits a captcha the solver can't pass, a backup account takes its "
                  "place, or BloxGen makes a new one.", shell.search)
    save = win._autorep_save_config

    sec = page.section("Auto replace", "Uses the keys, plan and game from Account generator.")
    win.autorep_enabled = K.Switch(bool(cfg.get("enabled", False)))
    win.autorep_enabled.toggled.connect(lambda _v: save())
    win.autorep_enabled.toggled.connect(win._autorep_toggled_live)
    sec.row("Auto replace", "While the manager runs, keeps the number of working accounts it started with: backups "
            "are promoted when it drops, then the backups are refilled", win.autorep_enabled)
    # One toggle: "Auto Replace" IS the hold-the-count logic; the alias keeps the old config / start refs working.
    win.autorep_maintain_count = win.autorep_enabled
    for attr, key, default, text, help_text in (
            ("autorep_trigger_bad", "trigger_bad", True, "Replace bad cookies", ""),
            ("autorep_trigger_cap", "trigger_cap", True, "Replace captcha locks", "After the solver gave up"),
            ("autorep_skip_generated", "skip_generated", True, "Skip accounts BloxGen made",
             "Stops a replacement from being replaced again and again"),
            ("autorep_disable_old", "disable_old", False, "Disable the flagged account", "Once it is replaced")):
        sw = K.Switch(bool(cfg.get(key, default)))
        sw.toggled.connect(lambda _v: save())
        setattr(win, attr, sw)
        sec.row(text, help_text, sw)
    win.autorep_max = _spin(0, 9999, int(cfg.get("max_replacements", 0) or 0))
    win.autorep_max.valueChanged.connect(lambda _v: save())
    sec.row("Most replacements per session", "0 means no limit. Starts over when M.J.JARAM restarts", win.autorep_max)

    sec = page.section("New accounts", "What BloxGen makes when no backup is left.")
    win.autorep_region = _region_combo(cfg.get("region", ""))
    win.autorep_region.currentTextChanged.connect(lambda _t: save())
    sec.row("Region", "Ultra plan only. A 2-letter code such as GB or US, or Any", win.autorep_region)
    saved_types = cfg.get("types")
    types_box, win.autorep_type_checks = _type_checks(saved_types if isinstance(saved_types, list) else ["alt"])
    for cb in win.autorep_type_checks.values():
        cb.toggled.connect(lambda _v: save())
    sec.row("Account types", "Only types your plan on Account generator can make are used", types_box)

    sec = page.section("Backup accounts", "Spare accounts kept ready and not launched.")
    win.autorep_backup_target = _spin(0, 999, int(cfg.get("backup_target", 0) or 0))
    win.autorep_backup_target.valueChanged.connect(
        lambda _v: (save(), win._bloxgen_update_backup_label(), win._bloxgen_backup_refill()))
    sec.row("Backups to keep", "A replacement uses a backup first (instant, works even when BloxGen is down), then "
            "the pool is refilled in the background. 0 turns this off. Mark any account as a backup from "
            "Accounts > More", win.autorep_backup_target)
    win.autorep_backup_label = K.label("Backup stored: 0 / 0", "help")
    win.autorep_backup_refill_btn = K.button("Refill now", win._bloxgen_backup_refill, size="sm")
    sec.row("Stored", "", win.autorep_backup_label, win.autorep_backup_refill_btn)

    sec = page.section("Activity", "What Auto replace did this session.")
    win.autorep_log = _log()
    sec.add(win.autorep_log, 1)

    # Keep the backup pool topped up in the background, and recover when BloxGen comes back after an outage.
    win._bloxgen_update_backup_label()
    win._bloxgen_backup_timer = QTimer(win)
    win._bloxgen_backup_timer.setInterval(120000)  # 2 min
    win._bloxgen_backup_timer.timeout.connect(win._bloxgen_backup_refill)
    win._bloxgen_backup_timer.start()
    win._bloxgen_backup_refill()

    # Sidebar state (On / Off), like the other automations.
    ov = getattr(win, "_overview_page", None)
    if ov is not None and isinstance(getattr(ov, "auto_specs", None), dict):
        ov.auto_specs["autoreplace"] = (lambda: win.autorep_enabled, lambda sw: sw.isChecked(), None)
    return page


# ── hooks other pages call ────────────────────────────────────────────────────
def captcha_solver_section(win, page) -> None:
    """Settings > Captcha solver (old Settings > Captcha Solver API). Saved by save_settings -> captcha_solver."""
    sec = page.section("Captcha solver", "Gets accounts past Roblox captchas. Without a key, a captcha just locks "
                       "the account. Turn it off for single accounts from Accounts > More.")
    win.settings_solver_provider = QComboBox()
    win.settings_solver_provider.addItems(PROVIDERS)
    win.settings_solver_provider.setFixedWidth(200)
    sec.row("Solver service", "The service your API key is from", win.settings_solver_provider)
    win.settings_solver_api_key = QLineEdit()
    win.settings_solver_api_key.setEchoMode(QLineEdit.EchoMode.Password)
    win.settings_solver_api_key.setPlaceholderText("YesCaptcha or OMOCaptcha key")
    win.settings_solver_api_key.setFixedWidth(FIELD_W)
    sec.row("API key", "Pays for each solved captcha", win.settings_solver_api_key)
    win.shell.autosave_settings.watch(win.settings_solver_provider, win.settings_solver_api_key)


def alert_channel_rows(win, sec) -> None:
    """Webhooks > Alert channel: old Alerts > CAP Solved Ping (alerts.cap_solved_message)."""
    win.cap_solved_msg_input = QLineEdit()
    win.cap_solved_msg_input.setPlaceholderText("Blank sends nothing")
    win.cap_solved_msg_input.setFixedWidth(FIELD_W)
    sec.row("Captcha solved message", "Sent when the solver gets an account past a captcha. Use {username}, {uid} "
            "and {discord_ping}.", win.cap_solved_msg_input)
    win.shell.autosave_settings.watch(win.cap_solved_msg_input)


def account_menu_actions(win, menu) -> None:
    """Accounts > More: old Solver On / Solver Off / Mark Backup / Mark Normal (selected accounts, or the one shown)."""
    menu.addSeparator()
    menu.addAction("Captcha solver on", lambda: win.set_solver_for_selected(win.accounts_list, True))
    menu.addAction("Captcha solver off", lambda: win.set_solver_for_selected(win.accounts_list, False))
    menu.addAction("Mark as backup", lambda: win.set_backup_for_selected(win.accounts_list, True))
    menu.addAction("Mark as normal", lambda: win.set_backup_for_selected(win.accounts_list, False))


def build(win, shell):
    return [("Account generator", _build_bloxgen(win, shell)), ("Auto replace", _build_autoreplace(win, shell))]
