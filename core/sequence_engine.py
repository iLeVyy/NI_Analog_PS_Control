"""
core/sequence_engine.py
=======================
Sequence engine (Project_Plan.md sections 10.2 - 10.5, decisions D-01 - D-03, D-08):

    validate_sequence()  - rules before a run (also used live by the editor)
    build_plan()         - converts the rows into a time schedule (RunPlan)
    SequenceRunner       - runs a plan in its own thread with a player,
                           Pause / Resume / Stop, end-state handling,
                           timing log and a thread-safe status snapshot

The runner talks to the hardware only through the DAQ service
(set_setpoint / set_output), which is thread-safe.

End states (D-03, section 10.5):
    normal end  -> sequence end_state: "output_off" (default) or "hold_last"
    user stop   -> always Output OFF + safe value
    error       -> always safe state (Output OFF)

LabVIEW analogy:
    A cloneable sequencer module: start / pause / stop requests, a helper
    loop (thread) that runs the timed steps, and a status "broadcast"
    that the UI polls.
"""

# Import math for the finite-number check.
import math
# Import sys to detect Windows (timer resolution).
import sys
# Import threading for the run thread and the status lock.
import threading
# Import deque to keep the latest timing records.
from collections import deque
# Import dataclass tools.
from dataclasses import dataclass, field, replace
# Import typing helpers.
from typing import Any, Callable, Deque, Dict, List, Optional

# Import the catalog type.
from core.catalogs import Catalog
# Import the scale builder (PS ranges and AO preview).
from core.config_manager import channel_scale
# Import the module logger factory.
from core.logger import get_logger
# Import the configuration type.
from core.models import AppConfig
# Import the player tools.
from core.sequence_players import (CURRENT, PLAYERS, RESULT_COMPLETED, RESULT_STOPPED, VOLTAGE,
                                   Clock, RunControl, SequencePlayer)
# Import the data model.
from core.sequence_model import Sequence, SequenceStep

# Logger for this module.
_log = get_logger("sequence_engine")

# Issue level: blocks the run.
ERROR = "error"
# Issue level: shown, does not block.
WARNING = "warning"

# Runner state: never started.
STATE_IDLE = "idle"
# Runner state: running.
STATE_RUNNING = "running"
# Runner state: paused.
STATE_PAUSED = "paused"
# Runner state: finished normally.
STATE_COMPLETED = "completed"
# Runner state: stopped by the user.
STATE_STOPPED = "stopped"
# Runner state: ended by an error.
STATE_ERROR = "error"
# States in which a run is active.
ACTIVE_STATES = (STATE_RUNNING, STATE_PAUSED)


@dataclass
class Issue:
    """
    One validation message.

    Attributes:
        row (Optional[int]): 0-based row index (None = whole sequence).
        field (Optional[str]): "duration", "unit", "voltage", "current",
            "action", or None.
        message (str): Text for the user.
        level (str): ERROR or WARNING.
    """

    # Row index.
    row: Optional[int]
    # Field name.
    field: Optional[str]
    # Message text.
    message: str
    # Level.
    level: str = ERROR

    def text(self) -> str:
        """Return the message with the row number (1-based) for display."""
        # Prefix the row number when the issue belongs to a row.
        return f"Row {self.row + 1}: {self.message}" if self.row is not None else self.message


def duration_seconds(step: SequenceStep, units: Catalog) -> Optional[float]:
    """
    Convert a step duration to seconds using config/presets/duration_units.json.

    Inputs:
        step (SequenceStep): Step.
        units (Catalog): duration_units catalog.

    Returns:
        Optional[float]: Seconds, or None when duration or unit is invalid.
    """
    # Unit item.
    unit = units.get(step.unit)
    # Missing duration or unknown unit.
    if step.duration is None or unit is None or not math.isfinite(step.duration):
        # Not convertible.
        return None
    # Duration times the unit factor.
    return step.duration * float(unit["factor_s"])


