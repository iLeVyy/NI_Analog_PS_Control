"""
ui/ui_loader.py
===============
Loads Qt Designer (.ui) files at run time and checks that every widget the
code needs is present.

Loading at run time (QUiLoader) means a layout change in Qt Designer needs
no code change and no conversion step - just save the .ui file.

LabVIEW analogy:
    The .ui file is the front panel; this loader opens it and finds the
    controls by label (object name).
"""

# Import Path for file handling.
from pathlib import Path
# Import typing helpers.
from typing import Dict, Optional

# Import Qt file access.
from PySide6.QtCore import QFile, QIODevice
# Import the Qt Designer loader.
from PySide6.QtUiTools import QUiLoader
# Import the widget base class.
from PySide6.QtWidgets import QWidget


class UiLoadError(Exception):
    """Raised when a .ui file is missing, cannot be parsed, or lacks a required widget."""


def load_ui(path: Path, parent: Optional[QWidget] = None) -> QWidget:
    """
    Load a .ui file into a widget.

    Inputs:
        path (Path): .ui file.
        parent (Optional[QWidget]): Parent of the loaded widget.

    Returns:
        QWidget: Root widget of the file.

    Raises:
        UiLoadError: If the file is missing or invalid.
    """
    # The file must exist.
    if not path.is_file():
        # Report the missing file.
        raise UiLoadError(f"UI file not found: {path}")
    # Qt file object for the loader.
    ui_file = QFile(str(path))
    # Open it for reading.
    if not ui_file.open(QIODevice.OpenModeFlag.ReadOnly):
        # Report the read problem.
        raise UiLoadError(f"UI file cannot be opened: {path}")
    # Create the loader.
    loader = QUiLoader()
    # Build the widget tree from the file.
    widget = loader.load(ui_file, parent)
    # Close the file.
    ui_file.close()
    # A None result means the XML was invalid.
    if widget is None:
        # Report the loader message.
        raise UiLoadError(f"UI file invalid ({path}): {loader.errorString()}")
    # Return the root widget.
    return widget


def find_widgets(root: QWidget, required: Dict[str, str]) -> Dict[str, QWidget]:
    """
    Find all required widgets by object name and check their Qt class.

    Inputs:
        root (QWidget): Loaded root widget.
        required (Dict[str, str]): {object name: Qt class name}.

    Returns:
        Dict[str, QWidget]: {object name: widget}.

    Raises:
        UiLoadError: Listing every missing or wrong-typed widget.
    """
    # Found widgets.
    found: Dict[str, QWidget] = {}
    # Problems.
    problems = []
    # Look up every required name.
    for name, class_name in required.items():
        # Search the whole widget tree.
        widget = root.findChild(QWidget, name)
        # Missing widget.
        if widget is None:
            # Record the problem.
            problems.append(f"missing '{name}' ({class_name})")
            # Next name.
            continue
        # Wrong class.
        if not widget.inherits(class_name):
            # Record the problem.
            problems.append(f"'{name}' is {widget.metaObject().className()}, expected {class_name}")
            # Next name.
            continue
        # Store the widget.
        found[name] = widget
    # Report all problems at once.
    if problems:
        # One message with every problem.
        raise UiLoadError("UI file does not match the code: " + "; ".join(problems))
    # Return the widgets.
    return found
