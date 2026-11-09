"""
core/simulation.py
==================
Simulation backend: behaves like NI-DAQmx without hardware.

The simulated devices are config-driven: they are read from
config/simulation/devices.json. Add an item there to simulate another module.

Extra test helpers (not part of the DaqBackend interface):
    * last_values      - last AO volts written per physical channel
    * history          - list of (physical_channel, volts) in write order
    * fail_next_writes - number of following writes that raise an error
    * remove_device()  - simulate an unplugged device

LabVIEW analogy:
    A "simulated HAL child class" used for testing without hardware.
"""

# Import json to read the simulated device list.
import json
# Import dataclass to define the channel handle.
from dataclasses import dataclass
# Import typing helpers.
from typing import Any, Dict, List, Optional, Tuple

# Import the path container.
from core.app_paths import AppPaths
# Import the backend interface and data types.
from core.daq_backend import (DaqBackend, DaqBackendError, DeviceInfo, DriverInfo,
                              device_of_channel)
# Import the module logger factory.
from core.logger import get_logger

# Logger for this module.
_log = get_logger("simulation")

# File name of the simulated device list inside config/simulation.
DEVICES_FILE = "devices.json"


@dataclass
class SimChannelHandle:
    """
    Handle of one open simulated AO channel.

    Attributes:
        physical_channel (str): Channel name.
        ao_min (float): Task minimum.
        ao_max (float): Task maximum.
        open (bool): False after close_ao_channel().
    """

    # Channel name.
    physical_channel: str
    # Task minimum.
    ao_min: float
    # Task maximum.
    ao_max: float
    # Open flag.
    open: bool = True


