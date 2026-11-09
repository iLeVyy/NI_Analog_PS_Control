"""
tests/test_settings_window_qt.py
================================
GUI tests of the Settings window with a real Qt application (offscreen,
no visible window). Skipped automatically when PySide6 is not installed.
"""

# Import os to select the offscreen Qt platform before Qt is imported.
import os

# Import pytest for fixtures and skipping.
import pytest

# Use the offscreen platform (no display needed) unless already set.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Skip the whole file without PySide6.
pytest.importorskip("PySide6")

# Import the Qt application class (after the skip check).
from PySide6.QtWidgets import QApplication  # noqa: E402

# Import the start-up helpers.
from core.bootstrap import startup  # noqa: E402
# Import the DAQ service.
from core.daq_service import DaqService  # noqa: E402
# Import the simulation backend.
from core.simulation import SimulatedBackend  # noqa: E402
# Import the window under test.
from ui.settings_window import SettingsWindow  # noqa: E402

# Project root (the .ui file is read from the real project).
from tests.conftest import PROJECT_ROOT  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """One Qt application for all tests of this file."""
    # Reuse or create.
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qapp, app_root):
    """
    Settings window on a temporary root with a synchronous simulation service.

    The service is not started, so commands run immediately in the test thread.
    """
    # The temporary root needs the .ui files.
    (app_root.root / "ui").mkdir()
    # Copy the Settings form.
    (app_root.root / "ui" / "settings_window.ui").write_bytes(
        (PROJECT_ROOT / "ui" / "settings_window.ui").read_bytes())
    # Start-up on the temporary root.
    ctx = startup(app_root)
    # Simulation backend.
    backend = SimulatedBackend(paths=app_root)
    # Catalog id.
    backend.backend_id = "simulation"
    # Simulation flag (normally set by the backend factory).
    backend.simulated = True
    # Synchronous service (no worker thread).
    service = DaqService(backend, backend.check_driver())
    # Create the window (scans automatically).
    win = SettingsWindow(ctx, service)
    # Do not show the "unsaved changes" question in tests.
    win.confirm_discard = lambda: True
    # Keep references for the tests.
    win.test_ctx, win.test_backend = ctx, backend
    # Give the window to the test.
    yield win
    # Close the window.
    win.close()
    # Close the service.
    service.close()


def test_window_loads_and_scans(window):
    """The form loads and the automatic scan fills the channel lists."""
    # Channel entries: not assigned + 6 simulated channels.
    assert window.channel_texts("voltage")[:3] == ("", "cDAQ1Mod1/ao0", "cDAQ1Mod1/ao1")
    # Device table rows.
    assert window.w["twDevices"].rowCount() == 2
    # SIMULATION indicator not hidden.
    assert not window.w["lblSimulation"].isHidden()


def test_select_channels_and_save(window):
    """Selecting channels and Save writes settings.json and configures the AO tasks."""
    # Voltage channel ao0.
    window.w["v_cbChannel"].setCurrentIndex(1)
    # Current channel ao1.
    window.w["i_cbChannel"].setCurrentIndex(2)
    # Save.
    assert window.save() is True
    # File written with the channels.
    saved = window.test_ctx.manager.load().config
    # Check the voltage channel.
    assert saved.voltage_channel.physical_channel == "cDAQ1Mod1/ao0"
    # Hardware set to the safe value (0 V on both channels).
    assert window.test_backend.last_values == {"cDAQ1Mod1/ao0": 0.0, "cDAQ1Mod1/ao1": 0.0}
    # Status text.
    assert "AO channels ready" in window.w["lblMessages"].text()


def test_save_blocked_without_channels(window):
    """Save with unassigned channels is refused."""
    # Save the defaults.
    assert window.save() is False
    # Status text.
    assert "Not saved" in window.w["lblMessages"].text()


