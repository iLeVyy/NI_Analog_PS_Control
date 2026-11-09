"""
tests/test_sequence_engine.py
=============================
Unit tests for the sequence model, validation, schedule and runner
(Project_Plan.md 10-11; D-01, D-02, D-03, D-08). Timing tests use the
deterministic FakeClock; one test checks real timing with the real clock.
"""

# Import json to inspect saved files.
import json

# Import pytest for fixtures, float comparison and exceptions.
import pytest

# Import the config manager.
from core.config_manager import ConfigManager
# Import the catalog loader.
from core.catalogs import load_all_catalogs
# Import the DAQ service.
from core.daq_service import DaqService
# Import the engine parts under test.
from core.sequence_engine import (ERROR, STATE_COMPLETED, STATE_ERROR, STATE_PAUSED, STATE_STOPPED,
                                  WARNING, SequenceRunner, build_plan, errors_of, preview_values,
                                  validate_sequence)
# Import the model parts under test.
from core.sequence_model import (Sequence, SequenceFileError, SequenceStep, load_sequence,
                                 save_sequence)
# Import the simulation backend.
from core.simulation import SimulatedBackend
# Import the fake clock.
from tests.fake_clock import FakeClock

# Voltage AO channel.
V_CH = "cDAQ1Mod1/ao0"
# Current AO channel.
I_CH = "cDAQ1Mod1/ao1"


def step(duration, unit, voltage, current=None, action="set"):
    """Shortcut to build a SequenceStep."""
    # Build the step.
    return SequenceStep(duration, unit, voltage, current, action)


# The example from the original request: 10 s 10 V, 20 s 15 V (ramp), 100 ms 5 V, Stop.
EXAMPLE = [step(10, "s", 10), step(20, "s", 15, action="ramp"), step(100, "ms", 5),
           step(None, "s", None, action="stop")]


@pytest.fixture
def env(app_root):
    """
    Configured synchronous simulation service + settings + catalogs.

    Returns:
        SimpleNamespace-like dict: config, catalogs, backend, service.
    """
    # Load the defaults.
    config = ConfigManager(app_root, "test").load().config
    # Assign the voltage channel.
    config.voltage_channel.device, config.voltage_channel.physical_channel = "cDAQ1Mod1", V_CH
    # Assign the current channel.
    config.current_channel.device, config.current_channel.physical_channel = "cDAQ1Mod1", I_CH
    # Simulation backend.
    backend = SimulatedBackend(paths=app_root)
    # Synchronous service (commands run in the caller thread).
    service = DaqService(backend, backend.check_driver())
    # Set up the AO channels.
    assert service.configure(config).result()[0]
    # Give everything to the test.
    yield {"config": config, "catalogs": load_all_catalogs(app_root.presets_dir),
           "backend": backend, "service": service}
    # Close the service.
    service.close()


def run(env, steps, clock=None, **options):
    """
    Run a sequence to the end with the fake clock.

    Returns:
        Tuple[SequenceRunner, RunStatus].
    """
    # Runner with a fake clock.
    runner = SequenceRunner(env["service"], env["config"], env["catalogs"], clock=clock or FakeClock())
    # Start.
    issues = runner.start(Sequence(steps=steps, **options))
    # Must start.
    assert not errors_of(issues), issues
    # Wait for the end.
    assert runner.join(10)
    # Return runner and final status.
    return runner, runner.status()


# ---------------------------------------------------------------------- validation


def test_example_is_valid(env):
    """The request example has no issues."""
    # Validate.
    assert validate_sequence(Sequence(steps=EXAMPLE), env["config"], env["catalogs"]) == []


@pytest.mark.parametrize("bad_step, field", [
    # Below 50 ms (D-01).
    (step(40, "ms", 5), "duration"),
    # Missing duration.
    (step(None, "s", 5), "duration"),
    # Unknown unit.
    (step(1, "h", 5), "unit"),
    # Missing voltage (D-02).
    (step(1, "s", None), "voltage"),
    # Above the 0-20 V range (PS units, not AO).
    (step(1, "s", 25), "voltage"),
    # Current above 0-100 A.
    (step(1, "s", 5, 150), "current"),
    # Unknown action.
    (step(1, "s", 5, action="jump"), "action"),
])
def test_row_errors(env, bad_step, field):
    """Each invalid row gives an error on the right field."""
    # Validate a one-row sequence.
    issues = validate_sequence(Sequence(steps=[bad_step]), env["config"], env["catalogs"])
    # An error on the expected field.
    assert any(i.level == ERROR and i.field == field and i.row == 0 for i in issues), issues


