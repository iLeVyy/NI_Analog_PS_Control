"""
tests/test_config_manager.py
============================
Unit tests for core/config_manager.py (Project_Plan.md section 9).
"""

# Import json to inspect and prepare settings files.
import json

# Import pytest for float comparison and exceptions.
import pytest

# Import the code under test.
from core.config_manager import (ConfigError, ConfigManager, STATUS_CREATED, STATUS_LOADED,
                                 STATUS_RESET_INVALID, deep_merge)


def test_first_start_creates_settings(app_root):
    """Missing settings.json is created from the default template."""
    # Load with no settings file present.
    result = ConfigManager(app_root, "0.1.0").load()
    # Status must be "created".
    assert result.status == STATUS_CREATED
    # The file must now exist.
    assert app_root.settings_file.is_file()


def test_defaults_match_decisions(app_root):
    """Defaults: 0-10 V span, PS min 0, slew 1 V/s and 5 A/s, 50 ms, software timing."""
    # Load the defaults.
    config = ConfigManager(app_root, "0.1.0").load().config
    # D-05 default span.
    assert (config.voltage_channel.ao_min, config.voltage_channel.ao_max) == (0.0, 10.0)
    # D-06 PS min default.
    assert config.voltage_channel.ps_min == 0.0
    # D-07 slew-rate defaults.
    assert (config.safety.slew_rate_voltage, config.safety.slew_rate_current) == (1.0, 5.0)
    # D-07 slew limit off by default.
    assert config.safety.slew_limit_enabled is False
    # D-01 minimum step and timing mode.
    assert (config.sequence.min_step_duration_ms, config.sequence.timing_mode) == (50, "software")


def test_unassigned_channels_give_warnings_not_errors(app_root):
    """Fresh defaults have no AO channels: warnings only."""
    # Load the defaults.
    result = ConfigManager(app_root, "0.1.0").load()
    # No blocking errors.
    assert result.errors == []
    # Two "no AO channel assigned" warnings.
    assert sum("no AO channel" in w for w in result.warnings) == 2


def test_save_and_reload_round_trip(app_root):
    """Saved values are loaded back and slope/offset are recalculated."""
    # Create the manager.
    manager = ConfigManager(app_root, "0.1.0")
    # Load the defaults.
    config = manager.load().config
    # Change the voltage PS range to 2-20 V.
    config.voltage_channel.ps_min = 2.0
    # Assign two different channels.
    config.voltage_channel.physical_channel = "Dev1/ao0"
    # Assign the current channel.
    config.current_channel.physical_channel = "Dev1/ao1"
    # Save.
    manager.save(config)
    # Load again.
    result = manager.load()
    # Normal load expected.
    assert result.status == STATUS_LOADED
    # The new offset must be recalculated (-10/9).
    assert result.config.voltage_channel.offset == pytest.approx(-10 / 9)
    # The meta group is filled.
    assert result.config.meta.app_version == "0.1.0" and result.config.meta.last_saved


def test_invalid_json_is_backed_up_and_reset(app_root):
    """Broken settings.json -> .bak backup + defaults."""
    # Make sure the config folder exists.
    app_root.config_dir.mkdir(parents=True, exist_ok=True)
    # Write invalid JSON.
    app_root.settings_file.write_text("{ not json", encoding="utf-8")
    # Load.
    result = ConfigManager(app_root, "0.1.0").load()
    # Status must be "reset_invalid".
    assert result.status == STATUS_RESET_INVALID
    # The backup must exist.
    assert (app_root.config_dir / "settings.json.bak").is_file()


def test_missing_keys_are_filled_from_template(app_root):
    """A partial settings.json is completed from the template."""
    # Make sure the config folder exists.
    app_root.config_dir.mkdir(parents=True, exist_ok=True)
    # Write a file that only contains the UI theme.
    app_root.settings_file.write_text(json.dumps({"meta": {"config_version": 1},
                                                  "ui": {"theme": "light"}}), encoding="utf-8")
    # Load.
    config = ConfigManager(app_root, "0.1.0").load().config
    # The user value is kept.
    assert config.ui.theme == "light"
    # Missing values come from the template.
    assert config.voltage_channel.ps_max == 20.0
    assert config.ui.last_manual_voltage is None
    assert config.ui.last_manual_current is None


def test_validation_errors(app_root):
    """Same channel twice, bad span and zero slew rate are reported."""
    # Create the manager.
    manager = ConfigManager(app_root, "0.1.0")
    # Load the defaults.
    config = manager.load().config
    # Same channel for both.
    config.voltage_channel.physical_channel = "Dev1/ao0"
    # Same channel for current.
    config.current_channel.physical_channel = "Dev1/ao0"
    # Span above the device range.
    config.voltage_channel.ao_max = 12.0
    # Invalid slew rate.
    config.safety.slew_rate_current = 0.0
    # Save the bad configuration.
    manager.save(config)
    # Load and validate.
    errors = manager.load().errors
    # Three distinct problems expected.
    assert any("different AO channels" in e for e in errors)
    # Span problem.
    assert any("above the device range" in e for e in errors)
    # Slew rate problem.
    assert any("Current slew rate" in e for e in errors)


def test_missing_template_is_fatal(app_root):
    """Without the default template the app cannot start."""
    # Delete the template.
    app_root.default_settings_file.unlink()
    # Loading must raise ConfigError.
    with pytest.raises(ConfigError):
        # Load.
        ConfigManager(app_root, "0.1.0").load()


def test_deep_merge_does_not_change_inputs():
    """deep_merge() returns a new dict and keeps the inputs unchanged."""
    # Template.
    base = {"a": {"x": 1, "y": 2}}
    # Override.
    override = {"a": {"y": 3}}
    # Merge.
    merged = deep_merge(base, override)
    # Result combines both.
    assert merged == {"a": {"x": 1, "y": 3}}
    # Template unchanged.
    assert base == {"a": {"x": 1, "y": 2}}
