"""Roblox Settings Editor dialog (upstream 2x94, moved out of gui.py as is).

Edits %LOCALAPPDATA%/Roblox/GlobalBasicSettings_13.xml and keeps named profiles next to the config.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from roblox_global_settings import (
    GlobalBasicSettingsDocument,
    GlobalBasicSettingsError,
    InvalidSettingValue,
    ProfileError,
    RobloxSettingsProfileStore,
    get_global_basic_settings_path,
    get_profiles_directory,
    is_file_read_only,
    set_file_read_only,
)


class ModernStyle:  # ponytail: the gui.ModernStyle colours this dialog reads (gui may run as __main__)
    BACKGROUND = "#161D25"
    SURFACE = "#1C252F"
    PRIMARY = "#4B5866"
    PRIMARY_VARIANT = "#5A6878"
    ERROR = "#FF7B72"
    TEXT_PRIMARY = "#E6EBF0"
    TEXT_SECONDARY = "#A9B4BF"
    BORDER = "#2C3742"


class _RobloxGlobalSettingsDialog(QDialog):
    """Typed editor and local profile manager for Roblox global settings."""

    _SETTINGS_SECTION_ORDER = (
        "Audio",
        "Chat & Language",
        "Display & Graphics",
        "View & Controls",
        "Accessibility",
        "VR",
        "Advanced & Internal",
    )

    # Roblox does not publish a schema for this file. Only include settings
    # whose behavior is clear enough to describe without guessing. Exact-name
    # matching also keeps unfamiliar future settings from receiving a tooltip.
    _SETTING_DESCRIPTIONS = {
        "AllTutorialsDisabled": "Records whether Roblox's in-app tutorials are disabled.",
        "CameraYInverted": "Inverts vertical camera movement when enabled.",
        "ChatTranslationEnabled": "Enables automatic translation of chat messages.",
        "ChatTranslationLocale": "Sets the language locale used for chat translation.",
        "ChatTranslationToggleEnabled": "Controls whether the in-experience chat translation toggle is enabled.",
        "ChatVisible": "Controls whether the in-experience chat window is visible.",
        "ComputerCameraMovementMode": "Selects how the camera is moved with desktop controls.",
        "ComputerMovementMode": "Selects the character movement mode used with desktop controls.",
        "FramerateCap": "Sets the maximum rendered frame rate.",
        "Fullscreen": "Starts Roblox in fullscreen mode when enabled.",
        "GamepadCameraSensitivity": "Adjusts camera sensitivity for a gamepad.",
        "GraphicsQualityLevel": "Sets the current graphics quality level.",
        "HapticStrength": "Adjusts controller vibration and haptic-feedback strength.",
        "MasterVolume": "Controls the overall Roblox player volume.",
        "MasterVolumeStudio": "Controls the overall volume in Roblox Studio.",
        "MaxQualityEnabled": "Allows Roblox to use its maximum graphics quality when enabled.",
        "MicroProfilerWebServerEnabled": "Allows Roblox's MicroProfiler to expose its local web interface.",
        "MouseSensitivity": "Adjusts general mouse camera sensitivity.",
        "MouseSensitivityFirstPerson": "Adjusts horizontal and vertical mouse sensitivity in first person.",
        "MouseSensitivityThirdPerson": "Adjusts horizontal and vertical mouse sensitivity in third person.",
        "OnScreenProfilerEnabled": "Shows Roblox's on-screen performance profiler when enabled.",
        "PartyVoiceVolume": "Controls the volume of party voice chat.",
        "PerformanceStatsVisible": "Shows the in-experience performance statistics overlay.",
        "PlayerListVisible": "Controls whether the in-experience player list is visible.",
        "PlayerNamesEnabled": "Controls whether player names are displayed in experiences.",
        "PreferredTextSize": "Selects the preferred Roblox interface text size.",
        "PreferredTransparency": "Adjusts the preferred transparency of supported interface elements.",
        "ReadAloud": "Enables supported screen-reader and read-aloud behavior.",
        "ReducedMotion": "Reduces supported interface animations and motion effects.",
        "StartMaximized": "Starts the Roblox window maximized when enabled.",
        "StartScreenPosition": "Stores the last Roblox window position as X and Y screen coordinates.",
        "StartScreenSize": "Stores the last Roblox window width and height.",
        "TouchCameraMovementMode": "Selects how the camera is moved with touch controls.",
        "TouchMovementMode": "Selects the character movement mode used with touch controls.",
        "UiNavigationKeyBindEnabled": "Enables Roblox's keyboard binding for interface navigation.",
        "VREnabled": "Enables Roblox virtual-reality mode.",
        "VRRotationIntensity": "Adjusts rotation intensity in VR.",
        "VRSmoothRotationEnabled": "Enables smooth rotation in VR instead of stepped rotation.",
        "VRThirdPersonFollowCamEnabled": "Enables the third-person follow camera in VR.",
        "VignetteEnabled": "Shows a comfort vignette during supported camera movement.",
        "VoiceChatVolume": "Controls the volume of in-experience voice chat.",
    }

    def __init__(self, config_dir: Path, parent=None):
        super().__init__(parent)
        self._target_path = get_global_basic_settings_path()
        self._profile_store = RobloxSettingsProfileStore(get_profiles_directory(config_dir))
        self._document: Optional[GlobalBasicSettingsDocument] = None
        self._bindings: List[dict] = []
        self._section_widgets: Dict[str, QGroupBox] = {}
        self._section_grids: Dict[str, QGridLayout] = {}
        self._dirty = False
        self._populating = False
        self._loaded_signature: Optional[tuple] = None

        self.setWindowTitle("Roblox Settings Editor")
        self.setModal(False)
        self.setMinimumSize(880, 620)
        target_height = 865
        try:
            screen = parent.screen() if parent is not None else QApplication.primaryScreen()
            if screen is not None:
                # Leave room for the title bar, taskbar, and window borders.
                target_height = min(target_height, max(620, screen.availableGeometry().height() - 48))
        except Exception:
            pass
        self.resize(1040, target_height)

        root = QVBoxLayout(self)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        title = QLabel("Roblox Settings Editor")
        title_font = QFont()
        title_font.setPointSize(17)
        title_font.setBold(True)
        title.setFont(title_font)
        root.addWidget(title)

        description = QLabel(
            "Edit every typed property in GlobalBasicSettings_13.xml. Changes are kept "
            "in this window until Apply / Save is clicked."
        )
        description.setWordWrap(True)
        description.setStyleSheet(f"color:{ModernStyle.TEXT_SECONDARY};")
        root.addWidget(description)

        self._read_only_checkbox = QCheckBox("Set file to Read-Only")
        self._read_only_checkbox.setToolTip(
            f"Controls the Windows Read-only attribute for:\n{self._target_path}\n\n"
            "JARAM temporarily clears it while saving and restores it immediately afterward."
        )
        self._read_only_checkbox.toggled.connect(self._on_read_only_toggled)

        profile_group = QGroupBox("Profiles")
        profile_layout = QHBoxLayout(profile_group)
        self._profile_combo = QComboBox()
        self._profile_combo.setMinimumWidth(190)
        self._profile_combo.currentIndexChanged.connect(self._update_profile_buttons)
        profile_layout.addWidget(self._profile_combo, 1)
        self._profile_save_as_button = QPushButton("Save As…")
        self._profile_update_button = QPushButton("Update")
        self._profile_apply_button = QPushButton("Apply Profile")
        self._profile_rename_button = QPushButton("Rename…")
        self._profile_delete_button = QPushButton("Delete")
        self._profile_save_as_button.clicked.connect(self._save_profile_as)
        self._profile_update_button.clicked.connect(self._update_profile)
        self._profile_apply_button.clicked.connect(self._apply_profile)
        self._profile_rename_button.clicked.connect(self._rename_profile)
        self._profile_delete_button.clicked.connect(self._delete_profile)
        for button in (
            self._profile_save_as_button,
            self._profile_update_button,
            self._profile_apply_button,
            self._profile_rename_button,
            self._profile_delete_button,
        ):
            profile_layout.addWidget(button)
        root.addWidget(profile_group)

        search_row = QHBoxLayout()
        search_row.addWidget(QLabel("Search:"))
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("Filter by setting name, XML type, or value")
        self._search_edit.setClearButtonEnabled(True)
        self._search_edit.textChanged.connect(self._apply_filter)
        search_row.addWidget(self._search_edit, 1)
        self._count_label = QLabel()
        self._count_label.setStyleSheet(f"color:{ModernStyle.TEXT_SECONDARY};")
        search_row.addWidget(self._count_label)
        root.addLayout(search_row)

        self._settings_scroll = QScrollArea()
        self._settings_scroll.setWidgetResizable(True)
        self._settings_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._settings_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._settings_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self._settings_cards_widget = QWidget()
        self._settings_cards_widget.setStyleSheet("background: transparent;")
        self._settings_cards_layout = QVBoxLayout(self._settings_cards_widget)
        self._settings_cards_layout.setContentsMargins(0, 0, 4, 0)
        self._settings_cards_layout.setSpacing(8)
        self._settings_cards_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._settings_scroll.setWidget(self._settings_cards_widget)
        root.addWidget(self._settings_scroll, 1)

        self._operation_label = QLabel()
        self._operation_label.setWordWrap(True)
        self._operation_label.setStyleSheet(f"color:{ModernStyle.TEXT_SECONDARY};")
        root.addWidget(self._operation_label)

        action_row = QHBoxLayout()
        self._refresh_button = QPushButton("Refresh")
        self._refresh_button.clicked.connect(lambda: self._reload_from_disk(prompt=True))
        action_row.addWidget(self._refresh_button)
        action_row.addWidget(self._read_only_checkbox)
        action_row.addStretch(1)
        self._apply_button = QPushButton("Apply / Save")
        self._apply_button.setProperty("class", "success")
        self._apply_button.clicked.connect(self._apply_to_file)
        action_row.addWidget(self._apply_button)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.close)
        action_row.addWidget(close_button)
        root.addLayout(action_row)

        self._refresh_profiles()
        self._reload_from_disk(prompt=False)

    @staticmethod
    def _signature(path: Path) -> Optional[tuple]:
        try:
            info = path.stat()
            return int(info.st_mtime_ns), int(info.st_size)
        except OSError:
            return None

    def _set_operation(self, text: str, *, error: bool = False) -> None:
        self._operation_label.setText(text)
        color = ModernStyle.ERROR if error else ModernStyle.TEXT_SECONDARY
        self._operation_label.setStyleSheet(f"color:{color};")

    def _set_dirty(self, dirty: bool = True) -> None:
        self._dirty = bool(dirty)
        suffix = " *" if self._dirty else ""
        self.setWindowTitle(f"Roblox Settings Editor{suffix}")

    def _confirm_discard(self, message: str) -> bool:
        if not self._dirty:
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved Changes",
            message,
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard

    def _sync_file_state(self) -> None:
        exists = self._target_path.is_file()
        read_only = False
        if exists:
            try:
                read_only = is_file_read_only(self._target_path)
            except Exception as exc:
                self._set_operation(f"Could not read the file attributes: {exc}", error=True)
        self._read_only_checkbox.blockSignals(True)
        self._read_only_checkbox.setChecked(read_only)
        self._read_only_checkbox.setEnabled(exists)
        self._read_only_checkbox.blockSignals(False)
        attribute_state = "Read-only" if read_only else "Writable"
        existence_state = attribute_state if exists else "File not found"
        self._read_only_checkbox.setToolTip(
            f"{self._target_path}\nStatus: {existence_state}\n\n"
            "JARAM temporarily clears the Read-only attribute while saving and restores it immediately afterward."
        )

    @classmethod
    def _section_for_setting(cls, name: str) -> str:
        key = str(name or "").casefold()
        if key.startswith("vr") or key == "haseverusedvr":
            return "VR"
        if key in {
            "preferredtextsize",
            "preferredtransparency",
            "readaloud",
            "reducedmotion",
        }:
            return "Accessibility"
        if any(
            token in key
            for token in (
                "mastervolume",
                "voicechatvolume",
                "partyvoicevolume",
            )
        ):
            return "Audio"
        if key.startswith("chattranslation") or key == "chatvisible":
            return "Chat & Language"
        if any(
            token in key
            for token in (
                "graphics",
                "quality",
                "fullscreen",
                "frameratecap",
                "performancestats",
                "onscreenprofiler",
                "startmaximized",
                "startscreen",
                "vignette",
                "badgevisible",
                "peoplepagelayout",
                "playerlistvisible",
                "playernamesenabled",
            )
        ):
            return "Display & Graphics"
        if any(
            token in key
            for token in (
                "camera",
                "movement",
                "controlmode",
                "sensitivity",
                "hapticstrength",
                "uinavigation",
                "playerheight",
                "usedcoregui",
                "usedcustomgui",
                "usedhidehud",
            )
        ):
            return "View & Controls"
        return "Advanced & Internal"

    def _create_setting_section(self, name: str) -> None:
        group = QGroupBox(name.replace("&", "&&"), self._settings_cards_widget)
        group.setObjectName("globalSettingsSection")
        group.setStyleSheet(
            f"""
            QGroupBox#globalSettingsSection {{
                background-color: transparent;
                border: 1px solid {ModernStyle.BORDER};
                border-radius: 10px;
                margin-top: 13px;
                padding-top: 7px;
                font-size: 14px;
                font-weight: 600;
            }}
            QGroupBox#globalSettingsSection::title {{
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 12px;
                padding: 0 7px;
                color: {ModernStyle.TEXT_PRIMARY};
                background-color: {ModernStyle.BACKGROUND};
            }}
            """
        )
        grid = QGridLayout(group)
        grid.setContentsMargins(10, 16, 10, 10)
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        grid.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._section_widgets[name] = group
        self._section_grids[name] = grid
        self._settings_cards_layout.addWidget(group)

    def _clear_setting_cards(self) -> None:
        for binding in self._bindings:
            card = binding.get("card")
            if card is not None:
                card.hide()
                card.setParent(None)
                card.deleteLater()
        self._bindings.clear()
        while self._settings_cards_layout.count():
            item = self._settings_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        self._section_widgets.clear()
        self._section_grids.clear()
        self._count_label.setText("0 settings")

    def _reload_from_disk(self, *, prompt: bool) -> bool:
        if prompt and not self._confirm_discard(
            "Refresh from disk and discard the changes currently in the editor?"
        ):
            return False

        self._populating = True
        try:
            if not self._target_path.is_file():
                self._document = None
                self._loaded_signature = None
                self._clear_setting_cards()
                self._apply_button.setEnabled(False)
                self._profile_save_as_button.setEnabled(False)
                self._set_dirty(False)
                self._set_operation("Roblox has not created GlobalBasicSettings_13.xml yet.")
                return False
            try:
                self._document = GlobalBasicSettingsDocument.load(self._target_path)
            except GlobalBasicSettingsError as exc:
                self._document = None
                self._loaded_signature = self._signature(self._target_path)
                self._clear_setting_cards()
                self._apply_button.setEnabled(False)
                self._profile_save_as_button.setEnabled(False)
                self._set_dirty(False)
                self._set_operation(str(exc), error=True)
                return False
            self._loaded_signature = self._signature(self._target_path)
            self._populate_setting_cards()
            self._apply_button.setEnabled(True)
            self._profile_save_as_button.setEnabled(True)
            self._set_dirty(False)
            self._set_operation("Settings loaded from Roblox.")
            return True
        finally:
            self._populating = False
            self._sync_file_state()
            self._update_profile_buttons()

    def _connect_editor(self, editor: QWidget) -> None:
        if isinstance(editor, QLineEdit):
            editor.textChanged.connect(self._on_editor_changed)
        elif isinstance(editor, QComboBox):
            editor.currentIndexChanged.connect(self._on_editor_changed)
        elif isinstance(editor, QCheckBox):
            editor.toggled.connect(
                lambda checked, control=editor: self._on_boolean_editor_toggled(control, checked)
            )

    def _on_boolean_editor_toggled(self, editor: QCheckBox, checked: bool) -> None:
        editor.setText("On" if checked else "Off")
        self._on_editor_changed()

    def _style_setting_card(self, card: QFrame, *, error: bool = False) -> None:
        border = ModernStyle.ERROR if error else ModernStyle.BORDER
        width = 2 if error else 1
        card.setStyleSheet(
            f"""
            QFrame#globalSettingsCard {{
                background-color: {ModernStyle.SURFACE};
                border: {width}px solid {border};
                border-radius: 9px;
            }}
            QLabel#globalSettingName {{
                background: transparent;
                border: none;
                border-radius: 6px;
                color: {ModernStyle.TEXT_PRIMARY};
                font-size: 13px;
                font-weight: 600;
                padding: 3px 6px;
            }}
            QLabel#globalSettingName:hover {{
                background: transparent;
                color: {ModernStyle.PRIMARY};
            }}
            QLabel#globalSettingType {{
                background-color: {ModernStyle.PRIMARY_VARIANT};
                border: none;
                border-radius: 7px;
                color: {ModernStyle.TEXT_PRIMARY};
                font-size: 10px;
                font-weight: 600;
                padding: 3px 7px;
            }}
            QLabel#globalSettingComponent {{
                background: transparent;
                border: none;
                color: {ModernStyle.TEXT_SECONDARY};
                font-size: 11px;
            }}
            """
        )

    def _build_setting_card(self, prop, values: tuple[str, ...]) -> dict:
        # Parent immediately so a card can never become a transient top-level
        # window while the grid is being populated or filtered.
        card = QFrame(self._settings_cards_widget)
        card.setObjectName("globalSettingsCard")
        card.setMinimumHeight(106)
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._style_setting_card(card)

        card_layout = QVBoxLayout(card)
        card_layout.setContentsMargins(13, 11, 13, 12)
        card_layout.setSpacing(9)

        header = QHBoxLayout()
        header.setSpacing(8)
        name_label = QLabel(prop.name)
        name_label.setObjectName("globalSettingName")
        name_label.setWordWrap(True)
        name_label.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        name_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        description = self._SETTING_DESCRIPTIONS.get(prop.name)
        if description:
            name_label.setToolTip(f"{prop.name}\n\n{description}")
            name_label.setToolTipDuration(12000)
            name_label.setCursor(Qt.CursorShape.WhatsThisCursor)
        header.addWidget(
            name_label,
            0,
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
        )
        header.addStretch(1)
        type_label = QLabel(prop.type_name)
        type_label.setObjectName("globalSettingType")
        type_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        type_label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        header.addWidget(type_label, 0, Qt.AlignmentFlag.AlignTop)
        card_layout.addLayout(header)

        binding: dict = {
            "index": prop.index,
            "section": self._section_for_setting(prop.name),
            "card": card,
            "editors": [],
        }
        if prop.is_boolean:
            editor = QCheckBox()
            editor.setChecked(values[0].casefold() == "true")
            editor.setText("On" if editor.isChecked() else "Off")
            editor.setToolTip("Toggle this Roblox boolean setting")
            card_layout.addWidget(editor)
            binding["editors"].append(editor)
            self._connect_editor(editor)
        elif len(values) == 1:
            editor = QLineEdit(values[0])
            editor.setPlaceholderText("Empty value")
            editor.setToolTip(f"{prop.type_name} value; validated when saved")
            card_layout.addWidget(editor)
            binding["editors"].append(editor)
            self._connect_editor(editor)
        else:
            components = QHBoxLayout()
            components.setSpacing(9)
            for label, value in zip(prop.component_names, values):
                component_widget = QWidget(card)
                component_widget.setStyleSheet("background: transparent;")
                component_layout = QVBoxLayout(component_widget)
                component_layout.setContentsMargins(0, 0, 0, 0)
                component_layout.setSpacing(3)
                component_label = QLabel(label)
                component_label.setObjectName("globalSettingComponent")
                component_layout.addWidget(component_label)
                editor = QLineEdit(value)
                editor.setMinimumWidth(80)
                component_layout.addWidget(editor)
                components.addWidget(component_widget, 1)
                binding["editors"].append(editor)
                self._connect_editor(editor)
            card_layout.addLayout(components)
        return binding

    def _populate_setting_cards(self) -> None:
        self._clear_setting_cards()
        document = self._document
        if document is None:
            return
        for prop in document.properties:
            self._bindings.append(self._build_setting_card(prop, prop.value_strings()))
        used_sections = {str(binding["section"]) for binding in self._bindings}
        for section in self._SETTINGS_SECTION_ORDER:
            if section in used_sections:
                self._create_setting_section(section)
        self._apply_filter()

    def _reflow_setting_cards(self, visible_bindings: List[dict]) -> None:
        for binding in self._bindings:
            binding["card"].hide()
        by_section: Dict[str, List[dict]] = {
            section: [] for section in self._SETTINGS_SECTION_ORDER
        }
        for binding in visible_bindings:
            by_section.setdefault(str(binding["section"]), []).append(binding)

        for section in self._SETTINGS_SECTION_ORDER:
            group = self._section_widgets.get(section)
            grid = self._section_grids.get(section)
            if group is None or grid is None:
                continue
            while grid.count():
                grid.takeAt(0)
            section_bindings = by_section.get(section, [])
            group.setVisible(bool(section_bindings))
            if not section_bindings:
                continue
            display_section = section.replace("&", "&&")
            group.setTitle(f"{display_section} ({len(section_bindings)})")
            for position, binding in enumerate(section_bindings):
                grid.addWidget(binding["card"], position // 2, position % 2)
                binding["card"].show()

    def _binding_values(self, binding: dict) -> tuple[str, ...]:
        values: list[str] = []
        for editor in binding.get("editors", []):
            if isinstance(editor, QCheckBox):
                values.append("true" if editor.isChecked() else "false")
            elif isinstance(editor, QComboBox):
                values.append(str(editor.currentData() or editor.currentText()))
            elif isinstance(editor, QLineEdit):
                values.append(editor.text())
        return tuple(values)

    def _on_editor_changed(self, *args) -> None:
        if self._populating:
            return
        self._set_dirty(True)

    def _apply_filter(self, *args) -> None:
        needle = self._search_edit.text().strip().casefold()
        shown = 0
        visible_bindings: List[dict] = []
        document = self._document
        if document is None:
            self._count_label.setText("0 settings")
            return
        for binding in self._bindings:
            prop = document.properties[int(binding["index"])]
            values = " ".join(self._binding_values(binding))
            haystack = f"{prop.name} {prop.type_name} {values}".casefold()
            visible = not needle or needle in haystack
            if visible:
                visible_bindings.append(binding)
                shown += 1
        self._reflow_setting_cards(visible_bindings)
        total = len(self._bindings)
        self._count_label.setText(f"{shown} of {total}" if needle else f"{total} settings")

    def _document_from_editor(self) -> Optional[GlobalBasicSettingsDocument]:
        if self._document is None:
            return None
        candidate = self._document.clone()
        updates = [(binding["index"], self._binding_values(binding)) for binding in self._bindings]
        for binding in self._bindings:
            self._style_setting_card(binding["card"])
        try:
            candidate.apply_updates(updates)
        except InvalidSettingValue as exc:
            message = str(exc)
            failed_binding = next(
                (
                    binding
                    for binding in self._bindings
                    if document_property_name(candidate, int(binding["index"])) in message
                ),
                None,
            )
            if failed_binding is not None:
                self._search_edit.clear()
                self._style_setting_card(failed_binding["card"], error=True)
                self._settings_scroll.ensureWidgetVisible(failed_binding["card"], 0, 70)
            QMessageBox.warning(self, "Invalid Setting", message)
            return None
        return candidate

    def _apply_to_file(self) -> bool:
        candidate = self._document_from_editor()
        if candidate is None:
            return False
        current_signature = self._signature(self._target_path)
        if self._loaded_signature is not None and current_signature != self._loaded_signature:
            answer = QMessageBox.question(
                self,
                "File Changed",
                "GlobalBasicSettings_13.xml changed outside this window. Overwrite it with "
                "the values currently shown?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            read_only_after = is_file_read_only(self._target_path) if self._target_path.exists() else False
            candidate.save(self._target_path, read_only_after=read_only_after)
            self._document = GlobalBasicSettingsDocument.load(self._target_path)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", f"Could not save the Roblox settings:\n{exc}")
            self._set_operation(f"Save failed: {exc}", error=True)
            self._sync_file_state()
            return False
        self._loaded_signature = self._signature(self._target_path)
        self._populating = True
        try:
            self._populate_setting_cards()
        finally:
            self._populating = False
        self._set_dirty(False)
        self._set_operation("Settings applied successfully.")
        self._sync_file_state()
        return True

    def _on_read_only_toggled(self, checked: bool) -> None:
        if self._populating:
            return
        if not self._target_path.exists():
            self._sync_file_state()
            return
        try:
            set_file_read_only(self._target_path, bool(checked))
        except Exception as exc:
            QMessageBox.critical(self, "Attribute Change Failed", str(exc))
            self._set_operation(f"Could not change the Read-only attribute: {exc}", error=True)
        else:
            self._set_operation(
                "The Roblox settings file is now Read-only."
                if checked
                else "The Roblox settings file is now writable."
            )
        self._sync_file_state()

    def _selected_profile(self) -> str:
        return str(self._profile_combo.currentData() or self._profile_combo.currentText()).strip()

    def _refresh_profiles(self, select_name: str = "") -> None:
        names = self._profile_store.list_profiles()
        current = select_name or self._selected_profile()
        self._profile_combo.blockSignals(True)
        self._profile_combo.clear()
        for name in names:
            self._profile_combo.addItem(name, name)
        if current:
            index = self._profile_combo.findData(current)
            if index < 0:
                index = self._profile_combo.findText(current)
            if index >= 0:
                self._profile_combo.setCurrentIndex(index)
        self._profile_combo.blockSignals(False)
        self._update_profile_buttons()

    def _update_profile_buttons(self, *args) -> None:
        selected = bool(self._selected_profile())
        for button in (
            self._profile_update_button,
            self._profile_apply_button,
            self._profile_rename_button,
            self._profile_delete_button,
        ):
            button.setEnabled(selected)
        self._profile_save_as_button.setEnabled(self._document is not None)

    def _save_profile_as(self) -> None:
        document = self._document_from_editor()
        if document is None:
            return
        name, accepted = QInputDialog.getText(self, "Save Profile", "Profile name:")
        if not accepted:
            return
        try:
            saved_name = self._profile_store.save(name, document)
        except ProfileError as exc:
            QMessageBox.warning(self, "Profile Not Saved", str(exc))
            return
        self._refresh_profiles(saved_name)
        self._set_operation(f'Profile "{saved_name}" saved.')

    def _update_profile(self) -> None:
        name = self._selected_profile()
        document = self._document_from_editor()
        if not name or document is None:
            return
        answer = QMessageBox.question(
            self,
            "Update Profile",
            f'Replace profile "{name}" with the values currently shown?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            saved_name = self._profile_store.save(name, document, overwrite=True)
        except ProfileError as exc:
            QMessageBox.warning(self, "Profile Not Updated", str(exc))
            return
        self._refresh_profiles(saved_name)
        self._set_operation(f'Profile "{saved_name}" updated.')

    def _apply_profile(self) -> None:
        name = self._selected_profile()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Apply Profile",
            f'Replace GlobalBasicSettings_13.xml with profile "{name}"?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            document = self._profile_store.load(name)
            read_only_after = is_file_read_only(self._target_path) if self._target_path.exists() else False
            document.save(self._target_path, read_only_after=read_only_after)
        except (ProfileError, GlobalBasicSettingsError, OSError) as exc:
            QMessageBox.critical(self, "Profile Apply Failed", str(exc))
            self._set_operation(f"Could not apply profile: {exc}", error=True)
            return
        self._dirty = False
        self._reload_from_disk(prompt=False)
        self._set_operation(f'Profile "{name}" applied.')

    def _rename_profile(self) -> None:
        old_name = self._selected_profile()
        if not old_name:
            return
        new_name, accepted = QInputDialog.getText(
            self, "Rename Profile", "New profile name:", text=old_name
        )
        if not accepted:
            return
        try:
            saved_name = self._profile_store.rename(old_name, new_name)
        except ProfileError as exc:
            QMessageBox.warning(self, "Profile Not Renamed", str(exc))
            return
        self._refresh_profiles(saved_name)
        self._set_operation(f'Profile renamed to "{saved_name}".')

    def _delete_profile(self) -> None:
        name = self._selected_profile()
        if not name:
            return
        answer = QMessageBox.question(
            self,
            "Delete Profile",
            f'Delete profile "{name}"? This cannot be undone.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            self._profile_store.delete(name)
        except ProfileError as exc:
            QMessageBox.warning(self, "Profile Not Deleted", str(exc))
            return
        self._refresh_profiles()
        self._set_operation(f'Profile "{name}" deleted.')

    def activate_from_menu(self) -> None:
        current_signature = self._signature(self._target_path)
        if not self._dirty and current_signature != self._loaded_signature:
            self._reload_from_disk(prompt=False)
        elif self._dirty and current_signature != self._loaded_signature:
            self._set_operation(
                "The file changed on disk while this editor has unsaved changes. Refresh to "
                "load the external version, or Apply / Save to overwrite it.",
                error=True,
            )
        self._sync_file_state()
        self._refresh_profiles()
        self.show()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:
        if not self._dirty:
            event.accept()
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Unsaved Changes")
        box.setText("Save changes to GlobalBasicSettings_13.xml before closing?")
        save_button = box.addButton("Save", QMessageBox.ButtonRole.AcceptRole)
        discard_button = box.addButton("Discard", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(save_button)
        box.exec()
        clicked = box.clickedButton()
        if clicked == save_button:
            if self._apply_to_file():
                event.accept()
            else:
                event.ignore()
        elif clicked == discard_button:
            self._dirty = False
            self._reload_from_disk(prompt=False)
            event.accept()
        else:
            event.ignore()


def document_property_name(document: GlobalBasicSettingsDocument, index: int) -> str:
    """Small GUI helper used to focus the card named by a validation error."""
    try:
        return document.properties[index].name
    except Exception:
        return ""
