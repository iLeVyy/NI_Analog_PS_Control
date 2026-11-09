"""
tests/test_main_panel_qt.py
===========================
GUI tests of the power supply front panel with a real Qt application
(offscreen) and the simulation backend. Skipped without PySide6.

The DAQ service is not started, so every command runs immediately in the
test thread (results and events arrive synchronously).
"""

# Import os to select the offscreen Qt platform before Qt is imported.
import os
import json

# Import pytest for fixtures, float comparison and skipping.
import pytest

# Use the offscreen platform (no display needed) unless already set.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Skip the whole file without PySide6.
pytest.importorskip("PySide6")

# Import the Qt application class.
from PySide6.QtWidgets import QApplication  # noqa: E402

# Import the start-up helper.
from core.bootstrap import startup  # noqa: E402
# Import the DAQ service.
from core.daq_service import DaqService  # noqa: E402
from core.activity_trace import activity_trace  # noqa: E402
# Import the simulation backend.
from core.simulation import SimulatedBackend  # noqa: E402
# Import the window under test.
from ui.main_panel import MainPanel  # noqa: E402
# Project root (to copy the .ui files).
from tests.conftest import PROJECT_ROOT  # noqa: E402

# Voltage AO channel used in the tests.
V_CH = "cDAQ1Mod1/ao0"
# Current AO channel used in the tests.
I_CH = "cDAQ1Mod1/ao1"


@pytest.fixture(scope="module")
def qapp():
    """One Qt application for all tests of this file."""
    # Reuse or create.
    return QApplication.instance() or QApplication([])


def make_panel(app_root, assign=True, first_start=False):
    """
    Build a front panel on a temporary root with a synchronous simulation service.

    Inputs:
        app_root (AppPaths): Temporary root (conftest fixture).
        assign (bool): Assign ao0 / ao1 before start-up.
        first_start (bool): Passed to the panel.

    Returns:
        MainPanel: Started panel with attributes test_ctx, test_backend, test_service.
    """
    # Folder for the forms.
    (app_root.root / "ui").mkdir(exist_ok=True)
    # Copy both forms.
    for name in ("main_panel.ui", "settings_window.ui"):
        # Copy one form.
        (app_root.root / "ui" / name).write_bytes((PROJECT_ROOT / "ui" / name).read_bytes())
    # Start-up on the temporary root.
    ctx = startup(app_root)
    # Assign the channels when requested.
    if assign:
        # Voltage channel.
        ctx.config.voltage_channel.device, ctx.config.voltage_channel.physical_channel = "cDAQ1Mod1", V_CH
        # Current channel.
        ctx.config.current_channel.device, ctx.config.current_channel.physical_channel = "cDAQ1Mod1", I_CH
    # Simulation backend.
    backend = SimulatedBackend(paths=app_root)
    # Ids normally set by the factory.
    backend.backend_id, backend.simulated = "simulation", True
    # Synchronous service.
    service = DaqService(backend, backend.check_driver())
    # Create the panel.
    panel = MainPanel(ctx, service, first_start=first_start)
    # Never block on message boxes.
    panel.confirm_exit = lambda: True
    # Never block on message boxes.
    panel.offer_settings = lambda errors: False
    # Keep references.
    panel.test_ctx, panel.test_backend, panel.test_service = ctx, backend, service
    # Run the start-up actions.
    panel.start()
    # Return the panel.
    return panel


@pytest.fixture
def panel(qapp, app_root):
    """Configured panel (ao0 / ao1); closed after the test."""
    # Build it.
    win = make_panel(app_root)
    # Give it to the test.
    yield win
    # Output OFF so close does not ask.
    win.output_on = False
    # Close the window.
    win.close()
    # Close the service.
    win.test_service.close()


def test_not_configured_disables_controls(qapp, app_root):
    """Without assigned channels the sections and the output button are disabled."""
    # Panel without channels.
    win = make_panel(app_root, assign=False)
    # Close everything at the end.
    try:
        # Not configured.
        assert not win.configured
        # Sections disabled.
        assert not win.w["v_gbSection"].isEnabled() and not win.w["btnOutput"].isEnabled()
        # Warning shown.
        assert "not set up" in win.w["lblStatusMessage"].text()
    # Clean up.
    finally:
        # Close the window.
        win.close()
        # Close the service.
        win.test_service.close()