def executed_rows(sequence: Sequence, actions: Catalog) -> List[int]:
    """
    Return the indexes of the rows that are executed (before the first Stop).

    Inputs:
        sequence (Sequence): Sequence.
        actions (Catalog): sequence_actions catalog.

    Returns:
        List[int]: Row indexes with a setpoint action, in order.
    """
    # Result.
    rows: List[int] = []
    # Walk the rows.
    for index, step in enumerate(sequence.steps):
        # Action item.
        action = actions.get(step.action)
        # The first non-setpoint action (Stop) ends the sequence.
        if action is not None and not action.get("needs_setpoint", True):
            # Stop here.
            break
        # Keep the row.
        rows.append(index)
    # Return the rows.
    return rows


def validate_sequence(sequence: Sequence, config: AppConfig, catalogs: Dict[str, Catalog]) -> List[Issue]:
    """
    Check a sequence before a run (section 10.3).

    Inputs:
        sequence (Sequence): Sequence to check.
        config (AppConfig): Active settings (PS ranges, minimum step time).
        catalogs (Dict[str, Catalog]): Loaded catalogs.

    Returns:
        List[Issue]: Errors and warnings (no ERROR = runnable).
    """
    # Collected issues.
    issues: List[Issue] = []
    # Catalogs used here.
    units, actions, end_states = (catalogs["duration_units"], catalogs["sequence_actions"],
                                  catalogs["end_states"])
    # Minimum step time in seconds (D-01).
    min_s = config.sequence.min_step_duration_ms / 1000.0
    # PS range per quantity (D-06).
    ranges = {VOLTAGE: (config.voltage_channel.ps_min, config.voltage_channel.ps_max, "V"),
              CURRENT: (config.current_channel.ps_min, config.current_channel.ps_max, "A")}
    # Only the software player exists in v1.0.
    if sequence.timing_mode not in PLAYERS:
        # Explain (FE-01).
        issues.append(Issue(None, None, f"Timing mode '{sequence.timing_mode}' is not available "
                                        f"(only: {', '.join(PLAYERS)})."))
    # End state must be known.
    if end_states.get(sequence.end_state) is None:
        # Explain.
        issues.append(Issue(None, None, f"Unknown end state '{sequence.end_state}'."))
    # Repeat count must not be negative.
    if sequence.repeat_count < 0:
        # Explain.
        issues.append(Issue(None, None, "Repeat count must be 0 (until stopped) or more."))
    # Rows that run.
    runnable = executed_rows(sequence, actions)
    # Check every row.
    for index, step in enumerate(sequence.steps):
        # Action item.
        action = actions.get(step.action)
        # Unknown action.
        if action is None:
            # Explain.
            issues.append(Issue(index, "action", f"Unknown action '{step.action}'."))
            # Nothing more to check in this row.
            continue
        # Rows after the first Stop are ignored.
        if index not in runnable:
            # Only warn for rows after the Stop row itself.
            if action.get("needs_setpoint", True):
                # Explain.
                issues.append(Issue(index, None, "Row after Stop - it is ignored.", WARNING))
            # Next row.
            continue
        # Duration in seconds.
        seconds = duration_seconds(step, units)
        # Unknown unit.
        if units.get(step.unit) is None:
            # Explain.
            issues.append(Issue(index, "unit", f"Unknown unit '{step.unit}'."))
        # Missing or invalid duration.
        elif seconds is None:
            # Explain.
            issues.append(Issue(index, "duration", "Duration missing or not a number."))
        # Shorter than the minimum (D-01).
        elif seconds < min_s - 1e-9:
            # Explain with the limit.
            issues.append(Issue(index, "duration", f"Duration below the minimum of "
                                                   f"{config.sequence.min_step_duration_ms:g} ms."))
        # Voltage is mandatory (D-02).
        if step.voltage is None or not math.isfinite(step.voltage):
            # Explain.
            issues.append(Issue(index, "voltage", "Voltage missing or not a number."))
        # Voltage inside the PS range.
        else:
            # Range check.
            issues.extend(_range_issue(index, VOLTAGE, step.voltage, ranges[VOLTAGE]))
        # Current is optional; check it when given.
        if step.current is not None:
            # Range check.
            issues.extend(_range_issue(index, CURRENT, step.current, ranges[CURRENT]))
    # At least one executed row is needed.
    if not runnable:
        # Explain.
        issues.append(Issue(None, None, "The sequence has no Set or Ramp row before Stop."))
    # Return everything.
    return issues


