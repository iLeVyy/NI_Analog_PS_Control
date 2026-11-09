"""
core/daq_backend.py
===================
Common interface of all DAQ backends plus the backend factory.

A backend is the lowest layer: it talks to one driver (NI-DAQmx or the
simulation). The DAQ service (core/daq_service.py) uses only this interface,
so the real hardware and the simulation are interchangeable.

Which backends exist is config-driven: config/presets/daq_backends.json lists
each backend with the module and class to load. Adding a backend = new class
+ new catalog item; the service code does not change.

LabVIEW analogy:
    DaqBackend is the parent class of a HAL; NiDaqmxBackend and
    SimulatedBackend are child classes using dynamic dispatch.
"""

# Import importlib to load backend classes named in the catalog.
import importlib
# Import abstract base class tools to define the interface.
from abc import ABC, abstractmethod
# Import dataclass to describe devices and driver information.
from dataclasses import dataclass, field
# Import typing helpers.
from typing import Any, Dict, List, Optional, Tuple

# Import the catalog type used by the factory.
from core.catalogs import Catalog
# Import the module logger factory.
from core.logger import get_logger

# Logger for this module.
_log = get_logger("daq_backend")


class DaqBackendError(Exception):
    """Raised by a backend when a driver call fails (device missing, write error, ...)."""


@dataclass
class DriverInfo:
    """
    Result of a driver check.

    Attributes:
        available (bool): True when the driver can be used.
        version (str): Driver version text, e.g. "23.8.0" ("" if unknown).
        message (str): Human-readable explanation (shown in the status bar / log).
    """

    # Driver usable or not.
    available: bool
    # Driver version text.
    version: str = ""
    # Explanation text.
    message: str = ""


@dataclass
class DeviceInfo:
    """
    One device found by a scan.

    Attributes:
        name (str): Device name, e.g. "cDAQ1Mod1".
        product_type (str): Product type, e.g. "NI 9263".
        serial (str): Serial number as text.
        ao_channels (List[str]): Physical AO channel names.
        ao_ranges (List[Tuple[float, float]]): Supported AO voltage ranges.
        simulated (bool): True for simulated devices (backend or NI MAX).
    """

    # Device name.
    name: str
    # Product type.
    product_type: str = ""
    # Serial number.
    serial: str = ""
    # AO physical channels.
    ao_channels: List[str] = field(default_factory=list)
    # Supported AO ranges as (min, max) pairs.
    ao_ranges: List[Tuple[float, float]] = field(default_factory=list)
    # Simulated device flag.
    simulated: bool = False

    @property
    def widest_range(self) -> Optional[Tuple[float, float]]:
        """
        Return the widest supported AO range (used as default device range).

        Returns:
            Optional[Tuple[float, float]]: (min, max) or None if no range is known.
        """
        # Without ranges there is nothing to choose.
        if not self.ao_ranges:
            # No range known.
            return None
        # Pick the range with the largest width (max - min).
        return max(self.ao_ranges, key=lambda rng: rng[1] - rng[0])

    def to_dict(self) -> Dict[str, Any]:
        """Return the device as a JSON-ready dictionary (for events and logs)."""
        # Build the dictionary field by field.
        return {"name": self.name, "product_type": self.product_type, "serial": self.serial,
                "ao_channels": list(self.ao_channels),
                "ao_ranges": [list(rng) for rng in self.ao_ranges],
                "simulated": self.simulated}


def device_of_channel(physical_channel: str) -> str:
    """
    Return the device part of a physical channel name.

    Inputs:
        physical_channel (str): e.g. "cDAQ1Mod1/ao0".

    Returns:
        str: e.g. "cDAQ1Mod1" ("" for an empty input).
    """
    # The device name is the text before the first "/".
    return physical_channel.split("/", 1)[0] if physical_channel else ""


