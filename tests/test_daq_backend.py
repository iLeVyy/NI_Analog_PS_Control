"""
tests/test_daq_backend.py
=========================
Unit tests for the backend factory, the simulation backend and the
NI-DAQmx backend (with a fake "nidaqmx" module - no hardware needed).
"""

# Import sys to inject the fake nidaqmx module.
import sys
# Import SimpleNamespace to build fake driver objects.
from types import SimpleNamespace

# Import pytest for fixtures and exceptions.
import pytest

# Import the catalog loader.
from core.catalogs import load_catalog
# Import the factory and types.
from core.daq_backend import DaqBackendError, DeviceInfo, create_backend, device_of_channel
# Import the NI backend and helper.
from core.nidaqmx_backend import NiDaqmxBackend, pair_ranges


def test_device_of_channel():
    """The device name is the part before the first slash."""
    # Normal channel.
    assert device_of_channel("cDAQ1Mod1/ao0") == "cDAQ1Mod1"
    # Empty input.
    assert device_of_channel("") == ""


def test_widest_range():
    """widest_range picks the largest width."""
    # Device with two ranges.
    device = DeviceInfo("Dev1", ao_ranges=[(-5.0, 5.0), (-10.0, 10.0)])
    # The +-10 V range is wider.
    assert device.widest_range == (-10.0, 10.0)


def test_pair_ranges():
    """DAQmx flat range list is converted into pairs."""
    # Convert.
    assert pair_ranges([-10, 10, -5, 5]) == [(-10.0, 10.0), (-5.0, 5.0)]


def test_factory_simulation(app_root):
    """The factory creates the simulation backend by id."""
    # Load the backend catalog.
    catalog = load_catalog(app_root.presets_dir, "daq_backends")
    # Create it.
    backend, info = create_backend(catalog, "simulation", paths=app_root)
    # Available and flagged as simulated.
    assert info.available and backend.simulated and backend.backend_id == "simulation"


def test_factory_auto_falls_back_to_simulation(app_root, monkeypatch):
    """'auto' uses the simulation when nidaqmx is not usable."""
    # Make "import nidaqmx" fail even if the package is installed.
    monkeypatch.setitem(sys.modules, "nidaqmx", None)
    # Load the backend catalog.
    catalog = load_catalog(app_root.presets_dir, "daq_backends")
    # Create in auto mode.
    backend, _ = create_backend(catalog, "auto", paths=app_root)
    # The simulation was chosen.
    assert backend.backend_id == "simulation"


def test_factory_fixed_nidaqmx_reports_missing_driver(app_root, monkeypatch):
    """A fixed 'nidaqmx' selection is returned with available=False when missing."""
    # Make "import nidaqmx" fail.
    monkeypatch.setitem(sys.modules, "nidaqmx", None)
    # Load the backend catalog.
    catalog = load_catalog(app_root.presets_dir, "daq_backends")
    # Create with the fixed id.
    backend, info = create_backend(catalog, "nidaqmx", paths=app_root)
    # Backend returned, driver not available.
    assert backend.backend_id == "nidaqmx" and not info.available


def test_factory_unknown_id(app_root):
    """Unknown backend ids raise DaqBackendError."""
    # Load the backend catalog.
    catalog = load_catalog(app_root.presets_dir, "daq_backends")
    # Unknown id.
    with pytest.raises(DaqBackendError):
        # Try to create it.
        create_backend(catalog, "does_not_exist")


class _FakeTask:
    """Minimal stand-in for nidaqmx.Task."""

    def __init__(self):
        """Create the fake task with an empty write log."""
        # Channels added (name, min, max).
        self.added = []
        # Values written.
        self.written = []
        # Closed flag.
        self.closed = False
        # Fake ao_channels collection with add_ao_voltage_chan().
        self.ao_channels = SimpleNamespace(
            add_ao_voltage_chan=lambda name, min_val, max_val: self.added.append((name, min_val, max_val)))

    def write(self, value, auto_start=True):
        """Record a written value."""
        # Store it.
        self.written.append(value)

    def stop(self):
        """Fake stop."""

    def close(self):
        """Mark as closed."""
        # Set the flag.
        self.closed = True


@pytest.fixture
def fake_nidaqmx(monkeypatch):
    """Inject a fake nidaqmx module with one AO device and one AI-only device."""
    # AO device.
    ao_device = SimpleNamespace(name="cDAQ1Mod1", product_type="NI 9263", serial_num=123,
                                ao_physical_chans=[SimpleNamespace(name="cDAQ1Mod1/ao0")],
                                ao_voltage_rngs=[-10.0, 10.0], is_simulated=False)
    # Device without AO (must be skipped).
    ai_device = SimpleNamespace(name="cDAQ1Mod2", product_type="NI 9215", serial_num=456,
                                ao_physical_chans=[], ao_voltage_rngs=[], is_simulated=False)
    # Fake system object.
    system = SimpleNamespace(devices=[ao_device, ai_device],
                             driver_version=SimpleNamespace(major_version=23, minor_version=8,
                                                            update_version=0))
    # Fake module tree.
    module = SimpleNamespace(Task=_FakeTask,
                             system=SimpleNamespace(System=SimpleNamespace(local=lambda: system)))
    # Inject it.
    monkeypatch.setitem(sys.modules, "nidaqmx", module)
    # Give it to the test.
    return module


def test_nidaqmx_backend_with_fake_driver(fake_nidaqmx):
    """Driver version, scan filtering, task creation and write mapping."""
    # Create the backend.
    backend = NiDaqmxBackend()
    # Driver check.
    info = backend.check_driver()
    # Version text from the fake driver.
    assert info.available and info.version == "23.8.0"
    # Scan.
    devices = backend.list_devices()
    # Only the AO device.
    assert [d.name for d in devices] == ["cDAQ1Mod1"] and devices[0].ao_ranges == [(-10.0, 10.0)]
    # Open a channel with the used span.
    task = backend.open_ao_channel("cDAQ1Mod1/ao0", 0.0, 10.0)
    # Task limits = used span.
    assert task.added == [("cDAQ1Mod1/ao0", 0.0, 10.0)]
    # Write.
    backend.write_ao(task, 5.0)
    # Value reached the task.
    assert task.written == [5.0]
    # Close.
    backend.close_ao_channel(task)
    # Closed.
    assert task.closed


def test_min_driver_version_check():
    """Too-old drivers are marked as not available; simulation and '' skip the check."""
    # Import here to keep the test self-contained.
    from core.daq_backend import DriverInfo, check_min_driver_version
    # Driver 23.8.0.
    info = DriverInfo(available=True, version="23.8.0")
    # Minimum 23.0 -> still available.
    assert check_min_driver_version(info, "23.0", False).available
    # Minimum 24.0 -> not available.
    assert not check_min_driver_version(info, "24.0", False).available
    # Empty minimum -> no check.
    assert check_min_driver_version(info, "", False).available
    # Simulation -> no check.
    assert check_min_driver_version(info, "99", True).available