def test_configured_enables_controls(panel):
    """After start-up configuration the controls are enabled and the output is OFF."""
    # Configured.
    assert panel.configured and not panel.output_on
    # Enabled.
    assert panel.w["v_gbSection"].isEnabled() and panel.w["btnOutput"].isEnabled()
    # Status bar channels.
    assert V_CH in panel.w["lblStatusChannels"].text()
    # SIMULATION indicator not hidden.
    assert not panel.w["lblSimulation"].isHidden()


def test_log_window_retains_activity_before_open_and_after_reopen(panel):
    """Log is nonmodal and retains recent activity even when it is hidden."""
    panel.set_target("voltage", 7)
    panel.w["btnLog"].click()
    log = panel.log_window
    assert log is not None and log.isVisible() and not log.isModal()
    assert "Manual voltage setpoint: 7" in log.entries.toPlainText()
    log.close()
    panel.set_target("current", 8)
    panel.w["btnLog"].click()
    log.refresh()
    assert panel.log_window is log
    assert "Manual current setpoint: 8" in log.entries.toPlainText()
    assert "DAQ configure: ready" in log.entries.toPlainText()


def test_log_window_shows_only_latest_50_entries(panel):
    """The displayed trace is capped even after a burst while the log is closed."""
    activity_trace.clear()
    for i in range(51):
        activity_trace.add(f"test entry {i}")
    log = panel.open_log()
    lines = log.entries.toPlainText().splitlines()
    assert len(lines) == 50
    assert lines[0].endswith("test entry 1")
    assert lines[-1].endswith("test entry 50")


def test_manual_setpoints_restore_with_output_off(qapp, app_root):
    """After closing, only manually selected values are restored without output writes."""
    first = make_panel(app_root)
    first.set_target("voltage", 12.5)
    first.set_target("current", 42)
    first._on_sequence_values({"voltage": 18, "current": 70})
    first.toggle_output()
    assert first.output_on
    first.close()
    first.test_service.close()
    second = make_panel(app_root)
    try:
        assert second.controls["voltage"].target == pytest.approx(12.5)
        assert second.controls["current"].target == pytest.approx(42)
        assert second.w["v_dsbEntry"].value() == pytest.approx(12.5)
        assert second.w["i_dsbEntry"].value() == pytest.approx(42)
        assert not second.output_on and not second.test_service.state.output_on
        assert second.test_backend.history == [(V_CH, 0.0), (I_CH, 0.0)]
        assert second.test_service.state.setpoints == {"voltage": 12.5, "current": 42}
    finally:
        second.close()
        second.test_service.close()


@pytest.mark.parametrize("saved", [None, -1, 21, "bad", True, float("nan"), float("inf"), 10 ** 400])
def test_invalid_saved_setpoint_uses_channel_minimum(qapp, app_root, saved):
    """Absent, invalid, nonfinite and out-of-range values fall back to PS minimum."""
    from core.config_manager import ConfigManager
    manager = ConfigManager(app_root, "test")
    config = manager.load().config
    config.voltage_channel.ps_min = 2.0
    config.ui.last_manual_voltage = saved
    manager.save(config)
    win = make_panel(app_root)
    try:
        assert win.controls["voltage"].target == 2.0
        assert not win.output_on
    finally:
        win.close()
        win.test_service.close()


def test_settings_save_does_not_replace_manual_targets(panel):
    """A settings working-copy replacement preserves unsaved manual targets."""
    panel.set_target("voltage", 12)
    dialog = panel.open_settings()
    dialog.w["v_dsbPsMax"].setValue(40)
    assert dialog.save()
    dialog.close()
    panel.close()
    saved = json.loads(panel.test_ctx.paths.settings_file.read_text(encoding="utf-8"))
    assert saved["ui"]["last_manual_voltage"] == 12


def test_range_change_resets_out_of_range_manual_target(panel):
    """A settings range change does not leave an invalid saved manual value."""
    panel.set_target("voltage", 15)
    dialog = panel.open_settings()
    dialog.w["v_dsbPsMax"].setValue(10)
    assert dialog.save()
    dialog.close()
    panel.close()
    saved = panel.test_ctx.manager.load().config
    assert saved.ui.last_manual_voltage == saved.voltage_channel.ps_min


def test_cancelled_close_does_not_save_manual_values(panel):
    """Declining the output-on close prompt does not persist the new values."""
    panel.set_target("voltage", 10)
    panel.toggle_output()
    panel.confirm_exit = lambda: False
    panel.show()
    panel.close()
    assert panel.isVisible()
    assert panel.test_ctx.manager.load().config.ui.last_manual_voltage is None
    panel.confirm_exit = lambda: True


