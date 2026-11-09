"""
core/front_panel.py
===================
Qt-free logic of the power supply front panel (Project_Plan.md section 6,
decision D-07).

    ChannelControl -> one voltage or current section: range, step size,
                      target setpoint, knob (dial) mapping, display format
    SlewRamp       -> moves an actual value toward a target at a fixed rate
                      (used when "Limit slew rate" is enabled)

The window (ui/main_panel.py) only shows these values and sends the
resulting setpoints to the DAQ service. All rules are unit-tested here.

LabVIEW analogy:
    The data cluster and the sub-VIs behind the front panel controls.
"""

# Import math for the decimal calculation.
import math
# Import dataclass tools.
from dataclasses import dataclass, field
# Import typing helpers.
from typing import Any, Dict, List

# Import the scaling clamp helper.
from core.scaling import clamp


def decimals_for_step(step: float) -> int:
    """
    Number of decimals needed to show values in a given step size.

    Inputs:
        step (float): Step size, e.g. 0.01, 0.1, 1, 0.5.

    Returns:
        int: Decimals, e.g. 2, 1, 0, 1.
    """
    # Non-positive steps make no sense; show 3 decimals as a safe default.
    if step <= 0:
        # Fallback.
        return 3
    # 10^-n for the first significant digit; the tiny offset avoids float rounding issues.
    return max(0, -math.floor(math.log10(step) + 1e-9))


@dataclass
class ChannelControl:
    """
    State of one front-panel section (voltage or current).

    Attributes:
        quantity (str): "voltage" or "current".
        unit (str): "V" or "A".
        ps_min (float): Power supply range minimum.
        ps_max (float): Power supply range maximum.
        step (float): Active step size.
        step_options (List[Dict[str, Any]]): Step size items of this quantity
            from config/presets/step_sizes.json.
        target (float): Setpoint chosen by the user (PS units).
    """

    # Quantity name.
    quantity: str
    # Engineering unit.
    unit: str
    # Range minimum.
    ps_min: float
    # Range maximum.
    ps_max: float
    # Active step size.
    step: float
    # Step size choices.
    step_options: List[Dict[str, Any]] = field(default_factory=list)
    # User setpoint.
    target: float = 0.0

    @classmethod
    def from_config(cls, quantity: str, channel: Any, step_items: List[Dict[str, Any]],
                    preferred_step: float, target: float) -> "ChannelControl":
        """
        Build a section from the channel settings and the step size catalog.

        Inputs:
            quantity (str): "voltage" or "current".
            channel (ChannelConfig): Channel settings (unit, ps_min, ps_max).
            step_items (List[Dict]): All items of step_sizes.json.
            preferred_step (float): Step size stored in settings.json (ui group).
            target (float): Previous setpoint (clamped to the new range).

        Returns:
            ChannelControl: The section state.
        """
        # Only the items of this quantity.
        options = [item for item in step_items if item.get("quantity") == quantity]
        # Values offered.
        values = [float(item["value"]) for item in options]
        # Use the preferred step when offered, else the first offered, else 0.1.
        step = preferred_step if preferred_step in values else (values[0] if values else 0.1)
        # Create the section.
        control = cls(quantity, channel.unit, channel.ps_min, channel.ps_max, step, options)
        # Clamp the previous setpoint into the (maybe new) range.
        control.target = control.clamp(target)
        # Return it.
        return control

    @property
    def decimals(self) -> int:
        """Decimals shown for the active step size."""
        # Derived from the step.
        return decimals_for_step(self.step)

    def clamp(self, value: float) -> float:
        """
        Limit a value to the power supply range.

        Inputs:
            value (float): Requested value.

        Returns:
            float: Value inside [ps_min, ps_max].
        """
        # Generic clamp.
        return clamp(float(value), self.ps_min, self.ps_max)

    def set_target(self, value: float) -> float:
        """
        Set a new setpoint (clamped).

        Inputs:
            value (float): Requested setpoint.

        Returns:
            float: Stored setpoint.
        """
        # Store the clamped value.
        self.target = self.clamp(value)
        # Return it.
        return self.target

    def nudge(self, direction: int) -> float:
        """
        Change the setpoint by one step (step buttons).

        Inputs:
            direction (int): +1 = up, -1 = down.

        Returns:
            float: New setpoint (clamped, rounded to the step decimals).
        """
        # One step in the requested direction, rounded to avoid float noise.
        return self.set_target(round(self.target + direction * self.step, self.decimals))

    @property
    def dial_max(self) -> int:
        """Number of knob positions above ps_min (knob range is 0..dial_max)."""
        # Range width in steps, at least 1.
        return max(1, round((self.ps_max - self.ps_min) / self.step))

    def value_from_dial(self, position: int) -> float:
        """
        Convert a knob position to a setpoint.

        Inputs:
            position (int): Knob position (0..dial_max).

        Returns:
            float: Setpoint (clamped, rounded).
        """
        # Linear mapping from the range minimum.
        return self.clamp(round(self.ps_min + position * self.step, self.decimals))

    def dial_from_value(self, value: float) -> int:
        """
        Convert a setpoint to the nearest knob position.

        Inputs:
            value (float): Setpoint.

        Returns:
            int: Knob position (0..dial_max).
        """
        # Nearest position, limited to the knob range.
        return int(clamp(round((value - self.ps_min) / self.step), 0, self.dial_max))

    def display_text(self, value: float) -> str:
        """
        Text for the 7-segment display.

        Inputs:
            value (float): Value to show.

        Returns:
            str: e.g. "10.00".
        """
        # Fixed decimals of the step size.
        return f"{value:.{self.decimals}f}"

    @property
    def display_digits(self) -> int:
        """Digit count the 7-segment display needs for the whole range (plus sign)."""
        # Widest text of the range limits, plus one place for a sign.
        return max(len(self.display_text(self.ps_max)), len(self.display_text(self.ps_min))) + 1

    def range_text(self) -> str:
        """Range text shown under the display, e.g. "Range 0 – 20 V"."""
        # Compact numbers.
        return f"Range {self.ps_min:g} – {self.ps_max:g} {self.unit}"


