"""
core/sequence_editor.py
=======================
Qt-free logic of the Sequence window (Project_Plan.md sections 10.1 - 10.3).

    * the sequence being edited, its file path and the "unsaved" flag
    * new / load / save
    * row tools: add, insert, delete, duplicate, move up / down
    * cell input parsing (numbers with "." or ","; empty current = unchanged)
    * live validation (core/sequence_engine.validate_sequence)
    * column headers with unit and PS range, e.g. "Voltage [V] (0–20)"
    * warning when the PS ranges changed since the file was saved

LabVIEW analogy:
    The data cluster and helper VIs behind the sequence table front panel.
"""

# Import copy for independent row copies.
import copy
# Import Path for file handling.
from pathlib import Path
# Import typing helpers.
from typing import Dict, List, Optional

# Import the catalog type.
from core.catalogs import Catalog
# Import the configuration type.
from core.models import AppConfig
# Import validation.
from core.sequence_engine import Issue, validate_sequence
# Import the data model and file functions.
from core.sequence_model import Sequence, SequenceStep, load_sequence, save_sequence
# Quantity names.
from core.sequence_players import CURRENT, VOLTAGE

# Editable numeric fields of a row.
NUMERIC_FIELDS = ("duration", "voltage", "current")


def parse_number(text: str) -> Optional[float]:
    """
    Convert cell text to a number; accepts "," as decimal separator.

    Inputs:
        text (str): Cell text.

    Returns:
        Optional[float]: Number, or None for empty / invalid text.
    """
    # Remove spaces and accept a decimal comma.
    cleaned = (text or "").strip().replace(",", ".")
    # Empty text.
    if not cleaned:
        # No value.
        return None
    # Try the conversion.
    try:
        # Number.
        return float(cleaned)
    # Not a number.
    except ValueError:
        # Invalid.
        return None


def format_number(value: Optional[float]) -> str:
    """
    Text for a numeric cell.

    Inputs:
        value (Optional[float]): Number or None.

    Returns:
        str: Compact text ("" for None).
    """
    # Empty for missing values; up to 6 significant digits otherwise.
    return "" if value is None else f"{value:.6g}"