def test_50ms_is_allowed_and_rows_after_stop_warn(env):
    """Exactly 50 ms is fine; rows after Stop only give a warning."""
    # 50 ms, Stop, one more row.
    issues = validate_sequence(Sequence(steps=[step(50, "ms", 1), step(None, "s", None, action="stop"),
                                               step(1, "s", 2)]), env["config"], env["catalogs"])
    # Only one warning on row 3.
    assert [(i.level, i.row) for i in issues] == [(WARNING, 2)]


def test_sequence_level_errors(env):
    """Empty sequence, hardware timing, negative repeat and unknown end state are errors."""
    # Build a bad sequence.
    sequence = Sequence(steps=[], timing_mode="hardware", repeat_count=-1, end_state="explode")
    # Validate.
    messages = " ".join(i.message for i in validate_sequence(sequence, env["config"], env["catalogs"]))
    # All four reasons.
    for text in ("no Set or Ramp", "Timing mode", "Repeat count", "end state"):
        # Each is reported.
        assert text in messages


def test_plan_offsets(env):
    """Schedule: offsets 0, 10, 30 s; cycle 30.1 s; Stop row not executed."""
    # Build the plan.
    plan = build_plan(Sequence(steps=EXAMPLE, repeat_count=3), env["catalogs"])
    # Offsets.
    assert [s.offset_s for s in plan.segments] == pytest.approx([0, 10, 30])
    # Cycle and total.
    assert plan.cycle_duration_s == pytest.approx(30.1) and plan.total_duration_s == pytest.approx(90.3)
    # Endless run.
    assert build_plan(Sequence(steps=EXAMPLE, repeat_count=0), env["catalogs"]).total_duration_s is None


def test_preview_values(env):
    """AO preview: 10 V -> 5 V; ramp 5 -> 7.5 V; unchanged current keeps its value."""
    # Preview with the current starting at 50 A.
    rows = preview_values(Sequence(steps=EXAMPLE), env["config"], {"voltage": 0.0, "current": 50.0})
    # Set row.
    assert rows[0]["voltage"] == pytest.approx((5.0, 5.0))
    # Ramp row.
    assert rows[1]["voltage"] == pytest.approx((5.0, 7.5))
    # Current unchanged at 50 A -> 5 V.
    assert rows[1]["current"] == pytest.approx((5.0, 5.0))
    # Stop row has no values.
    assert rows[3] == {"voltage": None, "current": None}


# ---------------------------------------------------------------------- file format


def test_save_and_load_round_trip(env, app_root):
    """Saving adds .json, time stamps and ranges; loading gives the same rows."""
    # Sequence with options.
    sequence = Sequence(name="Demo", steps=EXAMPLE, end_state="hold_last", repeat_count=5)
    # Save without extension.
    path = save_sequence(sequence, app_root.sequences_dir / "demo", {"voltage": [0, 20], "current": [0, 100]})
    # Extension added.
    assert path.name == "demo.json"
    # Raw file content.
    data = json.loads(path.read_text(encoding="utf-8"))
    # Units are PS units; current null = unchanged.
    assert data["units"] == {"voltage": "V", "current": "A"} and data["steps"][0]["current"] is None
    # Load again.
    loaded = load_sequence(path)
    # Same rows and options.
    assert loaded.steps == EXAMPLE and loaded.repeat_count == 5 and loaded.end_state == "hold_last"
    # Ranges stored.
    assert loaded.ps_range_at_save == {"voltage": [0, 20], "current": [0, 100]}