class DaqBackend(ABC):
    """
    Interface every backend implements.

    All methods are called from the DAQ worker thread only, so a backend
    does not need its own locking.
    """

    # Catalog id of the backend (set by the factory).
    backend_id: str = ""
    # True for the simulation backend.
    simulated: bool = False

    @abstractmethod
    def check_driver(self) -> DriverInfo:
        """
        Check whether the driver is installed and usable.

        Returns:
            DriverInfo: Availability, version and message. Must not raise.
        """

    @abstractmethod
    def list_devices(self) -> List[DeviceInfo]:
        """
        Scan for devices with AO channels.

        Returns:
            List[DeviceInfo]: Devices that have at least one AO channel.

        Raises:
            DaqBackendError: If the scan fails.
        """

    @abstractmethod
    def open_ao_channel(self, physical_channel: str, ao_min: float, ao_max: float) -> Any:
        """
        Create an on-demand AO voltage task for one channel.

        Inputs:
            physical_channel (str): e.g. "cDAQ1Mod1/ao0".
            ao_min (float): Task minimum = used AO span minimum.
            ao_max (float): Task maximum = used AO span maximum.

        Returns:
            Any: Backend-specific handle passed to write_ao() / close_ao_channel().

        Raises:
            DaqBackendError: If the channel cannot be created.
        """

    @abstractmethod
    def write_ao(self, handle: Any, volts: float) -> None:
        """
        Write one AO value (software-timed, on demand).

        Inputs:
            handle (Any): Handle from open_ao_channel().
            volts (float): AO voltage (already scaled and clamped).

        Raises:
            DaqBackendError: If the write fails.
        """

    @abstractmethod
    def close_ao_channel(self, handle: Any) -> None:
        """
        Stop and release one AO task. Must not raise.

        Inputs:
            handle (Any): Handle from open_ao_channel().
        """


def _load_backend_class(item: Dict[str, Any]):
    """
    Import the backend class named in a catalog item.

    Inputs:
        item (Dict[str, Any]): Catalog item with "module" and "class".

    Returns:
        type: The backend class.

    Raises:
        DaqBackendError: If the module or class cannot be loaded.
    """
    # Try to import the module and read the class from it.
    try:
        # Import the module by its dotted name.
        module = importlib.import_module(item["module"])
        # Read the class attribute from the module.
        return getattr(module, item["class"])
    # Import failed or the class is missing.
    except (ImportError, AttributeError, KeyError) as exc:
        # Report the broken catalog item.
        raise DaqBackendError(f"Backend '{item.get('id')}' cannot be loaded: {exc}") from exc


def create_backend(catalog: Catalog, backend_id: str, **kwargs: Any) -> Tuple[DaqBackend, DriverInfo]:
    """
    Create the backend selected in settings.json.

    "auto" tries the ids in the catalog's "auto_order" list and returns the
    first backend whose driver check passes.

    Inputs:
        catalog (Catalog): The daq_backends catalog.
        backend_id (str): Backend id or "auto".
        **kwargs: Extra arguments for the backend constructor (e.g. paths).

    Returns:
        Tuple[DaqBackend, DriverInfo]: The backend and its driver check result.

    Raises:
        DaqBackendError: If the id is unknown or no backend is usable in auto mode.
    """
    # Decide which ids to try: the auto order, or just the selected id.
    if backend_id == "auto":
        # Read the order from the catalog (fall back to file order).
        candidates = catalog.raw.get("auto_order") or catalog.ids()
    # A fixed backend was selected.
    else:
        # Only this one id is tried.
        candidates = [backend_id]
    # Remember the last driver result for the error message.
    last_info = DriverInfo(available=False, message="No backend tried")
    # Try each candidate in order.
    for candidate in candidates:
        # Find the catalog item.
        item = catalog.get(candidate)
        # Unknown ids are a configuration error.
        if item is None:
            # Report the unknown id.
            raise DaqBackendError(f"Unknown DAQ backend '{candidate}' (see config/presets/daq_backends.json)")
        # Load the class named in the item.
        backend_class = _load_backend_class(item)
        # Create the backend instance.
        backend = backend_class(**kwargs)
        # Store the catalog id on the instance.
        backend.backend_id = candidate
        # Store the simulated flag from the catalog.
        backend.simulated = bool(item.get("simulated", False))
        # Check the driver.
        info = backend.check_driver()
        # Log the check result.
        _log.info("Backend '%s': driver %s (%s)", candidate,
                  "available" if info.available else "NOT available", info.message)
        # A fixed selection is returned even when the driver is missing (service reports it).
        if backend_id != "auto" or info.available:
            # Use this backend.
            return backend, info
        # Remember the result and try the next candidate.
        last_info = info
    # No candidate worked in auto mode.
    raise DaqBackendError(f"No usable DAQ backend found: {last_info.message}")