def test_span_preset_updates_fields_and_preview(window):
    """Choosing 0-5 V updates AO max and the preview (20 V -> 5 V)."""
    # Index of the 0-5 V preset.
    combo = window.w["v_cbSpanPreset"]
    # Select it.
    combo.setCurrentIndex(combo.findData("0_5"))
    # AO max field.
    assert window.w["v_dsbAoMax"].value() == 5.0
    # AO fields disabled for fixed presets.
    assert not window.w["v_dsbAoMax"].isEnabled()
    # Last preview row, AO column.
    assert window.w["v_twPreview"].item(4, 2).text() == "5"


def test_custom_span_enables_fields(window):
    """The custom preset enables the AO min/max fields."""
    # Span selector.
    combo = window.w["i_cbSpanPreset"]
    # Select custom.
    combo.setCurrentIndex(combo.findData("custom"))
    # Fields enabled.
    assert window.w["i_dsbAoMin"].isEnabled() and window.w["i_dsbAoMax"].isEnabled()


def test_ps_min_updates_preview(window):
    """D-06: PS min 2 V -> first preview row AO 0 V, offset note shown."""
    # Set PS min.
    window.w["v_dsbPsMin"].setValue(2.0)
    # First row PS value.
    assert window.w["v_twPreview"].item(0, 1).text() == "2"
    # Note in the scale label.
    assert "PS min is 2" in window.w["v_lblScale"].text()


def test_slew_checkbox_enables_rates(window):
    """Rate fields follow the slew-limit checkbox (D-07)."""
    # Off by default.
    assert not window.w["dsbSlewVoltage"].isEnabled()
    # Switch on.
    window.w["chkSlewLimit"].setChecked(True)
    # Rate fields enabled.
    assert window.w["dsbSlewVoltage"].isEnabled()
    # Stored in the working copy.
    assert window.editor.config.safety.slew_limit_enabled is True


def test_restore_defaults(window):
    """Restore Defaults refills the form with the template values."""
    # Change PS max.
    window.w["v_dsbPsMax"].setValue(60.0)
    # Restore.
    window.restore_defaults()
    # Back to 20 V.
    assert window.w["v_dsbPsMax"].value() == 20.0


def test_threaded_service_results_reach_the_window(qapp, app_root):
    """With a started worker thread, scan and save results arrive through the bridge."""
    # Import time for the wait loop.
    import time
    # The temporary root needs the .ui files.
    (app_root.root / "ui").mkdir()
    # Copy the Settings form.
    (app_root.root / "ui" / "settings_window.ui").write_bytes(
        (PROJECT_ROOT / "ui" / "settings_window.ui").read_bytes())
    # Start-up on the temporary root.
    ctx = startup(app_root)
    # Simulation backend.
    backend = SimulatedBackend(paths=app_root)
    # Threaded service.
    service = DaqService(backend, backend.check_driver())
    # Start the worker thread.
    service.start()
    # Create the window (scan is queued).
    win = SettingsWindow(ctx, service)
    # Do not ask about unsaved changes.
    win.confirm_discard = lambda: True

    def wait_for(condition) -> bool:
        """Process Qt events until condition() is True or 5 s passed."""
        # Time limit.
        end = time.monotonic() + 5
        # Loop until the limit.
        while time.monotonic() < end:
            # Deliver queued signals.
            qapp.processEvents()
            # Stop when the condition is met.
            if condition():
                # Success.
                return True
            # Short pause.
            time.sleep(0.01)
        # Timed out.
        return False

    # Close everything even if an assertion fails.
    try:
        # The scan result arrives.
        assert wait_for(lambda: len(win.channel_texts("voltage")) > 1)
        # Select the channels.
        win.w["v_cbChannel"].setCurrentIndex(1)
        # Current channel.
        win.w["i_cbChannel"].setCurrentIndex(2)
        # Save (configure runs in the worker thread).
        assert win.save()
        # The configure result arrives.
        assert wait_for(lambda: "AO channels ready" in win.w["lblMessages"].text())
    # Clean up.
    finally:
        # Close the window.
        win.close()
        # Stop the service.
        service.close()