def test_invalid_files(app_root):
    """Broken JSON, wrong types and newer formats are refused."""
    # Folder.
    app_root.sequences_dir.mkdir(parents=True, exist_ok=True)
    # Test files.
    cases = {"broken.json": "{", "types.json": '{"steps": [{"duration": "ten"}]}',
             "newer.json": '{"format_version": 99, "steps": []}'}
    # Each case.
    for name, text in cases.items():
        # Write it.
        (app_root.sequences_dir / name).write_text(text, encoding="utf-8")
        # Loading fails.
        with pytest.raises(SequenceFileError):
            # Load.
            load_sequence(app_root.sequences_dir / name)


# ---------------------------------------------------------------------- runner (fake clock)


def test_example_run_values_and_end_state(env):
    """Example run: 5 V, ramp to 7.5 V, 2.5 V AO; 30.1 s; then Output OFF + 0 V."""
    # Run.
    runner, status = run(env, EXAMPLE)
    # Completed after 30.1 s.
    assert status.state == STATE_COMPLETED and status.elapsed_s == pytest.approx(30.1)
    # Voltage AO values in write order.
    ao = [value for channel, value in env["backend"].history if channel == V_CH]
    # Step 1 = 5 V AO (written before Output ON and at the step start).
    assert 5.0 in ao
    # Highest value = ramp end 7.5 V AO.
    assert max(ao) == pytest.approx(7.5)
    # Step 3 = 2.5 V AO, then the safe value 0 V.
    assert ao[-2:] == pytest.approx([2.5, 0.0])
    # Output OFF at the end (default end state).
    assert not env["service"].state.output_on
    # No timing deviation with the fake clock.
    assert status.max_dev_ms == pytest.approx(0.0) and status.steps_done == 3


def test_ramp_is_linear_in_50ms_steps(env):
    """A 1 s ramp 0 -> 10 V writes ~20 values that rise linearly."""
    # Ramp only.
    runner, _ = run(env, [step(1, "s", 10, action="ramp")])
    # Voltage AO values written by the ramp (after Output ON wrote the start value 0).
    ao = [value for channel, value in env["backend"].history if channel == V_CH][2:-1]
    # About 20 values (50 ms interval).
    assert 18 <= len(ao) <= 21
    # Strictly rising.
    assert all(b > a for a, b in zip(ao, ao[1:]))
    # Ends at 10 V -> 5 V AO.
    assert ao[-1] == pytest.approx(5.0)


def test_current_optional(env):
    """Empty current leaves the current channel untouched; a value is written."""
    # First row sets 50 A, second row leaves it unchanged.
    run(env, [step(1, "s", 5, 50), step(1, "s", 6)])
    # Current AO values written.
    ao_i = [value for channel, value in env["backend"].history if channel == I_CH]
    # 50 A = 5 V AO was written; the only later value is the safe value at the end.
    assert ao_i[-2:] == pytest.approx([5.0, 0.0])


def test_hold_last_keeps_output_on(env):
    """D-03: end state 'hold last value' leaves the output ON with the last value."""
    # Run with hold_last.
    run(env, [step(1, "s", 8)], end_state="hold_last")
    # Output still ON.
    assert env["service"].state.output_on
    # 8 V -> 4 V AO held.
    assert env["backend"].last_values[V_CH] == pytest.approx(4.0)


def test_repeat_count_and_drift_free_cycles(env):
    """D-08: three cycles of 0.5 s -> 1.5 s, cycle counter 3."""
    # Run 3 cycles.
    _, status = run(env, [step(250, "ms", 1), step(250, "ms", 2)], repeat_count=3)
    # Duration and cycle.
    assert status.elapsed_s == pytest.approx(1.5) and status.cycle == 3 and status.steps_done == 6


def test_stop_forces_output_off_even_with_hold_last(env):
    """D-03: user Stop always ends with Output OFF + safe value."""
    # Fake clock with a Stop at 2.5 s.
    clock = FakeClock()
    # Runner.
    runner = SequenceRunner(env["service"], env["config"], env["catalogs"], clock=clock)
    # Schedule the Stop.
    clock.at(2.5, runner.stop)
    # Endless run with hold_last.
    runner.start(Sequence(steps=[step(1, "s", 10)], repeat_count=0, end_state="hold_last"))
    # Wait.
    assert runner.join(10)
    # Stopped, output OFF, safe value.
    assert runner.status().state == STATE_STOPPED and not env["service"].state.output_on
    # 0 V on the AO.
    assert env["backend"].last_values[V_CH] == 0.0


