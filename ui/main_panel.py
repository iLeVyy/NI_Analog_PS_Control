"""
ui/main_panel.py
================
Power supply front panel - the main window (Project_Plan.md section 6).

Layout: ui/main_panel.ui (Qt Designer). Logic: core/front_panel.py.
Hardware: only through the DAQ service; results arrive via ui/daq_bridge.py.

Behavior (decisions D-02, D-07):
    * All values in power supply units (V / A); the service scales to AO volts.
    * Knob, step buttons and step size change the setpoint immediately;
      the numeric entry applies on Enter or when leaving the field.
    * Output OFF: setpoints are only stored. Output ON: setpoints are written.
    * Slew-rate limit off (default): changes jump to the new value.
      On: changes ramp at the configured rate in steps of slew_interval_ms;
      Output ON starts from the safe value; a new setpoint during a ramp
      continues from the current value; Output OFF cancels the ramp.
    * Controls are disabled while the AO channels are not set up.
    * Start-up: sets up the AO channels; opens Settings on first start and
      offers it when the configured device is missing (plan 9.3).
    * Sequence button opens the Sequence window (non-modal). While a
      sequence runs, the sections and Settings are locked (FR-13), the
      displays follow the sequence values, and the Output OFF button
      stops the sequence (output OFF + safe value).

LabVIEW analogy:
    The main front panel VI with its event structure; ChannelControl and
    SlewRamp are its sub-VIs.
"""

# Import copy to hand independent configuration copies to the service.
import copy
# Import html to escape message text.
import html
import math
# Import time for the ramp interval measurement.
import time
# Import Future for type hints.
from concurrent.futures import Future
# Import typing helpers.
from typing import Dict, Optional

# Import Qt core types.
from PySide6.QtCore import QRect, QTimer, Qt
# Import the Qt widgets used here.
from PySide6.QtWidgets import QApplication, QLCDNumber, QMainWindow, QMessageBox, QWidget

# Import the application context.
from core.bootstrap import AppContext
from core.activity_trace import activity_trace
# Import the front-panel logic.
from core.front_panel import ChannelControl, SlewRamp
# Import the service and its constants.
from core.daq_service import (CURRENT, EVT_CONFIGURED, EVT_ERROR, EVT_OUTPUT, EVT_WRITTEN,
                              QUANTITIES, VOLTAGE, DaqService)
# Import the logger factory.
from core.logger import get_logger
# Import the Qt bridge.
from ui.daq_bridge import DaqBridge
# Import the widget specification.
from ui.main_panel_widgets import SECTION_PREFIXES, all_required_widgets
from ui.log_window import LogWindow
# Import the Sequence window.
from ui.sequence_window import SequenceWindow
# Import the Settings window.
from ui.settings_window import SettingsWindow
# Import the .ui loader helpers.
from ui.ui_loader import find_widgets, load_ui

# Logger for this module.
_log = get_logger("main_panel")


