"""
ui/settings_window.py
=====================
Settings window (Project_Plan.md section 7).

Layout: ui/settings_window.ui (Qt Designer). Logic: core/settings_editor.py.
This class only connects the two:
    widget input  -> SettingsEditor method -> refresh affected widgets
    Scan / Save   -> DaqService (asynchronous, results through DaqBridge)

Behavior:
    * Opens with a working copy; nothing is written until Save.
    * Scans automatically on open when a driver is available.
    * Live validation text below the form (errors red, warnings orange).
    * Save: validate -> write settings.json -> configure the DAQ service.
    * Cancel / closing the window asks before discarding unsaved changes.
    * Restore Defaults fills the form with the default template (not saved).

LabVIEW analogy:
    A dialog VI with an event structure; SettingsEditor is its data cluster.
"""

# Import copy to hand independent configuration copies to other modules.
import copy
# Import html to escape message text in the rich-text label.
import html
# Import Future for type hints of the command results.
from concurrent.futures import Future
# Import typing helpers.
from typing import List, Optional, Tuple

# Import Qt core types.
from PySide6.QtCore import Qt, Signal
# Import the Qt widgets used here.
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHeaderView, QMessageBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

# Import the application context.
from core.bootstrap import AppContext
# Import the service constants.
from core.daq_service import DaqService
# Import the settings logic.
from core.settings_editor import CURRENT, VOLTAGE, SettingsEditor, format_range
# Import the logger factory.
from core.logger import get_logger
from core.activity_trace import activity_trace
# Import the Qt bridge.
from ui.daq_bridge import DaqBridge
# Import the .ui loader helpers.
from ui.ui_loader import find_widgets, load_ui
# Import the widget name specification.
from ui.settings_widgets import CHANNEL_PREFIXES, all_required_widgets

# Logger for this module.
_log = get_logger("settings_window")

# Unit per quantity (also used as spin box suffix).
UNITS = {VOLTAGE: "V", CURRENT: "A"}
# Largest value accepted in the power supply range fields.
PS_LIMIT = 100000.0
# Largest absolute value accepted in the AO fields (volts).
AO_LIMIT = 100.0


def range_key(rng: Tuple[float, float]) -> str:
    """
    Convert a (min, max) range into a text key stored as combo box item data.

    Text keys are used because Qt may convert Python tuples into lists.

    Inputs:
        rng (Tuple[float, float]): (min, max) in volts.

    Returns:
        str: e.g. "-10.0|10.0".
    """
    # Join both numbers with a separator.
    return f"{float(rng[0])}|{float(rng[1])}"


def parse_range_key(key: str) -> Tuple[float, float]:
    """
    Convert a text key from range_key() back into (min, max).

    Inputs:
        key (str): e.g. "-10.0|10.0".

    Returns:
        Tuple[float, float]: (min, max).
    """
    # Split at the separator.
    low, high = key.split("|")
    # Convert both parts to numbers.
    return float(low), float(high)


