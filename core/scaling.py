"""
core/scaling.py
===============
Linear scaling between power supply engineering units (V or A) and the AO
voltage sent to the NI hardware.

Formulas (Project_Plan.md section 8):
    slope  = (AO_max - AO_min) / (PS_max - PS_min)
    offset = AO_min - slope * PS_min
    AO     = clamp(slope * PS + offset, AO_min, AO_max)
    PS     = (AO - offset) / slope

Rules:
    * The user always works in PS units; AO volts are internal only (D-02).
    * This module is the single conversion point for front panel and sequences.
    * The AO result is always clamped to the used AO span (safety layer).

LabVIEW analogy:
    Equivalent to a DAQmx linear custom scale, but calculated in our code.
"""

# Import dataclass to define a small immutable value object.
from dataclasses import dataclass
# Import typing helpers for clear function signatures.
from typing import Iterable, List, Tuple


def clamp(value: float, low: float, high: float) -> float:
    """
    Limit a value to the range [low, high].

    Inputs:
        value (float): Value to limit.
        low (float): Lower limit.
        high (float): Upper limit.

    Returns:
        float: low if value < low, high if value > high, otherwise value.
    """
    # max() raises the value to low; min() then caps it at high.
    return min(max(value, low), high)


@dataclass(frozen=True)
class LinearScale:
    """
    Linear mapping PS units <-> AO volts for one channel.

    Attributes:
        ps_min (float): Power supply range minimum (e.g. 0 V).
        ps_max (float): Power supply range maximum (e.g. 20 V).
        ao_min (float): Used AO span minimum (e.g. 0 V).
        ao_max (float): Used AO span maximum (e.g. 10 V).
    """

    # Power supply range minimum in engineering units.
    ps_min: float
    # Power supply range maximum in engineering units.
    ps_max: float
    # Used AO span minimum in volts.
    ao_min: float
    # Used AO span maximum in volts.
    ao_max: float

    def validate(self) -> List[str]:
        """
        Check that the ranges allow a valid linear scale.

        Inputs:
            None.

        Returns:
            List[str]: Error messages; empty list when the scale is valid.
        """
        # Collect all problems instead of stopping at the first one.
        errors: List[str] = []
        # The PS range must have a positive width, otherwise slope is undefined.
        if self.ps_max <= self.ps_min:
            # Describe the PS range problem.
            errors.append(f"PS max ({self.ps_max}) must be greater than PS min ({self.ps_min}).")
        # The AO span must have a positive width.
        if self.ao_max <= self.ao_min:
            # Describe the AO span problem.
            errors.append(f"AO max ({self.ao_max}) must be greater than AO min ({self.ao_min}).")
        # Return the collected problems.
        return errors

    @property
    def is_valid(self) -> bool:
        """Return True when validate() reports no errors."""
        # The scale is valid when the error list is empty.
        return not self.validate()

    @property
    def slope(self) -> float:
        """
        Return the slope in AO volts per PS unit.

        Raises:
            ValueError: If the scale is invalid (zero or negative range width).
        """
        # Refuse to calculate with invalid ranges (would divide by zero or invert).
        if not self.is_valid:
            # Report all validation problems in one message.
            raise ValueError("Invalid scale: " + " ".join(self.validate()))
        # Apply the slope formula.
        return (self.ao_max - self.ao_min) / (self.ps_max - self.ps_min)

    @property
    def offset(self) -> float:
        """Return the offset in AO volts (AO value at PS = 0)."""
        # Apply the offset formula (slope also validates the ranges).
        return self.ao_min - self.slope * self.ps_min

    def clamp_ps(self, ps_value: float) -> float:
        """
        Limit a PS setpoint to the power supply range.

        Inputs:
            ps_value (float): Setpoint in PS units.

        Returns:
            float: Setpoint limited to [ps_min, ps_max].
        """
        # Use the generic clamp with the PS limits.
        return clamp(ps_value, self.ps_min, self.ps_max)

    def to_ao(self, ps_value: float) -> float:
        """
        Convert a PS setpoint to the AO voltage to write.

        Inputs:
            ps_value (float): Setpoint in PS units (V or A).

        Returns:
            float: AO voltage, clamped to [ao_min, ao_max].
        """
        # Calculate the raw AO value with the linear formula.
        raw = self.slope * ps_value + self.offset
        # Clamp to the used AO span as the final safety layer.
        return clamp(raw, self.ao_min, self.ao_max)

    def to_ps(self, ao_value: float) -> float:
        """
        Convert an AO voltage back to PS units (inverse scale, for display).

        Inputs:
            ao_value (float): AO voltage.

        Returns:
            float: Equivalent value in PS units.
        """
        # Apply the inverse formula.
        return (ao_value - self.offset) / self.slope

    def preview(self, percents: Iterable[float]) -> List[Tuple[float, float, float]]:
        """
        Build the scaling preview table shown in Settings.

        Inputs:
            percents (Iterable[float]): Percent points of the PS range
                (read from config/presets/scaling_preview.json).

        Returns:
            List[Tuple[float, float, float]]: (percent, PS value, AO value) per point.
        """
        # Result rows of the preview table.
        rows: List[Tuple[float, float, float]] = []
        # Width of the PS range, used to turn percent into a PS value.
        span = self.ps_max - self.ps_min
        # Build one row per requested percent point.
        for percent in percents:
            # Convert the percent to a PS value inside the range.
            ps_value = self.ps_min + span * float(percent) / 100.0
            # Add the row with the matching AO value.
            rows.append((float(percent), ps_value, self.to_ao(ps_value)))
        # Return the finished table.
        return rows


def validate_span_in_device_range(ao_min: float, ao_max: float,
                                  device_min: float, device_max: float) -> Tuple[List[str], List[str]]:
    """
    Check the used AO span against the device AO range read from DAQmx (D-05).

    Inputs:
        ao_min (float): Used span minimum.
        ao_max (float): Used span maximum.
        device_min (float): Device range minimum (e.g. -10 V).
        device_max (float): Device range maximum (e.g. +10 V).

    Returns:
        Tuple[List[str], List[str]]: (errors, warnings). Errors block saving;
        warnings are shown but do not block.
    """
    # Blocking problems.
    errors: List[str] = []
    # Non-blocking hints.
    warnings: List[str] = []
    # The span must have a positive width.
    if ao_max <= ao_min:
        # Describe the width problem.
        errors.append(f"AO max ({ao_max}) must be greater than AO min ({ao_min}).")
    # The span minimum must not be below the device minimum.
    if ao_min < device_min:
        # Describe the lower-limit problem.
        errors.append(f"AO min ({ao_min}) is below the device range minimum ({device_min}).")
    # The span maximum must not be above the device maximum.
    if ao_max > device_max:
        # Describe the upper-limit problem.
        errors.append(f"AO max ({ao_max}) is above the device range maximum ({device_max}).")
    # Negative voltages are allowed but usually wrong for PS control inputs.
    if ao_min < 0:
        # Warn the user without blocking.
        warnings.append("The used AO span includes negative voltages. Check the power supply control input.")
    # Return both lists.
    return errors, warnings