def _range_issue(row: int, quantity: str, value: float, rng) -> List[Issue]:
    """
    Check one value against the power supply range.

    Inputs:
        row (int): Row index.
        quantity (str): VOLTAGE or CURRENT.
        value (float): Value to check.
        rng (Tuple[float, float, str]): (min, max, unit).

    Returns:
        List[Issue]: One error, or an empty list.
    """
    # Unpack the range.
    low, high, unit = rng
    # Outside the range (small tolerance for float input).
    if not math.isfinite(value) or value < low - 1e-9 or value > high + 1e-9:
        # Explain with the range.
        return [Issue(row, quantity, f"{quantity.capitalize()} {value:g} {unit} outside the power "
                                     f"supply range {low:g} – {high:g} {unit}.")]
    # Inside.
    return []


def errors_of(issues: List[Issue]) -> List[Issue]:
    """Return only the ERROR issues."""
    # Filter by level.
    return [issue for issue in issues if issue.level == ERROR]


@dataclass
class Segment:
    """
    One executed row in the time schedule.

    Attributes:
        row (int): Row index in the sequence.
        kind (str): "set" or "ramp".
        offset_s (float): Start time within one cycle.
        duration_s (float): Duration.
        voltage (float): Voltage target.
        current (Optional[float]): Current target (None = unchanged).
    """

    # Row index.
    row: int
    # "set" or "ramp".
    kind: str
    # Start within the cycle.
    offset_s: float
    # Duration.
    duration_s: float
    # Voltage target.
    voltage: float
    # Current target or None.
    current: Optional[float]


@dataclass
class RunPlan:
    """
    Time schedule of a sequence.

    Attributes:
        segments (List[Segment]): Segments of one cycle.
        cycle_duration_s (float): Duration of one cycle.
        repeat_count (int): 1 = once, N = N times, 0 = until stopped.
    """

    # Segments of one cycle.
    segments: List[Segment]
    # Cycle duration.
    cycle_duration_s: float
    # Repeat count.
    repeat_count: int

    @property
    def total_duration_s(self) -> Optional[float]:
        """Total duration, or None for "until stopped"."""
        # Infinite for repeat 0.
        return None if self.repeat_count == 0 else self.cycle_duration_s * self.repeat_count


def build_plan(sequence: Sequence, catalogs: Dict[str, Catalog]) -> RunPlan:
    """
    Convert a VALID sequence into a time schedule.

    Inputs:
        sequence (Sequence): Sequence without errors (validate_sequence first).
        catalogs (Dict[str, Catalog]): Loaded catalogs.

    Returns:
        RunPlan: The schedule.
    """
    # Segment list.
    segments: List[Segment] = []
    # Running start time.
    offset = 0.0
    # Executed rows only.
    for index in executed_rows(sequence, catalogs["sequence_actions"]):
        # Row.
        step = sequence.steps[index]
        # Duration in seconds.
        seconds = duration_seconds(step, catalogs["duration_units"])
        # Add the segment.
        segments.append(Segment(index, step.action, offset, seconds, step.voltage, step.current))
        # Next start time.
        offset += seconds
    # Build the plan.
    return RunPlan(segments, offset, sequence.repeat_count)


def preview_values(sequence: Sequence, config: AppConfig, start: Dict[str, float]) -> List[Dict[str, Any]]:
    """
    Calculate the AO volts per row for the optional preview columns (section 10.2).

    Inputs:
        sequence (Sequence): Sequence (invalid rows give None values).
        config (AppConfig): Active settings (scales).
        start (Dict[str, float]): PS values before the first row.

    Returns:
        List[Dict[str, Any]]: Per row {"voltage": (ao_from, ao_to) or None,
        "current": (ao_from, ao_to) or None}; from == to for Set rows.
    """
    # Scales of both channels (None when the ranges are invalid).
    scales = {}
    # Build each scale.
    for quantity, channel in ((VOLTAGE, config.voltage_channel), (CURRENT, config.current_channel)):
        # Scale.
        scale = channel_scale(channel)
        # Keep only valid scales.
        scales[quantity] = scale if scale.is_valid else None
    # Values before the current row.
    values = dict(start)
    # Result rows.
    rows: List[Dict[str, Any]] = []
    # Walk all rows.
    for step in sequence.steps:
        # Preview of this row.
        row: Dict[str, Any] = {VOLTAGE: None, CURRENT: None}
        # Only Set / Ramp rows have values.
        if step.action in ("set", "ramp"):
            # Both quantities.
            for quantity, target in ((VOLTAGE, step.voltage), (CURRENT, step.current)):
                # Unchanged current keeps the previous value.
                target = values.get(quantity) if (quantity == CURRENT and target is None) else target
                # Skip missing values or invalid scales.
                if target is None or scales[quantity] is None:
                    # Nothing to show.
                    continue
                # Start value: previous (ramp) or target (set).
                origin = values.get(quantity, target) if step.action == "ramp" else target
                # AO volts.
                row[quantity] = (scales[quantity].to_ao(origin), scales[quantity].to_ao(target))
                # Remember for the next row.
                values[quantity] = target
        # Store the row.
        rows.append(row)
    # Return the preview.
    return rows


