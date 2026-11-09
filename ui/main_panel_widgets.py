"""
ui/main_panel_widgets.py
========================
Names and types of the widgets the front panel code expects in
ui/main_panel.ui (Qt Designer file). No Qt import, so tests can check the
.ui file without PySide6 (tests/test_ui_files.py).

Section widgets exist twice with the prefix of each quantity
("v_" = voltage, "i_" = current), e.g. "v_lcdSetpoint" / "i_lcdSetpoint".
"""

# Prefix per quantity for the section widgets.
SECTION_PREFIXES = {"voltage": "v_", "current": "i_"}

# Widgets that exist once: object name -> Qt class name.
GENERAL_WIDGETS = {
    # Application title.
    "lblTitle": "QLabel",
    # SIMULATION indicator.
    "lblSimulation": "QLabel",
    # Settings button.
    "btnSettings": "QPushButton",
    # Sequence button (Phase 5).
    "btnSequence": "QPushButton",
    # Activity log button.
    "btnLog": "QPushButton",
    # Output ON/OFF button.
    "btnOutput": "QPushButton",
    # Output LED.
    "lblLed": "QLabel",
    # Output state text.
    "lblOutputState": "QLabel",
    # Status: device / channels.
    "lblStatusChannels": "QLabel",
    # Status: AO volts sent.
    "lblStatusAo": "QLabel",
    # Status: last message / error.
    "lblStatusMessage": "QLabel",
}

# Widgets per section (name without prefix -> Qt class name).
SECTION_WIDGETS = {
    # Section frame (title = quantity).
    "gbSection": "QGroupBox",
    # 7-segment setpoint display.
    "lcdSetpoint": "QLCDNumber",
    # Actual commanded value during a ramp.
    "lblActual": "QLabel",
    # Range text.
    "lblRange": "QLabel",
    # Knob.
    "dialKnob": "QDial",
    # Numeric entry.
    "dsbEntry": "QDoubleSpinBox",
    # Step up button.
    "btnStepUp": "QPushButton",
    # Step down button.
    "btnStepDown": "QPushButton",
    # Step size selector.
    "cbStepSize": "QComboBox",
    # Reserved measured-value area (FE-02, hidden in v1.0).
    "gbMeasured": "QGroupBox",
    # Measured value text (FE-02).
    "lblMeasured": "QLabel",
}


def all_required_widgets() -> dict:
    """
    Return every required widget name with its Qt class.

    Returns:
        dict: {object name: Qt class name} including both section prefixes.
    """
    # Start with the general widgets.
    result = dict(GENERAL_WIDGETS)
    # Add the section widgets for every prefix.
    for prefix in SECTION_PREFIXES.values():
        # Loop over the section widget names.
        for name, cls in SECTION_WIDGETS.items():
            # Prefixed name.
            result[prefix + name] = cls
    # Return the complete map.
    return result