class SimulatedBackend(DaqBackend):
    """Backend that simulates AO devices read from config/simulation/devices.json."""

    def __init__(self, paths: Optional[AppPaths] = None,
                 devices: Optional[List[Dict[str, Any]]] = None, **_: Any) -> None:
        """
        Create the simulation backend.

        Inputs:
            paths (Optional[AppPaths]): Application paths (to find devices.json).
            devices (Optional[List[Dict]]): Device items given directly (tests);
                overrides the file when not None.
            **_: Other keyword arguments are ignored (common factory signature).

        Returns:
            None.
        """
        # Store the paths for the driver check / scan.
        self._paths = paths
        # Device items given directly (None = read from file).
        self._given_devices = devices
        # Driver version text from the file.
        self._driver_version = "SIM"
        # Names of devices "unplugged" with remove_device().
        self._removed: set = set()
        # Last AO value per physical channel.
        self.last_values: Dict[str, float] = {}
        # All writes in order.
        self.history: List[Tuple[str, float]] = []
        # Number of following writes that should fail.
        self.fail_next_writes = 0

    def _load_items(self) -> List[Dict[str, Any]]:
        """
        Get the simulated device items (direct list or devices.json).

        Returns:
            List[Dict[str, Any]]: Device items.

        Raises:
            DaqBackendError: If devices.json is missing or invalid.
        """
        # Use the directly given list when available.
        if self._given_devices is not None:
            # Return the given items.
            return self._given_devices
        # Without paths the file cannot be found.
        if self._paths is None:
            # Report the missing paths.
            raise DaqBackendError("Simulation backend needs the application paths")
        # Path of the device list.
        path = self._paths.simulation_dir / DEVICES_FILE
        # Try to read and parse the file.
        try:
            # Parse the JSON file.
            data = json.loads(path.read_text(encoding="utf-8"))
        # Missing file or invalid JSON.
        except (OSError, ValueError) as exc:
            # Report the problem.
            raise DaqBackendError(f"Simulated device list not readable ({path}): {exc}") from exc
        # Remember the simulated driver version.
        self._driver_version = str(data.get("driver_version", "SIM"))
        # Return the device items.
        return list(data.get("items", []))

    def check_driver(self) -> DriverInfo:
        """
        The simulation is available when its device list can be read.

        Returns:
            DriverInfo: Availability, version and message.
        """
        # Try to read the device list.
        try:
            # Load the items (also reads the version).
            items = self._load_items()
        # The list is not readable.
        except DaqBackendError as exc:
            # Report "not available".
            return DriverInfo(available=False, message=str(exc))
        # Report "available" with the number of simulated devices.
        return DriverInfo(available=True, version=self._driver_version,
                          message=f"Simulation with {len(items)} device(s)")

    def list_devices(self) -> List[DeviceInfo]:
        """
        Return the simulated devices that are not "removed".

        Returns:
            List[DeviceInfo]: Simulated devices.
        """
        # Result list.
        devices: List[DeviceInfo] = []
        # Convert each item to a DeviceInfo.
        for item in self._load_items():
            # Skip devices that were unplugged with remove_device().
            if item["id"] in self._removed:
                # Not visible in the scan.
                continue
            # Build the device description.
            devices.append(DeviceInfo(
                # Device name.
                name=item["id"],
                # Product type text.
                product_type=item.get("product_type", "Simulated"),
                # Serial number text.
                serial=str(item.get("serial", "")),
                # AO channel names.
                ao_channels=list(item.get("ao_channels", [])),
                # AO ranges as tuples.
                ao_ranges=[(float(lo), float(hi)) for lo, hi in item.get("ao_ranges", [])],
                # Always simulated.
                simulated=True))
        # Return the list.
        return devices

    def _channel_exists(self, physical_channel: str) -> bool:
        """
        Check whether a channel belongs to a present simulated device.

        Inputs:
            physical_channel (str): Channel name.

        Returns:
            bool: True when the channel exists.
        """
        # Search all present devices.
        return any(physical_channel in device.ao_channels for device in self.list_devices())

    def open_ao_channel(self, physical_channel: str, ao_min: float, ao_max: float) -> SimChannelHandle:
        """
        Open a simulated AO channel.

        Inputs:
            physical_channel (str): Channel name.
            ao_min (float): Task minimum.
            ao_max (float): Task maximum.

        Returns:
            SimChannelHandle: Handle for later writes.

        Raises:
            DaqBackendError: If the channel does not exist.
        """
        # The channel must belong to a present device.
        if not self._channel_exists(physical_channel):
            # Report the unknown channel like DAQmx would.
            raise DaqBackendError(f"Physical channel not found: {physical_channel}")
        # Log the open.
        _log.debug("SIM open %s (%g..%g V)", physical_channel, ao_min, ao_max)
        # Return a new handle.
        return SimChannelHandle(physical_channel, ao_min, ao_max)

    def write_ao(self, handle: SimChannelHandle, volts: float) -> None:
        """
        Store a simulated AO write.

        Inputs:
            handle (SimChannelHandle): Channel handle.
            volts (float): AO voltage.

        Raises:
            DaqBackendError: On injected failures, closed handles, removed
                devices, or values outside the task range (like DAQmx).
        """
        # Injected failure for tests.
        if self.fail_next_writes > 0:
            # Count down the remaining failures.
            self.fail_next_writes -= 1
            # Raise the simulated write error.
            raise DaqBackendError(f"Simulated write failure on {handle.physical_channel}")
        # Writing to a closed handle is a programming error.
        if not handle.open:
            # Report the closed task.
            raise DaqBackendError(f"Task for {handle.physical_channel} is closed")
        # An unplugged device cannot be written.
        if device_of_channel(handle.physical_channel) in self._removed:
            # Report the missing device.
            raise DaqBackendError(f"Device not present: {device_of_channel(handle.physical_channel)}")
        # DAQmx rejects values outside the task min/max; do the same.
        if not handle.ao_min - 1e-9 <= volts <= handle.ao_max + 1e-9:
            # Report the range violation.
            raise DaqBackendError(f"{volts} V outside task range {handle.ao_min}..{handle.ao_max} V")
        # Store the last value.
        self.last_values[handle.physical_channel] = volts
        # Append to the history.
        self.history.append((handle.physical_channel, volts))

    def close_ao_channel(self, handle: SimChannelHandle) -> None:
        """
        Close a simulated channel.

        Inputs:
            handle (SimChannelHandle): Channel handle.
        """
        # Mark the handle as closed.
        handle.open = False

    def remove_device(self, name: str) -> None:
        """
        Test helper: simulate unplugging a device.

        Inputs:
            name (str): Device name.
        """
        # Add the device to the removed set.
        self._removed.add(name)
