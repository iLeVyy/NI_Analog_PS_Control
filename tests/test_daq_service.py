"""
tests/test_daq_service.py
=========================
Unit tests for core/daq_service.py using the simulation backend
(Project_Plan.md sections 12, 13; decisions D-02, D-05, D-06).
"""

# Import pytest for fixtures, float comparison and exceptions.
import pytest

# Import the config manager to get a valid configuration.
from core.config_manager import ConfigManager
# Import the backend error type.
from core.daq_backend import DaqBackendError
# Import the service and its constants.
from core.daq_service import (CURRENT, EVT_CONFIGURED, EVT_ERROR, EVT_OUTPUT, EVT_SAFE_STATE,
                              VOLTAGE, DaqService, DaqServiceError)
# Import the simulation backend.
from core.simulation import SimulatedBackend

# Seconds to wait for one service command in the tests.
TIMEOUT = 5


@pytest.fixture
def config(app_root):
    """
    Default configuration with cDAQ1Mod1/ao0 (voltage) and ao1 (current).

    Returns:
        AppConfig: Valid configuration (0-20 V, 0-100 A, 0-10 V span).
    """
    # Load the defaults.
    cfg = ConfigManager(app_root, "test").load().config
    # Assign the voltage channel.
    cfg.voltage_channel.device, cfg.voltage_channel.physical_channel = "cDAQ1Mod1", "cDAQ1Mod1/ao0"
    # Assign the current channel.
    cfg.current_channel.device, cfg.current_channel.physical_channel = "cDAQ1Mod1", "cDAQ1Mod1/ao1"
    # Return the configuration.
    return cfg


@pytest.fixture
def sim(app_root):
    """Simulation backend reading the shipped config/simulation/devices.json."""
    # Create the backend.
    backend = SimulatedBackend(paths=app_root)
    # Give it its catalog id.
    backend.backend_id = "simulation"
    # Return it.
    return backend


@pytest.fixture
def service(sim):
    """
    Started DAQ service with an event recorder; closed after the test.

    Returns:
        DaqService: Service with attribute "events" (list of DaqEvent).
    """
    # Create the service.
    svc = DaqService(sim, sim.check_driver())
    # Event list for assertions.
    svc.events = []
    # Record every event.
    svc.add_listener(svc.events.append)
    # Start the worker thread.
    svc.start()
    # Give the service to the test.
    yield svc
    # Always close at the end.
    svc.close()


def kinds(svc):
    """Return the list of event kinds received so far."""
    # Extract the kinds.
    return [event.kind for event in svc.events]


def test_scan_lists_simulated_devices(service):
    """Scan returns the devices from devices.json with ranges."""
    # Scan.
    devices = service.scan().result(TIMEOUT)
    # Both simulated devices are found.
    assert [d.name for d in devices] == ["cDAQ1Mod1", "Dev2"]
    # The NI 9263 range is +-10 V.
    assert devices[0].widest_range == (-10.0, 10.0)


def test_configure_ok_applies_safe_value(service, sim, config):
    """Configure opens both tasks, output OFF, safe value 0 written."""
    # Configure.
    ok, errors, _ = service.configure(config).result(TIMEOUT)
    # Must succeed.
    assert ok and errors == []
    # Output must be OFF.
    assert service.state.output_on is False
    # Both channels at 0 V AO.
    assert sim.last_values == {"cDAQ1Mod1/ao0": 0.0, "cDAQ1Mod1/ao1": 0.0}
    # Configured event was sent.
    assert EVT_CONFIGURED in kinds(service)


def test_unassigned_channels_block_configure(service, app_root):
    """Fresh defaults (no channels) cannot be configured."""
    # Default config without channels.
    cfg = ConfigManager(app_root, "test").load().config
    # Configure.
    ok, errors, _ = service.configure(cfg).result(TIMEOUT)
    # Must fail with two errors.
    assert not ok and len(errors) == 2


def test_missing_device_is_reported(service, config):
    """A device that is not present blocks configuration (plan 9.3)."""
    # Point the voltage channel to an unknown device.
    config.voltage_channel.device, config.voltage_channel.physical_channel = "Dev9", "Dev9/ao0"
    # Configure.
    ok, errors, _ = service.configure(config).result(TIMEOUT)
    # Must fail with "not present".
    assert not ok and any("not present" in e for e in errors)


def test_span_outside_device_range_is_reported(service, config):
    """A span the device does not support blocks configuration (D-05)."""
    # Device range of the simulated device is +-10 V; claim a wider device range in settings.
    config.voltage_channel.device_ao_min, config.voltage_channel.device_ao_max = -20.0, 20.0
    # Use a 0-15 V span (valid against the claimed range, not against the real device).
    config.voltage_channel.ao_max = 15.0
    # Configure.
    ok, errors, _ = service.configure(config).result(TIMEOUT)
    # Must fail with the hardware check message.
    assert not ok and any("not supported" in e for e in errors)


def test_setpoint_stored_but_not_written_while_off(service, sim, config):
    """With Output OFF a setpoint is stored only."""
    # Configure.
    service.configure(config).result(TIMEOUT)
    # Number of writes so far.
    writes = len(sim.history)
    # Set 10 V.
    assert service.set_setpoint(VOLTAGE, 10).result(TIMEOUT) == 10
    # No new write.
    assert len(sim.history) == writes


