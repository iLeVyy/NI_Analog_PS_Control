"""
ui/settings_widgets.py
======================
Names and types of the widgets the Settings window code expects in
ui/settings_window.ui (Qt Designer file).

This module has no Qt import, so a unit test can check the .ui file
against it without PySide6 (tests/test_ui_files.py). If you rename a widget
in Qt Designer, rename it here too.

Channel widgets exist twice, with the prefix of each quantity
("v_" = voltage, "i_" = current), e.g. "v_cbChannel" and "i_cbChannel".
"""

# Prefix per quantity for the channel widgets.
CHANNEL_PREFIXES = {"voltage": "v_", "current": "i_"}

# Widgets that exist once: object name -> Qt class name.
GENERAL_WIDGETS = {
    # DAQ backend selector.
    "cbBackend": "QComboBox",
    # Driver / backend information text.
    "lblBackendInfo": "QLabel",
    # SIMULATION indicator.
    "lblSimulation": "QLabel",
    # Scan button.
    "btnScan": "QPushButton",
    # Device table.
    "twDevices": "QTableWidget",
    # Safe value at start-up / Output OFF / error.
    "dsbSafeStartup": "QDoubleSpinBox",
    # Safe value at exit.
    "dsbSafeExit": "QDoubleSpinBox",
    # Slew-rate limit checkbox.
    "chkSlewLimit": "QCheckBox",
    # Voltage slew rate.
    "dsbSlewVoltage": "QDoubleSpinBox",
    # Current slew rate.
    "dsbSlewCurrent": "QDoubleSpinBox",
    # Validation / result messages.
    "lblMessages": "QLabel",
    # Restore Defaults button.
    "btnRestoreDefaults": "QPushButton",
    # Cancel button.
    "btnCancel": "QPushButton",
    # Save button.
    "btnSave": "QPushButton",
}

# Widgets that exist once per channel (name without prefix -> Qt class name).
CHANNEL_WIDGETS = {
    # Physical AO channel selector.
    "cbChannel": "QComboBox",
    # Device AO range selector.
    "cbDeviceRange": "QComboBox",
    # Used span preset selector.
    "cbSpanPreset": "QComboBox",
    # Used span minimum.
    "dsbAoMin": "QDoubleSpinBox",
    # Used span maximum.
    "dsbAoMax": "QDoubleSpinBox",
    # Power supply range minimum.
    "dsbPsMin": "QDoubleSpinBox",
    # Power supply range maximum.
    "dsbPsMax": "QDoubleSpinBox",
    # Slope / offset text.
    "lblScale": "QLabel",
    # Scaling preview table.
    "twPreview": "QTableWidget",
}


def all_required_widgets() -> dict:
    """
    Return every required widget name with its Qt class.

    Inputs:
        None.

    Returns:
        dict: {object name: Qt class name} including both channel prefixes.
    """
    # Start with the general widgets.
    result = dict(GENERAL_WIDGETS)
    # Add the channel widgets for every prefix.
    for prefix in CHANNEL_PREFIXES.values():
        # Loop over the channel widget names.
        for name, cls in CHANNEL_WIDGETS.items():
            # Prefixed name.
            result[prefix + name] = cls
    # Return the complete map.
    return result
