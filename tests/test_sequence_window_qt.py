"""
tests/test_sequence_window_qt.py
================================
GUI tests of the Sequence window and its link to the front panel
(offscreen Qt, simulation backend). Skipped without PySide6.
"""

# Import os to select the offscreen Qt platform before Qt is imported.
import os

# Import pytest for fixtures, float comparison and skipping.
import pytest

# Use the offscreen platform unless already set.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Skip the whole file without PySide6.
pytest.importorskip("PySide6")

# Import the Qt application class.
from PySide6.QtWidgets import QApplication  # noqa: E402

# Import the engine states.
from core.sequence_engine import STATE_COMPLETED, STATE_STOPPED  # noqa: E402
# Import the model.
from core.sequence_model import Sequence, SequenceStep  # noqa: E402
# Reuse the front-panel test helper.
from tests.test_main_panel_qt import V_CH, make_panel  # noqa: E402


@pytest.fixture(scope="module")
def qapp():
    """One Qt application for all tests of this file."""
    # Reuse or create.
    return QApplication.instance() or QApplication([])


@pytest.fixture
def setup(qapp, app_root):
    """
    Front panel (configured) + open Sequence window; closed after the test.

    Returns:
        Tuple[MainPanel, SequenceWindow].
    """
    # Copy the Sequence form as well.
    (app_root.root / "ui").mkdir(exist_ok=True)
    # Copy it from the project.
    from tests.conftest import PROJECT_ROOT
    # Write the file.
    (app_root.root / "ui" / "sequence_window.ui").write_bytes(
        (PROJECT_ROOT / "ui" / "sequence_window.ui").read_bytes())
    # Panel.
    panel = make_panel(app_root)
    # Sequence window.
    window = panel.open_sequence()
    # Never block on questions.
    window.confirm_discard = lambda: True
    # Never block on questions.
    window.confirm_stop = lambda: True
    # Give both to the test.
    yield panel, window
    # Stop anything still running.
    window.stop_and_wait()
    # Output OFF for a quiet close.
    panel.output_on = False
    # Close the panel (closes the window).
    panel.close()
    # Close the service.
    panel.test_service.close()


def load(window, steps, **options):
    """Put a sequence into the window."""
    # Replace the sequence.
    window.editor.sequence = Sequence(steps=steps, **options)
    # Refill the widgets.
    window._load_all()


def finish(window, timeout=5.0):
    """Wait for the run thread and process the final poll."""
    # Wait.
    assert window.runner.join(timeout)
    # Final poll (normally done by the timer).
    window.poll()


def test_window_opens_with_one_row(setup):
    """New window: one default row, valid, Start enabled, Pause/Stop disabled."""
    # Unpack.
    _panel, window = setup
    # One row.
    assert window.w["twSteps"].rowCount() == 1
    # Valid.
    assert "Sequence is valid" in window.w["lblMessages"].text()
    # Buttons.
    assert window.w["btnStart"].isEnabled() and not window.w["btnStop"].isEnabled()


def test_cell_edit_and_validation_colors(setup):
    """Typing an out-of-range voltage stores it and colors the cell (PS range, not AO)."""
    # Unpack.
    _panel, window = setup
    # Voltage cell of row 1.
    cell = window.w["twSteps"].item(0, 2)
    # 25 V on a 0-20 V supply.
    cell.setText("25")
    # Stored.
    assert window.editor.sequence.steps[0].voltage == 25.0
    # Error message.
    assert "outside the power supply range" in window.w["lblMessages"].text()
    # Cell colored (non-default background).
    assert cell.background().style() != 0
    # Start refused.
    assert window.start_run() is False


def test_row_tool_buttons(setup):
    """Add / Duplicate / Delete buttons change the table."""
    # Unpack.
    _panel, window = setup
    # Add.
    window.w["btnAddRow"].click()
    # Duplicate the selected row.
    window.w["btnDuplicateRow"].click()
    # Three rows.
    assert window.w["twSteps"].rowCount() == 3
    # Delete.
    window.w["btnDeleteRow"].click()
    # Two rows and the file label shows unsaved changes.
    assert window.w["twSteps"].rowCount() == 2 and window.w["lblFile"].text().endswith("*")