@dataclass
class TimingRecord:
    """
    Planned vs. actual start of one segment.

    Attributes:
        cycle (int): Cycle number.
        row (int): Row index.
        planned_ms (float): Planned run time.
        actual_ms (float): Actual run time.
    """

    # Cycle number.
    cycle: int
    # Row index.
    row: int
    # Planned time.
    planned_ms: float
    # Actual time.
    actual_ms: float

    @property
    def deviation_ms(self) -> float:
        """Actual minus planned time in ms."""
        # Difference.
        return self.actual_ms - self.planned_ms


@dataclass
class RunStatus:
    """
    Snapshot of a run for the UI (thread-safe copy).

    Attributes:
        state (str): STATE_* constant.
        message (str): End reason / error text.
        cycle (int): Current cycle (1-based, 0 before start).
        repeat_count (int): 0 = until stopped.
        row (Optional[int]): Current row index.
        step_start_s (float): Run time at which the current step started.
        step_duration_s (float): Duration of the current step.
        elapsed_s (float): Run time without pauses.
        cycle_duration_s (float): Duration of one cycle.
        total_duration_s (Optional[float]): None = until stopped.
        values (Dict[str, float]): Last commanded PS values.
        steps_done (int): Number of segment starts.
        max_dev_ms (float): Largest absolute timing deviation.
        avg_dev_ms (float): Average absolute timing deviation.
    """

    # State.
    state: str = STATE_IDLE
    # Message.
    message: str = ""
    # Cycle.
    cycle: int = 0
    # Repeat count.
    repeat_count: int = 1
    # Row.
    row: Optional[int] = None
    # Step start.
    step_start_s: float = 0.0
    # Step duration.
    step_duration_s: float = 0.0
    # Run time.
    elapsed_s: float = 0.0
    # Cycle duration.
    cycle_duration_s: float = 0.0
    # Total duration.
    total_duration_s: Optional[float] = None
    # Commanded values.
    values: Dict[str, float] = field(default_factory=dict)
    # Segment starts.
    steps_done: int = 0
    # Max deviation.
    max_dev_ms: float = 0.0
    # Average deviation.
    avg_dev_ms: float = 0.0

    @property
    def active(self) -> bool:
        """True while running or paused."""
        # Active states.
        return self.state in ACTIVE_STATES


class _PlayerIO:
    """Interface object handed to the player (see SequencePlayer docstring)."""

    def __init__(self, runner: "SequenceRunner") -> None:
        """
        Create the interface for one run.

        Inputs:
            runner (SequenceRunner): Owning runner.
        """
        # Owning runner.
        self._runner = runner
        # Clock.
        self.clock = runner.clock
        # Control flags.
        self.control = runner.control
        # Last commanded values.
        self.values = runner.values
        # Ramp interval in seconds.
        self.ramp_interval_s = runner.config.sequence.ramp_interval_ms / 1000.0
        # Active-wait time in seconds.
        self.spin_wait_s = runner.config.sequence.spin_wait_ms / 1000.0

    def write(self, values: Dict[str, float]) -> None:
        """Write PS values through the runner."""
        # Delegate.
        self._runner._write(values)

    def check(self) -> None:
        """Raise when the output was switched OFF outside the sequence."""
        # Delegate.
        self._runner._check_output()

    def on_segment(self, cycle: int, segment: Segment, planned_s: float, actual_s: float) -> None:
        """Record the start of a segment."""
        # Delegate.
        self._runner._record(cycle, segment, planned_s, actual_s)


