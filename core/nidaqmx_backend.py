"""
core/nidaqmx_backend.py
=======================
Real hardware backend using the official NI "nidaqmx" Python package.

The package is imported lazily inside the methods, so the application still
starts (and can use the simulation) on a PC without nidaqmx / NI-DAQmx.

DAQmx calls used:
    nidaqmx.system.System.local()          -> driver version, device list
    Device.product_type / serial_num       -> device description
    Device.ao_physical_chans               -> AO channel names
    Device.ao_voltage_rngs                 -> flat list [min1, max1, min2, max2, ...]
    Device.is_simulated                    -> NI MAX simulated device flag
    Task.ao_channels.add_ao_voltage_chan() -> on-demand AO task (min/max = used span)
    Task.write(value, auto_start=True)     -> software-timed single write
    Task.stop() / Task.close()             -> release

NOTE: Written against the public nidaqmx API; not executed on real hardware
in the creation environment. Verify on the target PC (see HANDOFF.md).

LabVIEW analogy:
    The DAQmx HAL child class (DAQmx Create Channel / Write / Clear Task).
"""

# Import typing helpers.
from typing import Any, List, Tuple

# Import the backend interface and data types.
from core.daq_backend import DaqBackend, DaqBackendError, DeviceInfo, DriverInfo
# Import the module logger factory.
from core.logger import get_logger

# Logger for this module.
_log = get_logger("nidaqmx_backend")


def pair_ranges(flat: List[float]) -> List[Tuple[float, float]]:
    """
    Convert DAQmx's flat range list into (min, max) pairs.

    Inputs:
        flat (List[float]): e.g. [-10.0, 10.0, -5.0, 5.0].

    Returns:
        List[Tuple[float, float]]: e.g. [(-10.0, 10.0), (-5.0, 5.0)].
    """
    # Take every even element as min and the following odd element as max.
    return [(float(flat[i]), float(flat[i + 1])) for i in range(0, len(flat) - 1, 2)]


def _import_nidaqmx():
    """
    Import the nidaqmx package.

    Returns:
        module: The nidaqmx module.

    Raises:
        DaqBackendError: If the package is not installed.
    """
    # Try the import.
    try:
        # Import the NI package.
        import nidaqmx  # noqa: WPS433 (lazy import on purpose)
    # The package is missing.
    except ImportError as exc:
        # Report it in plain words.
        raise DaqBackendError("Python package 'nidaqmx' is not installed") from exc
    # Return the module.
    return nidaqmx