def test_save_failure_is_shown_but_close_remains_safe(panel, monkeypatch):
    """A preference write failure is visible and does not block safe shutdown."""
    messages = []
    def fail(_config):
        raise OSError("disk unavailable")
    monkeypatch.setattr(panel.test_ctx.manager, "save", fail)
    monkeypatch.setattr("ui.main_panel.QMessageBox.warning",
                        lambda _parent, title, text: messages.append((title, text)))
    panel.show()
    panel.close()
    assert not panel.isVisible()
    assert messages and "disk unavailable" in messages[0][1]


def test_setpoint_while_off_is_not_written(panel):
    """With the output OFF a setpoint is stored, not written."""
    # Writes so far.
    writes = len(panel.test_backend.history)
    # New setpoint.
    panel.set_target("voltage", 10)
    # No new write.
    assert len(panel.test_backend.history) == writes
    # Service stores it.
    assert panel.test_service.state.setpoints["voltage"] == 10


def test_output_on_jumps_to_setpoints(panel):
    """Plan example: 10 V -> 5 V AO, 50 A -> 5 V AO when switched ON (jump)."""
    # Voltage setpoint.
    panel.set_target("voltage", 10)
    # Current setpoint.
    panel.set_target("current", 50)
    # Output ON.
    panel.toggle_output()
    # AO values.
    assert panel.test_backend.last_values[V_CH] == pytest.approx(5.0)
    # Current AO.
    assert panel.test_backend.last_values[I_CH] == pytest.approx(5.0)
    # LED / button.
    assert panel.output_on and panel.w["btnOutput"].text() == "Output OFF"
    # Change while ON writes immediately.
    panel.set_target("voltage", 20)
    # 20 V -> 10 V AO.
    assert panel.test_backend.last_values[V_CH] == pytest.approx(10.0)


def test_knob_entry_and_display(panel):
    """Knob position, entry and display show the same setpoint."""
    # Turn the knob to position 125 (0.1 V steps).
    panel.w["v_dialKnob"].setValue(125)
    # Setpoint.
    assert panel.controls["voltage"].target == pytest.approx(12.5)
    # Entry.
    assert panel.w["v_dsbEntry"].value() == pytest.approx(12.5)
    # Display.
    assert panel.w["v_lcdSetpoint"].value() == pytest.approx(12.5)
    # Typed value (valueChanged = Enter / focus loss).
    panel.w["v_dsbEntry"].setValue(3.3)
    # Knob follows.
    assert panel.w["v_dialKnob"].value() == 33


def test_step_buttons_and_step_size(panel):
    """Step buttons use the selected step; changing the step changes the knob range."""
    # Step up once (0.1 V).
    panel.w["v_btnStepUp"].click()
    # 0.1 V.
    assert panel.controls["voltage"].target == pytest.approx(0.1)
    # Select the 1 V step.
    combo = panel.w["v_cbStepSize"]
    # Find it.
    combo.setCurrentIndex(combo.findData(1.0))
    # Knob range 0..20.
    assert panel.w["v_dialKnob"].maximum() == 20
    # Preference stored.
    assert panel.test_ctx.config.ui.voltage_step == 1.0


def test_slew_ramp_from_safe_value(panel):
    """D-07: with the slew limit, Output ON ramps from the safe value at the set rates."""
    # Enable the slew limit (1 V/s, 5 A/s defaults).
    panel.test_ctx.config.safety.slew_limit_enabled = True
    # Setpoints.
    panel.set_target("voltage", 10)
    # Current setpoint.
    panel.set_target("current", 50)
    # Output ON.
    panel.toggle_output()
    # Starts at the safe value (0 V AO).
    assert panel.test_backend.last_values[V_CH] == pytest.approx(0.0)
    # One second of ramp.
    panel._on_ramp_tick(dt=1.0)
    # 1 V -> 0.5 V AO.
    assert panel.test_backend.last_values[V_CH] == pytest.approx(0.5)
    # 5 A -> 0.5 V AO.
    assert panel.test_backend.last_values[I_CH] == pytest.approx(0.5)
    # Display shows the target, not the ramp value.
    assert panel.w["v_lcdSetpoint"].value() == pytest.approx(10)
    # "Sent" shows the ramp value.
    assert "1.0 V" in panel.w["v_lblActual"].text()