class SequenceRunner:
    """
    Runs one sequence at a time in its own thread.

    Usage:
        runner = SequenceRunner(service, config, catalogs)
        issues = runner.start(sequence)     # errors -> not started
        runner.pause(); runner.resume(); runner.stop()
        runner.status()                     # snapshot for the UI
        runner.join(timeout)
    """

    def __init__(self, service: Any, config: AppConfig, catalogs: Dict[str, Catalog],
                 clock: Optional[Clock] = None,
                 player_factory: Optional[Callable[[str], SequencePlayer]] = None) -> None:
        """
        Create the runner.

        Inputs:
            service (DaqService): Started DAQ service.
            config (AppConfig): Active settings (read at start()).
            catalogs (Dict[str, Catalog]): Loaded catalogs.
            clock (Optional[Clock]): Time source (tests use a fake clock).
            player_factory (Optional[Callable]): Creates the player for a
                timing mode (default: PLAYERS table).

        Returns:
            None.
        """
        # DAQ service.
        self.service = service
        # Settings.
        self.config = config
        # Catalogs.
        self.catalogs = catalogs
        # Time source.
        self.clock = clock or Clock()
        # Player factory.
        self._player_factory = player_factory or (lambda mode: PLAYERS[mode]())
        # Control flags of the current run.
        self.control = RunControl()
        # Last commanded PS values.
        self.values: Dict[str, float] = {}
        # Status snapshot (protected by the lock).
        self._status = RunStatus()
        # Lock for the status.
        self._lock = threading.Lock()
        # Run thread.
        self._thread: Optional[threading.Thread] = None
        # Player of the current run.
        self._player: Optional[SequencePlayer] = None
        # Latest timing records (memory-bounded for endless runs).
        self.timing: Deque[TimingRecord] = deque(maxlen=2000)
        # Sum of absolute deviations (for the average).
        self._dev_sum = 0.0

    # ------------------------------------------------------------------ public API

    def status(self) -> RunStatus:
        """Return a copy of the run status (any thread)."""
        # Copy under the lock.
        with self._lock:
            # Copy including the values dict.
            snapshot = replace(self._status, values=dict(self._status.values))
        # Live run time from the player.
        if snapshot.active and self._player is not None:
            # Current run time.
            snapshot.elapsed_s = self._player.elapsed()
        # Return it.
        return snapshot

    @property
    def active(self) -> bool:
        """True while a run is active."""
        # From the status.
        return self.status().active

    def start(self, sequence: Sequence) -> List[Issue]:
        """
        Validate and start a sequence.

        Inputs:
            sequence (Sequence): Sequence to run.

        Returns:
            List[Issue]: Issues; when it contains errors the run did NOT start.
        """
        # Only one run at a time.
        if self.active:
            # Refuse.
            return [Issue(None, None, "A sequence is already running.")]
        # Validate the sequence.
        issues = validate_sequence(sequence, self.config, self.catalogs)
        # The AO channels must be set up.
        if self.service is None or not self.service.state.configured:
            # Refuse.
            issues.append(Issue(None, None, "AO channels are not set up - check Settings."))
        # Errors block the start.
        if errors_of(issues):
            # Return the reasons.
            return issues
        # Build the schedule.
        plan = build_plan(sequence, self.catalogs)
        # Fresh control flags.
        self.control = RunControl()
        # Start values = setpoints stored in the service.
        self.values = dict(self.service.state.setpoints)
        # Fresh timing statistics.
        self.timing.clear()
        # Reset the deviation sum.
        self._dev_sum = 0.0
        # Player for the timing mode.
        self._player = self._player_factory(sequence.timing_mode)
        # Initial status.
        with self._lock:
            # New snapshot.
            self._status = RunStatus(state=STATE_RUNNING, cycle=0, repeat_count=plan.repeat_count,
                                     cycle_duration_s=plan.cycle_duration_s,
                                     total_duration_s=plan.total_duration_s, values=dict(self.values))
        # Run thread.
        self._thread = threading.Thread(target=self._run, args=(sequence, plan),
                                        name="SequenceRunner", daemon=True)
        # Start it.
        self._thread.start()
        # Log the start.
        _log.info("Sequence '%s' started: %d step(s)/cycle, cycle %.3f s, repeat %s, end state %s",
                  sequence.name or "(unnamed)", len(plan.segments), plan.cycle_duration_s,
                  plan.repeat_count or "until stopped", sequence.end_state)
        # Return the (warning) issues.
        return issues

    def pause(self) -> None:
        """Pause the run (values are held, time stands still)."""
        # Only while running.
        if self.status().state != STATE_RUNNING:
            # Nothing to do.
            return
        # Request the pause.
        self.control.pause()
        # Update the state.
        self._set_status(state=STATE_PAUSED)
        # Log it.
        _log.info("Sequence paused")

    def resume(self) -> None:
        """Resume a paused run."""
        # Only while paused.
        if self.status().state != STATE_PAUSED:
            # Nothing to do.
            return
        # Update the state first (the player continues right away).
        self._set_status(state=STATE_RUNNING)
        # Request the resume.
        self.control.resume()
        # Log it.
        _log.info("Sequence resumed")

    def stop(self) -> None:
        """Request Stop (abort): the run ends with Output OFF + safe value."""
        # Request the stop (no effect when not running).
        self.control.request_stop()

    def join(self, timeout: Optional[float] = None) -> bool:
        """
        Wait for the run thread to end.

        Inputs:
            timeout (Optional[float]): Seconds to wait (None = forever).

        Returns:
            bool: True when no run thread is alive any more.
        """
        # No thread: nothing to wait for.
        if self._thread is None:
            # Done.
            return True
        # Wait.
        self._thread.join(timeout)
        # Report whether it ended.
        return not self._thread.is_alive()

    # ------------------------------------------------------------------ run thread

    def _set_status(self, **changes: Any) -> None:
        """Change status fields under the lock."""
        # Lock.
        with self._lock:
            # Apply each change.
            for name, value in changes.items():
                # Set the field.
                setattr(self._status, name, value)

    def _timeout(self) -> float:
        """Seconds to wait for one DAQ command."""
        # From the DAQ settings.
        return self.config.daq.write_timeout_s

    def _write(self, values: Dict[str, float]) -> None:
        """
        Write PS values through the DAQ service and remember them.

        Inputs:
            values (Dict[str, float]): {quantity: PS value}.

        Raises:
            Exception: Service errors (the service already applied its safe state).
        """
        # Each quantity.
        for quantity, value in values.items():
            # Write and wait for the result (raises on errors).
            self.values[quantity] = self.service.set_setpoint(quantity, value).result(self._timeout())
        # Status copy of the values.
        self._set_status(values=dict(self.values))
        # Output must still be ON.
        self._check_output()

    def _check_output(self) -> None:
        """Raise when the output is not ON any more (error or Output OFF elsewhere)."""
        # Output state of the service.
        if not self.service.state.output_on:
            # Abort the run.
            raise RuntimeError("Output was switched OFF outside the sequence")

    def _record(self, cycle: int, segment: Segment, planned_s: float, actual_s: float) -> None:
        """
        Store and log the timing of a segment start; update the status.

        Inputs:
            cycle (int): Cycle number.
            segment (Segment): Segment.
            planned_s (float): Planned run time.
            actual_s (float): Actual run time.
        """
        # Timing record.
        record = TimingRecord(cycle, segment.row, planned_s * 1000.0, actual_s * 1000.0)
        # Keep it.
        self.timing.append(record)
        # Absolute deviation.
        deviation = abs(record.deviation_ms)
        # Add to the sum.
        self._dev_sum += deviation
        # Update the status.
        with self._lock:
            # One more segment.
            self._status.steps_done += 1
            # Largest deviation.
            self._status.max_dev_ms = max(self._status.max_dev_ms, deviation)
            # Average deviation.
            self._status.avg_dev_ms = self._dev_sum / self._status.steps_done
            # Current position.
            self._status.cycle, self._status.row = cycle, segment.row
            # Current step timing.
            self._status.step_start_s, self._status.step_duration_s = planned_s, segment.duration_s
        # Timing log line (FR-15).
        _log.info("Seq timing: cycle %d row %d planned %.1f ms actual %.1f ms deviation %+.1f ms",
                  cycle, segment.row + 1, record.planned_ms, record.actual_ms, record.deviation_ms)

    def _prepare_output(self, plan: RunPlan) -> None:
        """
        Store the first values and switch the output ON (before the timing starts).

        Inputs:
            plan (RunPlan): Schedule.
        """
        # First segment.
        first = plan.segments[0]
        # A Set row starts with its own values (a Ramp starts from the stored ones).
        if first.kind == "set":
            # First values.
            targets = {VOLTAGE: first.voltage}
            # Current given?
            if first.current is not None:
                # Add it.
                targets[CURRENT] = first.current
            # Store / write them.
            for quantity, value in targets.items():
                # Store in the service (written when the output is ON).
                self.values[quantity] = self.service.set_setpoint(quantity, value).result(self._timeout())
        # Output ON (writes the stored setpoints).
        if not self.service.state.output_on:
            # Switch ON and wait.
            self.service.set_output(True).result(self._timeout())

    def _apply_end_state(self, state: str, end_state: str) -> None:
        """
        Apply the end state (section 10.5).

        Inputs:
            state (str): STATE_COMPLETED, STATE_STOPPED or STATE_ERROR.
            end_state (str): Sequence end state id (normal end only).
        """
        # Normal end with "hold last value": nothing to do.
        if state == STATE_COMPLETED and end_state == "hold_last":
            # Log it.
            _log.info("Sequence end state: hold last value (output stays ON)")
            # Done.
            return
        # All other cases: Output OFF + safe value (best effort).
        try:
            # Switch OFF and wait.
            self.service.set_output(False).result(self._timeout())
        # Even the safe state failed (e.g. device removed).
        except Exception as exc:  # noqa: BLE001
            # Log it; the service already marked the output OFF.
            _log.error("End state (output OFF) failed: %s", exc)

    def _run(self, sequence: Sequence, plan: RunPlan) -> None:
        """
        Run thread: prepare, play, apply the end state, log the summary.

        Inputs:
            sequence (Sequence): Sequence (end state).
            plan (RunPlan): Schedule.
        """
        # Finer Windows timer for this run (ignored elsewhere).
        restore_timer = _begin_fine_timer()
        # Final state and message.
        state, message = STATE_ERROR, ""
        # Run and always apply the end state.
        try:
            # First values and Output ON.
            self._prepare_output(plan)
            # Execute the schedule.
            result = self._player.play(plan, _PlayerIO(self))
            # Translate the result.
            state = STATE_COMPLETED if result == RESULT_COMPLETED else STATE_STOPPED
            # Message.
            message = "Finished" if result == RESULT_COMPLETED else "Stopped by user"
        # Any error (write failed, output OFF elsewhere, ...).
        except Exception as exc:  # noqa: BLE001
            # Error state.
            state, message = STATE_ERROR, str(exc)
            # Log it.
            _log.error("Sequence error: %s", exc)
        # End handling.
        finally:
            # End state (D-03).
            self._apply_end_state(state, sequence.end_state)
            # Restore the Windows timer.
            restore_timer()
            # Status snapshot.
            snapshot = self.status()
            # Final status.
            self._set_status(state=state, message=message, elapsed_s=snapshot.elapsed_s)
            # Summary log line.
            _log.info("Sequence %s after %.3f s: %d step start(s), max deviation %.1f ms, avg %.1f ms",
                      state, snapshot.elapsed_s, snapshot.steps_done, snapshot.max_dev_ms, snapshot.avg_dev_ms)


def _begin_fine_timer() -> Callable[[], None]:
    """
    Request 1 ms timer resolution on Windows for better sleep accuracy.

    Returns:
        Callable[[], None]: Function that restores the previous resolution.
    """
    # Only on Windows.
    if sys.platform != "win32":
        # Nothing to restore.
        return lambda: None
    # Try the multimedia timer API.
    try:
        # Import ctypes only on Windows.
        import ctypes
        # Multimedia library.
        winmm = ctypes.WinDLL("winmm")
        # Request 1 ms.
        winmm.timeBeginPeriod(1)
        # Restore function.
        return lambda: winmm.timeEndPeriod(1)
    # API not available.
    except (OSError, AttributeError):
        # Nothing to restore.
        return lambda: None