def test_action_combo_changes_model(setup):
    """Selecting Ramp in the action selector updates the row."""
    # Unpack.
    _panel, window = setup
    # Action selector of row 1.
    combo = window.w["twSteps"].cellWidget(0, 4)
    # Select Ramp.
    combo.setCurrentIndex(combo.findData("ramp"))
    # Stored.
    assert window.editor.sequence.steps[0].action == "ramp"


def test_end_state_warning(setup):
    """Hold last value shows the power-stays-ON warning (D-03)."""
    # Unpack.
    _panel, window = setup
    # End state selector.
    combo = window.w["cbEndState"]
    # Select hold_last.
    combo.setCurrentIndex(combo.findData("hold_last"))
    # Warning text.
    assert "Power stays ON" in window.w["lblEndWarning"].text()


def test_run_locks_panel_and_follows_values(setup):
    """During a run the panel is locked; afterwards it is unlocked with the last values."""
    # Unpack.
    panel, window = setup
    # Short sequence: 200 ms 10 V, 200 ms 4 V.
    load(window, [SequenceStep(200, "ms", 10, None, "set"), SequenceStep(200, "ms", 4, None, "set")])
    # Start.
    assert window.start_run()
    # Panel locked, Settings disabled.
    assert not panel.w["v_gbSection"].isEnabled() and not panel.w["btnSettings"].isEnabled()
    # Editing locked in the window.
    assert not window.w["btnAddRow"].isEnabled() and window.w["btnStop"].isEnabled()
    # Wait for the end.
    finish(window)
    # Completed.
    assert window.runner.status().state == STATE_COMPLETED and "Finished" in window.w["lblRunState"].text()
    # Panel unlocked with the last value; output OFF (default end state).
    assert panel.w["v_gbSection"].isEnabled() and panel.controls["voltage"].target == pytest.approx(4)
    # Output OFF.
    assert not panel.output_on and panel.test_backend.last_values[V_CH] == 0.0


def test_log_remains_usable_during_sequence_and_manual_value_is_retained(setup):
    """The sequence can update its display without changing the remembered manual value."""
    panel, window = setup
    panel.set_target("voltage", 7)
    load(window, [SequenceStep(200, "ms", 10, None, "set")])
    assert window.start_run()
    log = panel.open_log()
    assert log.isVisible() and not log.isModal()
    finish(window)
    log.refresh()
    assert "Sequence" in log.entries.toPlainText()
    assert "Seq timing" in log.entries.toPlainText()
    panel.close()
    assert panel.test_ctx.manager.load().config.ui.last_manual_voltage == 7


def test_output_off_button_stops_sequence(setup):
    """The front-panel Output OFF button stops a running sequence."""
    # Unpack.
    panel, window = setup
    # Endless sequence.
    load(window, [SequenceStep(1, "s", 5, None, "set")], repeat_count=0)
    # Start.
    window.start_run()
    # The panel knows the sequence runs.
    window.poll()
    # Output button = stop.
    panel.toggle_output()
    # Wait.
    finish(window)
    # Stopped, output OFF.
    assert window.runner.status().state == STATE_STOPPED and not panel.test_service.state.output_on


def test_save_and_load_buttons(setup, app_root):
    """Save asks for a file once, Load reads it back and remembers it as last file."""
    # Unpack.
    panel, window = setup
    # Target file.
    target = app_root.sequences_dir / "panel_test.json"
    # Fake dialogs.
    window.ask_save_path = lambda: target
    # Open dialog returns the same file.
    window.ask_open_path = lambda: target
    # Change something.
    window.w["sbRepeat"].setValue(3)
    # Save.
    assert window.save_sequence() and target.is_file()
    # New sequence.
    window.new_sequence()
    # Load.
    assert window.load_sequence()
    # Repeat back, last file remembered.
    assert window.w["sbRepeat"].value() == 3
    # Remembered path.
    assert panel.test_ctx.config.ui.last_sequence_file.replace("\\", "/") == "sequences/panel_test.json"


def test_ao_preview_columns(setup):
    """The AO preview columns show scaled values (10 V -> 5 V AO)."""
    # Unpack.
    _panel, window = setup
    # One row 10 V.
    load(window, [SequenceStep(1, "s", 10, None, "set")])
    # Columns hidden by default.
    assert window.w["twSteps"].isColumnHidden(5)
    # Show them.
    window.w["chkAoPreview"].setChecked(True)
    # Visible with the scaled value.
    assert not window.w["twSteps"].isColumnHidden(5) and window.w["twSteps"].item(0, 5).text() == "5"