def test_slew_retarget_and_output_off_cancels(panel):
    """New target continues from the actual value; Output OFF cancels and writes the safe value."""
    # Enable the slew limit.
    panel.test_ctx.config.safety.slew_limit_enabled = True
    # Setpoint 10 V.
    panel.set_target("voltage", 10)
    # Output ON.
    panel.toggle_output()
    # Two seconds: 2 V.
    panel._on_ramp_tick(dt=2.0)
    # New target 0 V: continues from 2 V.
    panel.set_target("voltage", 0)
    # Half a second down: 1.5 V -> 0.75 V AO.
    panel._on_ramp_tick(dt=0.5)
    # Check.
    assert panel.test_backend.last_values[V_CH] == pytest.approx(0.75)
    # Output OFF.
    panel.toggle_output()
    # Ramp stopped.
    assert not any(ramp.active for ramp in panel.ramps.values())
    # Safe value written.
    assert panel.test_backend.last_values[V_CH] == pytest.approx(0.0)


def test_write_error_switches_off(panel):
    """A hardware error during ON: output OFF, LED off, error message."""
    # Output ON.
    panel.toggle_output()
    # Next write fails.
    panel.test_backend.fail_next_writes = 1
    # New setpoint (write fails).
    panel.set_target("voltage", 5)
    # Output OFF.
    assert not panel.output_on and panel.w["lblOutputState"].text() == "OFF"
    # Error shown.
    assert "failure" in panel.w["lblStatusMessage"].text()


def test_first_start_opens_settings(qapp, app_root):
    """Plan 9.3: on the first start the Settings window opens automatically."""
    # Panel on first start without channels.
    win = make_panel(app_root, assign=False, first_start=True)
    # Close everything at the end.
    try:
        # Dialog open.
        assert win.settings_dialog is not None and win.settings_dialog.isVisible()
    # Clean up.
    finally:
        # Close the window (closes the dialog too).
        win.close()
        # Close the service.
        win.test_service.close()


def test_missing_device_offers_settings(qapp, app_root, monkeypatch):
    """Plan 9.3: configured device missing -> offer to open Settings."""
    # Remember whether the offer was made.
    offered = []
    # Patch the class method so the instance created in make_panel uses it.
    monkeypatch.setattr(MainPanel, "offer_settings", lambda self, errors: offered.append(errors) or True)
    # Folder for the forms.
    (app_root.root / "ui").mkdir(exist_ok=True)
    # Copy both forms.
    for name in ("main_panel.ui", "settings_window.ui"):
        # Copy one form.
        (app_root.root / "ui" / name).write_bytes((PROJECT_ROOT / "ui" / name).read_bytes())
    # Start-up.
    ctx = startup(app_root)
    # Device that does not exist.
    ctx.config.voltage_channel.device, ctx.config.voltage_channel.physical_channel = "Dev9", "Dev9/ao0"
    # Current channel.
    ctx.config.current_channel.device, ctx.config.current_channel.physical_channel = "cDAQ1Mod1", I_CH
    # Backend and synchronous service.
    backend = SimulatedBackend(paths=app_root)
    # Service.
    service = DaqService(backend, backend.check_driver())
    # Panel.
    win = MainPanel(ctx, service)
    # Start-up.
    win.start()
    # Close everything at the end.
    try:
        # Offer made and Settings opened.
        assert offered and win.settings_dialog is not None
    # Clean up.
    finally:
        # Close the window.
        win.close()
        # Close the service.
        service.close()


def test_settings_save_updates_panel(panel):
    """Saving a new PS range in Settings updates the front panel and re-configures."""
    # Open Settings.
    dialog = panel.open_settings()
    # New voltage maximum.
    dialog.w["v_dsbPsMax"].setValue(40.0)
    # Save.
    assert dialog.save()
    # Range text on the panel.
    assert panel.w["v_lblRange"].text() == "Range 0 – 40 V"
    # Still configured.
    assert panel.configured
    # Close the dialog.
    dialog.close()


def test_close_with_output_on_asks(panel):
    """Closing while ON asks; 'No' keeps the window open, 'Yes' closes and saves preferences."""
    # Show the window so isVisible() is meaningful.
    panel.show()
    # Output ON.
    panel.toggle_output()
    # User says No.
    panel.confirm_exit = lambda: False
    # Try to close.
    panel.close()
    # Still open.
    assert panel.isVisible()
    # User says Yes.
    panel.confirm_exit = lambda: True
    # Close.
    panel.close()
    # Closed.
    assert not panel.isVisible()
    # Geometry saved.
    assert panel.test_ctx.manager.load().config.ui.window_geometry is not None
