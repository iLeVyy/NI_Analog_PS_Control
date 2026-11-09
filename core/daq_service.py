"""
core/daq_service.py
===================
The DAQ service: the ONLY module that controls the AO hardware
(Project_Plan.md sections 4, 12, 13).

Responsibilities:
    * driver check result, device scan
    * configure: validate settings against the scanned hardware and open
      one on-demand AO task per channel (task min/max = used AO span, D-05)
    * setpoints in power supply units (V / A); conversion to AO volts only
      through core/scaling (D-02)
    * Output ON/OFF; safe state on start-up, Output OFF, errors and exit
    * events to listeners (UI in Phase 3+)

Threading (section 4.4):
    All hardware calls run in ONE worker thread that processes a command
    queue. Public methods are thread-safe: they put a command into the queue
    and return a concurrent.futures.Future. Listeners are called from the
    worker thread - the UI (Phase 3) must forward them to the Qt main thread
    (e.g. with a queued Qt signal).

LabVIEW analogy:
    A DQMH module: public methods = requests, the worker thread = the
    helper loop / message handling loop, listeners = broadcasts.
"""

# Import queue for the thread-safe command queue.
import queue
# Import threading for the worker thread and the state lock.
import threading
# Import Future so callers can wait for a command result.
from concurrent.futures import Future
# Import dataclass tools for the event and state containers.
from dataclasses import dataclass, field, replace
# Import typing helpers.
from typing import Any, Callable, Dict, List, Optional

# Import the backend interface and data types.
from core.daq_backend import (DaqBackend, DaqBackendError, DeviceInfo, DriverInfo,
                              check_hardware)
# Import config validation and the channel scale builder.
from core.config_manager import channel_scale, validate_config
# Import the module logger factory.
from core.logger import get_logger
from core.activity_trace import activity_trace
# Import the configuration data classes.
from core.models import AppConfig, ChannelConfig
# Import the scale type.
from core.scaling import LinearScale

# Logger for this module.
_log = get_logger("daq_service")

# Quantity name of the voltage control channel.
VOLTAGE = "voltage"
# Quantity name of the current control channel.
CURRENT = "current"
# Both quantities in a fixed order.
QUANTITIES = (VOLTAGE, CURRENT)

# Event: device scan finished; data = {"devices": [device dicts]}.
EVT_SCAN = "scan"
# Event: configure finished; data = {"ok": bool, "errors": [...], "warnings": [...]}.
EVT_CONFIGURED = "configured"
# Event: output state changed; data = {"on": bool, "reason": str}.
EVT_OUTPUT = "output"
# Event: a value was written; data = {"quantity", "ps", "ao"}.
EVT_WRITTEN = "written"
# Event: safe state applied; data = {"reason": str}.
EVT_SAFE_STATE = "safe_state"
# Event: an error occurred; data = {"message": str}.
EVT_ERROR = "error"
# Event: service closed; data = {}.
EVT_CLOSED = "closed"

# Safe-state reason: start-up / configure.
REASON_STARTUP = "startup"
# Safe-state reason: user switched Output OFF.
REASON_OUTPUT_OFF = "output_off"
# Safe-state reason: error.
REASON_ERROR = "error"
# Safe-state reason: application exit.
REASON_EXIT = "exit"


class DaqServiceError(Exception):
    """Raised (through the Future) when a command is refused, e.g. Output ON while not configured."""


@dataclass
class DaqEvent:
    """
    One event sent to listeners.

    Attributes:
        kind (str): One of the EVT_* constants.
        data (Dict[str, Any]): Event details.
    """

    # Event kind.
    kind: str
    # Event details.
    data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DaqState:
    """
    Snapshot of the service state (returned as a copy, safe to read in any thread).

    Attributes:
        backend_id (str): Active backend id.
        simulated (bool): True for the simulation backend.
        driver (DriverInfo): Driver check result.
        devices (List[DeviceInfo]): Result of the last scan.
        configured (bool): True when both AO tasks are open and valid.
        output_on (bool): Output state.
        setpoints (Dict[str, float]): Setpoints in PS units per quantity.
        ao_values (Dict[str, float]): Last AO volts written per quantity.
        channels (Dict[str, str]): Physical channel per quantity.
        last_error (str): Last error message ("" = none).
    """

    # Active backend id.
    backend_id: str = ""
    # Simulation flag.
    simulated: bool = False
    # Driver check result.
    driver: DriverInfo = field(default_factory=lambda: DriverInfo(available=False))
    # Last scan result.
    devices: List[DeviceInfo] = field(default_factory=list)
    # Configured flag.
    configured: bool = False
    # Output flag.
    output_on: bool = False
    # Setpoints in PS units.
    setpoints: Dict[str, float] = field(default_factory=lambda: {VOLTAGE: 0.0, CURRENT: 0.0})
    # Last written AO volts.
    ao_values: Dict[str, float] = field(default_factory=dict)
    # Physical channel names.
    channels: Dict[str, str] = field(default_factory=dict)
    # Last error message.
    last_error: str = ""