class NiDaqmxBackend(DaqBackend):
    """Backend for real NI hardware (and NI MAX simulated devices)."""

    def __init__(self, **_: Any) -> None:
        """
        Create the backend. Keyword arguments are ignored (common factory signature).

        Returns:
            None.
        """
        # Nothing to prepare; the package is imported when first needed.
        self._nidaqmx = None

    def _module(self):
        """
        Return the imported nidaqmx module (import on first use).

        Returns:
            module: nidaqmx.

        Raises:
            DaqBackendError: If the package is missing.
        """
        # Import only once.
        if self._nidaqmx is None:
            # Import and remember the module.
            self._nidaqmx = _import_nidaqmx()
        # Return the module.
        return self._nidaqmx

    def check_driver(self) -> DriverInfo:
        """
        Check that the nidaqmx package and the NI-DAQmx driver are installed.

        Returns:
            DriverInfo: Availability, version and message. Never raises.
        """
        # Try to import the package and read the driver version.
        try:
            # Import the package.
            nidaqmx = self._module()
            # Open the local system object.
            system = nidaqmx.system.System.local()
            # Read the driver version (named tuple major/minor/update).
            ver = system.driver_version
            # Build the version text.
            version = f"{ver.major_version}.{ver.minor_version}.{ver.update_version}"
        # Package missing.
        except DaqBackendError as exc:
            # Report "not available".
            return DriverInfo(available=False, message=str(exc))
        # Driver missing or not loadable (DaqNotFoundError, OSError, DaqError, ...).
        except Exception as exc:  # noqa: BLE001 (any driver failure means "not available")
            # Report "not available" with the driver message.
            return DriverInfo(available=False, message=f"NI-DAQmx driver not available: {exc}")
        # Report "available".
        return DriverInfo(available=True, version=version, message=f"NI-DAQmx {version}")

    def list_devices(self) -> List[DeviceInfo]:
        """
        Scan all NI devices and return the ones with AO channels.

        Returns:
            List[DeviceInfo]: Devices with AO channels.

        Raises:
            DaqBackendError: If the scan fails.
        """
        # Result list.
        devices: List[DeviceInfo] = []
        # Wrap all driver calls so any driver error becomes a DaqBackendError.
        try:
            # Open the local system object.
            system = self._module().system.System.local()
            # Loop over every device known to NI-DAQmx.
            for device in system.devices:
                # Read the AO channel names (empty for devices without AO).
                channels = [chan.name for chan in device.ao_physical_chans]
                # Skip devices without AO channels.
                if not channels:
                    # Not useful for this tool.
                    continue
                # Build the device description.
                devices.append(DeviceInfo(
                    # Device name.
                    name=device.name,
                    # Product type text.
                    product_type=str(device.product_type),
                    # Serial number text.
                    serial=str(device.serial_num),
                    # AO channel names.
                    ao_channels=channels,
                    # Supported AO voltage ranges as pairs.
                    ao_ranges=pair_ranges(list(device.ao_voltage_rngs)),
                    # NI MAX simulated device flag.
                    simulated=bool(device.is_simulated)))
        # Already converted errors pass through.
        except DaqBackendError:
            # Re-raise unchanged.
            raise
        # Any driver error.
        except Exception as exc:  # noqa: BLE001
            # Convert to the common error type.
            raise DaqBackendError(f"Device scan failed: {exc}") from exc
        # Return the devices.
        return devices

    def open_ao_channel(self, physical_channel: str, ao_min: float, ao_max: float) -> Any:
        """
        Create an on-demand AO voltage task.

        Inputs:
            physical_channel (str): e.g. "cDAQ1Mod1/ao0".
            ao_min (float): Task minimum (used span minimum).
            ao_max (float): Task maximum (used span maximum).

        Returns:
            nidaqmx.Task: The task (used as handle).

        Raises:
            DaqBackendError: If the task cannot be created.
        """
        # Import the package.
        nidaqmx = self._module()
        # No task yet (needed for clean-up on failure).
        task = None
        # Wrap the driver calls.
        try:
            # Create a new task.
            task = nidaqmx.Task()
            # Add the AO voltage channel with the used span as limits.
            task.ao_channels.add_ao_voltage_chan(physical_channel, min_val=ao_min, max_val=ao_max)
        # Any driver error.
        except Exception as exc:  # noqa: BLE001
            # Release a half-created task.
            if task is not None:
                # Close it.
                self.close_ao_channel(task)
            # Convert to the common error type.
            raise DaqBackendError(f"Cannot create AO task for {physical_channel}: {exc}") from exc
        # Log the creation.
        _log.debug("AO task created: %s (%g..%g V)", physical_channel, ao_min, ao_max)
        # Return the task as handle.
        return task

    def write_ao(self, handle: Any, volts: float) -> None:
        """
        Write one value (software-timed, task started automatically).

        Inputs:
            handle (nidaqmx.Task): Task from open_ao_channel().
            volts (float): AO voltage.

        Raises:
            DaqBackendError: If the write fails.
        """
        # Wrap the driver call.
        try:
            # Single on-demand write; auto_start starts the task if needed.
            handle.write(float(volts), auto_start=True)
        # Any driver error.
        except Exception as exc:  # noqa: BLE001
            # Convert to the common error type.
            raise DaqBackendError(f"AO write failed: {exc}") from exc

    def close_ao_channel(self, handle: Any) -> None:
        """
        Stop and clear a task. Never raises.

        Inputs:
            handle (nidaqmx.Task): Task to close.
        """
        # Stop the task; ignore errors (task may not be running).
        try:
            # Stop.
            handle.stop()
        # Ignore stop errors.
        except Exception:  # noqa: BLE001
            # Nothing else to do.
            pass
        # Close (clear) the task; ignore errors.
        try:
            # Close.
            handle.close()
        # Ignore close errors.
        except Exception as exc:  # noqa: BLE001
            # Log for diagnostics only.
            _log.warning("Closing AO task failed: %s", exc)
