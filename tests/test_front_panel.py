"""
tests/test_front_panel.py
=========================
Unit tests for core/front_panel.py (front-panel logic, no Qt; D-07).
"""

# Import SimpleNamespace to build a minimal channel object.
from types import SimpleNamespace

# Import pytest for float comparison.
import pytest

# Import the code under test.
from core.front_panel import ChannelControl, SlewRamp, decimals_for_step

# Step size items like config/presets/step_sizes.json.
STEPS = [
    # Voltage steps.
    {"id": "v1", "quantity": "voltage", "value": 0.01}, {"id": "v2", "quantity": "voltage", "value": 0.1},
    # More voltage steps.
    {"id": "v3", "quantity": "voltage", "value": 1.0},
    # Current step.
    {"id": "a1", "quantity": "current", "value": 0.1},
]
# 0-20 V channel.
CHANNEL = SimpleNamespace(unit="V", ps_min=0.0, ps_max=20.0)


def control(step=0.1, target=0.0):
    """Build a 0-20 V voltage section."""
    # Use the helper constructor.
    return ChannelControl.from_config("voltage", CHANNEL, STEPS, step, target)


def test_decimals_for_step():
    """Decimals follow the step size."""
    # Several step sizes.
    assert [decimals_for_step(s) for s in (0.01, 0.1, 1.0, 0.5, 10)] == [2, 1, 0, 1, 0]


def test_from_config_filters_steps_and_clamps_target():
    """Only voltage steps are offered; an old target outside the range is clamped."""
    # Target above the range.
    ctl = control(target=50)
    # Three voltage steps.
    assert [o["value"] for o in ctl.step_options] == [0.01, 0.1, 1.0]
    # Clamped target.
    assert ctl.target == 20.0


def test_unknown_preferred_step_uses_first_offered():
    """A preferred step not in the catalog falls back to the first item."""
    # 0.25 is not offered.
    assert control(step=0.25).step == 0.01


def test_nudge_and_limits():
    """Step buttons move by one step and stop at the range limits."""
    # Start at 19.95 with 0.1 steps.
    ctl = control(target=19.95)
    # Up: clamped to 20.
    assert ctl.nudge(+1) == 20.0
    # Down from 0: stays 0.
    ctl.set_target(0)
    # Check.
    assert ctl.nudge(-1) == 0.0
    # No float noise: 0.1 + 0.1 + 0.1.
    for _ in range(3):
        # Step up.
        ctl.nudge(+1)
    # Exactly 0.3.
    assert ctl.target == 0.3


def test_dial_mapping():
    """Knob positions map to steps of the range."""
    # 0.1 V steps on 0-20 V.
    ctl = control()
    # 200 positions.
    assert ctl.dial_max == 200
    # Position 125 = 12.5 V.
    assert ctl.value_from_dial(125) == pytest.approx(12.5)
    # And back.
    assert ctl.dial_from_value(12.5) == 125


def test_dial_mapping_with_non_zero_min():
    """D-06: knob position 0 = PS min."""
    # 2-20 V channel.
    ctl = ChannelControl.from_config("voltage", SimpleNamespace(unit="V", ps_min=2.0, ps_max=20.0), STEPS, 1.0, 0)
    # Position 0.
    assert ctl.value_from_dial(0) == 2.0 and ctl.dial_max == 18


def test_display_text_and_digits():
    """Display text uses the step decimals; digits fit the range."""
    # 0.01 V steps.
    ctl = control(step=0.01, target=5)
    # Text.
    assert ctl.display_text(5) == "5.00"
    # "20.00" plus sign place.
    assert ctl.display_digits == 6
    # Range text.
    assert ctl.range_text() == "Range 0 – 20 V"


def test_slew_ramp_reaches_target_at_rate():
    """1 V/s ramp from 0 to 1 V in 0.25 s steps."""
    # Ramp.
    ramp = SlewRamp(rate=1.0)
    # Start.
    ramp.start(0, 1)
    # Four steps.
    values = [ramp.step(0.25) for _ in range(4)]
    # Linear values.
    assert values == pytest.approx([0.25, 0.5, 0.75, 1.0])
    # Finished.
    assert not ramp.active


def test_slew_ramp_down_and_no_overshoot():
    """Ramps down and never passes the target."""
    # Ramp 5 A/s.
    ramp = SlewRamp(rate=5.0)
    # From 10 to 9.
    ramp.start(10, 9)
    # Large step.
    assert ramp.step(1.0) == 9.0


def test_slew_ramp_retarget_continues_from_actual():
    """A new target continues from the current value (D-07)."""
    # Ramp.
    ramp = SlewRamp(rate=1.0)
    # 0 -> 10.
    ramp.start(0, 10)
    # Half a second.
    ramp.step(0.5)
    # New target below.
    ramp.retarget(0)
    # Goes down from 0.5.
    assert ramp.step(0.25) == pytest.approx(0.25)


def test_slew_ramp_cancel():
    """cancel() stops where the ramp is."""
    # Ramp.
    ramp = SlewRamp(rate=1.0)
    # 0 -> 10.
    ramp.start(0, 10)
    # One step.
    ramp.step(1)
    # Cancel.
    ramp.cancel()
    # Not active, stays at 1.
    assert not ramp.active and ramp.step(1) == 1.0
