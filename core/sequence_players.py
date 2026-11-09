"""
core/sequence_players.py
========================
Sequence players: the part that executes a schedule in time
(Project_Plan.md section 10.4, decision D-01).

    Clock            - real time source (monotonic, high resolution)
    RunControl       - stop / pause / resume flags shared with the runner
    SequencePlayer   - interface of all players
    SoftwarePlayer   - v1.0 player: software timing with an absolute schedule

Absolute schedule: the target time of every step is
    start time + paused time + cumulative step durations,
so waiting errors never add up (no drift over long sequences or many cycles).

Waiting: coarse sleeps (max. 50 ms, interruptible by Stop) until shortly
before the target, then active waiting for the last spin_wait_ms.

FE-01 (future): a HardwarePlayer with the same interface can output the
schedule as a hardware-timed AO waveform; editor, file format and runner
do not change.

LabVIEW analogy:
    SequencePlayer is a parent class; SoftwarePlayer the child class with a
    timed loop that waits on absolute deadlines.
"""

# Import threading for the stop / pause events.
import threading
# Import time for the monotonic high-resolution clock.
import time
# Import abstract base class tools.
from abc import ABC, abstractmethod
# Import typing helpers.
from typing import Any, Dict, Optional

# Quantity name of the voltage channel.
VOLTAGE = "voltage"
# Quantity name of the current channel.
CURRENT = "current"
# Longest single sleep in seconds (keeps Stop / Pause responsive).
MAX_SLEEP_S = 0.05
# Shortest useful sleep in seconds; shorter waits are done actively.
MIN_SLEEP_S = 0.0001
# Result: all cycles finished.
RESULT_COMPLETED = "completed"
# Result: stopped by the user.
RESULT_STOPPED = "stopped"


class SequenceStopped(Exception):
    """Raised inside a player when Stop was requested."""


class Clock:
    """Real time source used by the players (replaceable in tests)."""

    def now(self) -> float:
        """Return the current time in seconds (monotonic, high resolution)."""
        # perf_counter is monotonic and has the best resolution.
        return time.perf_counter()

    def sleep(self, seconds: float, event: threading.Event) -> None:
        """
        Sleep up to "seconds"; return early when the event is set.

        Inputs:
            seconds (float): Maximum sleep time.
            event (threading.Event): Wake-up event (Stop).
        """
        # Event.wait returns early when the event is set.
        event.wait(max(0.0, seconds))

    def spin_until(self, deadline: float) -> None:
        """
        Wait actively until the deadline (only used for the last milliseconds).

        Inputs:
            deadline (float): Target time from now().
        """
        # Busy loop: maximum accuracy for a very short time.
        while self.now() < deadline:
            # Keep looping.
            pass


class RunControl:
    """
    Stop / pause / resume flags shared between the runner (any thread) and the player.
    """

    def __init__(self) -> None:
        """Create the flags (not stopped, not paused)."""
        # Set when Stop is requested.
        self.stop_event = threading.Event()
        # Set while NOT paused.
        self._run_event = threading.Event()
        # Start in the running state.
        self._run_event.set()

    @property
    def stop_requested(self) -> bool:
        """True after request_stop()."""
        # Read the flag.
        return self.stop_event.is_set()

    @property
    def paused(self) -> bool:
        """True while paused."""
        # Paused = run event cleared.
        return not self._run_event.is_set()

    def request_stop(self) -> None:
        """Request Stop (also ends a pause)."""
        # Set the stop flag.
        self.stop_event.set()
        # Wake a paused player.
        self._run_event.set()

    def pause(self) -> None:
        """Request Pause."""
        # Clear the run flag.
        self._run_event.clear()

    def resume(self) -> None:
        """Request Resume."""
        # Set the run flag.
        self._run_event.set()


class SequencePlayer(ABC):
    """
    Interface of all players.

    A player gets the run plan and an "io" object from the runner with:
        io.clock            Clock
        io.control          RunControl
        io.values           Dict[str, float] last commanded PS values
        io.write(values)    write {quantity: value}; raises on errors
        io.check()          raises when the output was switched OFF elsewhere
        io.on_segment(cycle, segment, planned_s, actual_s)   timing record
        io.ramp_interval_s  time between two ramp values
        io.spin_wait_s      active-wait part of each wait
    """

    # Timing mode name handled by this player (sequence "timing_mode").
    timing_mode: str = ""

    @abstractmethod
    def play(self, plan: Any, io: Any) -> str:
        """
        Execute the plan.

        Inputs:
            plan (RunPlan): Segments, cycle duration, repeat count.
            io: Runner interface (see class docstring).

        Returns:
            str: RESULT_COMPLETED or RESULT_STOPPED.

        Raises:
            Exception: Write / output errors (the runner applies the safe state).
        """

    @abstractmethod
    def elapsed(self) -> float:
        """Return the run time in seconds without paused time (frozen while paused)."""