class MainPanel(QMainWindow):
    """Power supply front panel (main window)."""

    def __init__(self, ctx: AppContext, service: Optional[DaqService],
                 first_start: bool = False, parent: Optional[QWidget] = None) -> None:
        """
        Build the window (call start() after show()).

        Inputs:
            ctx (AppContext): Start-up context.
            service (Optional[DaqService]): DAQ service (None = no backend).
            first_start (bool): True when settings.json was just created.
            parent (Optional[QWidget]): Parent widget.

        Returns:
            None.
        """
        # Initialize the main window.
        super().__init__(parent)
        # Store the context.
        self._ctx = ctx
        # Store the service.
        self._service = service
        # First start flag.
        self._first_start = first_start
        # Bridge from the worker thread to this window.
        self._bridge = DaqBridge(service, self)
        # Section states per quantity (filled by apply_config()).
        self.controls: Dict[str, ChannelControl] = {}
        # Manual setpoints are independent of values displayed during sequences.
        self._manual_targets: Dict[str, float] = {}
        # Ramps per quantity.
        self.ramps: Dict[str, SlewRamp] = {}
        # Last AO volts written per quantity.
        self._ao: Dict[str, float] = {}
        # True when the AO channels are set up.
        self.configured = False
        # True while the output is ON (set immediately on request, corrected by events).
        self.output_on = False
        # Guard that suppresses widget signals while the code fills widgets.
        self._loading = False
        # Open Settings dialog (None when closed).
        self.settings_dialog: Optional[SettingsWindow] = None
        # Sequence window (created on first use, then reused).
        self.sequence_window: Optional[SequenceWindow] = None
        self.log_window: Optional[LogWindow] = None
        # True while a sequence runs.
        self.sequence_running = False
        # Time of the last ramp step (seconds, monotonic clock).
        self._last_tick = 0.0
        # Ramp timer (interval from settings.json).
        self._ramp_timer = QTimer(self)
        # Load the Designer form.
        self.form = load_ui(ctx.paths.root / "ui" / "main_panel.ui", self)
        # Find and check all required widgets.
        self.w = find_widgets(self.form, all_required_widgets())
        # The form is the central area of the main window.
        self.setCentralWidget(self.form)
        # Window title with the version.
        self.setWindowTitle(f"PS Control {ctx.version}")
        # Prepare static widget properties.
        self._setup_widgets()
        # Connect signals.
        self._connect_signals()
        # Colors and stylesheet of the configured theme.
        self.apply_theme()
        # Fill the sections from the configuration.
        self.apply_config()
        # Enabled state and output display.
        self._refresh_state()
        # Size and position.
        self._place_window()

    # ------------------------------------------------------------------ setup

    def _sec(self, quantity: str, name: str) -> QWidget:
        """
        Return a section widget.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            name (str): Widget name without prefix.

        Returns:
            QWidget: The widget.
        """
        # Prefix + name.
        return self.w[SECTION_PREFIXES[quantity] + name]

    def _setup_widgets(self) -> None:
        """Set static widget properties."""
        # Both sections.
        for quantity in QUANTITIES:
            # 7-segment display without 3D segments.
            self._sec(quantity, "lcdSetpoint").setSegmentStyle(QLCDNumber.SegmentStyle.Flat)
            # Numeric entry: emit only on Enter / focus loss.
            self._sec(quantity, "dsbEntry").setKeyboardTracking(False)
            # Knob: emit while turning.
            self._sec(quantity, "dialKnob").setTracking(True)
            # Measured-value area is reserved for FE-02 (hidden in v1.0).
            self._sec(quantity, "gbMeasured").setVisible(False)
        # Title font: larger and bold.
        self.w["lblTitle"].setStyleSheet("font-size:16pt; font-weight:bold;")
        # Output state text: larger and bold.
        self.w["lblOutputState"].setStyleSheet("font-size:14pt; font-weight:bold;")
        # Status message may contain rich text.
        self.w["lblStatusMessage"].setTextFormat(Qt.TextFormat.RichText)
        # SIMULATION indicator only for the simulation backend.
        self.w["lblSimulation"].setVisible(bool(self._service and self._service.state.simulated))

    def _connect_signals(self) -> None:
        """Connect widget, timer and service signals."""
        # Toolbar buttons.
        self.w["btnSettings"].clicked.connect(lambda: self.open_settings())
        # Sequence window.
        self.w["btnSequence"].clicked.connect(lambda: self.open_sequence())
        # Activity log.
        self.w["btnLog"].clicked.connect(self.open_log)
        # Output button.
        self.w["btnOutput"].clicked.connect(self.toggle_output)
        # Ramp timer.
        self._ramp_timer.timeout.connect(self._on_ramp_tick)
        # Both sections.
        for quantity in QUANTITIES:
            # Knob (default argument binds the quantity).
            self._sec(quantity, "dialKnob").valueChanged.connect(
                lambda pos, q=quantity: self._on_dial(q, pos))
            # Numeric entry.
            self._sec(quantity, "dsbEntry").valueChanged.connect(
                lambda value, q=quantity: self._on_entry(q, value))
            # Step up.
            self._sec(quantity, "btnStepUp").clicked.connect(lambda _c=False, q=quantity: self.nudge(q, +1))
            # Step down.
            self._sec(quantity, "btnStepDown").clicked.connect(lambda _c=False, q=quantity: self.nudge(q, -1))
            # Step size.
            self._sec(quantity, "cbStepSize").currentIndexChanged.connect(
                lambda _i, q=quantity: self._on_step_size(q))
        # Service events (UI thread).
        self._bridge.event_received.connect(self._on_daq_event)

    def _place_window(self) -> None:
        """Restore the saved geometry if it fits on a screen, otherwise center the window."""
        # Saved geometry [x, y, width, height] or None.
        saved = self._ctx.config.ui.window_geometry
        # Screen used for centering.
        screen = self.screen()
        # Without a screen keep the default size.
        if screen is None:
            # Nothing to do.
            return
        # Usable screen area.
        area = screen.availableGeometry()
        # Restore when the saved rectangle is valid and visible.
        if isinstance(saved, list) and len(saved) == 4:
            # Saved rectangle.
            rect = QRect(*[int(v) for v in saved])
            # Only when it overlaps the visible area of any screen.
            if any(s.availableGeometry().intersects(rect) for s in QApplication.screens()):
                # Apply it.
                self.setGeometry(rect)
                # Done.
                return
        # Default size from the .ui file, limited to 90 % of the screen.
        self.resize(min(self.form.width(), int(area.width() * 0.9)),
                    min(self.form.height(), int(area.height() * 0.9)))
        # Frame rectangle.
        frame = self.frameGeometry()
        # Center it.
        frame.moveCenter(area.center())
        # Apply the position.
        self.move(frame.topLeft())

    # ------------------------------------------------------------------ configuration / theme

    def apply_config(self, *_args) -> None:
        """Rebuild both sections from the active configuration (also after Settings → Save)."""
        # Active configuration.
        config = self._ctx.config
        # Step size items from the catalog.
        step_items = self._ctx.catalogs["step_sizes"].items
        # Channel settings and preferred steps per quantity.
        sources = {VOLTAGE: (config.voltage_channel, config.ui.voltage_step),
                   CURRENT: (config.current_channel, config.ui.current_step)}
        saved_manual = {VOLTAGE: config.ui.last_manual_voltage,
                        CURRENT: config.ui.last_manual_current}
        # Rates per quantity.
        rates = {VOLTAGE: config.safety.slew_rate_voltage, CURRENT: config.safety.slew_rate_current}
        # Build both sections.
        for quantity, (channel, step) in sources.items():
            # Invalid, missing or out-of-range manual values use the channel minimum.
            manual = self._manual_targets.get(quantity, saved_manual[quantity])
            if (isinstance(manual, bool) or not isinstance(manual, (int, float))
                    or not channel.ps_min <= manual <= channel.ps_max or not math.isfinite(manual)):
                if manual is not None:
                    _log.warning("Invalid saved manual %s setpoint %r; using range minimum %g",
                                 quantity, manual, channel.ps_min)
                manual = channel.ps_min
            self._manual_targets[quantity] = float(manual)
            # Preserve the displayed target after Settings saves.
            previous = self.controls[quantity].target if quantity in self.controls else manual
            # New section state.
            self.controls[quantity] = ChannelControl.from_config(quantity, channel, step_items, step, previous)
            # Keep an existing ramp position, update its rate.
            ramp = self.ramps.get(quantity) or SlewRamp(rates[quantity])
            # New rate.
            ramp.rate = rates[quantity]
            # Store the ramp.
            self.ramps[quantity] = ramp
            # Fill the widgets.
            self._fill_section(quantity)
        # Ramp timer interval.
        self._ramp_timer.setInterval(int(config.safety.slew_interval_ms))
        # The Sequence window uses the new ranges too.
        if self.sequence_window is not None:
            # Refresh it.
            self.sequence_window.apply_config()
        # Channel text in the status bar.
        self._refresh_status()

    def _fill_section(self, quantity: str) -> None:
        """
        Fill all widgets of one section from its ChannelControl.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Section state.
        control = self.controls[quantity]
        # Suppress handlers while filling.
        self._loading = True
        # Fill and always release the guard.
        try:
            # Step size selector.
            combo = self._sec(quantity, "cbStepSize")
            # Remove the old entries.
            combo.clear()
            # Add the catalog entries.
            for item in control.step_options:
                # Text and value.
                combo.addItem(item.get("label", str(item["value"])), float(item["value"]))
            # Select the active step.
            combo.setCurrentIndex(max(0, combo.findData(control.step)))
            # Numeric entry.
            entry = self._sec(quantity, "dsbEntry")
            # Decimals first (setRange rounds to them).
            entry.setDecimals(control.decimals)
            # Range of the power supply.
            entry.setRange(control.ps_min, control.ps_max)
            # Arrow step.
            entry.setSingleStep(control.step)
            # Unit.
            entry.setSuffix(f" {control.unit}")
            # Knob.
            dial = self._sec(quantity, "dialKnob")
            # Knob range.
            dial.setRange(0, control.dial_max)
            # Page step (Page Up/Down, notch spacing): about 1/20 of the range.
            dial.setPageStep(max(1, control.dial_max // 20))
            # Notch spacing in pixels.
            dial.setNotchTarget(8.0)
            # Very fine knobs (many positions) look cluttered with notches: hide them.
            dial.setNotchesVisible(control.dial_max <= 400)
            # Display width.
            self._sec(quantity, "lcdSetpoint").setDigitCount(control.display_digits)
            # Range text.
            self._sec(quantity, "lblRange").setText(control.range_text())
        # Release the guard.
        finally:
            # Handlers active again.
            self._loading = False
        # Show the setpoint everywhere.
        self._show_target(quantity)

    def _show_target(self, quantity: str) -> None:
        """
        Show the setpoint of a section on display, knob and entry.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Section state.
        control = self.controls[quantity]
        # Suppress handlers while updating.
        self._loading = True
        # Update and always release the guard.
        try:
            # 7-segment display.
            self._sec(quantity, "lcdSetpoint").display(control.display_text(control.target))
            # Knob position.
            self._sec(quantity, "dialKnob").setValue(control.dial_from_value(control.target))
            # Numeric entry.
            self._sec(quantity, "dsbEntry").setValue(control.target)
        # Release the guard.
        finally:
            # Handlers active again.
            self._loading = False

    def apply_theme(self) -> None:
        """Apply the stylesheet and colors of the configured theme (config-driven)."""
        # Colors of the theme.
        self._colors = self._ctx.theme_colors()
        # Theme entry (for the stylesheet file).
        theme = self._ctx.themes.get(self._ctx.config.ui.theme, {})
        # Stylesheet file of the theme.
        qss_file = theme.get("folder") / theme.get("stylesheet", "style.qss") if theme else None
        # Application stylesheet (empty when the file is missing).
        qss = qss_file.read_text(encoding="utf-8") if qss_file and qss_file.is_file() else ""
        # Apply it to the whole application.
        QApplication.instance().setStyleSheet(qss)
        # Display colors.
        lcd_style = (f"QLCDNumber {{ background:{self._colors.get('display_background', '#11161D')};"
                     f" color:{self._colors.get('display_text', '#4FC3F7')}; border-radius:6px; }}")
        # Both displays.
        for quantity in QUANTITIES:
            # Apply the style.
            self._sec(quantity, "lcdSetpoint").setStyleSheet(lcd_style)
        # SIMULATION label in the warning color.
        self.w["lblSimulation"].setStyleSheet(
            f"background:{self._colors.get('warning', '#F2994A')}; color:#000; font-weight:bold; padding:2px 8px;")
        # LED color follows the output state.
        self._refresh_led()

    # ------------------------------------------------------------------ state display

    def _refresh_led(self) -> None:
        """Show the output state on the LED, the state text and the output button."""
        # LED color.
        color = self._colors.get("led_on" if self.output_on else "led_off", "#27AE60" if self.output_on else "#4A5563")
        # Round LED.
        self.w["lblLed"].setStyleSheet(f"background:{color}; border-radius:18px; border:2px solid #222;")
        # State text.
        self.w["lblOutputState"].setText("ON" if self.output_on else "OFF")
        # Button text shows the next action.
        self.w["btnOutput"].setText("Output OFF" if self.output_on else "Output ON")

    def _refresh_state(self) -> None:
        """Enable or disable the controls and refresh LED and status bar."""
        # Controls only work with set-up channels and no running sequence (FR-13).
        for quantity in QUANTITIES:
            # Whole section.
            self._sec(quantity, "gbSection").setEnabled(self.configured and not self.sequence_running)
        # Settings cannot change while a sequence runs.
        self.w["btnSettings"].setEnabled(not self.sequence_running)
        # Output button.
        self.w["btnOutput"].setEnabled(self.configured)
        # LED and texts.
        self._refresh_led()
        # Status bar.
        self._refresh_status()

    def _refresh_status(self) -> None:
        """Show channels and AO volts in the status bar."""
        # Active configuration.
        config = self._ctx.config
        # Channel text.
        if self.configured:
            # Both channels.
            text = f"V: {config.voltage_channel.physical_channel}   I: {config.current_channel.physical_channel}"
        # Not set up.
        else:
            # Hint.
            text = "AO channels not set up"
        # Show it.
        self.w["lblStatusChannels"].setText(text)
        # AO volts text (only values written so far).
        parts = [f"{'V' if q == VOLTAGE else 'I'} {self._ao[q]:.3f} V" for q in QUANTITIES if q in self._ao]
        # Show it.
        self.w["lblStatusAo"].setText("AO sent: " + ("   ".join(parts) if parts else "-"))

    def show_message(self, text: str, level: str = "info") -> None:
        """
        Show a message in the status bar.

        Inputs:
            text (str): Message.
            level (str): "info", "warning" or "error" (color from the theme).
        """
        # Status messages are visible in the session trace without creating a file log.
        activity_trace.add(f"{level.upper()} | {text}")
        # Color per level.
        color = {"error": self._colors.get("error", "#EB5757"),
                 "warning": self._colors.get("warning", "#F2994A")}.get(level)
        # Escaped text.
        safe = html.escape(text)
        # Colored for warnings and errors.
        self.w["lblStatusMessage"].setText(f"<span style='color:{color}'>{safe}</span>" if color else safe)
        # Log errors and warnings.
        if level != "info":
            # Log line.
            _log.log(40 if level == "error" else 30, text)

    # ------------------------------------------------------------------ start-up / settings

    def open_log(self) -> LogWindow:
        """Open or focus the nonmodal session activity log."""
        if self.log_window is None:
            self.log_window = LogWindow(self)
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()
        return self.log_window

    def start(self) -> None:
        """Start-up actions after the window is shown: set up the AO channels, open Settings if needed."""
        # No backend at all.
        if self._service is None:
            # Explain.
            self.show_message("No DAQ backend available - check the installation.", "error")
        # Driver missing.
        elif not self._service.state.driver.available:
            # Explain.
            self.show_message(f"Driver not available: {self._service.state.driver.message}", "error")
        # Normal case: set up the channels.
        else:
            # Progress text.
            self.show_message("Setting up AO channels ...")
            # Queue the configuration.
            self._bridge.when_done(self._service.configure(copy.deepcopy(self._ctx.config)),
                                   self._on_startup_configured)
        # First start: open Settings directly (plan 9.3).
        if self._first_start:
            # Open with the first-start hint.
            self.open_settings(first_start=True)

    def _on_startup_configured(self, future: Future) -> None:
        """
        Result of the start-up configuration (UI thread).

        Inputs:
            future (Future): Finished configure future.
        """
        # Read the result.
        try:
            # (ok, errors, warnings).
            ok, errors, _warnings = future.result()
        # Unexpected failure.
        except Exception as exc:  # noqa: BLE001
            # Treat as failed.
            ok, errors = False, [str(exc)]
        # Success is handled by the configured event.
        if ok or self._first_start:
            # Nothing more to do.
            return
        # Device missing: offer Settings (plan 9.3).
        if any("not present" in message for message in errors) and self.offer_settings(errors):
            # Open Settings.
            self.open_settings()

    def offer_settings(self, errors) -> bool:
        """
        Ask whether the Settings window should be opened.

        Inputs:
            errors (List[str]): Reasons shown to the user.

        Returns:
            bool: True = open Settings.
        """
        # Ask the user.
        answer = QMessageBox.question(self, "AO channels not ready",
                                      "\n".join(errors) + "\n\nOpen Settings now?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.Yes)
        # Yes = open.
        return answer == QMessageBox.StandardButton.Yes

    def open_settings(self, first_start: bool = False) -> SettingsWindow:
        """
        Action: open the Settings window (modal, non-blocking).

        Inputs:
            first_start (bool): Show the first-start hint.

        Returns:
            SettingsWindow: The open dialog.
        """
        activity_trace.add("Settings opened")
        # Bring an already open dialog to the front.
        if self.settings_dialog is not None and self.settings_dialog.isVisible():
            # Activate it.
            self.settings_dialog.raise_()
            # Return it.
            return self.settings_dialog
        # Create the dialog.
        dialog = SettingsWindow(self._ctx, self._service, parent=self, first_start=first_start)
        # Refresh the panel after Save.
        dialog.settings_saved.connect(self.apply_config)
        # Forget the dialog when it closes.
        dialog.finished.connect(lambda _r: setattr(self, "settings_dialog", None))
        # Modal: the panel is blocked while Settings is open.
        dialog.setModal(True)
        # Show without blocking this function.
        dialog.show()
        # Keep a reference.
        self.settings_dialog = dialog
        # Return it.
        return dialog

    # ------------------------------------------------------------------ sequence

    def open_sequence(self) -> SequenceWindow:
        """
        Action: open the Sequence window (non-modal; created once, then reused).

        Returns:
            SequenceWindow: The window.
        """
        activity_trace.add("Sequence window opened")
        # Create on first use.
        if self.sequence_window is None:
            # New window with this panel as parent.
            self.sequence_window = SequenceWindow(self._ctx, self._service, parent=self)
            # Lock / unlock the panel.
            self.sequence_window.running_changed.connect(self._on_sequence_running)
            # Follow the sequence values.
            self.sequence_window.values_changed.connect(self._on_sequence_values)
        # Show and bring to the front.
        self.sequence_window.show()
        # Front.
        self.sequence_window.raise_()
        # Keyboard focus.
        self.sequence_window.activateWindow()
        # Return it.
        return self.sequence_window

    def _on_sequence_running(self, running: bool) -> None:
        """
        A sequence started or ended.

        Inputs:
            running (bool): True = started.
        """
        # Store the state.
        self.sequence_running = running
        # Sequences ignore the front-panel slew limit (D-07): stop panel ramps.
        self._stop_ramps()
        # Lock / unlock.
        self._refresh_state()
        # Status text.
        self.show_message("Sequence running - front panel locked. Output OFF stops the sequence."
                          if running else "Sequence ended - front panel unlocked.")

    def _on_sequence_values(self, values: dict) -> None:
        """
        Show the values commanded by the sequence on the displays.

        Inputs:
            values (dict): {quantity: PS value}.
        """
        # Each quantity.
        for quantity, value in values.items():
            # Unknown quantities are ignored.
            if quantity not in self.controls:
                # Next.
                continue
            # New setpoint (no write - the sequence already wrote it).
            self.controls[quantity].set_target(value)
            # Panel ramps continue from here after the sequence (hold last value).
            self.ramps[quantity].start(value, value)
            # Show it.
            self._show_target(quantity)

    # ------------------------------------------------------------------ setpoint input

    def _on_dial(self, quantity: str, position: int) -> None:
        """
        Handler: knob turned.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            position (int): Knob position.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Convert and apply.
        self.set_target(quantity, self.controls[quantity].value_from_dial(position))

    def _on_entry(self, quantity: str, value: float) -> None:
        """
        Handler: numeric entry confirmed (Enter / focus loss / arrows).

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            value (float): Entered value.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Apply.
        self.set_target(quantity, value)

    def _on_step_size(self, quantity: str) -> None:
        """
        Handler: step size changed.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Selected step.
        step = float(self._sec(quantity, "cbStepSize").currentData())
        # Store it in the section.
        self.controls[quantity].step = step
        # Store it as preference.
        if quantity == VOLTAGE:
            # Voltage step.
            self._ctx.config.ui.voltage_step = step
        # Current section.
        else:
            # Current step.
            self._ctx.config.ui.current_step = step
        # Refill (knob range and decimals depend on the step).
        self._fill_section(quantity)

    def nudge(self, quantity: str, direction: int) -> None:
        """
        Action: step button.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            direction (int): +1 or -1.
        """
        # Change by one step and apply.
        self.set_target(quantity, self.controls[quantity].nudge(direction))

    def set_target(self, quantity: str, value: float) -> float:
        """
        Set a new setpoint, show it and send it (jump or ramp).

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            value (float): Requested setpoint (clamped).

        Returns:
            float: Stored setpoint.
        """
        # Store the clamped value.
        target = self.controls[quantity].set_target(value)
        self._manual_targets[quantity] = target
        activity_trace.add(f"Manual {quantity} setpoint: {target:g} {self.controls[quantity].unit}")
        # Show it.
        self._show_target(quantity)
        # Nothing to send without set-up channels.
        if not self.configured or self._service is None:
            # Stored only.
            return target
        # Ramp when the output is ON and the slew limit is enabled.
        if self.output_on and self._ctx.config.safety.slew_limit_enabled:
            # Continue from the current value toward the new target.
            self.ramps[quantity].retarget(target)
            # Make sure the timer runs.
            self._start_ramp_timer()
        # Otherwise jump (written when ON, stored when OFF).
        else:
            # Send the setpoint.
            self._send(quantity, target)
        # Return the setpoint.
        return target

    def _send(self, quantity: str, value: float) -> None:
        """
        Send one setpoint to the service and remember it as the ramp position.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            value (float): Value in PS units.
        """
        # The ramp continues from what was sent last.
        self.ramps[quantity].start(value, value)
        # Queue the command; errors are shown when it finishes.
        self._bridge.when_done(self._service.set_setpoint(quantity, value), self._check_result)

    def _check_result(self, future: Future) -> None:
        """
        Show the error of a finished command, if any (UI thread).

        Inputs:
            future (Future): Finished command future.
        """
        # Error of the command (None = OK).
        exc = future.exception()
        # Show errors.
        if exc is not None:
            # Error text in the status bar.
            self.show_message(str(exc), "error")

    # ------------------------------------------------------------------ slew ramp

    def _start_ramp_timer(self) -> None:
        """Start the ramp timer if it is not running."""
        # Already running.
        if self._ramp_timer.isActive():
            # Nothing to do.
            return
        # Reference time for the first interval.
        self._last_tick = time.monotonic()
        # Start.
        self._ramp_timer.start()

    def _stop_ramps(self) -> None:
        """Stop the timer and cancel all ramps where they are."""
        # Stop the timer.
        self._ramp_timer.stop()
        # Cancel each ramp.
        for ramp in self.ramps.values():
            # Target = actual.
            ramp.cancel()

    def _on_ramp_tick(self, dt: Optional[float] = None) -> None:
        """
        Timer handler: advance every active ramp and send the new values.

        Inputs:
            dt (Optional[float]): Elapsed seconds (tests); None = measured.
        """
        # Current time.
        now = time.monotonic()
        # Elapsed time since the last tick.
        elapsed = (now - self._last_tick) if dt is None else dt
        # Remember the time.
        self._last_tick = now
        # Ramps only run while the output is ON.
        if not self.output_on or self._service is None:
            # Stop everything.
            self._stop_ramps()
            # Done.
            return
        # Advance each active ramp.
        for quantity, ramp in self.ramps.items():
            # Skip finished ramps.
            if not ramp.active:
                # Next quantity.
                continue
            # New value.
            value = ramp.step(elapsed)
            # Send it (without restarting the ramp).
            self._bridge.when_done(self._service.set_setpoint(quantity, value), self._check_result)
        # Stop the timer when all ramps are finished.
        if not any(ramp.active for ramp in self.ramps.values()):
            # Stop.
            self._ramp_timer.stop()

    # ------------------------------------------------------------------ output

    def toggle_output(self) -> None:
        """Action: Output button (stops a running sequence)."""
        # A running sequence is stopped instead (its end handling switches the output OFF).
        if self.sequence_running and self.sequence_window is not None:
            # Stop the sequence.
            self.sequence_window.stop_run()
            # Status text.
            self.show_message("Stopping sequence - output OFF.")
            # Done.
            return
        # Switch to the other state.
        if self.output_on:
            # OFF.
            self.switch_output_off()
        # Output is OFF.
        else:
            # ON.
            self.switch_output_on()

    def switch_output_on(self) -> None:
        """Switch the output ON: jump to the setpoints, or ramp from the safe value (D-07)."""
        # Only with set-up channels.
        if not self.configured or self._service is None:
            # Explain.
            self.show_message("Output cannot be switched ON - AO channels not set up.", "warning")
            # Done.
            return
        # Cancel leftovers.
        self._stop_ramps()
        # Slew limit setting.
        slew = self._ctx.config.safety.slew_limit_enabled
        # Prepare both channels.
        for quantity in QUANTITIES:
            # Section state.
            control = self.controls[quantity]
            # Start value: safe value (ramp) or setpoint (jump).
            start = control.clamp(self._ctx.config.safety.safe_value_startup) if slew else control.target
            # Store the start value in the service (written by set_output).
            self._send(quantity, start)
            # Ramp toward the setpoint.
            self.ramps[quantity].start(start, control.target)
        # Output is ON from now on (corrected if the command fails).
        self.output_on = True
        # Queue Output ON (after the setpoints, same queue).
        self._bridge.when_done(self._service.set_output(True), self._on_output_done)
        # Start ramping when needed.
        if slew:
            # Timer.
            self._start_ramp_timer()
        # Update LED and button.
        self._refresh_led()

    def switch_output_off(self) -> None:
        """Switch the output OFF: cancel ramps, safe value (service)."""
        # Cancel ramps.
        self._stop_ramps()
        # Output is OFF from now on.
        self.output_on = False
        # Queue Output OFF.
        if self._service is not None:
            # Command.
            self._bridge.when_done(self._service.set_output(False), self._check_result)
        # Update LED and button.
        self._refresh_led()
        # Status text.
        self.show_message("Output OFF - safe value applied.")

    def _on_output_done(self, future: Future) -> None:
        """
        Result of Output ON (UI thread).

        Inputs:
            future (Future): Finished set_output future.
        """
        # Error of the command.
        exc = future.exception()
        # Failure: back to OFF.
        if exc is not None:
            # Stop ramps.
            self._stop_ramps()
            # Output OFF.
            self.output_on = False
            # Update LED.
            self._refresh_led()
            # Show the reason.
            self.show_message(f"Output ON failed: {exc}", "error")
        # Success.
        else:
            # Status text.
            self.show_message("Output ON.")

    # ------------------------------------------------------------------ service events

    def _on_daq_event(self, event) -> None:
        """
        Handler: DAQ service event (UI thread).

        Inputs:
            event (DaqEvent): Service event.
        """
        # Value written.
        if event.kind == EVT_WRITTEN:
            # Remember the AO volts.
            self._ao[event.data["quantity"]] = event.data["ao"]
            # Section of the quantity.
            quantity = event.data["quantity"]
            # Show the PS value sent (differs from the setpoint while ramping).
            self._sec(quantity, "lblActual").setText(
                f"Sent: {self.controls[quantity].display_text(event.data['ps'])} {self.controls[quantity].unit}")
            # Status bar.
            self._refresh_status()
        # Output state changed in the service.
        elif event.kind == EVT_OUTPUT:
            # OFF from the service (error, configure, ...): stop ramps.
            if not event.data["on"]:
                # Cancel.
                self._stop_ramps()
            # Follow the service.
            self.output_on = bool(event.data["on"])
            # Update LED.
            self._refresh_led()
        # Error.
        elif event.kind == EVT_ERROR:
            # Show it.
            self.show_message(event.data.get("message", "DAQ error"), "error")
        # Configuration result (start-up or Settings → Save).
        elif event.kind == EVT_CONFIGURED:
            # Handle it.
            self._on_configured(event.data)

    def _on_configured(self, data: dict) -> None:
        """
        Handle a configure result: enable controls and store the setpoints.

        Inputs:
            data (dict): {"ok": bool, "errors": [...], "warnings": [...]}.
        """
        # New state.
        self.configured = bool(data.get("ok"))
        # Configure always switches the output OFF.
        self._stop_ramps()
        # Output OFF.
        self.output_on = False
        # Success.
        if self.configured:
            # Store both setpoints in the service (output stays OFF).
            for quantity in QUANTITIES:
                # Send.
                self._send(quantity, self.controls[quantity].target)
            # Status text.
            self.show_message("AO channels ready - output OFF.")
        # Failure.
        else:
            # Show the reasons.
            self.show_message("AO channels not set up: " + "; ".join(data.get("errors", [])), "warning")
        # Section texts show the output state.
        for quantity in QUANTITIES:
            # Reset the "Sent" text.
            self._sec(quantity, "lblActual").setText("")
        # Enabled state, LED, status.
        self._refresh_state()

    # ------------------------------------------------------------------ closing

    def _save_preferences(self) -> None:
        """Save manual setpoints and window preferences to settings.json."""
        # Window rectangle.
        geometry = self.geometry()
        # Store as a JSON list.
        self._ctx.config.ui.window_geometry = [geometry.x(), geometry.y(), geometry.width(), geometry.height()]
        self._ctx.config.ui.last_manual_voltage = self._manual_targets[VOLTAGE]
        self._ctx.config.ui.last_manual_current = self._manual_targets[CURRENT]
        # Save the active configuration.
        try:
            # Write the file.
            self._ctx.manager.save(self._ctx.config)
        # A failed preference save must be visible even without a console.
        except OSError as exc:
            _log.error("Preferences not saved: %s", exc)
            QMessageBox.warning(self, "Preferences not saved",
                                f"Could not save the manual setpoints and window preferences:\n{exc}\n\n"
                                "The application will close and apply the exit safe value.")

    def confirm_exit(self) -> bool:
        """
        Ask before closing while the output is ON.

        Returns:
            bool: True = close.
        """
        # Ask the user.
        answer = QMessageBox.question(self, "Output is ON",
                                      "The output is ON. Switch it OFF (exit safe value) and close PS Control?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        # Yes = close.
        return answer == QMessageBox.StandardButton.Yes

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt method name)
        """
        Qt close handler: confirm when the output is ON, save preferences.

        Inputs:
            event (QCloseEvent): Close event.
        """
        # Output ON and the user says No: stay open.
        if self.output_on and not self.confirm_exit():
            # Cancel the close.
            event.ignore()
            # Done.
            return
        # Sequence window: stop a running sequence (already confirmed above), then close it.
        if self.sequence_window is not None:
            # Stop and wait for the end state (no question - exit was confirmed).
            self.sequence_window.stop_and_wait()
            # Close it; this may ask about unsaved sequence changes.
            self.sequence_window.close()
            # The user kept it open: keep the panel open too.
            if self.sequence_window.isVisible():
                # Cancel.
                event.ignore()
                # Done.
                return
        # Stop ramping.
        self._stop_ramps()
        # Save UI preferences.
        self._save_preferences()
        # Close an open Settings dialog without asking.
        if self.settings_dialog is not None:
            # Do not ask about unsaved changes.
            self.settings_dialog.confirm_discard = lambda: True
            # Close it.
            self.settings_dialog.close()
        # Close normally (main.py closes the service: exit safe value).
        event.accept()