@dataclass
class SlewRamp:
    """
    Linear ramp of an actual value toward a target at a fixed rate (D-07).

    Attributes:
        rate (float): Units per second (V/s or A/s), > 0.
        actual (float): Value commanded last.
        target (float): Value to reach.
    """

    # Rate in units per second.
    rate: float
    # Current value.
    actual: float = 0.0
    # Final value.
    target: float = 0.0

    @property
    def active(self) -> bool:
        """True while the target is not reached."""
        # Small tolerance against float noise.
        return abs(self.target - self.actual) > 1e-9

    def start(self, actual: float, target: float) -> None:
        """
        Start a ramp from a known value.

        Inputs:
            actual (float): Start value.
            target (float): Final value.
        """
        # Store the start value.
        self.actual = float(actual)
        # Store the final value.
        self.target = float(target)

    def retarget(self, target: float) -> None:
        """
        Change the final value; the ramp continues from the current actual value.

        Inputs:
            target (float): New final value.
        """
        # Only the target changes.
        self.target = float(target)

    def cancel(self) -> None:
        """Stop the ramp where it is (target = actual)."""
        # Nothing left to do.
        self.target = self.actual

    def step(self, dt: float) -> float:
        """
        Advance the ramp by a time interval.

        Inputs:
            dt (float): Elapsed time in seconds since the last step.

        Returns:
            float: New actual value.
        """
        # Largest change allowed in this interval.
        max_delta = self.rate * max(dt, 0.0)
        # Remaining distance.
        remaining = self.target - self.actual
        # Close enough: jump to the target.
        if abs(remaining) <= max_delta:
            # Target reached.
            self.actual = self.target
        # Otherwise move by the allowed change in the right direction.
        else:
            # Signed step.
            self.actual += math.copysign(max_delta, remaining)
        # Return the new value.
        return self.actual
