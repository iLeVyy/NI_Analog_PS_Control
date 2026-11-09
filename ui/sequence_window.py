"""
ui/sequence_window.py
=====================
Sequence window (Project_Plan.md section 10).

Layout: ui/sequence_window.ui (Qt Designer).
Logic:  core/sequence_editor.py (editing, files, validation) and
        core/sequence_engine.py (SequenceRunner: timing, pause, stop, end state).

The window is non-modal so the front panel stays visible. It polls the
runner status every sequence.status_poll_ms and tells the front panel:
    running_changed(bool)   - lock / unlock the front panel (FR-13)
    values_changed(dict)    - PS values commanded by the sequence

LabVIEW analogy:
    The sequence editor front panel with a timed loop that reads the
    sequencer module's status.
"""

# Import html to escape message text.
import html
# Import Path for file handling.
from pathlib import Path
# Import typing helpers.
from typing import Dict, Optional

# Import Qt core types.
from PySide6.QtCore import QTimer, Qt, Signal
# Import Qt GUI types.
from PySide6.QtGui import QBrush, QColor
# Import the Qt widgets used here.
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHeaderView, QMessageBox,
                               QTableWidgetItem, QVBoxLayout, QWidget)

# Import the application context.
from core.bootstrap import AppContext
# Import the service type.
from core.daq_service import DaqService
# Import the logger factory.
from core.logger import get_logger
# Import the editor model.
from core.sequence_editor import SequenceEditor, format_number
# Import engine types.
from core.sequence_engine import (ERROR, STATE_COMPLETED, STATE_ERROR, STATE_IDLE, STATE_PAUSED,
                                  STATE_RUNNING, STATE_STOPPED, SequenceRunner, errors_of, preview_values)
# Import the file error type.
from core.sequence_model import SequenceFileError
# Import the widget specification.
from ui.sequence_widgets import TABLE_COLUMNS, all_required_widgets
# Import the .ui loader helpers.
from ui.ui_loader import find_widgets, load_ui

# Logger for this module.
_log = get_logger("sequence_window")

# Column index per field.
COLUMN = {name: index for index, (name, _kind) in enumerate(TABLE_COLUMNS)}
# Texts of the run states.
STATE_TEXT = {STATE_IDLE: "Idle", STATE_RUNNING: "Running", STATE_PAUSED: "Paused",
              STATE_COMPLETED: "Finished", STATE_STOPPED: "Stopped by user", STATE_ERROR: "Error"}


def format_seconds(seconds: Optional[float]) -> str:
    """
    Format a time for the run monitor.

    Inputs:
        seconds (Optional[float]): Time in seconds (None = unknown / infinite).

    Returns:
        str: "12.3 s", "2:05.0 min" or "∞".
    """
    # Unknown / endless.
    if seconds is None:
        # Infinity sign.
        return "∞"
    # Negative values from rounding are shown as zero.
    seconds = max(0.0, seconds)
    # Below one minute.
    if seconds < 60:
        # Seconds with one decimal.
        return f"{seconds:.1f} s"
    # Minutes and seconds.
    minutes, rest = divmod(seconds, 60)
    # m:ss.s format.
    return f"{int(minutes)}:{rest:04.1f} min"