def test_output_on_writes_scaled_values(service, sim, config):
    """Plan example: 10 V -> 5 V AO, 50 A -> 5 V AO when Output ON."""
    # Configure.
    service.configure(config).result(TIMEOUT)
    # Set the voltage.
    service.set_setpoint(VOLTAGE, 10).result(TIMEOUT)
    # Set the current.
    service.set_setpoint(CURRENT, 50).result(TIMEOUT)
    # Switch ON.
    assert service.set_output(True).result(TIMEOUT) is True
    # Voltage channel at 5 V AO.
    assert sim.last_values["cDAQ1Mod1/ao0"] == pytest.approx(5.0)
    # Current channel at 5 V AO.
    assert sim.last_values["cDAQ1Mod1/ao1"] == pytest.approx(5.0)
    # Changing the setpoint while ON writes immediately.
    service.set_setpoint(VOLTAGE, 20).result(TIMEOUT)
    # 20 V -> 10 V AO.
    assert sim.last_values["cDAQ1Mod1/ao0"] == pytest.approx(10.0)


def test_non_zero_ps_min(service, sim, config):
    """D-06: 2-20 V range, 11 V -> 5 V AO."""
    # Set PS min to 2 V.
    config.voltage_channel.ps_min = 2.0
    # Configure.
    service.configure(config).result(TIMEOUT)
    # Set 11 V.
    service.set_setpoint(VOLTAGE, 11).result(TIMEOUT)
    # Switch ON.
    service.set_output(True).result(TIMEOUT)
    # Check the AO value.
    assert sim.last_values["cDAQ1Mod1/ao0"] == pytest.approx(5.0)


def test_setpoint_is_clamped_to_ps_range(service, sim, config):
    """Values above PS max are clamped; AO never exceeds the span."""
    # Configure.
    service.configure(config).result(TIMEOUT)
    # Request 50 V on a 20 V supply.
    assert service.set_setpoint(VOLTAGE, 50).result(TIMEOUT) == 20
    # Switch ON.
    service.set_output(True).result(TIMEOUT)
    # AO at span maximum.
    assert sim.last_values["cDAQ1Mod1/ao0"] == pytest.approx(10.0)


def test_output_off_applies_safe_value(service, sim, config):
    """Output OFF writes the safe value to both channels."""
    # Configure.
    service.configure(config).result(TIMEOUT)
    # Set a value.
    service.set_setpoint(VOLTAGE, 10).result(TIMEOUT)
    # ON.
    service.set_output(True).result(TIMEOUT)
    # OFF.
    service.set_output(False).result(TIMEOUT)
    # Both channels back at 0 V.
    assert sim.last_values == {"cDAQ1Mod1/ao0": 0.0, "cDAQ1Mod1/ao1": 0.0}
    # Output event for OFF was sent.
    assert any(e.kind == EVT_OUTPUT and e.data["on"] is False for e in service.events)


def test_output_on_refused_when_not_configured(service):
    """Output ON without configuration raises DaqServiceError."""
    # Request ON.
    with pytest.raises(DaqServiceError):
        # Wait for the refused command.
        service.set_output(True).result(TIMEOUT)


def test_write_error_forces_safe_state(service, sim, config):
    """A write error: error event, Output OFF, safe value written (plan 13)."""
    # Configure.
    service.configure(config).result(TIMEOUT)
    # ON.
    service.set_output(True).result(TIMEOUT)
    # Make the next write fail.
    sim.fail_next_writes = 1
    # The setpoint write fails.
    with pytest.raises(DaqBackendError):
        # Wait for the failing command.
        service.set_setpoint(VOLTAGE, 10).result(TIMEOUT)
    # Output must be OFF.
    assert service.state.output_on is False
    # Error and safe-state events were sent.
    assert EVT_ERROR in kinds(service) and EVT_SAFE_STATE in kinds(service)
    # Safe value written on both channels.
    assert sim.last_values == {"cDAQ1Mod1/ao0": 0.0, "cDAQ1Mod1/ao1": 0.0}


def test_device_removed_while_on(service, sim, config):
    """Unplugged device: write fails, Output OFF, last error stored."""
    # Configure.
    service.configure(config).result(TIMEOUT)
    # ON.
    service.set_output(True).result(TIMEOUT)
    # Unplug the device.
    sim.remove_device("cDAQ1Mod1")
    # The next write fails.
    with pytest.raises(DaqBackendError):
        # Wait for the failing command.
        service.set_setpoint(VOLTAGE, 5).result(TIMEOUT)
    # Output OFF and error stored.
    assert service.state.output_on is False and "not present" in service.state.last_error


def test_close_writes_exit_value(sim, config):
    """close() writes safe_value_exit and refuses further commands."""
    # Use a non-zero exit value to see it.
    config.safety.safe_value_exit = 2.0
    # Create and start a service.
    svc = DaqService(sim, sim.check_driver())
    # Start.
    svc.start()
    # Configure.
    svc.configure(config).result(TIMEOUT)
    # Close.
    svc.close()
    # 2 V on a 0-20 V supply -> 1 V AO.
    assert sim.last_values["cDAQ1Mod1/ao0"] == pytest.approx(1.0)
    # Commands after close are refused.
    with pytest.raises(DaqServiceError):
        # Wait for the refused command.
        svc.scan().result(TIMEOUT)


def test_listener_error_does_not_stop_service(service, config):
    """A failing listener is logged; the service keeps working."""
    # Listener that always fails.
    def broken(_event):
        """Raise on every event."""
        # Simulated listener bug.
        raise RuntimeError("listener bug")
    # Register it.
    service.add_listener(broken)
    # Configure still works.
    ok, _, _ = service.configure(config).result(TIMEOUT)
    # Must succeed.
    assert ok


def test_service_without_thread_runs_synchronously(sim, config):
    """Without start() commands run in the caller's thread."""
    # Create without starting.
    svc = DaqService(sim, sim.check_driver())
    # Configure synchronously.
    ok, _, _ = svc.configure(config).result(0)
    # Must succeed.
    assert ok
    # Close.
    svc.close()
