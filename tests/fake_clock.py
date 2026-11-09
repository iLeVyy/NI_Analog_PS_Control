"""
tests/fake_clock.py
===================
Deterministic clock for sequence tests: time only moves when the player
sleeps or waits, and actions can be scheduled at given times
(e.g. "press Pause at 1.5 s").
"""

# Import typing helpers.
from typing import Callable, List, Tuple

# Import the real clock interface.
from core.sequence_players import Clock


class FakeClock(Clock):
    """Clock whose time advances only through sleep() / spin_until()."""

    def __init__(self) -> None:
        """Start at time 0 with no scheduled actions."""
        # Current time in seconds.
        self.t = 0.0
        # Scheduled (time, action) pairs.
        self._hooks: List[Tuple[float, Callable[[], None]]] = []

    def at(self, when: float, action: Callable[[], None]) -> None:
        """
        Run an action when the time reaches "when".

        Inputs:
            when (float): Time in seconds.
            action (Callable): Function without arguments.
        """
        # Store and keep the list sorted by time.
        self._hooks.append((when, action))
        # Sort by time.
        self._hooks.sort(key=lambda hook: hook[0])

    def _run_hooks(self) -> None:
        """Run every action whose time has come."""
        # Run due actions in time order.
        while self._hooks and self._hooks[0][0] <= self.t + 1e-12:
            # Remove and run the first one.
            self._hooks.pop(0)[1]()

    def now(self) -> float:
        """Return the fake time."""
        # Current time.
        return self.t

    def sleep(self, seconds, event) -> None:
        """Advance the time (unless the event is already set) and run due actions."""
        # Stop requested: return immediately like Event.wait.
        if event.is_set():
            # No time passes.
            return
        # Advance.
        self.t += max(0.0, seconds)
        # Run due actions.
        self._run_hooks()

    def spin_until(self, deadline) -> None:
        """Jump to the deadline and run due actions."""
        # Advance.
        self.t = max(self.t, deadline)
        # Run due actions.
        self._run_hooks()
