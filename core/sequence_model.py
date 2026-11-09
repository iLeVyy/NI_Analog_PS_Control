"""
core/sequence_model.py
======================
Data model and file format of sequences (Project_Plan.md sections 10, 11).

A sequence file is JSON in <root>/sequences/*.json:

    {
      "format_version": 1,
      "name": "...", "description": "...",
      "created": "...", "modified": "...",
      "timing_mode": "software",
      "end_state": "output_off" | "hold_last",
      "repeat_count": 1,                       (0 = until stopped)
      "units": {"voltage": "V", "current": "A"},
      "ps_range_at_save": {"voltage": [0, 20], "current": [0, 100]},
      "steps": [
        {"duration": 10, "unit": "s", "voltage": 10, "current": null, "action": "set"},
        ...
      ]
    }

Values are always power supply units (V / A), never AO volts (D-02).
"current": null means "current limit unchanged".

LabVIEW analogy:
    The typedef cluster of a sequence plus "Flatten/Unflatten from JSON".
"""

# Import json to read and write sequence files.
import json
# Import os for the atomic file replace.
import os
# Import dataclass tools.
from dataclasses import asdict, dataclass, field
# Import datetime for the created / modified time stamps.
from datetime import datetime
# Import Path for file handling.
from pathlib import Path
# Import typing helpers.
from typing import Any, Dict, List, Optional

# Structure version of sequence files; raise it when the structure changes.
FORMAT_VERSION = 1


class SequenceFileError(Exception):
    """Raised when a sequence file cannot be read or has an invalid structure."""


def _now() -> str:
    """Return the current time as ISO text (seconds precision)."""
    # Local time, ISO format.
    return datetime.now().isoformat(timespec="seconds")


def _optional_float(value: Any) -> Optional[float]:
    """
    Convert a JSON value to float, keeping None (empty / missing).

    Inputs:
        value (Any): JSON value.

    Returns:
        Optional[float]: Number or None.

    Raises:
        SequenceFileError: If the value is not a number.
    """
    # Missing value stays missing.
    if value is None:
        # None.
        return None
    # Convert numbers (bool is rejected because it is an int subtype).
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        # Float value.
        return float(value)
    # Anything else is a format error.
    raise SequenceFileError(f"Number expected, found {value!r}")


@dataclass
class SequenceStep:
    """
    One row of a sequence.

    Attributes:
        duration (Optional[float]): Duration in "unit" (None = missing/invalid).
        unit (str): Duration unit id from config/presets/duration_units.json.
        voltage (Optional[float]): Voltage setpoint in V (None = missing/invalid).
        current (Optional[float]): Current limit in A (None = unchanged).
        action (str): Action id from config/presets/sequence_actions.json.
    """

    # Duration in the step unit.
    duration: Optional[float]
    # Duration unit id.
    unit: str
    # Voltage setpoint.
    voltage: Optional[float]
    # Current setpoint (None = unchanged).
    current: Optional[float]
    # Action id.
    action: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SequenceStep":
        """
        Build a step from one JSON step object.

        Inputs:
            data (Dict[str, Any]): Step object.

        Returns:
            SequenceStep: The step.

        Raises:
            SequenceFileError: For wrong value types.
        """
        # The step must be an object.
        if not isinstance(data, dict):
            # Report the wrong structure.
            raise SequenceFileError("Each step must be a JSON object")
        # Build the step field by field.
        return cls(
            # Duration (number or null).
            duration=_optional_float(data.get("duration")),
            # Unit id (text).
            unit=str(data.get("unit", "")),
            # Voltage (number or null).
            voltage=_optional_float(data.get("voltage")),
            # Current (number or null).
            current=_optional_float(data.get("current")),
            # Action id (text).
            action=str(data.get("action", "")),
        )