def _version_tuple(text: str) -> Tuple[int, ...]:
    """
    Convert a version text into a tuple of numbers for comparison.

    Inputs:
        text (str): e.g. "23.8.0" (non-numeric parts are ignored).

    Returns:
        Tuple[int, ...]: e.g. (23, 8, 0); empty tuple if no number is found.
    """
    # Keep only the parts that are plain numbers.
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def check_min_driver_version(info: DriverInfo, minimum: str, simulated: bool) -> DriverInfo:
    """
    Mark the driver as not available when it is older than the configured minimum.

    The check is skipped for the simulation and when minimum is "" (settings.json
    daq.min_driver_version).

    Inputs:
        info (DriverInfo): Driver check result.
        minimum (str): Minimum version text, e.g. "23.0" ("" = no check).
        simulated (bool): True for the simulation backend.

    Returns:
        DriverInfo: The same result, or a "not available" result if too old.
    """
    # Nothing to check: no minimum, simulation, or driver already missing.
    if not minimum or simulated or not info.available:
        # Return unchanged.
        return info
    # Compare the versions as number tuples.
    if _version_tuple(info.version) < _version_tuple(minimum):
        # Build the "too old" result.
        return DriverInfo(available=False, version=info.version,
                          message=f"NI-DAQmx {info.version} is older than the required {minimum}")
    # Version is fine.
    return info


def check_hardware(voltage_channel: Any, current_channel: Any, devices: List[DeviceInfo]) -> List[str]:
    """
    Check the configured channels against a device scan (Project_Plan.md 7.3, 9.3).

    Checks per channel: device present, channel belongs to the device, and the
    used AO span fits into one supported device range (D-05).

    Inputs:
        voltage_channel (ChannelConfig): Voltage channel settings.
        current_channel (ChannelConfig): Current channel settings.
        devices (List[DeviceInfo]): Scan result.

    Returns:
        List[str]: Error messages (empty = OK). Unassigned channels are skipped.
    """
    # Collected errors.
    errors: List[str] = []
    # Devices by name for quick lookup.
    by_name = {device.name: device for device in devices}
    # Check both channels with a readable label.
    for label, channel in (("Voltage", voltage_channel), ("Current", current_channel)):
        # Unassigned channels are reported elsewhere.
        if not channel.physical_channel:
            # Skip this channel.
            continue
        # Device name from the settings or from the physical channel.
        device_name = channel.device or device_of_channel(channel.physical_channel)
        # Find the device.
        device = by_name.get(device_name)
        # The device must be present.
        if device is None:
            # Report the missing device.
            errors.append(f"{label} channel: configured device not present: {device_name}")
            # Nothing more to check for this channel.
            continue
        # The channel must belong to the device.
        if channel.physical_channel not in device.ao_channels:
            # Report the unknown channel.
            errors.append(f"{label} channel: {channel.physical_channel} not found on {device_name}")
        # The used span must fit into one supported device range.
        if device.ao_ranges and not any(lo - 1e-9 <= channel.ao_min and channel.ao_max <= hi + 1e-9
                                        for lo, hi in device.ao_ranges):
            # Report the unsupported span.
            errors.append(f"{label} channel: span {channel.ao_min:g}..{channel.ao_max:g} V "
                          f"not supported by {device_name}")
    # Return the errors.
    return errors
