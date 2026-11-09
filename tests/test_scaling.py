"""
tests/test_scaling.py
=====================
Unit tests for core/scaling.py (Project_Plan.md section 8, decisions D-05, D-06).
"""

# Import pytest for assertions on exceptions and float comparison.
import pytest

# Import the code under test.
from core.scaling import LinearScale, clamp, validate_span_in_device_range


def test_clamp_limits_values():
    """clamp() keeps values inside [low, high]."""
    # Below the range -> low.
    assert clamp(-1, 0, 10) == 0
    # Above the range -> high.
    assert clamp(11, 0, 10) == 10
    # Inside the range -> unchanged.
    assert clamp(5, 0, 10) == 5


def test_voltage_example_20v_on_0_10v():
    """Plan example: 0-20 V on 0-10 V -> slope 0.5, 10 V set -> 5 V AO."""
    # Build the scale.
    scale = LinearScale(ps_min=0, ps_max=20, ao_min=0, ao_max=10)
    # Check the slope.
    assert scale.slope == pytest.approx(0.5)
    # Check the offset.
    assert scale.offset == pytest.approx(0.0)
    # Check the conversion.
    assert scale.to_ao(10) == pytest.approx(5.0)


def test_current_example_100a_on_0_10v():
    """Plan example: 0-100 A on 0-10 V -> slope 0.1, 50 A -> 5 V."""
    # Build the scale.
    scale = LinearScale(ps_min=0, ps_max=100, ao_min=0, ao_max=10)
    # Check the slope.
    assert scale.slope == pytest.approx(0.1)
    # Check the conversion.
    assert scale.to_ao(50) == pytest.approx(5.0)


def test_non_zero_ps_min():
    """D-06 example: 2-20 V on 0-10 V -> 2 V = 0 V, 11 V = 5 V, 20 V = 10 V."""
    # Build the scale.
    scale = LinearScale(ps_min=2, ps_max=20, ao_min=0, ao_max=10)
    # Check the slope (10/18).
    assert scale.slope == pytest.approx(10 / 18)
    # Check the offset (-1.111...).
    assert scale.offset == pytest.approx(-10 / 9)
    # Lower end.
    assert scale.to_ao(2) == pytest.approx(0.0)
    # Middle.
    assert scale.to_ao(11) == pytest.approx(5.0)
    # Upper end.
    assert scale.to_ao(20) == pytest.approx(10.0)


def test_0_5v_span_preset():
    """0-20 V on a 0-5 V span -> 20 V = 5 V AO."""
    # Build the scale.
    scale = LinearScale(ps_min=0, ps_max=20, ao_min=0, ao_max=5)
    # Check the full-scale conversion.
    assert scale.to_ao(20) == pytest.approx(5.0)


def test_to_ao_is_clamped_to_span():
    """Values outside the PS range never leave the AO span."""
    # Build the scale.
    scale = LinearScale(ps_min=0, ps_max=20, ao_min=0, ao_max=10)
    # Too high -> AO max.
    assert scale.to_ao(30) == pytest.approx(10.0)
    # Too low -> AO min.
    assert scale.to_ao(-5) == pytest.approx(0.0)


def test_inverse_conversion():
    """to_ps() is the inverse of to_ao() inside the range."""
    # Build a scale with an offset.
    scale = LinearScale(ps_min=2, ps_max=20, ao_min=0, ao_max=10)
    # Round trip for several values.
    for value in (2, 7.5, 11, 20):
        # Convert there and back.
        assert scale.to_ps(scale.to_ao(value)) == pytest.approx(value)


def test_invalid_scale_reports_errors_and_raises():
    """Zero-width ranges are invalid and slope raises ValueError."""
    # PS range with zero width.
    scale = LinearScale(ps_min=5, ps_max=5, ao_min=0, ao_max=10)
    # It must be reported as invalid.
    assert not scale.is_valid
    # Exactly one error expected (PS range).
    assert len(scale.validate()) == 1
    # Calculating the slope must fail.
    with pytest.raises(ValueError):
        # Access the slope.
        _ = scale.slope


def test_preview_rows():
    """preview() returns (percent, PS, AO) rows."""
    # Build the scale.
    scale = LinearScale(ps_min=0, ps_max=20, ao_min=0, ao_max=10)
    # Build the preview for three points.
    rows = scale.preview([0, 50, 100])
    # Check the middle row.
    assert rows[1] == pytest.approx((50.0, 10.0, 5.0))


def test_span_validation_against_device_range():
    """D-05: span must fit into the device range; negative span gives a warning."""
    # Valid 0-10 V span on a +-10 V device.
    errors, warnings = validate_span_in_device_range(0, 10, -10, 10)
    # No errors and no warnings.
    assert errors == [] and warnings == []
    # Span above the device maximum.
    errors, _ = validate_span_in_device_range(0, 12, -10, 10)
    # One error expected.
    assert len(errors) == 1
    # Negative span inside the device range.
    errors, warnings = validate_span_in_device_range(-5, 5, -10, 10)
    # Allowed, but with a warning.
    assert errors == [] and len(warnings) == 1