class SequenceWindow(QWidget):
    """
    Sequence editor and run monitor.

    Signals:
        running_changed(bool): A run started (True) or ended (False).
        values_changed(dict): PS values commanded by the running sequence.
    """

    # Run started / ended.
    running_changed = Signal(bool)
    # Commanded values while running.
    values_changed = Signal(dict)

    def __init__(self, ctx: AppContext, service: Optional[DaqService],
                 parent: Optional[QWidget] = None) -> None:
        """
        Build the window.

        Inputs:
            ctx (AppContext): Start-up context.
            service (Optional[DaqService]): DAQ service (None = no backend).
            parent (Optional[QWidget]): Parent window (the front panel).

        Returns:
            None.
        """
        # Separate top-level window that stays above its parent.
        super().__init__(parent, Qt.WindowType.Window)
        # Store the context.
        self._ctx = ctx
        # Store the service.
        self._service = service
        # Editor model.
        self.editor = SequenceEditor(ctx.config, ctx.catalogs, ctx.paths.sequences_dir)
        # Runner (thread starts with each run).
        self.runner = SequenceRunner(service, ctx.config, ctx.catalogs)
        # Guard that suppresses widget signals while the code fills widgets.
        self._loading = False
        # True while a run is active (as last reported to the panel).
        self._was_active = False
        # Extra status line (result of the last action).
        self._status = ""
        # Theme colors.
        self._colors = ctx.theme_colors()
        # Load the Designer form.
        self.form = load_ui(ctx.paths.root / "ui" / "sequence_window.ui", self)
        # Find and check all required widgets.
        self.w = find_widgets(self.form, all_required_widgets())
        # Layout that holds the form.
        layout = QVBoxLayout(self)
        # Small margin.
        layout.setContentsMargins(8, 8, 8, 8)
        # Put the form into the window.
        layout.addWidget(self.form)
        # Window title.
        self.setWindowTitle(self.form.windowTitle())
        # Run monitor timer.
        self._poll = QTimer(self)
        # Static widget setup.
        self._setup_widgets()
        # Signals.
        self._connect_signals()
        # Last used file (relative to the app root), if it still exists.
        self._open_last_file()
        # Fill everything.
        self._load_all()
        # Size and position.
        self._place_window()

    # ------------------------------------------------------------------ setup

    def _setup_widgets(self) -> None:
        """Set static widget properties."""
        # Step table.
        table = self.w["twSteps"]
        # Column count.
        table.setColumnCount(len(TABLE_COLUMNS))
        # Select single rows.
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        # One row at a time.
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        # Columns share the width.
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        # End state items from end_states.json.
        for item in self._ctx.catalogs["end_states"].items:
            # Text and id.
            self.w["cbEndState"].addItem(item.get("label", item["id"]), item["id"])
        # Repeat: 0 = until stopped.
        self.w["sbRepeat"].setRange(0, 1_000_000)
        # Text shown for 0.
        self.w["sbRepeat"].setSpecialValueText("∞ until stopped")
        # Repeat applies on Enter / focus loss.
        self.w["sbRepeat"].setKeyboardTracking(False)
        # Messages may contain rich text.
        self.w["lblMessages"].setTextFormat(Qt.TextFormat.RichText)
        # End-state warning in the warning color (D-03).
        self.w["lblEndWarning"].setStyleSheet(f"color:{self._colors.get('warning', '#F2994A')}; font-weight:bold;")
        # SIMULATION indicator.
        self.w["lblSimulation"].setStyleSheet(
            f"background:{self._colors.get('warning', '#F2994A')}; color:#000; font-weight:bold; padding:2px 8px;")
        # Only for the simulation backend.
        self.w["lblSimulation"].setVisible(bool(self._service and self._service.state.simulated))
        # Monitor refresh interval from settings.json.
        self._poll.setInterval(int(self._ctx.config.sequence.status_poll_ms))

    def _connect_signals(self) -> None:
        """Connect widget and timer signals."""
        # File buttons.
        self.w["btnNew"].clicked.connect(self.new_sequence)
        # Load.
        self.w["btnLoad"].clicked.connect(self.load_sequence)
        # Save.
        self.w["btnSave"].clicked.connect(lambda: self.save_sequence(False))
        # Save As.
        self.w["btnSaveAs"].clicked.connect(lambda: self.save_sequence(True))
        # Row tools.
        self.w["btnAddRow"].clicked.connect(lambda: self._row_action(lambda r: self.editor.add_row()))
        # Insert above the selection.
        self.w["btnInsertRow"].clicked.connect(lambda: self._row_action(lambda r: self.editor.insert_row(max(r, 0))))
        # Delete the selection.
        self.w["btnDeleteRow"].clicked.connect(lambda: self._row_action(self.editor.delete_row))
        # Duplicate the selection.
        self.w["btnDuplicateRow"].clicked.connect(lambda: self._row_action(self.editor.duplicate_row))
        # Move up.
        self.w["btnMoveUp"].clicked.connect(lambda: self._row_action(lambda r: self.editor.move_row(r, -1)))
        # Move down.
        self.w["btnMoveDown"].clicked.connect(lambda: self._row_action(lambda r: self.editor.move_row(r, +1)))
        # Cell edits (numeric cells).
        self.w["twSteps"].itemChanged.connect(self._on_item_changed)
        # Options.
        self.w["leName"].editingFinished.connect(self._on_options)
        # Description.
        self.w["leDescription"].editingFinished.connect(self._on_options)
        # End state.
        self.w["cbEndState"].currentIndexChanged.connect(self._on_options)
        # Repeat.
        self.w["sbRepeat"].valueChanged.connect(self._on_options)
        # AO preview columns.
        self.w["chkAoPreview"].toggled.connect(lambda _on: self._refresh_view())
        # Run buttons.
        self.w["btnStart"].clicked.connect(self.start_run)
        # Pause / Resume.
        self.w["btnPause"].clicked.connect(self.toggle_pause)
        # Stop.
        self.w["btnStop"].clicked.connect(self.stop_run)
        # Monitor timer.
        self._poll.timeout.connect(self.poll)

    def _place_window(self) -> None:
        """Size to the screen (max. 90 %) and center."""
        # Screen.
        screen = self.screen()
        # Without a screen keep the .ui size.
        if screen is None:
            # Nothing to do.
            return
        # Usable area.
        area = screen.availableGeometry()
        # Size.
        self.resize(min(self.form.width(), int(area.width() * 0.9)),
                    min(self.form.height(), int(area.height() * 0.9)))
        # Frame.
        frame = self.frameGeometry()
        # Center.
        frame.moveCenter(area.center())
        # Apply.
        self.move(frame.topLeft())

    def _open_last_file(self) -> None:
        """Load the last used sequence file from settings.json (ui.last_sequence_file)."""
        # Stored path (relative to the app root, or absolute).
        stored = self._ctx.config.ui.last_sequence_file
        # Nothing stored.
        if not stored:
            # Keep the new sequence.
            return
        # Resolve against the app root.
        path = Path(stored) if Path(stored).is_absolute() else self._ctx.paths.root / stored
        # Load when the file still exists.
        if path.is_file():
            # Try to load; a broken file only gives a status message.
            try:
                # Load it.
                self.editor.load(path)
            # Broken file.
            except SequenceFileError as exc:
                # Show it.
                self._status = f"Last sequence not loaded: {exc}"

    # ------------------------------------------------------------------ fill / refresh

    def _load_all(self) -> None:
        """Fill options and table from the editor."""
        # Sequence.
        seq = self.editor.sequence
        # Suppress handlers.
        self._loading = True
        # Fill and always release the guard.
        try:
            # Name.
            self.w["leName"].setText(seq.name)
            # Description.
            self.w["leDescription"].setText(seq.description)
            # End state.
            self.w["cbEndState"].setCurrentIndex(max(0, self.w["cbEndState"].findData(seq.end_state)))
            # Repeat.
            self.w["sbRepeat"].setValue(max(0, seq.repeat_count))
        # Release the guard.
        finally:
            # Handlers active.
            self._loading = False
        # Table and messages.
        self._fill_table()

    def _combo(self, catalog_name: str, current: str, row: int, field: str) -> QComboBox:
        """
        Build a selector cell from a catalog.

        Inputs:
            catalog_name (str): "duration_units" or "sequence_actions".
            current (str): Selected id.
            row (int): Row index.
            field (str): "unit" or "action".

        Returns:
            QComboBox: The selector.
        """
        # New selector.
        combo = QComboBox()
        # Catalog items.
        for item in self._ctx.catalogs[catalog_name].items:
            # Text and id.
            combo.addItem(item.get("label", item["id"]), item["id"])
        # Unknown ids from a file stay visible.
        if combo.findData(current) < 0:
            # Add the unknown id.
            combo.addItem(f"{current} (unknown)", current)
        # Select the current id.
        combo.setCurrentIndex(combo.findData(current))
        # Change handler (row and field bound).
        combo.currentIndexChanged.connect(lambda _i, c=combo: self._on_combo(row, field, c.currentData()))
        # Return it.
        return combo

    def _fill_table(self) -> None:
        """Rebuild the table from the editor (after loading and row tools)."""
        # Table.
        table = self.w["twSteps"]
        # Suppress itemChanged while filling.
        self._loading = True
        # Fill and always release the guard.
        try:
            # Remember the selection.
            selected = self.selected_row()
            # Hide the old selector widgets at once (Qt deletes them later; they must not stay visible).
            for row in range(table.rowCount()):
                # Both selector columns.
                for field in ("unit", "action"):
                    # Old widget.
                    old = table.cellWidget(row, COLUMN[field])
                    # Hide and detach it.
                    if old is not None:
                        # Hide.
                        old.hide()
                        # Detach (Qt deletes it later).
                        table.removeCellWidget(row, COLUMN[field])
            # Row count.
            table.setRowCount(len(self.editor.sequence.steps))
            # Every row.
            for row, step in enumerate(self.editor.sequence.steps):
                # Numeric cells.
                for field in ("duration", "voltage", "current"):
                    # Item with the formatted value.
                    table.setItem(row, COLUMN[field], QTableWidgetItem(format_number(getattr(step, field))))
                # Unit selector.
                table.setCellWidget(row, COLUMN["unit"], self._combo("duration_units", step.unit, row, "unit"))
                # Action selector.
                table.setCellWidget(row, COLUMN["action"], self._combo("sequence_actions", step.action, row, "action"))
                # Preview cells (read-only).
                for field in ("ao_voltage", "ao_current"):
                    # Empty item.
                    item = QTableWidgetItem("")
                    # Not editable.
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                    # Put it in.
                    table.setItem(row, COLUMN[field], item)
            # Restore the selection.
            if 0 <= selected < table.rowCount():
                # Select the row again.
                table.selectRow(selected)
        # Release the guard.
        finally:
            # Handlers active.
            self._loading = False
        # Headers, preview, validation, buttons.
        self._refresh_view()

    def _refresh_view(self) -> None:
        """Refresh headers, preview columns, file label, warnings, validation and buttons."""
        # Table.
        table = self.w["twSteps"]
        # Headers from the editor plus preview titles.
        headers = self.editor.column_headers()
        # Preview titles.
        headers.update({"ao_voltage": "AO V-channel [V]", "ao_current": "AO I-channel [V]"})
        # Apply in column order.
        table.setHorizontalHeaderLabels([headers[name] for name, _kind in TABLE_COLUMNS])
        # Hint on the current column header.
        header_item = table.horizontalHeaderItem(COLUMN["current"])
        # Tooltip when the header item exists.
        if header_item is not None:
            # Explain the empty value.
            header_item.setToolTip("Leave empty to keep the current limit unchanged.")
        # Preview columns on/off.
        preview = self.w["chkAoPreview"].isChecked()
        # Show / hide both preview columns.
        for field in ("ao_voltage", "ao_current"):
            # Column visibility.
            table.setColumnHidden(COLUMN[field], not preview)
        # Fill the preview.
        if preview:
            # Preview values.
            self._fill_preview()
        # File label (with * for unsaved changes).
        name = self.editor.path.name if self.editor.path else "(not saved)"
        # Show it.
        self.w["lblFile"].setText(name + (" *" if self.editor.dirty else ""))
        # End-state warning (catalog item "warning": true).
        item = self._ctx.catalogs["end_states"].get(self.editor.sequence.end_state) or {}
        # Show / clear it.
        self.w["lblEndWarning"].setText("⚠ Power stays ON after the sequence ends (Stop button and errors still "
                                        "switch the output OFF)." if item.get("warning") else "")
        # Validation and cell colors.
        self._refresh_messages()
        # Buttons.
        self._refresh_buttons()

    def _fill_preview(self) -> None:
        """Fill the AO preview columns (read-only)."""
        # Start values: setpoints stored in the service (front panel values).
        start = dict(self._service.state.setpoints) if self._service else {"voltage": 0.0, "current": 0.0}
        # Preview rows.
        rows = preview_values(self.editor.sequence, self._ctx.config, start)
        # Suppress itemChanged.
        self._loading = True
        # Fill and always release the guard.
        try:
            # Every row.
            for row, values in enumerate(rows):
                # Both channels.
                for quantity, field in (("voltage", "ao_voltage"), ("current", "ao_current")):
                    # Value pair or None.
                    pair = values[quantity]
                    # Text: "5" for Set, "5 → 7.5" for Ramp.
                    text = "" if pair is None else (f"{pair[1]:.4g}" if abs(pair[0] - pair[1]) < 1e-12
                                                    else f"{pair[0]:.4g} → {pair[1]:.4g}")
                    # Cell.
                    cell = self.w["twSteps"].item(row, COLUMN[field])
                    # Set the text when the cell exists.
                    if cell is not None:
                        # Text.
                        cell.setText(text)
        # Release the guard.
        finally:
            # Handlers active.
            self._loading = False

    def _refresh_messages(self) -> None:
        """Validate, color invalid cells and show the messages."""
        # Issues.
        issues = self.editor.validate()
        # Table.
        table = self.w["twSteps"]
        # Background colors.
        error_bg = QColor(self._colors.get("error", "#EB5757"))
        # Light version for cell backgrounds.
        error_bg.setAlpha(90)
        # Warning background.
        warn_bg = QColor(self._colors.get("warning", "#F2994A"))
        # Light version.
        warn_bg.setAlpha(70)
        # Suppress itemChanged while coloring.
        self._loading = True
        # Color and always release the guard.
        try:
            # Clear all colors.
            for row in range(table.rowCount()):
                # Numeric cells.
                for field in ("duration", "voltage", "current"):
                    # Cell.
                    cell = table.item(row, COLUMN[field])
                    # Default background.
                    if cell is not None:
                        # Clear.
                        cell.setBackground(QBrush())
            # Color the cells with issues.
            for issue in issues:
                # Only row issues on numeric fields can be colored.
                if issue.row is None or issue.field not in ("duration", "voltage", "current"):
                    # Skip.
                    continue
                # Cell.
                cell = table.item(issue.row, COLUMN[issue.field])
                # Color it.
                if cell is not None:
                    # Error or warning color.
                    cell.setBackground(error_bg if issue.level == ERROR else warn_bg)
        # Release the guard.
        finally:
            # Handlers active.
            self._loading = False
        # Message lines.
        lines = []
        # Status line first.
        if self._status:
            # Bold.
            lines.append(f"<b>{html.escape(self._status)}</b>")
        # Range warning after loading.
        if self.editor.load_warning:
            # Warning color.
            lines.append(f"<span style='color:{self._colors.get('warning', '#F2994A')}'>&#9888; "
                         f"{html.escape(self.editor.load_warning)}</span>")
        # Issues.
        for issue in issues:
            # Color per level.
            color = self._colors.get("error", "#EB5757") if issue.level == ERROR else self._colors.get("warning", "#F2994A")
            # Symbol per level.
            symbol = "&#10006;" if issue.level == ERROR else "&#9888;"
            # Line.
            lines.append(f"<span style='color:{color}'>{symbol} {html.escape(issue.text())}</span>")
        # No issues at all.
        if not issues:
            # OK line.
            lines.append(f"<span style='color:{self._colors.get('ok', '#27AE60')}'>&#10004; Sequence is valid.</span>")
        # Show.
        self.w["lblMessages"].setText("<br>".join(lines))

    def _refresh_buttons(self) -> None:
        """Enable / disable buttons and editing for the run state."""
        # Run status.
        status = self.runner.status()
        # Active run.
        active = status.active
        # Editing widgets are locked while running.
        for name in ("btnNew", "btnLoad", "btnAddRow", "btnInsertRow", "btnDeleteRow", "btnDuplicateRow",
                     "btnMoveUp", "btnMoveDown", "leName", "leDescription", "cbEndState", "sbRepeat"):
            # Enable when idle.
            self.w[name].setEnabled(not active)
        # Table cells read-only while running.
        self.w["twSteps"].setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers if active else
                                          QAbstractItemView.EditTrigger.AllEditTriggers)
        # Selectors in the table.
        for row in range(self.w["twSteps"].rowCount()):
            # Both selector columns.
            for field in ("unit", "action"):
                # Selector widget.
                combo = self.w["twSteps"].cellWidget(row, COLUMN[field])
                # Enable when idle.
                if combo is not None:
                    # Apply.
                    combo.setEnabled(not active)
        # Start needs a configured service and no running sequence.
        self.w["btnStart"].setEnabled(not active and bool(self._service and self._service.state.configured))
        # Pause / Resume while active.
        self.w["btnPause"].setEnabled(active)
        # Pause button text.
        self.w["btnPause"].setText("▶ Resume" if status.state == STATE_PAUSED else "❚❚ Pause")
        # Stop while active.
        self.w["btnStop"].setEnabled(active)

    def selected_row(self) -> int:
        """Return the selected row index (-1 = none)."""
        # Selected rows.
        rows = self.w["twSteps"].selectionModel().selectedRows() if self.w["twSteps"].selectionModel() else []
        # First selected row or the current row.
        return rows[0].row() if rows else self.w["twSteps"].currentRow()

    # ------------------------------------------------------------------ edit handlers

    def _row_action(self, action) -> None:
        """
        Run a row tool and select the resulting row.

        Inputs:
            action (Callable[[int], int]): Editor function taking the selected
                row and returning the row to select.
        """
        # Run the tool.
        row = action(self.selected_row())
        # Rebuild the table.
        self._fill_table()
        # Select the resulting row.
        if 0 <= row < self.w["twSteps"].rowCount():
            # Select it.
            self.w["twSteps"].selectRow(row)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        """
        Handler: a numeric cell was edited.

        Inputs:
            item (QTableWidgetItem): Changed cell.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Field of the column.
        field = TABLE_COLUMNS[item.column()][0]
        # Only numeric cells are edited by text.
        if field not in ("duration", "voltage", "current"):
            # Ignore.
            return
        # Store the parsed value.
        self.editor.set_cell(item.row(), field, item.text())
        # Show valid numbers in a clean format (invalid text stays for correction).
        value = getattr(self.editor.sequence.steps[item.row()], field)
        # Reformat valid values.
        if value is not None:
            # Suppress the recursive itemChanged.
            self._loading = True
            # Clean text.
            item.setText(format_number(value))
            # Handlers active.
            self._loading = False
        # Refresh preview, validation, file label.
        self._refresh_view()

    def _on_combo(self, row: int, field: str, value: str) -> None:
        """
        Handler: unit or action selector changed.

        Inputs:
            row (int): Row index.
            field (str): "unit" or "action".
            value (str): Selected id.
        """
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Store.
        self.editor.set_cell(row, field, value)
        # Refresh.
        self._refresh_view()

    def _on_options(self, *_args) -> None:
        """Handler: name, description, end state or repeat changed."""
        # Ignore while filling.
        if self._loading:
            # Nothing to do.
            return
        # Store all options.
        self.editor.set_options(name=self.w["leName"].text(), description=self.w["leDescription"].text(),
                                end_state=self.w["cbEndState"].currentData(),
                                repeat_count=self.w["sbRepeat"].value())
        # Refresh.
        self._refresh_view()

    def apply_config(self, *_args) -> None:
        """Use the active settings again (after Settings → Save): ranges, headers, validation."""
        # Active configuration object.
        self.editor.config = self._ctx.config
        # Runner uses it for the next run.
        self.runner.config = self._ctx.config
        # Refresh everything.
        self._refresh_view()

    # ------------------------------------------------------------------ files

    def confirm_discard(self) -> bool:
        """Ask whether unsaved changes may be discarded (True = yes)."""
        # Ask the user.
        answer = QMessageBox.question(self, "Unsaved changes", "The sequence has unsaved changes. Discard them?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        # Yes = discard.
        return answer == QMessageBox.StandardButton.Yes

    def ask_open_path(self) -> Optional[Path]:
        """Ask for a file to open (None = cancelled)."""
        # Make sure the folder exists.
        self._ctx.paths.sequences_dir.mkdir(parents=True, exist_ok=True)
        # File dialog in the sequences folder.
        name, _filter = QFileDialog.getOpenFileName(self, "Load sequence", str(self._ctx.paths.sequences_dir),
                                                    "Sequence files (*.json)")
        # Path or None.
        return Path(name) if name else None

    def ask_save_path(self) -> Optional[Path]:
        """Ask for a file name to save (None = cancelled)."""
        # Make sure the folder exists.
        self._ctx.paths.sequences_dir.mkdir(parents=True, exist_ok=True)
        # Suggested name: sequence name or "sequence".
        suggested = self._ctx.paths.sequences_dir / ((self.editor.sequence.name or "sequence") + ".json")
        # File dialog.
        name, _filter = QFileDialog.getSaveFileName(self, "Save sequence", str(suggested), "Sequence files (*.json)")
        # Path or None.
        return Path(name) if name else None

    def _remember_file(self) -> None:
        """Store the current file as ui.last_sequence_file (saved with the preferences)."""
        # Relative path.
        self._ctx.config.ui.last_sequence_file = self.editor.relative_path()

    def new_sequence(self) -> None:
        """Action: New."""
        # Ask before discarding.
        if self.editor.dirty and not self.confirm_discard():
            # Cancelled.
            return
        # New sequence.
        self.editor.new()
        # Status.
        self._status = "New sequence."
        # Refill.
        self._load_all()

    def load_sequence(self, path: Optional[Path] = None) -> bool:
        """
        Action: Load.

        Inputs:
            path (Optional[Path]): File (None = ask).

        Returns:
            bool: True when loaded.
        """
        # Ask before discarding.
        if self.editor.dirty and not self.confirm_discard():
            # Cancelled.
            return False
        # Ask for the file.
        path = path or self.ask_open_path()
        # Cancelled.
        if path is None:
            # Nothing loaded.
            return False
        # Load.
        try:
            # Read the file.
            self.editor.load(path)
        # Broken file.
        except SequenceFileError as exc:
            # Show it.
            self._status = f"Not loaded: {exc}"
            # Refresh.
            self._refresh_view()
            # Failed.
            return False
        # Remember the file.
        self._remember_file()
        # Status.
        self._status = f"Loaded {Path(path).name}."
        # Refill.
        self._load_all()
        # Loaded.
        return True

    def save_sequence(self, save_as: bool = False) -> bool:
        """
        Action: Save / Save As.

        Inputs:
            save_as (bool): True = always ask for a file name.

        Returns:
            bool: True when saved.
        """
        # Take the option fields over first (editingFinished may not have fired).
        self._on_options()
        # File name: ask for Save As or when unsaved.
        path = self.ask_save_path() if (save_as or self.editor.path is None) else self.editor.path
        # Cancelled.
        if path is None:
            # Not saved.
            return False
        # Write.
        try:
            # Save.
            written = self.editor.save(path)
        # Disk problem.
        except OSError as exc:
            # Show it.
            self._status = f"Not saved: {exc}"
            # Refresh.
            self._refresh_view()
            # Failed.
            return False
        # Remember the file.
        self._remember_file()
        # Status (invalid sequences can be saved, but a hint is given).
        self._status = f"Saved {written.name}." + (" (The sequence still has errors.)"
                                                   if errors_of(self.editor.validate()) else "")
        # The name may have been filled from the file name.
        self._load_all()
        # Saved.
        return True

    # ------------------------------------------------------------------ run

    def start_run(self) -> bool:
        """
        Action: Start.

        Returns:
            bool: True when the run started.
        """
        # Take the option fields over first.
        self._on_options()
        # Use the active settings.
        self.runner.config = self._ctx.config
        # Start (validates).
        issues = self.runner.start(self.editor.sequence)
        # Errors: not started.
        if errors_of(issues):
            # Status.
            self._status = "Not started - fix the errors below."
            # Show the runner's extra reasons (e.g. channels not set up) as status too.
            extra = [issue.text() for issue in issues if issue.row is None and "set up" in issue.message]
            # Append them.
            if extra:
                # Add.
                self._status += " " + " ".join(extra)
            # Refresh.
            self._refresh_view()
            # Not started.
            return False
        # Status.
        self._status = "Sequence started."
        # Poll the status.
        self._poll.start()
        # First refresh right away.
        self.poll()
        # Started.
        return True

    def toggle_pause(self) -> None:
        """Action: Pause / Resume."""
        # Resume when paused.
        if self.runner.status().state == STATE_PAUSED:
            # Resume.
            self.runner.resume()
        # Pause otherwise.
        else:
            # Pause.
            self.runner.pause()
        # Refresh.
        self.poll()

    def stop_run(self) -> None:
        """Action: Stop (abort): output OFF + safe value."""
        # Request the stop.
        self.runner.stop()
        # Status.
        self._status = "Stopping ..."
        # Refresh.
        self.poll()

    def poll(self) -> None:
        """Timer: update the run monitor and inform the front panel."""
        # Snapshot.
        status = self.runner.status()
        # State text.
        state_text = STATE_TEXT.get(status.state, status.state)
        # Add the message for errors.
        if status.state == STATE_ERROR and status.message:
            # Error text.
            state_text += f": {status.message}"
        # Show it.
        self.w["lblRunState"].setText(state_text)
        # Cycle text.
        total_cycles = "∞" if status.repeat_count == 0 else str(status.repeat_count)
        # Show it.
        self.w["lblCycle"].setText(f"Cycle {status.cycle} / {total_cycles}" if status.cycle else "")
        # Step text.
        if status.row is not None:
            # Elapsed time in the step.
            in_step = min(status.elapsed_s - status.step_start_s, status.step_duration_s)
            # Show it.
            self.w["lblStep"].setText(f"Step {status.row + 1}: {format_seconds(in_step)} / "
                                      f"{format_seconds(status.step_duration_s)}  (remaining "
                                      f"{format_seconds(status.step_duration_s - in_step)})")
        # Before the first step.
        else:
            # Empty.
            self.w["lblStep"].setText("")
        # Total time text: remaining for finite runs, elapsed only for endless runs.
        if status.total_duration_s is None:
            # Endless.
            self.w["lblTime"].setText(f"Elapsed {format_seconds(status.elapsed_s)}")
            # Progress = position in the current cycle.
            fraction = ((status.elapsed_s % status.cycle_duration_s) / status.cycle_duration_s
                        if status.cycle_duration_s > 0 else 0.0)
        # Finite.
        else:
            # Total and remaining.
            self.w["lblTime"].setText(f"Total {format_seconds(status.elapsed_s)} / {format_seconds(status.total_duration_s)}"
                                      f"  (remaining {format_seconds(status.total_duration_s - status.elapsed_s)})")
            # Progress = position in the whole run.
            fraction = status.elapsed_s / status.total_duration_s if status.total_duration_s > 0 else 0.0
        # Progress bar 0..100.
        self.w["pbProgress"].setValue(int(max(0.0, min(1.0, fraction)) * 100))
        # Timing statistics.
        self.w["lblTiming"].setText(f"Timing: {status.steps_done} step start(s), max deviation "
                                    f"{status.max_dev_ms:.1f} ms, average {status.avg_dev_ms:.1f} ms"
                                    if status.steps_done else "")
        # Highlight the current row while active.
        if status.active and status.row is not None:
            # Select it.
            self.w["twSteps"].selectRow(status.row)
        # Values to the front panel while active.
        if status.active:
            # Commanded values.
            self.values_changed.emit(dict(status.values))
        # Run started.
        if status.active and not self._was_active:
            # Remember.
            self._was_active = True
            # Tell the front panel.
            self.running_changed.emit(True)
            # Buttons.
            self._refresh_buttons()
        # Run ended.
        elif not status.active and self._was_active:
            # Remember.
            self._was_active = False
            # Stop polling.
            self._poll.stop()
            # Final values to the front panel.
            self.values_changed.emit(dict(status.values))
            # Tell the front panel.
            self.running_changed.emit(False)
            # Status text.
            self._status = f"Sequence {STATE_TEXT.get(status.state, status.state).lower()}" + (
                f": {status.message}" if status.state == STATE_ERROR else ".")
            # Refresh everything.
            self._refresh_view()
        # Pause button text while active.
        elif status.active:
            # Buttons.
            self._refresh_buttons()

    # ------------------------------------------------------------------ closing

    def confirm_stop(self) -> bool:
        """Ask whether a running sequence may be stopped to close the window (True = yes)."""
        # Ask the user.
        answer = QMessageBox.question(self, "Sequence running",
                                      "A sequence is running. Stop it (output OFF) and close the window?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        # Yes = stop.
        return answer == QMessageBox.StandardButton.Yes

    def stop_and_wait(self, timeout: float = 3.0) -> None:
        """
        Stop a running sequence and wait for its end state (used when closing).

        Inputs:
            timeout (float): Seconds to wait.
        """
        # Nothing running.
        if not self.runner.active:
            # Done.
            return
        # Stop.
        self.runner.stop()
        # Wait for the run thread.
        self.runner.join(timeout)
        # Final update (unlocks the front panel).
        self.poll()

    def closeEvent(self, event) -> None:  # noqa: N802 (Qt method name)
        """
        Qt close handler: stop a running sequence (after asking) and check unsaved changes.

        Inputs:
            event (QCloseEvent): Close event.
        """
        # Running and the user says No: stay open.
        if self.runner.active and not self.confirm_stop():
            # Cancel.
            event.ignore()
            # Done.
            return
        # Stop and wait for the end state.
        self.stop_and_wait()
        # Unsaved changes and the user says No: stay open.
        if self.editor.dirty and not self.confirm_discard():
            # Cancel.
            event.ignore()
            # Done.
            return
        # Close.
        event.accept()