class SettingsWindow(QDialog):
    """
    Settings dialog.

    Signals:
        settings_saved(AppConfig): Emitted after settings.json was written
            (a copy of the saved configuration).
    """

    # Emitted after a successful save.
    settings_saved = Signal(object)

    def __init__(self, ctx: AppContext, service: Optional[DaqService],
                 parent: Optional[QWidget] = None, first_start: bool = False) -> None:
        """
        Build the window.

        Inputs:
            ctx (AppContext): Start-up context (paths, catalogs, manager, ...).
            service (Optional[DaqService]): DAQ service (None = no backend usable).
            parent (Optional[QWidget]): Parent window.
            first_start (bool): True shows a "first start" hint.

        Returns:
            None.
        """
        # Initialize the dialog.
        super().__init__(parent)
        # Store the context.
        self._ctx = ctx
        # Store the service.
        self._service = service
        # Bridge from the worker thread to this window.
        self._bridge = DaqBridge(service, self)
        # Editor with a working copy of the active configuration.
        self.editor = SettingsEditor(ctx.config, ctx.catalogs, ctx.defaults)
        # Guard that suppresses widget signals while the code fills widgets.
        self._loading = False
        # Extra message shown above the validation text (e.g. save result).
        self._status = "First start: assign the AO channels and press Save." if first_start else ""
        # Colors of the active theme (config-driven).
        self._colors = ctx.theme_colors()
        # Load the Designer form.
        self.form = load_ui(ctx.paths.root / "ui" / "settings_window.ui", self)
        # Find and check all required widgets.
        self.w = find_widgets(self.form, all_required_widgets())
        # Layout that holds the form.
        layout = QVBoxLayout(self)
        # Small margin so messages and buttons do not touch the window edge.
        layout.setContentsMargins(8, 8, 8, 8)
        # Put the form into the dialog.
        layout.addWidget(self.form)
        # Window title from the form.
        self.setWindowTitle(self.form.windowTitle())
        # Allow maximize / minimize like a normal window.
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        # Prepare static widget properties.
        self._setup_widgets()
        # Connect the widget signals.
        self._connect_signals()
        # Fill the widgets from the working copy.
        self._load_all()
        # Size and center on the screen.
        self._place_window()
        # First scan when a usable driver exists.
        if self._service is not None and self._service.state.driver.available:
            # Start the scan.
            self.start_scan()

    # ------------------------------------------------------------------ setup

    def _ch(self, quantity: str, name: str) -> QWidget:
        """
        Return a channel widget.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            name (str): Widget name without prefix, e.g. "cbChannel".

        Returns:
            QWidget: The widget.
        """
        # Prefix + name.
        return self.w[CHANNEL_PREFIXES[quantity] + name]

    @staticmethod
    def _setup_table(table: QTableWidget, headers: List[str]) -> None:
        """
        Prepare a read-only table.

        Inputs:
            table (QTableWidget): Table to prepare.
            headers (List[str]): Column titles.

        Returns:
            None.
        """
        # Number of columns.
        table.setColumnCount(len(headers))
        # Column titles.
        table.setHorizontalHeaderLabels(headers)
        # Read-only.
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Select whole rows.
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # Columns share the width.
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # No row numbers.
        table.verticalHeader().setVisible(False)

    @staticmethod
    def _setup_spin(spin, low: float, high: float, decimals: int, suffix: str, step: float) -> None:
        """
        Prepare a QDoubleSpinBox.

        Inputs:
            spin (QDoubleSpinBox): Spin box.
            low (float): Minimum.
            high (float): Maximum.
            decimals (int): Shown decimals.
            suffix (str): Unit text after the number.
            step (float): Arrow-key step.

        Returns:
            None.
        """
        # Allowed range.
        spin.setRange(low, high)
        # Decimals.
        spin.setDecimals(decimals)
        # Unit suffix.
        spin.setSuffix(suffix)
        # Step size.
        spin.setSingleStep(step)
        # Emit valueChanged only after Enter / focus loss (no update per keystroke).
        spin.setKeyboardTracking(False)

    def _setup_widgets(self) -> None:
        """Set static properties: tables, spin box ranges, selector items."""
        # Device table columns.
        self._setup_table(self.w["twDevices"], ["Device", "Product type", "Serial", "AO channels", "AO ranges"])
        # Backend selector items (auto + catalog).
        for backend_id, label in self.editor.backend_options():
            # Text and id.
            self.w["cbBackend"].addItem(label, backend_id)
        # SIMULATION indicator style (warning color from the theme).
        self.w["lblSimulation"].setStyleSheet(
            f"background:{self._colors.get('warning', '#F2994A')}; color:#000; font-weight:bold; padding:2px 8px;")
        # Show the indicator only for the simulation backend.
        self.w["lblSimulation"].setVisible(bool(self._service and self._service.state.simulated))
        # Channel widgets for both quantities.
        for quantity, unit in UNITS.items():
            # Span preset items from ao_spans.json.
            for item in self.editor.span_presets():
                # Text and id.
                self._ch(quantity, "cbSpanPreset").addItem(item.get("label", item["id"]), item["id"])
            # AO span fields in volts.
            for name in ("dsbAoMin", "dsbAoMax"):
                # Range and format.
                self._setup_spin(self._ch(quantity, name), -AO_LIMIT, AO_LIMIT, 3, " V", 0.5)
            # Power supply range fields in the channel unit.
            for name in ("dsbPsMin", "dsbPsMax"):
                # Range and format.
                self._setup_spin(self._ch(quantity, name), -PS_LIMIT, PS_LIMIT, 3, f" {unit}", 1.0)
            # Preview table columns.
            self._setup_table(self._ch(quantity, "twPreview"), ["% of range", f"Power supply [{unit}]", "AO [V]"])
        # Safe value fields (power supply units, both channels).
        for name in ("dsbSafeStartup", "dsbSafeExit"):
            # Range and format.
            self._setup_spin(self.w[name], -PS_LIMIT, PS_LIMIT, 3, "", 0.1)
        # Voltage slew rate field.
        self._setup_spin(self.w["dsbSlewVoltage"], 0.001, PS_LIMIT, 3, " V/s", 0.5)
        # Current slew rate field.
        self._setup_spin(self.w["dsbSlewCurrent"], 0.001, PS_LIMIT, 3, " A/s", 0.5)
        # Messages may contain rich text.
        self.w["lblMessages"].setTextFormat(Qt.TextFormat.RichText)
        # Save is the default button (Enter).
        self.w["btnSave"].setDefault(True)

    def _connect_signals(self) -> None:
        """Connect widget signals to the handlers."""
        # Scan button.
        self.w["btnScan"].clicked.connect(self.start_scan)
        # Save button.
        self.w["btnSave"].clicked.connect(self.save)
        # Cancel button closes (closeEvent asks about unsaved changes).
        self.w["btnCancel"].clicked.connect(self.close)
        # Restore Defaults button.
        self.w["btnRestoreDefaults"].clicked.connect(self.restore_defaults)
        # Backend selector.
        self.w["cbBackend"].currentIndexChanged.connect(self._on_backend_changed)
        # Safety fields all use one handler.
        for name in ("dsbSafeStartup", "dsbSafeExit", "dsbSlewVoltage", "dsbSlewCurrent"):
            # Value changes.
            self.w[name].valueChanged.connect(self._on_safety_changed)
        # Slew checkbox.
        self.w["chkSlewLimit"].toggled.connect(self._on_safety_changed)
        # Channel widgets for both quantities.
        for quantity in UNITS:
            # Channel selector (default argument binds the quantity).
            self._ch(quantity, "cbChannel").currentIndexChanged.connect(
                lambda _i, q=quantity: self._on_channel_changed(q))
            # Device range selector.
            self._ch(quantity, "cbDeviceRange").currentIndexChanged.connect(
                lambda _i, q=quantity: self._on_device_range_changed(q))
            # Span preset selector.
            self._ch(quantity, "cbSpanPreset").currentIndexChanged.connect(
                lambda _i, q=quantity: self._on_span_preset_changed(q))
            # AO span fields.
            for name in ("dsbAoMin", "dsbAoMax"):
                # Value changes.
                self._ch(quantity, name).valueChanged.connect(lambda _v, q=quantity: self._on_ao_span_changed(q))
            # PS range fields.
            for name in ("dsbPsMin", "dsbPsMax"):
                # Value changes.
                self._ch(quantity, name).valueChanged.connect(lambda _v, q=quantity: self._on_ps_changed(q))
        # Service events (in the UI thread).
        self._bridge.event_received.connect(self._on_daq_event)

    def _place_window(self) -> None:
        """Size the window to fit the screen and center it."""
        # Screen of the window (primary screen before the first show).
        screen = self.screen()
        # Without a screen keep the .ui size.
        if screen is None:
            # Nothing to do.
            return
        # Usable screen area (without task bar).
        area = screen.availableGeometry()
        # Preferred size from the .ui file, limited to 90 % of the screen.
        width = min(self.form.width(), int(area.width() * 0.9))
        # Same for the height.
        height = min(self.form.height(), int(area.height() * 0.9))
        # Apply the size.
        self.resize(width, height)
        # Window frame rectangle.
        frame = self.frameGeometry()
        # Move its center to the screen center.
        frame.moveCenter(area.center())
        # Apply the position.
        self.move(frame.topLeft())

    # ------------------------------------------------------------------ fill widgets

    @staticmethod
    def _select_data(combo, data) -> None:
        """
        Select the combo box entry with the given item data.

        Inputs:
            combo (QComboBox): Combo box.
            data: Item data to select.

        Returns:
            None.
        """
        # Find the entry.
        index = combo.findData(data)
        # Select it when found.
        if index >= 0:
            # Change the selection.
            combo.setCurrentIndex(index)

    def _load_all(self) -> None:
        """Fill every widget from the editor's working copy."""
        # Suppress handlers while filling.
        self._loading = True
        # Fill and always release the guard.
        try:
            # Backend selector.
            self._select_data(self.w["cbBackend"], self.editor.config.daq.backend)
            # Backend information text.
            self._show_backend_info()
            # Device table.
            self._fill_device_table()
            # Safety group.
            safety = self.editor.config.safety
            # Safe value at start-up.
            self.w["dsbSafeStartup"].setValue(safety.safe_value_startup)
            # Safe value at exit.
            self.w["dsbSafeExit"].setValue(safety.safe_value_exit)
            # Slew checkbox.
            self.w["chkSlewLimit"].setChecked(safety.slew_limit_enabled)
            # Voltage slew rate.
            self.w["dsbSlewVoltage"].setValue(safety.slew_rate_voltage)
            # Current slew rate.
            self.w["dsbSlewCurrent"].setValue(safety.slew_rate_current)
            # Rate fields only matter when the limit is on.
            self._update_slew_enabled()
        # Release the guard.
        finally:
            # Handlers active again.
            self._loading = False
        # Both channel groups.
        for quantity in UNITS:
            # Fill the channel.
            self._load_channel(quantity)
        # Validation text.
        self._refresh_messages()

    def _load_channel(self, quantity: str) -> None:
        """
        Fill the widgets of one channel group and its preview.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            None.
        """
        # Channel settings.
        channel = self.editor.channel(quantity)
        # Suppress handlers while filling.
        self._loading = True
        # Fill and always release the guard.
        try:
            # Channel selector.
            combo = self._ch(quantity, "cbChannel")
            # Remove the old entries.
            combo.clear()
            # Add the current entries.
            for option in self.editor.channel_options(quantity):
                # Text and channel name.
                combo.addItem(option.label, option.physical_channel)
            # Select the configured channel.
            self._select_data(combo, channel.physical_channel)
            # Device range selector.
            ranges = self._ch(quantity, "cbDeviceRange")
            # Remove the old entries.
            ranges.clear()
            # Add the supported ranges.
            for rng in self.editor.device_ranges(quantity):
                # Text and range key.
                ranges.addItem(format_range(rng), range_key(rng))
            # Select the configured range.
            self._select_data(ranges, range_key((channel.device_ao_min, channel.device_ao_max)))
            # Span preset selector.
            self._select_data(self._ch(quantity, "cbSpanPreset"), channel.ao_span_preset)
            # AO span values.
            self._ch(quantity, "dsbAoMin").setValue(channel.ao_min)
            # AO span maximum.
            self._ch(quantity, "dsbAoMax").setValue(channel.ao_max)
            # AO fields editable only for the custom preset.
            custom = self.editor.is_custom_span(quantity)
            # Minimum field.
            self._ch(quantity, "dsbAoMin").setEnabled(custom)
            # Maximum field.
            self._ch(quantity, "dsbAoMax").setEnabled(custom)
            # PS range values.
            self._ch(quantity, "dsbPsMin").setValue(channel.ps_min)
            # PS maximum.
            self._ch(quantity, "dsbPsMax").setValue(channel.ps_max)
        # Release the guard.
        finally:
            # Handlers active again.
            self._loading = False
        # Preview.
        self._refresh_preview(quantity)

    def _refresh_preview(self, quantity: str) -> None:
        """
        Show slope, offset and the preview table of one channel.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            None.
        """
        # Calculate the preview.
        preview = self.editor.preview(quantity)
        # Scale label.
        label = self._ch(quantity, "lblScale")
        # Preview table.
        table = self._ch(quantity, "twPreview")
        # Invalid scale: show the reasons and an empty table.
        if not preview.valid:
            # Error text in the error color.
            label.setText(f"<span style='color:{self._colors.get('error', '#EB5757')}'>"
                          + html.escape(" ".join(preview.errors)) + "</span>")
            # Empty table.
            table.setRowCount(0)
            # Done.
            return
        # Slope / offset text (plus the PS-min hint).
        text = f"Slope {preview.slope:.6g} V/{UNITS[quantity]}   Offset {preview.offset:.6g} V"
        # Add the hint on a new line when present.
        label.setText(text + (f"<br>{html.escape(preview.note)}" if preview.note else ""))
        # One row per preview point.
        table.setRowCount(len(preview.rows))
        # Fill the rows.
        for row, (percent, ps_value, ao_value) in enumerate(preview.rows):
            # Three columns.
            for column, text in enumerate((f"{percent:g} %", f"{ps_value:.4g}", f"{ao_value:.4g}")):
                # Table cell.
                cell = QTableWidgetItem(text)
                # Right-aligned numbers.
                cell.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                # Put it into the table.
                table.setItem(row, column, cell)

    def _fill_device_table(self) -> None:
        """Show the scanned devices in the device table."""
        # Device table.
        table = self.w["twDevices"]
        # One row per device.
        table.setRowCount(len(self.editor.devices))
        # Fill the rows.
        for row, device in enumerate(self.editor.devices):
            # Column texts.
            texts = (device.name, device.product_type, device.serial,
                     ", ".join(name.split("/")[-1] for name in device.ao_channels),
                     ", ".join(format_range(rng) for rng in device.ao_ranges))
            # Fill the columns.
            for column, text in enumerate(texts):
                # Put the cell into the table.
                table.setItem(row, column, QTableWidgetItem(text))

    def _show_backend_info(self, extra: str = "") -> None:
        """
        Show the active backend and driver status.

        Inputs:
            extra (str): Additional text (e.g. "Scanning ...").

        Returns:
            None.
        """
        # No service: no usable backend.
        if self._service is None:
            # Explain and disable scanning.
            self.w["lblBackendInfo"].setText("No DAQ backend available - devices cannot be scanned.")
            # Scan impossible.
            self.w["btnScan"].setEnabled(False)
            # Done.
            return
        # Service state snapshot.
        state = self._service.state
        # Base text: backend and driver message.
        text = f"Active backend: {state.backend_id} - {state.driver.message}"
        # Driver missing: say what that means.
        if not state.driver.available:
            # Add the hint.
            text += "  (driver not available - scanning is disabled)"
        # Scan only with a driver.
        self.w["btnScan"].setEnabled(state.driver.available)
        # Show the text plus the extra information.
        self.w["lblBackendInfo"].setText(text + (f"  |  {extra}" if extra else ""))

    def _update_slew_enabled(self) -> None:
        """Enable the rate fields only while the slew-rate limit is checked."""
        # Checkbox state.
        enabled = self.w["chkSlewLimit"].isChecked()
        # Voltage rate field.
        self.w["dsbSlewVoltage"].setEnabled(enabled)
        # Current rate field.
        self.w["dsbSlewCurrent"].setEnabled(enabled)

    def _refresh_messages(self) -> None:
        """Validate the working copy and show status, errors and warnings."""
        # Validate.
        errors, warnings = self.editor.validate()
        # Message lines (HTML).
        lines = []
        # Status line first.
        if self._status:
            # Plain status text.
            lines.append(f"<b>{html.escape(self._status)}</b>")
        # Errors in the error color.
        lines += [f"<span style='color:{self._colors.get('error', '#EB5757')}'>&#10006; {html.escape(m)}</span>"
                  for m in errors]
        # Warnings in the warning color.
        lines += [f"<span style='color:{self._colors.get('warning', '#F2994A')}'>&#9888; {html.escape(m)}</span>"
                  for m in warnings]
        # Everything fine.
        if not errors and not warnings:
            # OK line in the ok color.
            lines.append(f"<span style='color:{self._colors.get('ok', '#27AE60')}'>&#10004; Settings are valid.</span>")
        # Show the lines.
        self.w["lblMessages"].setText("<br>".join(lines))

    # ------------------------------------------------------------------ handlers

    def _on_backend_changed(self) -> None:
        """Handler: backend selector changed."""
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Store the selection.
        self.editor.set_backend(self.w["cbBackend"].currentData())
        # Update messages.
        self._refresh_messages()

    def _on_safety_changed(self, *_args) -> None:
        """Handler: any safety field changed."""
        # Rate fields follow the checkbox.
        self._update_slew_enabled()
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Store all safety values.
        self.editor.set_safety(self.w["dsbSafeStartup"].value(), self.w["dsbSafeExit"].value(),
                               self.w["chkSlewLimit"].isChecked(),
                               self.w["dsbSlewVoltage"].value(), self.w["dsbSlewCurrent"].value())
        # Update messages.
        self._refresh_messages()

    def _on_channel_changed(self, quantity: str) -> None:
        """
        Handler: channel selector changed.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Assign the selected channel (also updates the device range).
        self.editor.assign_channel(quantity, self._ch(quantity, "cbChannel").currentData() or "")
        # Refill the group (device ranges may differ).
        self._load_channel(quantity)
        # Update messages.
        self._refresh_messages()

    def _on_device_range_changed(self, quantity: str) -> None:
        """
        Handler: device range selector changed.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Selected range key.
        key = self._ch(quantity, "cbDeviceRange").currentData()
        # Store it when valid.
        if key:
            # Convert the key back into (min, max).
            self.editor.set_device_range(quantity, parse_range_key(key))
        # Update messages.
        self._refresh_messages()

    def _on_span_preset_changed(self, quantity: str) -> None:
        """
        Handler: span preset selector changed.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Apply the preset.
        self.editor.apply_span_preset(quantity, self._ch(quantity, "cbSpanPreset").currentData())
        # Refill the group (span values and enabled state).
        self._load_channel(quantity)
        # Update messages.
        self._refresh_messages()

    def _on_ao_span_changed(self, quantity: str) -> None:
        """
        Handler: custom AO min/max changed.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Store the custom span.
        self.editor.set_custom_span(quantity, self._ch(quantity, "dsbAoMin").value(),
                                    self._ch(quantity, "dsbAoMax").value())
        # Update the preview.
        self._refresh_preview(quantity)
        # Update messages.
        self._refresh_messages()

    def _on_ps_changed(self, quantity: str) -> None:
        """
        Handler: power supply min/max changed.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Store the range.
        self.editor.set_ps_range(quantity, self._ch(quantity, "dsbPsMin").value(),
                                 self._ch(quantity, "dsbPsMax").value())
        # Update the preview.
        self._refresh_preview(quantity)
        # Update messages.
        self._refresh_messages()

    def _on_daq_event(self, event) -> None:
        """
        Handler: DAQ service event (UI thread).

        Inputs:
            event (DaqEvent): Service event.
        """
        # Only errors are shown here; scan / configure results come through futures.
        if event.kind == "error":
            # Show the error as status.
            self._status = f"DAQ error: {event.data.get('message', '')}"
            # Update messages.
            self._refresh_messages()

    # ------------------------------------------------------------------ actions

    def start_scan(self) -> None:
        """Action: scan devices (asynchronous)."""
        # Without a service nothing can be scanned.
        if self._service is None:
            # Nothing to do.
            return
        # Prevent double clicks while scanning.
        self.w["btnScan"].setEnabled(False)
        # Show progress.
        self._show_backend_info("Scanning ...")
        # Queue the scan and handle the result in the UI thread.
        self._bridge.when_done(self._service.scan(), self._on_scan_done)

    def _on_scan_done(self, future: Future) -> None:
        """
        Result of a scan (UI thread).

        Inputs:
            future (Future): Finished scan future.
        """
        # Read the result.
        try:
            # Device list.
            devices = future.result()
        # Scan failed.
        except Exception as exc:  # noqa: BLE001
            # Show the problem.
            self._show_backend_info(f"Scan failed: {exc}")
            # Done.
            return
        # Store the devices.
        self.editor.set_devices(devices)
        # Show the backend line with the count.
        self._show_backend_info(f"{len(devices)} device(s) with analog outputs found")
        # Device table.
        self._fill_device_table()
        # Channel groups (new channel lists).
        for quantity in UNITS:
            # Refill.
            self._load_channel(quantity)
        # Messages (hardware checks now active).
        self._refresh_messages()

    def save(self) -> bool:
        """
        Action: validate, write settings.json and configure the DAQ service.

        Returns:
            bool: True when the settings were written.
        """
        activity_trace.add("Settings save requested")
        # Validate the working copy.
        errors, _warnings = self.editor.validate()
        # Errors block saving.
        if errors:
            activity_trace.add("Settings not saved: " + "; ".join(errors))
            # Explain.
            self._status = "Not saved - fix the errors below."
            # Show.
            self._refresh_messages()
            # Not saved.
            return False
        # Backend that is running now (for the restart hint).
        old_backend = self._ctx.config.daq.backend
        # Write the file.
        try:
            # Save the working copy.
            self._ctx.manager.save(self.editor.config)
        # Disk problem.
        except OSError as exc:
            activity_trace.add(f"Settings save failed: {exc}")
            # Show it.
            self._status = f"Not saved - file error: {exc}"
            # Show.
            self._refresh_messages()
            # Not saved.
            return False
        # The saved copy becomes the active configuration.
        self._ctx.load_result.config = copy.deepcopy(self.editor.config)
        activity_trace.add("Settings saved")
        # No unsaved changes any more.
        self.editor.mark_saved()
        # Tell other windows.
        self.settings_saved.emit(copy.deepcopy(self.editor.config))
        # Status text.
        self._status = "Saved."
        # Backend change hint.
        if self.editor.config.daq.backend != old_backend:
            # Add the hint.
            self._status += " The backend change applies at the next start."
        # Configure the hardware when a usable service exists.
        if self._service is not None and self._service.state.driver.available:
            # Progress text.
            self._status += " Setting up AO channels ..."
            # Queue the configuration with an independent copy.
            self._bridge.when_done(self._service.configure(copy.deepcopy(self.editor.config)),
                                   self._on_configure_done)
        # Show.
        self._refresh_messages()
        # Saved.
        return True

    def _on_configure_done(self, future: Future) -> None:
        """
        Result of configure (UI thread).

        Inputs:
            future (Future): Finished configure future.
        """
        # Read the result.
        try:
            # (ok, errors, warnings).
            ok, errors, _warnings = future.result()
        # Unexpected failure.
        except Exception as exc:  # noqa: BLE001
            # Treat as not configured.
            ok, errors = False, [str(exc)]
        # Replace the progress part of the status.
        base = self._status.replace(" Setting up AO channels ...", "")
        # Result text.
        if ok:
            # Success.
            self._status = base + " AO channels ready (output OFF, safe value applied)."
        # Failure.
        else:
            # First reason.
            self._status = base + " AO channels NOT ready: " + "; ".join(errors)
        # Show.
        self._refresh_messages()

    def restore_defaults(self) -> None:
        """Action: fill the form with the default template (saved only with Save)."""
        # Replace the working copy.
        self.editor.restore_defaults()
        # Status hint.
        self._status = "Defaults loaded - press Save to keep them."
        # Refill everything.
        self._load_all()

    def confirm_discard(self) -> bool:
        """
        Ask whether unsaved changes may be discarded.

        Returns:
            bool: True = discard and close.
        """
        # Ask the user.
        answer = QMessageBox.question(self, "Unsaved changes",
                                      "There are unsaved changes. Close without saving?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        # Yes means discard.
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt method name)
        """
        Qt close handler: ask before discarding unsaved changes.

        Inputs:
            event (QCloseEvent): Close event.
        """
        # Unsaved changes and the user says No: keep the window open.
        if self.editor.is_dirty() and not self.confirm_discard():
            # Cancel the close.
            event.ignore()
            # Done.
            return
        # Close normally.
        event.accept()

    def reject(self) -> None:
        """Escape key: same as Cancel (goes through closeEvent)."""
        # Close via closeEvent so unsaved changes are checked.
        self.close()

    # ------------------------------------------------------------------ test helpers

    def channel_texts(self, quantity: str) -> Tuple[str, ...]:
        """
        Return the entries of a channel selector (used by tests).

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            Tuple[str, ...]: Physical channel names in selector order.
        """
        # Channel selector.
        combo = self._ch(quantity, "cbChannel")
        # Item data of every entry.
        return tuple(combo.itemData(index) for index in range(combo.count()))
