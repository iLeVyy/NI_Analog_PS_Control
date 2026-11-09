"""
ui/sequence_widgets.py
======================
Names and types of the widgets the Sequence window code expects in
ui/sequence_window.ui (Qt Designer file). No Qt import, so tests can check
the .ui file without PySide6 (tests/test_ui_files.py).
"""

# Required widgets: object name -> Qt class name.
REQUIRED_WIDGETS = {
    # File buttons.
    "btnNew": "QPushButton", "btnLoad": "QPushButton",
    # More file buttons.
    "btnSave": "QPushButton", "btnSaveAs": "QPushButton",
    # File name text.
    "lblFile": "QLabel",
    # SIMULATION indicator.
    "lblSimulation": "QLabel",
    # Name and description.
    "leName": "QLineEdit", "leDescription": "QLineEdit",
    # End state selector and its warning.
    "cbEndState": "QComboBox", "lblEndWarning": "QLabel",
    # Repeat count.
    "sbRepeat": "QSpinBox",
    # AO preview columns on/off.
    "chkAoPreview": "QCheckBox",
    # Step table.
    "twSteps": "QTableWidget",
    # Row tools.
    "btnAddRow": "QPushButton", "btnInsertRow": "QPushButton", "btnDeleteRow": "QPushButton",
    # More row tools.
    "btnDuplicateRow": "QPushButton", "btnMoveUp": "QPushButton", "btnMoveDown": "QPushButton",
    # Validation messages.
    "lblMessages": "QLabel",
    # Run buttons.
    "btnStart": "QPushButton", "btnPause": "QPushButton", "btnStop": "QPushButton",
    # Run monitor texts.
    "lblRunState": "QLabel", "lblCycle": "QLabel", "lblStep": "QLabel", "lblTime": "QLabel",
    # Progress bar.
    "pbProgress": "QProgressBar",
    # Timing statistics.
    "lblTiming": "QLabel",
}

# Table columns in order: (field, editor kind). Kinds: "number", "combo", "preview".
TABLE_COLUMNS = [
    # Duration value.
    ("duration", "number"),
    # Duration unit (duration_units.json).
    ("unit", "combo"),
    # Voltage in V.
    ("voltage", "number"),
    # Current in A (empty = unchanged).
    ("current", "number"),
    # Action (sequence_actions.json).
    ("action", "combo"),
    # AO preview of the voltage channel (read-only, optional).
    ("ao_voltage", "preview"),
    # AO preview of the current channel (read-only, optional).
    ("ao_current", "preview"),
]


def all_required_widgets() -> dict:
    """Return {object name: Qt class name} of every required widget."""
    # Copy of the table.
    return dict(REQUIRED_WIDGETS)
