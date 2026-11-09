"""
tests/test_settings_editor.py
=============================
Unit tests for core/settings_editor.py (Settings window logic, no Qt).
"""

# Import pytest for fixtures and float comparison.
import pytest

# Import the catalog loader.
from core.catalogs import load_all_catalogs
# Import the config manager.
from core.config_manager import ConfigManager
# Import the device type.
from core.daq_backend import DeviceInfo
# Import the code under test.
from core.settings_editor import CURRENT, VOLTAGE, SettingsEditor, format_range

# Two scanned devices: a +-10 V module and a module with two ranges.
DEVICES = [
    # NI 9263-like module.
    DeviceInfo("cDAQ1Mod1", "NI 9263", "1", ["cDAQ1Mod1/ao0", "cDAQ1Mod1/ao1"], [(-10.0, 10.0)]),
    # Module with a +-5 V and a +-10 V range.
    DeviceInfo("Dev2", "PCIe-6738", "2", ["Dev2/ao0"], [(-5.0, 5.0), (-10.0, 10.0)]),
]


@pytest.fixture
def editor(app_root):
    """Editor on the default configuration with the shipped catalogs."""
    # Manager on the temporary root.
    manager = ConfigManager(app_root, "test")
    # Default configuration.
    config = manager.load().config
    # Create the editor.
    return SettingsEditor(config, load_all_catalogs(app_root.presets_dir), manager.load_defaults_dict())


def test_working_copy_does_not_change_original(app_root):
    """Edits change only the working copy."""
    # Manager on the temporary root.
    manager = ConfigManager(app_root, "test")
    # Original configuration.
    config = manager.load().config
    # Editor.
    ed = SettingsEditor(config, load_all_catalogs(app_root.presets_dir), manager.load_defaults_dict())
    # Change the PS max.
    ed.set_ps_range(VOLTAGE, 0, 30)
    # Original unchanged.
    assert config.voltage_channel.ps_max == 20.0


def test_channel_options_from_scan(editor):
    """Options: 'not assigned' + every scanned AO channel."""
    # Store the scan.
    editor.set_devices(DEVICES)
    # Channel names in order.
    names = [o.physical_channel for o in editor.channel_options(VOLTAGE)]
    # Expected list.
    assert names == ["", "cDAQ1Mod1/ao0", "cDAQ1Mod1/ao1", "Dev2/ao0"]


def test_missing_configured_channel_stays_visible(editor):
    """A configured channel not in the scan is listed as '(not present)'."""
    # Configure a channel of an unknown device.
    editor.assign_channel(VOLTAGE, "Dev9/ao0")
    # Store the scan.
    editor.set_devices(DEVICES)
    # Last option.
    last = editor.channel_options(VOLTAGE)[-1]
    # Marked as missing.
    assert last.physical_channel == "Dev9/ao0" and not last.present and "not present" in last.label


def test_assign_channel_takes_device_and_range(editor):
    """Assigning sets the device name; unsupported device range -> widest range."""
    # Store the scan.
    editor.set_devices(DEVICES)
    # Pretend the old range was something Dev2 does not have.
    editor.channel(VOLTAGE).device_ao_min, editor.channel(VOLTAGE).device_ao_max = -20.0, 20.0
    # Assign a Dev2 channel.
    editor.assign_channel(VOLTAGE, "Dev2/ao0")
    # Device name.
    assert editor.channel(VOLTAGE).device == "Dev2"
    # Widest range of Dev2.
    assert (editor.channel(VOLTAGE).device_ao_min, editor.channel(VOLTAGE).device_ao_max) == (-10.0, 10.0)
    # Range list of Dev2.
    assert editor.device_ranges(VOLTAGE) == [(-5.0, 5.0), (-10.0, 10.0)]


def test_span_presets(editor):
    """Fixed preset sets min/max; custom keeps the values (D-05)."""
    # 0-5 V preset.
    editor.apply_span_preset(VOLTAGE, "0_5")
    # Values from the preset.
    assert (editor.channel(VOLTAGE).ao_min, editor.channel(VOLTAGE).ao_max) == (0.0, 5.0)
    # Not custom.
    assert not editor.is_custom_span(VOLTAGE)
    # Custom keeps 0-5.
    editor.apply_span_preset(VOLTAGE, "custom")
    # Still 0-5 and now custom.
    assert editor.channel(VOLTAGE).ao_max == 5.0 and editor.is_custom_span(VOLTAGE)
    # Free values switch to custom.
    editor.apply_span_preset(VOLTAGE, "0_10")
    # Set free values.
    editor.set_custom_span(VOLTAGE, 1, 5)
    # Preset is custom.
    assert editor.channel(VOLTAGE).ao_span_preset == "custom"