class SoftwarePlayer(SequencePlayer):
    """Software-timed player with an absolute schedule (D-01)."""

    # Handles sequences with timing_mode "software".
    timing_mode = "software"

    def __init__(self) -> None:
        """Create the player (times are set when play() starts)."""
        # Clock (set in play()).
        self._clock: Optional[Clock] = None
        # Start time.
        self._t0 = 0.0
        # Sum of paused time.
        self._paused_total = 0.0
        # Start of the current pause (None = not paused).
        self._pause_start: Optional[float] = None

    def elapsed(self) -> float:
        """Run time without paused time; frozen while paused."""
        # Not started yet.
        if self._clock is None:
            # Zero.
            return 0.0
        # Reference time: pause start while paused, otherwise now.
        reference = self._pause_start if self._pause_start is not None else self._clock.now()
        # Subtract the start and the paused time.
        return reference - self._t0 - self._paused_total

    def _wait_until(self, target: float, io: Any) -> None:
        """
        Wait until the run time reaches "target" (absolute schedule).

        Handles Stop (raises SequenceStopped) and Pause (time stands still).

        Inputs:
            target (float): Target run time in seconds.
            io: Runner interface.
        """
        # Loop until the target is reached.
        while True:
            # Stop requested: leave immediately.
            if io.control.stop_requested:
                # Signal the stop to play().
                raise SequenceStopped()
            # Pause requested: wait until resumed or stopped.
            if io.control.paused:
                # Remember when the pause started.
                self._pause_start = self._clock.now()
                # Wait in short slices (Stop also ends the pause).
                while io.control.paused and not io.control.stop_requested:
                    # Sleep a slice.
                    self._clock.sleep(MAX_SLEEP_S, io.control.stop_event)
                # Add the paused time.
                self._paused_total += self._clock.now() - self._pause_start
                # Not paused any more.
                self._pause_start = None
                # Check stop / pause again.
                continue
            # Remaining time.
            remaining = target - self.elapsed()
            # Target reached.
            if remaining <= 0:
                # Done.
                return
            # Output must still be ON (raises otherwise).
            io.check()
            # Far away: coarse sleep (interruptible), ending before the spin window.
            # The 0.1 ms margin avoids endless near-zero sleeps at the window edge.
            if remaining > io.spin_wait_s + MIN_SLEEP_S:
                # Sleep at most MAX_SLEEP_S.
                self._clock.sleep(min(remaining - io.spin_wait_s, MAX_SLEEP_S), io.control.stop_event)
            # Close: active wait to the exact deadline.
            else:
                # Absolute deadline on the clock.
                self._clock.spin_until(self._t0 + self._paused_total + target)

    def play(self, plan: Any, io: Any) -> str:
        """
        Execute all cycles of the plan.

        Inputs:
            plan (RunPlan): Segments, cycle duration, repeat count.
            io: Runner interface.

        Returns:
            str: RESULT_COMPLETED or RESULT_STOPPED.
        """
        # Use the runner's clock.
        self._clock = io.clock
        # Start time of the run.
        self._t0 = self._clock.now()
        # No paused time yet.
        self._paused_total = 0.0
        # Not paused.
        self._pause_start = None
        # Cycle counter.
        cycle = 0
        # Run and convert a stop request into the result.
        try:
            # Repeat until the count is reached (0 = until stopped).
            while plan.repeat_count == 0 or cycle < plan.repeat_count:
                # Next cycle.
                cycle += 1
                # Run time at which this cycle starts.
                base = (cycle - 1) * plan.cycle_duration_s
                # Execute every segment.
                for segment in plan.segments:
                    # Run the segment.
                    self._play_segment(cycle, base + segment.offset_s, segment, io)
            # Hold the last step for its full duration.
            self._wait_until(cycle * plan.cycle_duration_s, io)
        # Stop requested.
        except SequenceStopped:
            # Report the stop.
            return RESULT_STOPPED
        # All cycles done.
        return RESULT_COMPLETED

    def _play_segment(self, cycle: int, start: float, segment: Any, io: Any) -> None:
        """
        Execute one Set or Ramp segment.

        Inputs:
            cycle (int): Cycle number (1-based).
            start (float): Planned run time of the segment start.
            segment (Segment): Segment to execute.
            io: Runner interface.
        """
        # Wait for the planned start.
        self._wait_until(start, io)
        # Record the timing (planned vs. actual).
        io.on_segment(cycle, segment, start, self.elapsed())
        # Target values of the segment (current only when given).
        targets: Dict[str, float] = {VOLTAGE: segment.voltage}
        # Current given?
        if segment.current is not None:
            # Add the current target.
            targets[CURRENT] = segment.current
        # Set: jump to the targets.
        if segment.kind == "set":
            # Write them.
            io.write(targets)
            # Done.
            return
        # Ramp: start values = values commanded right now.
        origins = {quantity: io.values[quantity] for quantity in targets}
        # Ramp end time.
        end = start + segment.duration_s
        # First intermediate value.
        tick = start + io.ramp_interval_s
        # Intermediate values until the end.
        while tick < end:
            # Wait for the tick.
            self._wait_until(tick, io)
            # Fraction of the ramp from the actual run time (correct even after a late wake-up).
            fraction = min(1.0, max(0.0, (self.elapsed() - start) / segment.duration_s))
            # Interpolate and write.
            io.write({q: origins[q] + (targets[q] - origins[q]) * fraction for q in targets})
            # Next tick.
            tick += io.ramp_interval_s
        # Wait for the ramp end.
        self._wait_until(end, io)
        # Write the exact end values.
        io.write(targets)


# Players by timing mode (FE-01 adds "hardware").
PLAYERS = {SoftwarePlayer.timing_mode: SoftwarePlayer}
