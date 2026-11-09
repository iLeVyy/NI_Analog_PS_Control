"""
tests/test_ui_files.py
======================
Checks the Qt Designer files against the widget names the code expects.
Runs WITHOUT PySide6 (plain XML parsing).
"""

# Import the XML parser.
import xml.etree.ElementTree as ET
# Import Path for file handling.
from pathlib import Path

# Import the widget specification of the front panel.
from ui import main_panel_widgets
# Import the widget specification of the Sequence window.
from ui import sequence_widgets
# Import the widget specification of the Settings window.
from ui.settings_widgets import all_required_widgets

# Project root = parent folder of the tests folder.
ROOT = Path(__file__).resolve().parent.parent


def widgets_in_ui(path: Path) -> dict:
    """
    Read all widgets of a .ui file.

    Inputs:
        path (Path): .ui file.

    Returns:
        dict: {object name: Qt class name}.
    """
    # Parse the file.
    tree = ET.parse(path)
    # Collect every <widget> element.
    return {w.get("name"): w.get("class") for w in tree.iter("widget")}


def test_settings_ui_has_all_required_widgets():
    """Every widget the code needs exists with the right class."""
    # Widgets in the file.
    found = widgets_in_ui(ROOT / "ui" / "settings_window.ui")
    # Compare with the specification.
    problems = [f"{name}: expected {cls}, found {found.get(name)}"
                for name, cls in all_required_widgets().items() if found.get(name) != cls]
    # No problems allowed.
    assert problems == []


def test_settings_ui_object_names_are_unique():
    """Qt Designer object names must be unique."""
    # Parse the file.
    tree = ET.parse(ROOT / "ui" / "settings_window.ui")
    # All names of widgets and layouts.
    names = [e.get("name") for e in tree.iter() if e.tag in ("widget", "layout", "spacer")]
    # No duplicates.
    assert len(names) == len(set(names))


def test_main_panel_ui_has_all_required_widgets():
    """Every front-panel widget the code needs exists with the right class."""
    # Widgets in the file.
    found = widgets_in_ui(ROOT / "ui" / "main_panel.ui")
    # Compare with the specification.
    problems = [f"{name}: expected {cls}, found {found.get(name)}"
                for name, cls in main_panel_widgets.all_required_widgets().items() if found.get(name) != cls]
    # No problems allowed.
    assert problems == []


def test_main_panel_ui_has_no_theme_button():
    """The front panel does not expose a theme-switching button."""
    # Widgets in the front-panel form.
    found = widgets_in_ui(ROOT / "ui" / "main_panel.ui")
    # Theme selection is no longer available from the main panel.
    assert "btnTheme" not in found


def test_main_panel_has_log_instead_of_exit():
    """Log replaces Exit while the normal window close remains available."""
    found = widgets_in_ui(ROOT / "ui" / "main_panel.ui")
    assert found["btnLog"] == "QPushButton"
    assert "btnExit" not in found


def test_main_panel_ui_object_names_are_unique():
    """Object names in the front-panel form are unique."""
    # Parse the file.
    tree = ET.parse(ROOT / "ui" / "main_panel.ui")
    # All names of widgets, layouts and spacers.
    names = [e.get("name") for e in tree.iter() if e.tag in ("widget", "layout", "spacer")]
    # No duplicates.
    assert len(names) == len(set(names))


def test_sequence_ui_has_all_required_widgets():
    """Every Sequence-window widget the code needs exists with the right class."""
    # Widgets in the file.
    found = widgets_in_ui(ROOT / "ui" / "sequence_window.ui")
    # Compare with the specification.
    problems = [f"{name}: expected {cls}, found {found.get(name)}"
                for name, cls in sequence_widgets.all_required_widgets().items() if found.get(name) != cls]
    # No problems allowed.
    assert problems == []


def test_sequence_ui_object_names_are_unique():
    """Object names in the Sequence form are unique."""
    # Parse the file.
    tree = ET.parse(ROOT / "ui" / "sequence_window.ui")
    # All names.
    names = [e.get("name") for e in tree.iter() if e.tag in ("widget", "layout", "spacer")]
    # No duplicates.
    assert len(names) == len(set(names))
