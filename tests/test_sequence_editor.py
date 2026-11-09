"""
tests/test_sequence_editor.py
=============================
Unit tests for core/sequence_editor.py (Sequence window logic, no Qt).
"""

# Import pytest for fixtures.
import pytest

# Import the catalog loader.
from core.catalogs import load_all_catalogs
# Import the config manager.
from core.config_manager import ConfigManager
# Import the code under test.
from core.sequence_editor import SequenceEditor, format_number, parse_number


@pytest.fixture
def editor(app_root):
    """Editor on the default settings."""
    # Default settings.
    config = ConfigManager(app_root, "test").load().config
    # Create the editor.
    return SequenceEditor(config, load_all_catalogs(app_root.presets_dir), app_root.sequences_dir)


def test_parse_and_format_numbers():
    """Decimal comma accepted; empty / invalid text gives None."""
    # Comma.
    assert parse_number(" 2,5 ") == 2.5
    # Empty.
    assert parse_number("") is None
    # Invalid.
    assert parse_number("abc") is None
    # Format.
    assert (format_number(10.0), format_number(None)) == ("10", "")


def test_new_sequence_defaults(editor):
    """New sequence: one row with settings / catalog defaults, not dirty."""
    # One row.
    row = editor.sequence.steps[0]
    # Defaults: 1 s, PS min, Set, current unchanged.
    assert (row.duration, row.unit, row.voltage, row.current, row.action) == (1.0, "s", 0.0, None, "set")
    # Defaults of the sequence.
    assert (editor.sequence.end_state, editor.sequence.repeat_count, editor.dirty) == ("output_off", 1, False)


def test_row_tools(editor):
    """Add copies the last row; insert, duplicate, move and delete keep the order right."""
    # Change row 1 so copies are recognizable.
    editor.set_cell(0, "voltage", "5")
    # Add copies the last row.
    assert editor.add_row() == 1 and editor.sequence.steps[1].voltage == 5.0
    # Make row 2 different.
    editor.set_cell(1, "voltage", "7")
    # Duplicate row 2.
    assert editor.duplicate_row(1) == 2
    # Move row 3 up.
    assert editor.move_row(2, -1) == 1
    # Delete row 1.
    editor.delete_row(0)
    # Remaining voltages.
    assert [s.voltage for s in editor.sequence.steps] == [7.0, 7.0]
    # Insert at the top.
    assert editor.insert_row(0) == 0 and len(editor.sequence.steps) == 3
    # Moving outside the table does nothing.
    assert editor.move_row(0, -1) == 0
    # Dirty after edits.
    assert editor.dirty


def test_set_cell_current_empty_means_unchanged(editor):
    """Empty current text stores None; invalid voltage gives a validation error."""
    # Current 50 A.
    editor.set_cell(0, "current", "50")
    # Empty again.
    editor.set_cell(0, "current", "")
    # None = unchanged.
    assert editor.sequence.steps[0].current is None
    # Invalid voltage text.
    editor.set_cell(0, "voltage", "ten")
    # Validation error on the voltage.
    assert any(i.field == "voltage" for i in editor.validate())


def test_save_load_and_range_warning(editor):
    """Save names the sequence after the file; changed PS ranges give a warning on load."""
    # Save.
    path = editor.save(editor.sequences_dir / "ramp_test")
    # File and name.
    assert path.name == "ramp_test.json" and editor.sequence.name == "ramp_test" and not editor.dirty
    # Relative path for settings.json.
    assert editor.relative_path().replace("\\", "/") == "sequences/ramp_test.json"
    # Change the PS range afterwards.
    editor.config.voltage_channel.ps_max = 40.0
    # Load again.
    editor.load(path)
    # Warning shown.
    assert "ranges changed" in editor.load_warning


def test_options_mark_dirty_only_on_change(editor):
    """set_options marks dirty only when a value really changes."""
    # Same values.
    editor.set_options(end_state="output_off", repeat_count=1)
    # Not dirty.
    assert not editor.dirty
    # New value.
    editor.set_options(repeat_count=0)
    # Dirty.
    assert editor.dirty


def test_headers_show_ranges(editor):
    """Column titles show unit and PS range (section 10.2)."""
    # Headers.
    headers = editor.column_headers()
    # Voltage and current titles.
    assert headers["voltage"] == "Voltage [V] (0–20)" and headers["current"] == "Current [A] (0–100)"