@dataclass
class _Command:
    """
    One queued command (internal).

    Attributes:
        func (Callable): Worker-side function to run.
        args (tuple): Positional arguments.
        future (Future): Receives the result or the exception.
    """

    # Function to run.
    func: Callable[..., Any]
    # Arguments.
    args: tuple
    # Result holder.
    future: Future


class DaqService:
    """
    Thread-safe AO control service.

    Usage:
        service = DaqService(backend, driver_info)
        service.add_listener(callback)
        service.start()
        service.scan().result(timeout)
        service.configure(config).result(timeout)
        service.set_setpoint("voltage", 10.0)
        service.set_output(True)
        ...
        service.close()
    """

    def __init__(self, backend: DaqBackend, driver: DriverInfo) -> None:
        """
        Create the service (the worker thread is started with start()).

        Inputs:
            backend (DaqBackend): Backend from core.daq_backend.create_backend().
            driver (DriverInfo): Driver check result of that backend.

        Returns:
            None.
        """
        # Store the backend (used only in the worker thread).
        self._backend = backend
        # Command queue between callers and the worker thread.
        self._queue: "queue.Queue[Optional[_Command]]" = queue.Queue()
        # Worker thread (created in start()).
        self._thread: Optional[threading.Thread] = None
        # Lock protecting the state snapshot.
        self._lock = threading.Lock()
        # Registered listener callbacks.
        self._listeners: List[Callable[[DaqEvent], None]] = []
        # Initial state.
        self._state = DaqState(backend_id=backend.backend_id, simulated=backend.simulated, driver=driver)
        # Active configuration (set by configure).
        self._config: Optional[AppConfig] = None
        # Open AO handles per quantity.
        self._handles: Dict[str, Any] = {}
        # Scales per quantity.
        self._scales: Dict[str, LinearScale] = {}
        # True after close().
        self._closed = False

    # ------------------------------------------------------------------ public API

    def add_listener(self, callback: Callable[[DaqEvent], None]) -> None:
        """
        Register a callback that receives every DaqEvent (called in the worker thread).

        Inputs:
            callback (Callable[[DaqEvent], None]): Listener function.
        """
        # Add the callback under the lock.
        with self._lock:
            # Store the listener.
            self._listeners.append(callback)

    @property
    def state(self) -> DaqState:
        """Return a copy of the current state (thread-safe)."""
        # Copy under the lock so the caller never sees a half-updated state.
        with self._lock:
            # Shallow copy plus copies of the mutable members.
            return replace(self._state, devices=list(self._state.devices),
                           setpoints=dict(self._state.setpoints),
                           ao_values=dict(self._state.ao_values),
                           channels=dict(self._state.channels))

    def start(self) -> None:
        """Start the worker thread (once)."""
        # Ignore repeated calls.
        if self._thread is not None:
            # Already running.
            return
        # Create the daemon worker thread.
        self._thread = threading.Thread(target=self._run, name="DaqWorker", daemon=True)
        # Start it.
        self._thread.start()
        # Log the start.
        _log.info("DAQ service started (backend: %s)", self._backend.backend_id)

    def scan(self) -> Future:
        """Queue a device scan. Future result: List[DeviceInfo]."""
        # Queue the worker function.
        return self._submit(self._do_scan)

    def configure(self, config: AppConfig) -> Future:
        """
        Queue a (re)configuration with the given settings.

        Output is switched OFF and both channels go to the safe value.
        Future result: Tuple[bool, List[str], List[str]] = (ok, errors, warnings).
        """
        # Queue the worker function.
        return self._submit(self._do_configure, config)

    def set_setpoint(self, quantity: str, ps_value: float) -> Future:
        """
        Queue a new setpoint in PS units; written immediately when Output is ON.

        Future result: float = the clamped PS setpoint that is now active.
        """
        # Queue the worker function.
        return self._submit(self._do_set_setpoint, quantity, float(ps_value))

    def set_output(self, on: bool) -> Future:
        """Queue Output ON (write setpoints) or OFF (safe state). Future result: bool."""
        # Queue the worker function.
        return self._submit(self._do_set_output, bool(on))

    def apply_safe_state(self, reason: str = REASON_ERROR) -> Future:
        """Queue the safe state (Output OFF + safe value). Future result: None."""
        # Queue the worker function.
        return self._submit(self._do_safe_state, reason)

    def close(self, timeout: float = 5.0) -> None:
        """
        Apply the exit safe value, close all tasks and stop the worker thread.

        Inputs:
            timeout (float): Seconds to wait for the worker thread.
        """
        # Closing twice does nothing.
        if self._closed:
            # Already closed.
            return
        # Without a running thread, run the close directly.
        if self._thread is None:
            # Direct call in the caller's thread.
            self._do_close()
        # With a running thread, queue the close and the stop marker.
        else:
            # Queue the close command.
            future = self._submit(self._do_close)
            # Queue the stop marker (None) so the loop ends.
            self._queue.put(None)
            # Wait for the close command (errors are already logged in the worker).
            try:
                # Wait for completion.
                future.result(timeout)
            # Ignore errors here; the service is going down anyway.
            except Exception as exc:  # noqa: BLE001
                # Log for diagnostics.
                _log.warning("Close finished with error: %s", exc)
            # Wait for the thread to end.
            self._thread.join(timeout)
        # Mark as closed.
        self._closed = True
        # Log the stop.
        _log.info("DAQ service closed")

    # ------------------------------------------------------------------ worker infrastructure

    def _submit(self, func: Callable[..., Any], *args: Any) -> Future:
        """
        Put a command into the queue (or run it directly if no thread runs).

        Inputs:
            func (Callable): Worker-side function.
            *args: Its arguments.

        Returns:
            Future: Receives the result or the exception.
        """
        # Create the result holder.
        future: Future = Future()
        # Refuse commands after close().
        if self._closed:
            # Report the closed service through the future.
            future.set_exception(DaqServiceError("DAQ service is closed"))
            # Return immediately.
            return future
        # Without a worker thread (e.g. simple scripts), run synchronously.
        if self._thread is None:
            # Execute and fill the future.
            self._execute(_Command(func, args, future))
        # With a worker thread, queue the command.
        else:
            # Put it into the queue.
            self._queue.put(_Command(func, args, future))
        # Return the future to the caller.
        return future

    def _execute(self, command: _Command) -> None:
        """
        Run one command and store its result or exception in its future.

        Inputs:
            command (_Command): Command to run.
        """
        # Run the function.
        try:
            # Store the return value.
            command.future.set_result(command.func(*command.args))
        # Any exception goes to the caller through the future.
        except Exception as exc:  # noqa: BLE001
            # Store the exception.
            command.future.set_exception(exc)

    def _run(self) -> None:
        """Worker thread loop: process commands until the stop marker (None)."""
        # Loop forever until the stop marker arrives.
        while True:
            # Wait for the next command.
            command = self._queue.get()
            # None = stop marker.
            if command is None:
                # Leave the loop.
                break
            # Run the command.
            self._execute(command)

    def _emit(self, kind: str, **data: Any) -> None:
        """
        Send an event to every listener; listener errors are logged, never raised.

        Inputs:
            kind (str): EVT_* constant.
            **data: Event details.
        """
        # Record DAQ activity separately from the persistent file log.
        if kind == EVT_SCAN:
            activity_trace.add(f"DAQ scan: {len(data['devices'])} device(s)")
        elif kind == EVT_CONFIGURED:
            activity_trace.add(f"DAQ configure: {'ready' if data['ok'] else 'failed'}; "
                               f"errors={data['errors']}; warnings={data['warnings']}")
        elif kind == EVT_WRITTEN:
            activity_trace.add(f"DAQ write: {data['quantity']} {data['ps']:g} PS units, {data['ao']:g} V AO")
        elif kind == EVT_OUTPUT:
            activity_trace.add(f"Output {'ON' if data['on'] else 'OFF'} ({data['reason']})")
        elif kind == EVT_SAFE_STATE:
            activity_trace.add(f"Safe state applied ({data['reason']})")
        elif kind == EVT_ERROR:
            activity_trace.add(f"DAQ error: {data['message']}")
        elif kind == EVT_CLOSED:
            activity_trace.add("DAQ service closed")
        # Build the event.
        event = DaqEvent(kind, data)
        # Copy the listener list under the lock.
        with self._lock:
            # Snapshot of listeners.
            listeners = list(self._listeners)
        # Call every listener.
        for listener in listeners:
            # Protect the worker from listener errors.
            try:
                # Deliver the event.
                listener(event)
            # A listener failed.
            except Exception as exc:  # noqa: BLE001
                # Log and continue with the next listener.
                _log.exception("Listener failed on '%s': %s", kind, exc)

    def _update(self, **changes: Any) -> None:
        """
        Change state fields under the lock.

        Inputs:
            **changes: Field name / new value pairs.
        """
        # Lock the state.
        with self._lock:
            # Apply every change.
            for name, value in changes.items():
                # Set the attribute.
                setattr(self._state, name, value)

    # ------------------------------------------------------------------ worker-side commands

    def _channel_cfg(self, quantity: str) -> ChannelConfig:
        """
        Return the channel configuration of a quantity.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            ChannelConfig: The channel.

        Raises:
            DaqServiceError: For an unknown quantity or missing configuration.
        """
        # A configuration must be active.
        if self._config is None:
            # Report it.
            raise DaqServiceError("DAQ service is not configured")
        # Voltage channel.
        if quantity == VOLTAGE:
            # Return it.
            return self._config.voltage_channel
        # Current channel.
        if quantity == CURRENT:
            # Return it.
            return self._config.current_channel
        # Anything else is a programming error.
        raise DaqServiceError(f"Unknown quantity '{quantity}'")

    def _do_scan(self) -> List[DeviceInfo]:
        """Worker: scan devices, store and emit them. Returns the device list."""
        # Ask the backend.
        try:
            # Scan.
            devices = self._backend.list_devices()
        # Scan failed.
        except DaqBackendError as exc:
            # Store and emit the error.
            self._report_error(str(exc))
            # Pass the error to the caller.
            raise
        # Store the result.
        self._update(devices=devices)
        # Log the result.
        _log.info("Scan: %d AO device(s): %s", len(devices), ", ".join(d.name for d in devices))
        # Emit the event.
        self._emit(EVT_SCAN, devices=[d.to_dict() for d in devices])
        # Return the devices.
        return devices

    def _close_handles(self) -> None:
        """Worker: close all open AO tasks."""
        # Close each handle.
        for handle in self._handles.values():
            # The backend method never raises.
            self._backend.close_ao_channel(handle)
        # Forget the handles.
        self._handles.clear()

    def _do_configure(self, config: AppConfig):
        """
        Worker: validate the settings, open both AO tasks, apply the safe value.

        Inputs:
            config (AppConfig): Settings to use.

        Returns:
            Tuple[bool, List[str], List[str]]: (ok, errors, warnings).
        """
        # Close tasks of a previous configuration.
        self._close_handles()
        # Not configured until everything succeeded; Output always OFF here.
        self._update(configured=False, output_on=False, channels={}, ao_values={})
        # Store the new configuration.
        self._config = config
        # Validate the settings themselves.
        errors, warnings = validate_config(config)
        # Unassigned channels block configuration (they are only warnings at start-up).
        for quantity, channel in ((VOLTAGE, config.voltage_channel), (CURRENT, config.current_channel)):
            # Missing assignment.
            if not channel.is_assigned:
                # Turn it into a blocking error.
                errors.append(f"{quantity.capitalize()} channel: no AO channel assigned.")
        # Only check hardware when the settings themselves are fine.
        if not errors:
            # Scan to know the present devices.
            try:
                # Scan (also emits EVT_SCAN).
                devices = self._do_scan()
            # Scan failed.
            except DaqBackendError as exc:
                # Use the scan error as configure error.
                errors.append(str(exc))
                # No devices.
                devices = []
            # Compare settings and hardware.
            if not errors:
                # Add hardware mismatches.
                errors.extend(check_hardware(config.voltage_channel, config.current_channel, devices))
        # Open the tasks when everything is valid.
        if not errors:
            # Try to open both channels.
            try:
                # Loop over both quantities.
                for quantity in QUANTITIES:
                    # Channel settings.
                    channel = self._channel_cfg(quantity)
                    # Open the task with the used span as limits.
                    self._handles[quantity] = self._backend.open_ao_channel(
                        channel.physical_channel, channel.ao_min, channel.ao_max)
                    # Build and store the scale.
                    self._scales[quantity] = channel_scale(channel)
            # Opening failed.
            except DaqBackendError as exc:
                # Release what was opened.
                self._close_handles()
                # Report it.
                errors.append(str(exc))
        # Failure: report and stop here.
        if errors:
            # Store the first error as last error.
            self._update(last_error=errors[0])
            # Log every error.
            for message in errors:
                # Log line.
                _log.error("Configure: %s", message)
            # Emit the result.
            self._emit(EVT_CONFIGURED, ok=False, errors=errors, warnings=warnings)
            # Return the result.
            return False, errors, warnings
        # Store the channel names.
        self._update(channels={q: self._channel_cfg(q).physical_channel for q in QUANTITIES},
                     last_error="")
        # Apply the start-up safe value (Output stays OFF).
        try:
            # Write the safe state.
            self._do_safe_state(REASON_STARTUP)
        # Writing the safe value failed.
        except DaqBackendError as exc:
            # Close the tasks again.
            self._close_handles()
            # Report it.
            errors.append(f"Safe value could not be written: {exc}")
            # Emit the result.
            self._emit(EVT_CONFIGURED, ok=False, errors=errors, warnings=warnings)
            # Return the result.
            return False, errors, warnings
        # Configuration succeeded.
        self._update(configured=True)
        # Log the success.
        _log.info("Configured: V=%s, I=%s", self._channel_cfg(VOLTAGE).physical_channel,
                  self._channel_cfg(CURRENT).physical_channel)
        # Emit the result.
        self._emit(EVT_CONFIGURED, ok=True, errors=[], warnings=warnings)
        # Return the result.
        return True, [], warnings

    def _write(self, quantity: str, ps_value: float) -> float:
        """
        Worker: convert a PS value with the channel scale and write it.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            ps_value (float): Value in PS units.

        Returns:
            float: AO volts written.

        Raises:
            DaqBackendError: If the write fails.
        """
        # The single conversion point PS -> AO (clamped to the span).
        ao_value = self._scales[quantity].to_ao(ps_value)
        # Write to the hardware.
        self._backend.write_ao(self._handles[quantity], ao_value)
        # Update the AO value in the state.
        with self._lock:
            # Store the written AO volts.
            self._state.ao_values[quantity] = ao_value
        # Emit the write event.
        self._emit(EVT_WRITTEN, quantity=quantity, ps=ps_value, ao=ao_value)
        # Return the AO value.
        return ao_value

    def _write_safe(self, safe_ps: float) -> None:
        """
        Worker: write the safe PS value to every open channel (best effort).

        Inputs:
            safe_ps (float): Safe value in PS units.

        Raises:
            DaqBackendError: The first write error after all channels were tried.
        """
        # First error seen (all channels are tried anyway).
        first_error: Optional[DaqBackendError] = None
        # Try every open channel.
        for quantity in list(self._handles):
            # Write and remember a failure.
            try:
                # Write the safe value.
                self._write(quantity, safe_ps)
            # Write failed.
            except DaqBackendError as exc:
                # Keep only the first error.
                first_error = first_error or exc
        # Raise the first error if there was one.
        if first_error is not None:
            # Re-raise.
            raise first_error

    def _do_safe_state(self, reason: str) -> None:
        """
        Worker: Output OFF and safe value on both channels.

        Inputs:
            reason (str): REASON_* constant (exit uses safe_value_exit).

        Raises:
            DaqBackendError: If writing the safe value failed.
        """
        # Choose the safe value: exit value at exit, start-up value otherwise.
        if self._config is None:
            # Without configuration there is nothing to write.
            safe_ps = 0.0
        # Exit value.
        elif reason == REASON_EXIT:
            # Value for application exit.
            safe_ps = self._config.safety.safe_value_exit
        # Start-up value for all other reasons.
        else:
            # Value for start-up / Output OFF / error.
            safe_ps = self._config.safety.safe_value_startup
        # Remember whether the output was on (to emit a change).
        was_on = self.state.output_on
        # Output is OFF from now on, even if the write fails.
        self._update(output_on=False)
        # Emit the output change when it really changed.
        if was_on:
            # Output switched OFF.
            self._emit(EVT_OUTPUT, on=False, reason=reason)
        # Write the safe value to all open channels.
        self._write_safe(safe_ps)
        # Log the safe state.
        _log.info("Safe state applied (%s): %g", reason, safe_ps)
        # Emit the safe-state event.
        self._emit(EVT_SAFE_STATE, reason=reason)

    def _report_error(self, message: str) -> None:
        """
        Worker: store, log and emit an error.

        Inputs:
            message (str): Error text.
        """
        # Store as last error.
        self._update(last_error=message)
        # Log it.
        _log.error(message)
        # Emit it.
        self._emit(EVT_ERROR, message=message)

    def _fail(self, exc: Exception) -> None:
        """
        Worker: react to a hardware error (Project_Plan.md section 13):
        report it, apply the safe state (best effort), Output OFF.

        Inputs:
            exc (Exception): The original error.
        """
        # Report the original error.
        self._report_error(str(exc))
        # Try the safe state.
        try:
            # Safe state with reason "error".
            self._do_safe_state(REASON_ERROR)
        # The safe state also failed (e.g. device removed).
        except DaqBackendError as safe_exc:
            # Log it; Output is already OFF in the state.
            _log.error("Safe state after error failed: %s", safe_exc)

    def _do_set_setpoint(self, quantity: str, ps_value: float) -> float:
        """
        Worker: store a new setpoint (clamped to the PS range) and write it if Output is ON.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            ps_value (float): Requested value in PS units.

        Returns:
            float: Active (clamped) setpoint.

        Raises:
            DaqServiceError: If not configured or the quantity is unknown.
            DaqBackendError: If the write failed (safe state was applied).
        """
        # Setpoints need a valid configuration.
        if not self.state.configured:
            # Refuse.
            raise DaqServiceError("Not configured - open Settings and assign the AO channels")
        # Clamp to the PS range of the channel.
        clamped = channel_scale(self._channel_cfg(quantity)).clamp_ps(ps_value)
        # Store the setpoint.
        with self._lock:
            # Update the setpoint.
            self._state.setpoints[quantity] = clamped
        # Write only when the output is on.
        if self.state.output_on:
            # Write with error handling.
            try:
                # Write the value.
                self._write(quantity, clamped)
            # Write failed.
            except DaqBackendError as exc:
                # Safe state and report.
                self._fail(exc)
                # Pass the error to the caller.
                raise
        # Return the active setpoint.
        return clamped

    def _do_set_output(self, on: bool) -> bool:
        """
        Worker: switch the output ON (write both setpoints) or OFF (safe state).

        Inputs:
            on (bool): Requested output state.

        Returns:
            bool: New output state.

        Raises:
            DaqServiceError: Output ON while not configured.
            DaqBackendError: If a write failed (safe state was applied).
        """
        # OFF: apply the safe state.
        if not on:
            # Safe state with reason "output_off".
            try:
                # Apply.
                self._do_safe_state(REASON_OUTPUT_OFF)
            # Safe value could not be written.
            except DaqBackendError as exc:
                # Report the error (output is already OFF).
                self._report_error(str(exc))
                # Pass it to the caller.
                raise
            # Output is OFF.
            return False
        # ON requires a valid configuration.
        if not self.state.configured:
            # Refuse.
            raise DaqServiceError("Output cannot be switched ON - not configured")
        # Write both setpoints (jump; slew limit is handled by the UI in Phase 4).
        try:
            # Loop over both quantities.
            for quantity in QUANTITIES:
                # Write the stored setpoint.
                self._write(quantity, self.state.setpoints[quantity])
        # A write failed.
        except DaqBackendError as exc:
            # Safe state and report.
            self._fail(exc)
            # Pass the error to the caller.
            raise
        # Mark the output as ON.
        self._update(output_on=True)
        # Emit the change.
        self._emit(EVT_OUTPUT, on=True, reason="user")
        # Output is ON.
        return True

    def _do_close(self) -> None:
        """Worker: exit safe value (best effort), close tasks, emit EVT_CLOSED."""
        # Write the exit safe value when tasks are open.
        if self._handles:
            # Best effort.
            try:
                # Safe state with reason "exit".
                self._do_safe_state(REASON_EXIT)
            # Write failed.
            except DaqBackendError as exc:
                # Log only; we are closing.
                _log.error("Exit safe value failed: %s", exc)
        # Close the tasks.
        self._close_handles()
        # Not configured any more.
        self._update(configured=False, output_on=False)
        # Emit the closed event.
        self._emit(EVT_CLOSED)