def test_pause_freezes_time(env):
    """Pause from 1 s to 4 s: the run takes 2 s of run time but 5 s of clock time."""
    # Fake clock.
    clock = FakeClock()
    # Runner.
    runner = SequenceRunner(env["service"], env["config"], env["catalogs"], clock=clock)
    # Pause at 1 s.
    clock.at(1.0, runner.pause)
    # Check the state while paused.
    clock.at(2.0, lambda: states.append(runner.status().state))
    # Resume at 4 s.
    clock.at(4.0, runner.resume)
    # States seen while paused.
    states = []
    # Two 1 s steps.
    runner.start(Sequence(steps=[step(1, "s", 1), step(1, "s", 2)]))
    # Wait.
    assert runner.join(10)
    # Paused state seen.
    assert states == [STATE_PAUSED]
    # Run time 2 s, clock time ~5 s.
    assert runner.status().elapsed_s == pytest.approx(2.0) and clock.t == pytest.approx(5.0, abs=0.06)
    # Second step started at run time 1 s (no shift by the pause).
    assert runner.timing[1].planned_ms == pytest.approx(1000) and runner.timing[1].deviation_ms == pytest.approx(0)


def test_write_error_ends_with_error_and_safe_state(env):
    """A hardware error ends the run with STATE_ERROR and Output OFF."""
    # Fake clock.
    clock = FakeClock()
    # Runner.
    runner = SequenceRunner(env["service"], env["config"], env["catalogs"], clock=clock)
    # Make the writes fail from 0.5 s on.
    clock.at(0.5, lambda: setattr(env["backend"], "fail_next_writes", 5))
    # Two steps.
    runner.start(Sequence(steps=[step(1, "s", 1), step(1, "s", 2)]))
    # Wait.
    assert runner.join(10)
    # Error state, output OFF.
    assert runner.status().state == STATE_ERROR and not env["service"].state.output_on


def test_output_off_elsewhere_aborts(env):
    """Output switched OFF outside the sequence (e.g. front panel) ends the run."""
    # Fake clock.
    clock = FakeClock()
    # Runner.
    runner = SequenceRunner(env["service"], env["config"], env["catalogs"], clock=clock)
    # Output OFF at 1.2 s.
    clock.at(1.2, lambda: env["service"].set_output(False))
    # Endless run.
    runner.start(Sequence(steps=[step(1, "s", 3)], repeat_count=0))
    # Wait.
    assert runner.join(10)
    # Error state with the reason.
    assert runner.status().state == STATE_ERROR and "switched OFF" in runner.status().message


def test_start_refused_when_invalid_or_not_configured(env, app_root):
    """Invalid sequences and unconfigured channels do not start."""
    # Runner.
    runner = SequenceRunner(env["service"], env["config"], env["catalogs"], clock=FakeClock())
    # Invalid sequence.
    assert errors_of(runner.start(Sequence(steps=[step(10, "ms", 1)])))
    # Unconfigured service.
    backend = SimulatedBackend(paths=app_root)
    # Not configured.
    idle = DaqService(backend, backend.check_driver())
    # Runner on it.
    runner2 = SequenceRunner(idle, env["config"], env["catalogs"], clock=FakeClock())
    # Refused.
    assert any("not set up" in i.message for i in runner2.start(Sequence(steps=[step(1, "s", 1)])))


# ---------------------------------------------------------------------- real clock


def test_real_time_accuracy(env):
    """Real clock: 5 steps of 100 ms; every step start within 15 ms, total within 30 ms."""
    # Runner with the real clock.
    runner = SequenceRunner(env["service"], env["config"], env["catalogs"])
    # Start.
    runner.start(Sequence(steps=[step(100, "ms", v) for v in (1, 2, 3, 4, 5)]))
    # Wait.
    assert runner.join(5)
    # Status.
    status = runner.status()
    # Completed.
    assert status.state == STATE_COMPLETED
    # Every step start close to plan (no drift).
    assert status.max_dev_ms < 15, [r.deviation_ms for r in runner.timing]
    # Total time close to 0.5 s.
    assert status.elapsed_s == pytest.approx(0.5, abs=0.03)