class SequenceEditor:
    """Holds the sequence being edited in the Sequence window."""

    def __init__(self, config: AppConfig, catalogs: Dict[str, Catalog], sequences_dir: Path) -> None:
        """
        Create the editor with a new (one-row) sequence.

        Inputs:
            config (AppConfig): Active settings (read live; may change after Settings → Save).
            catalogs (Dict[str, Catalog]): Loaded catalogs.
            sequences_dir (Path): Folder of the sequence files.

        Returns:
            None.
        """
        # Settings.
        self.config = config
        # Catalogs.
        self.catalogs = catalogs
        # Sequence folder.
        self.sequences_dir = sequences_dir
        # Sequence being edited.
        self.sequence = Sequence()
        # File of the sequence (None = not saved yet).
        self.path: Optional[Path] = None
        # Unsaved changes flag.
        self.dirty = False
        # Range warning shown after loading.
        self.load_warning = ""
        # Start with a new sequence.
        self.new()

    # ------------------------------------------------------------------ rows

    def default_step(self, template: Optional[SequenceStep] = None) -> SequenceStep:
        """
        Build a new row: a copy of the template row, or catalog / settings defaults.

        Inputs:
            template (Optional[SequenceStep]): Row to copy (e.g. the row above).

        Returns:
            SequenceStep: New row.
        """
        # Copy a Set/Ramp template (a Stop row is not a useful template).
        if template is not None and template.action != "stop":
            # Independent copy.
            return copy.deepcopy(template)
        # Defaults: duration from settings, unit and action from the catalogs, voltage = PS min.
        return SequenceStep(duration=self.config.sequence.new_step_duration,
                            unit=self.catalogs["duration_units"].default,
                            voltage=self.config.voltage_channel.ps_min, current=None,
                            action=self.catalogs["sequence_actions"].default)

    def _changed(self) -> None:
        """Mark the sequence as changed."""
        # Unsaved changes.
        self.dirty = True

    def add_row(self) -> int:
        """
        Append a row (copy of the last row).

        Returns:
            int: Index of the new row.
        """
        # Template = last row.
        template = self.sequence.steps[-1] if self.sequence.steps else None
        # Append.
        self.sequence.steps.append(self.default_step(template))
        # Changed.
        self._changed()
        # New index.
        return len(self.sequence.steps) - 1

    def insert_row(self, index: int) -> int:
        """
        Insert a row before "index" (copy of that row).

        Inputs:
            index (int): Position (clamped to the valid range).

        Returns:
            int: Index of the new row.
        """
        # Clamp the index.
        index = max(0, min(index, len(self.sequence.steps)))
        # Template = row at the position.
        template = self.sequence.steps[index] if index < len(self.sequence.steps) else None
        # Insert.
        self.sequence.steps.insert(index, self.default_step(template))
        # Changed.
        self._changed()
        # New index.
        return index

    def delete_row(self, index: int) -> int:
        """
        Delete a row.

        Inputs:
            index (int): Row to delete.

        Returns:
            int: Index to select afterwards (-1 when the table is empty).
        """
        # Ignore invalid indexes.
        if not 0 <= index < len(self.sequence.steps):
            # Nothing deleted.
            return index
        # Delete.
        del self.sequence.steps[index]
        # Changed.
        self._changed()
        # Select the row now at that position (or the new last row).
        return min(index, len(self.sequence.steps) - 1)

    def duplicate_row(self, index: int) -> int:
        """
        Insert a copy of a row directly below it.

        Inputs:
            index (int): Row to copy.

        Returns:
            int: Index of the copy.
        """
        # Ignore invalid indexes.
        if not 0 <= index < len(self.sequence.steps):
            # Nothing copied.
            return index
        # Insert the exact copy (also for Stop rows).
        self.sequence.steps.insert(index + 1, copy.deepcopy(self.sequence.steps[index]))
        # Changed.
        self._changed()
        # Index of the copy.
        return index + 1

    def move_row(self, index: int, direction: int) -> int:
        """
        Move a row up (-1) or down (+1).

        Inputs:
            index (int): Row to move.
            direction (int): -1 or +1.

        Returns:
            int: New index of the row.
        """
        # Target position.
        target = index + direction
        # Both positions must exist.
        if not (0 <= index < len(self.sequence.steps) and 0 <= target < len(self.sequence.steps)):
            # Not moved.
            return index
        # Swap the rows.
        steps = self.sequence.steps
        # Swap.
        steps[index], steps[target] = steps[target], steps[index]
        # Changed.
        self._changed()
        # New index.
        return target

    def set_cell(self, index: int, field: str, value) -> None:
        """
        Change one cell.

        Inputs:
            index (int): Row.
            field (str): "duration", "voltage", "current" (text is parsed),
                or "unit", "action" (ids).
            value: Cell text or id.

        Raises:
            ValueError: For an unknown field.
        """
        # Row.
        row = self.sequence.steps[index]
        # Numeric cells are parsed (invalid text -> None, reported by validation).
        if field in NUMERIC_FIELDS:
            # Parsed value.
            setattr(row, field, parse_number(str(value)))
        # Id cells are stored as text.
        elif field in ("unit", "action"):
            # Store the id.
            setattr(row, field, str(value))
        # Anything else is a programming error.
        else:
            # Report it.
            raise ValueError(f"Unknown field '{field}'")
        # Changed.
        self._changed()

    # ------------------------------------------------------------------ options

    def set_options(self, name: Optional[str] = None, description: Optional[str] = None,
                    end_state: Optional[str] = None, repeat_count: Optional[int] = None) -> None:
        """
        Change sequence options (only the given ones).

        Inputs:
            name, description (Optional[str]): Texts.
            end_state (Optional[str]): End state id (D-03).
            repeat_count (Optional[int]): 0 = until stopped (D-08).
        """
        # Sequence.
        seq = self.sequence
        # Apply each given option.
        for attr, value in (("name", name), ("description", description),
                            ("end_state", end_state), ("repeat_count", repeat_count)):
            # Skip options that were not given or did not change.
            if value is None or getattr(seq, attr) == value:
                # Next option.
                continue
            # Store.
            setattr(seq, attr, value)
            # Changed.
            self._changed()

    # ------------------------------------------------------------------ files

    def ps_ranges(self) -> Dict[str, List[float]]:
        """Return the current PS ranges for the file ({"voltage": [min, max], ...})."""
        # Both channels.
        return {VOLTAGE: [self.config.voltage_channel.ps_min, self.config.voltage_channel.ps_max],
                CURRENT: [self.config.current_channel.ps_min, self.config.current_channel.ps_max]}

    def new(self) -> None:
        """Start a new sequence with one default row."""
        # New sequence with catalog / settings defaults.
        self.sequence = Sequence(timing_mode=self.config.sequence.timing_mode,
                                 end_state=self.catalogs["end_states"].default, repeat_count=1,
                                 steps=[self.default_step()])
        # No file yet.
        self.path = None
        # No range warning.
        self.load_warning = ""
        # Nothing to save yet.
        self.dirty = False

    def load(self, path: Path) -> None:
        """
        Load a sequence file (raises SequenceFileError on problems).

        Inputs:
            path (Path): File to load.
        """
        # Read the file (raises on errors; the current sequence stays unchanged).
        sequence = load_sequence(path)
        # Use it.
        self.sequence = sequence
        # Remember the file.
        self.path = Path(path)
        # Freshly loaded.
        self.dirty = False
        # Ranges at save time vs. now.
        saved = sequence.ps_range_at_save
        # Warn when they differ (values are PS units of the old ranges).
        if saved and {k: [float(x) for x in v] for k, v in saved.items()} != self.ps_ranges():
            # Warning text.
            self.load_warning = (f"Power supply ranges changed since this file was saved "
                                 f"(then: {saved}) - check the values.")
        # Ranges equal or unknown.
        else:
            # No warning.
            self.load_warning = ""

    def save(self, path: Optional[Path] = None) -> Path:
        """
        Save to "path" (or the current file).

        Inputs:
            path (Optional[Path]): Target; None = current file.

        Returns:
            Path: Written file.

        Raises:
            ValueError: When no path is known.
        """
        # Target file.
        target = Path(path) if path is not None else self.path
        # A path is required.
        if target is None:
            # Report it.
            raise ValueError("No file name")
        # Default name from the file when the sequence has none.
        if not self.sequence.name:
            # File name without extension.
            self.sequence.name = target.stem
        # Write.
        self.path = save_sequence(self.sequence, target, self.ps_ranges())
        # Saved.
        self.dirty = False
        # No range warning any more.
        self.load_warning = ""
        # Return the file.
        return self.path

    def relative_path(self) -> str:
        """Return the file path relative to the sequences folder parent ("" when unsaved)."""
        # Not saved.
        if self.path is None:
            # Empty.
            return ""
        # Relative to the app root when possible (portable settings).
        try:
            # Relative path.
            return str(self.path.relative_to(self.sequences_dir.parent))
        # Outside the app root.
        except ValueError:
            # Absolute path.
            return str(self.path)

    # ------------------------------------------------------------------ validation / display

    def validate(self) -> List[Issue]:
        """Validate the sequence with the active settings."""
        # Engine validation.
        return validate_sequence(self.sequence, self.config, self.catalogs)

    def column_headers(self) -> Dict[str, str]:
        """
        Column titles with unit and range (section 10.2).

        Returns:
            Dict[str, str]: Title per field.
        """
        # Channel settings.
        v, i = self.config.voltage_channel, self.config.current_channel
        # Titles.
        return {"duration": "Duration", "unit": "Unit",
                "voltage": f"Voltage [V] ({v.ps_min:g}–{v.ps_max:g})",
                "current": f"Current [A] ({i.ps_min:g}–{i.ps_max:g})",
                "action": "Action"}