def test_preview_plan_example(editor):
    """0-20 V on 0-10 V: slope 0.5; 50 % -> 10 V -> 5 V AO."""
    # Calculate.
    preview = editor.preview(VOLTAGE)
    # Slope.
    assert preview.valid and preview.slope == pytest.approx(0.5)
    # Middle row from scaling_preview.json (0, 25, 50, 75, 100).
    assert preview.rows[2] == pytest.approx((50.0, 10.0, 5.0))
    # No PS-min note.
    assert preview.note == ""


def test_preview_non_zero_min_note(editor):
    """D-06: PS min 2 V shows the offset note."""
    # Set 2-20 V.
    editor.set_ps_range(VOLTAGE, 2, 20)
    # Calculate.
    preview = editor.preview(VOLTAGE)
    # Note present and offset negative.
    assert "PS min is 2" in preview.note and preview.offset == pytest.approx(-10 / 9)


def test_preview_invalid(editor):
    """Invalid PS range gives errors instead of rows."""
    # Max below min.
    editor.set_ps_range(CURRENT, 10, 5)
    # Calculate.
    preview = editor.preview(CURRENT)
    # Not valid, with an error.
    assert not preview.valid and preview.errors


def test_validate_requires_channels(editor):
    """Save needs both channels: unassigned = error."""
    # Validate the defaults.
    errors, warnings = editor.validate()
    # Two assignment errors.
    assert sum("no AO channel assigned" in e for e in errors) == 2
    # Not scanned hint.
    assert any("not scanned" in w for w in warnings)


def test_validate_ok_after_assign_and_scan(editor):
    """Assigned channels on scanned hardware validate without errors."""
    # Store the scan.
    editor.set_devices(DEVICES)
    # Voltage channel.
    editor.assign_channel(VOLTAGE, "cDAQ1Mod1/ao0")
    # Current channel.
    editor.assign_channel(CURRENT, "cDAQ1Mod1/ao1")
    # Validate.
    errors, warnings = editor.validate()
    # No errors and no warnings.
    assert errors == [] and warnings == []


def test_validate_hardware_mismatch(editor):
    """A channel missing in the scan is an error after scanning."""
    # Assign channels, one on an unknown device.
    editor.assign_channel(VOLTAGE, "Dev9/ao0")
    # Current channel.
    editor.assign_channel(CURRENT, "cDAQ1Mod1/ao1")
    # Store the scan.
    editor.set_devices(DEVICES)
    # Validate.
    errors, _ = editor.validate()
    # Not-present error.
    assert any("not present" in e for e in errors)


def test_dirty_and_mark_saved(editor):
    """is_dirty() follows edits and mark_saved()."""
    # Fresh editor.
    assert not editor.is_dirty()
    # Edit.
    editor.set_safety(0, 0, True, 2, 5)
    # Dirty.
    assert editor.is_dirty()
    # Mark saved.
    editor.mark_saved()
    # Clean again.
    assert not editor.is_dirty()


def test_restore_defaults_keeps_ui(editor):
    """Restore Defaults resets values but keeps UI preferences."""
    # Change a value and the theme.
    editor.set_ps_range(VOLTAGE, 0, 60)
    # Theme preference.
    editor.config.ui.theme = "light"
    # Restore.
    editor.restore_defaults()
    # Value back to default.
    assert editor.channel(VOLTAGE).ps_max == 20.0
    # Theme kept.
    assert editor.config.ui.theme == "light"


def test_backend_options_from_catalog(editor):
    """Backend choices: auto + every catalog item."""
    # Ids only.
    ids = [option[0] for option in editor.backend_options()]
    # Expected order.
    assert ids == ["auto", "nidaqmx", "simulation"]


def test_format_range():
    """format_range() gives compact text."""
    # Check the text.
    assert format_range((-10.0, 10.0)) == "-10 .. 10 V"