@dataclass
class Sequence:
    """
    A complete sequence.

    Attributes:
        name, description: Free text.
        created, modified: ISO time stamps.
        timing_mode: "software" (v1.0); "hardware" reserved for FE-01.
        end_state: End state id from config/presets/end_states.json (D-03).
        repeat_count: 1 = once, N = N times, 0 = until stopped (D-08).
        steps: Rows of the sequence.
        ps_range_at_save: PS ranges when the file was saved (informational,
            used to warn when the settings changed since).
    """

    # Sequence name.
    name: str = ""
    # Description text.
    description: str = ""
    # Creation time.
    created: str = ""
    # Last modification time.
    modified: str = ""
    # Timing mode.
    timing_mode: str = "software"
    # End state id.
    end_state: str = "output_off"
    # Repeat count.
    repeat_count: int = 1
    # Rows.
    steps: List[SequenceStep] = field(default_factory=list)
    # PS ranges at save time.
    ps_range_at_save: Dict[str, List[float]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert to the JSON file structure.

        Returns:
            Dict[str, Any]: JSON-ready dictionary.
        """
        # Build the dictionary in a readable key order.
        return {
            # Structure version.
            "format_version": FORMAT_VERSION,
            # Texts and time stamps.
            "name": self.name, "description": self.description,
            # Time stamps.
            "created": self.created, "modified": self.modified,
            # Run options.
            "timing_mode": self.timing_mode, "end_state": self.end_state,
            # Repeat count.
            "repeat_count": self.repeat_count,
            # Units are fixed: values are always PS units (D-02).
            "units": {"voltage": "V", "current": "A"},
            # Ranges at save time.
            "ps_range_at_save": self.ps_range_at_save,
            # Steps.
            "steps": [asdict(step) for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Sequence":
        """
        Build a sequence from the JSON file structure.

        Inputs:
            data (Dict[str, Any]): File content.

        Returns:
            Sequence: The sequence.

        Raises:
            SequenceFileError: For a wrong structure or a newer format version.
        """
        # Top level must be an object.
        if not isinstance(data, dict):
            # Report the wrong structure.
            raise SequenceFileError("Sequence file must contain a JSON object")
        # Files from a newer program version are refused.
        if int(data.get("format_version", 1)) > FORMAT_VERSION:
            # Report the version.
            raise SequenceFileError(f"Sequence file format {data.get('format_version')} is newer than "
                                    f"supported ({FORMAT_VERSION})")
        # Step list must be a list.
        steps = data.get("steps", [])
        # Check the type.
        if not isinstance(steps, list):
            # Report the wrong structure.
            raise SequenceFileError("'steps' must be a list")
        # Repeat count must be an integer.
        try:
            # Convert.
            repeat = int(data.get("repeat_count", 1))
        # Not a number.
        except (TypeError, ValueError) as exc:
            # Report it.
            raise SequenceFileError("'repeat_count' must be an integer") from exc
        # Build the sequence.
        return cls(
            # Texts.
            name=str(data.get("name", "")), description=str(data.get("description", "")),
            # Time stamps.
            created=str(data.get("created", "")), modified=str(data.get("modified", "")),
            # Run options.
            timing_mode=str(data.get("timing_mode", "software")),
            # End state.
            end_state=str(data.get("end_state", "output_off")),
            # Repeat count.
            repeat_count=repeat,
            # Steps.
            steps=[SequenceStep.from_dict(step) for step in steps],
            # Ranges at save time.
            ps_range_at_save=dict(data.get("ps_range_at_save", {}) or {}),
        )


def load_sequence(path: Path) -> Sequence:
    """
    Read a sequence file.

    Inputs:
        path (Path): File to read.

    Returns:
        Sequence: The loaded sequence.

    Raises:
        SequenceFileError: If the file is missing, not JSON, or invalid.
    """
    # Read and parse.
    try:
        # Parse the JSON text.
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    # Missing file or invalid JSON.
    except (OSError, ValueError) as exc:
        # Report it.
        raise SequenceFileError(f"Cannot read {path}: {exc}") from exc
    # Convert to the model.
    return Sequence.from_dict(data)


def save_sequence(sequence: Sequence, path: Path, ps_ranges: Dict[str, List[float]]) -> Path:
    """
    Write a sequence file (atomic) and update its time stamps and ranges.

    Inputs:
        sequence (Sequence): Sequence to save (time stamps are updated in place).
        path (Path): Target file (".json" is added when missing).
        ps_ranges (Dict[str, List[float]]): Current PS ranges, e.g.
            {"voltage": [0, 20], "current": [0, 100]}.

    Returns:
        Path: The written file.
    """
    # Make sure the file ends with .json.
    path = Path(path) if Path(path).suffix.lower() == ".json" else Path(str(path) + ".json")
    # Create the folder when needed.
    path.parent.mkdir(parents=True, exist_ok=True)
    # Creation time only once.
    sequence.created = sequence.created or _now()
    # Modification time every save.
    sequence.modified = _now()
    # Remember the ranges the values refer to.
    sequence.ps_range_at_save = {key: list(value) for key, value in ps_ranges.items()}
    # Temporary file next to the target.
    temp = path.with_suffix(".json.tmp")
    # Write pretty JSON.
    temp.write_text(json.dumps(sequence.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    # Replace the target in one step.
    os.replace(temp, path)
    # Return the path.
    return path
