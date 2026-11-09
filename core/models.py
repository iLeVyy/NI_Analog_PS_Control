"""
core/models.py
==============
Typed data classes that mirror config/settings.json (Project_Plan.md section 9).

Every group of the JSON file has one class with:
    * from_dict(data) -> build the object from a JSON dictionary
    * to_dict()       -> convert the object back to a JSON dictionary

Default VALUES are not defined here - they come from the data file
config/defaults/settings.default.json (config-driven rule). The config
manager merges the user file over that template before calling from_dict().

LabVIEW analogy:
    Each class is a typedef cluster; from_dict / to_dict are the
    "unflatten from JSON" / "flatten to JSON" steps.
"""

# Import dataclass tools to define the containers and convert them to dicts.
from dataclasses import dataclass, asdict, fields
# Import typing helpers for clear signatures.
from typing import Any, Dict, Optional, Type, TypeVar

# Generic type variable so _build() returns the exact class it was given.
T = TypeVar("T")


def _build(cls: Type[T], data: Dict[str, Any]) -> T:
    """
    Create a data class instance from a dict, ignoring unknown keys.

    Inputs:
        cls (Type[T]): Data class to create.
        data (Dict[str, Any]): Source dictionary (one JSON group).

    Returns:
        T: New instance of cls.

    Raises:
        TypeError: If a required field is missing in data.
    """
    # Collect the field names the class actually has.
    names = {f.name for f in fields(cls)}
    # Keep only keys that belong to the class (unknown keys are ignored, not fatal).
    filtered = {key: value for key, value in data.items() if key in names}
    # Create and return the instance.
    return cls(**filtered)


@dataclass
class ChannelConfig:
    """
    Configuration of one control channel (voltage or current).

    Attributes:
        device: NI device name, e.g. "cDAQ1Mod1" ("" = not assigned).
        physical_channel: AO channel, e.g. "cDAQ1Mod1/ao0".
        device_ao_min / device_ao_max: Device AO range read from DAQmx.
        ao_span_preset: Id of the used span preset (config/presets/ao_spans.json).
        ao_min / ao_max: Used AO span in volts.
        ps_min / ps_max: Power supply range in engineering units.
        unit: "V" or "A".
        slope / offset: Stored for traceability; recalculated on every load.
    """

    # NI device name.
    device: str
    # NI physical AO channel.
    physical_channel: str
    # Device AO range minimum (from DAQmx).
    device_ao_min: float
    # Device AO range maximum (from DAQmx).
    device_ao_max: float
    # Selected span preset id.
    ao_span_preset: str
    # Used AO span minimum.
    ao_min: float
    # Used AO span maximum.
    ao_max: float
    # Power supply range minimum.
    ps_min: float
    # Power supply range maximum.
    ps_max: float
    # Engineering unit.
    unit: str
    # Calculated slope (informational).
    slope: float
    # Calculated offset (informational).
    offset: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ChannelConfig":
        """Build a ChannelConfig from one JSON channel group."""
        # Delegate to the generic builder.
        return _build(cls, data)

    @property
    def is_assigned(self) -> bool:
        """Return True when a physical AO channel is assigned."""
        # An empty string means "not assigned yet".
        return bool(self.physical_channel)


@dataclass
class SafetyConfig:
    """
    Safety settings (Project_Plan.md sections 6.2, 13; decision D-07).

    Attributes:
        safe_value_startup / safe_value_exit: Safe value in PS units (V / A),
            written at start-up, Output OFF, errors and exit. It is converted
            with the channel scale and clamped to the AO span.
        slew_limit_enabled: Enables the front-panel slew-rate limit.
        slew_rate_voltage: Voltage slew rate in V/s (default 1 V/s).
        slew_rate_current: Current slew rate in A/s (default 5 A/s).
        slew_interval_ms: Time between two ramp steps in ms (default 50).
    """

    # Safe value at startup.
    safe_value_startup: float
    # Safe value at exit.
    safe_value_exit: float
    # Slew-rate limit on/off.
    slew_limit_enabled: bool
    # Voltage slew rate in V/s.
    slew_rate_voltage: float
    # Current slew rate in A/s.
    slew_rate_current: float
    # Time between two ramp steps in milliseconds.
    slew_interval_ms: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SafetyConfig":
        """Build a SafetyConfig from the JSON "safety" group."""
        # Delegate to the generic builder.
        return _build(cls, data)


@dataclass
class SequenceSettings:
    """
    Global sequence settings (decision D-01).

    Attributes:
        min_step_duration_ms: Minimum step duration in ms (default 50).
        timing_mode: "software" in v1.0 ("hardware" reserved for FE-01).
        ramp_interval_ms: Time between two values of a Ramp step (default 50).
        spin_wait_ms: Last part of each wait done by active waiting for
            better timing accuracy (default 2 ms).
        new_step_duration: Duration of a newly added step (default 1.0).
        status_poll_ms: Refresh interval of the run monitor (default 100).
    """

    # Minimum step duration in milliseconds.
    min_step_duration_ms: int
    # Timing mode name.
    timing_mode: str
    # Ramp value interval in milliseconds.
    ramp_interval_ms: int
    # Active-wait part of each wait in milliseconds.
    spin_wait_ms: float
    # Duration of a new step (in the default unit).
    new_step_duration: float
    # Run monitor refresh interval in milliseconds.
    status_poll_ms: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SequenceSettings":
        """Build SequenceSettings from the JSON "sequence" group."""
        # Delegate to the generic builder.
        return _build(cls, data)


@dataclass
class DaqSettings:
    """
    DAQ service settings (Phase 2).

    Attributes:
        backend: Backend id from config/presets/daq_backends.json, or "auto"
                 (try the catalog's auto_order and use the first that works).
        min_driver_version: Minimum NI-DAQmx driver version, e.g. "23.0"
                 ("" = no check).
        write_timeout_s: Seconds the caller waits for one DAQ command.
    """

    # Backend id or "auto".
    backend: str
    # Minimum driver version ("" = no check).
    min_driver_version: str
    # Timeout for one DAQ command in seconds.
    write_timeout_s: float

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DaqSettings":
        """Build DaqSettings from the JSON "daq" group."""
        # Delegate to the generic builder.
        return _build(cls, data)


@dataclass
class LoggingSettings:
    """
    Logging settings.

    Attributes:
        retention_days: Days to keep log files.
        level: Log level name ("DEBUG", "INFO", ...).
    """

    # Days to keep old log files.
    retention_days: int
    # Log level name.
    level: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoggingSettings":
        """Build LoggingSettings from the JSON "logging" group."""
        # Delegate to the generic builder.
        return _build(cls, data)


@dataclass
class UiSettings:
    """
    User interface settings.

    Attributes:
        theme: Theme id (folder name under resources/themes).
        voltage_step / current_step: Selected front-panel step sizes.
        last_manual_voltage / last_manual_current: Saved manual setpoints or None.
        window_geometry: Saved window position/size (None = centered default).
        last_sequence_file: Last opened sequence file (relative path).
    """

    # Theme id.
    theme: str
    # Voltage step size.
    voltage_step: float
    # Current step size.
    current_step: float
    # Last manually chosen setpoints, or None before first exit.
    last_manual_voltage: Optional[float]
    last_manual_current: Optional[float]
    # Saved window geometry or None.
    window_geometry: Optional[Any]
    # Last sequence file.
    last_sequence_file: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "UiSettings":
        """Build UiSettings from the JSON "ui" group."""
        # Delegate to the generic builder.
        return _build(cls, data)


@dataclass
class MetaInfo:
    """
    File meta information.

    Attributes:
        config_version: Structure version of settings.json (for migration).
        app_version: Application version that saved the file.
        last_saved: ISO timestamp of the last save.
    """

    # Structure version of the file.
    config_version: int
    # Application version at save time.
    app_version: str
    # Timestamp of the last save.
    last_saved: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MetaInfo":
        """Build MetaInfo from the JSON "meta" group."""
        # Delegate to the generic builder.
        return _build(cls, data)


@dataclass
class AppConfig:
    """
    Complete application configuration (one object = one settings.json).

    Attributes:
        meta, voltage_channel, current_channel, safety, sequence, daq, logging, ui.
    """

    # File meta information.
    meta: MetaInfo
    # Voltage control channel.
    voltage_channel: ChannelConfig
    # Current control channel.
    current_channel: ChannelConfig
    # Safety settings.
    safety: SafetyConfig
    # Sequence settings.
    sequence: SequenceSettings
    # DAQ service settings.
    daq: DaqSettings
    # Logging settings.
    logging: LoggingSettings
    # UI settings.
    ui: UiSettings

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppConfig":
        """
        Build the full configuration from the complete JSON dictionary.

        Inputs:
            data (Dict[str, Any]): Whole settings.json content (already merged with defaults).

        Returns:
            AppConfig: New configuration object.
        """
        # Build every group with its own class and combine them.
        return cls(
            # Meta group.
            meta=MetaInfo.from_dict(data["meta"]),
            # Voltage channel group.
            voltage_channel=ChannelConfig.from_dict(data["voltage_channel"]),
            # Current channel group.
            current_channel=ChannelConfig.from_dict(data["current_channel"]),
            # Safety group.
            safety=SafetyConfig.from_dict(data["safety"]),
            # Sequence group.
            sequence=SequenceSettings.from_dict(data["sequence"]),
            # DAQ group.
            daq=DaqSettings.from_dict(data["daq"]),
            # Logging group.
            logging=LoggingSettings.from_dict(data["logging"]),
            # UI group.
            ui=UiSettings.from_dict(data["ui"]),
        )

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert the configuration to a JSON-ready dictionary.

        Inputs:
            None.

        Returns:
            Dict[str, Any]: Nested dictionary with the same structure as settings.json.
        """
        # asdict() converts nested data classes recursively.
        return asdict(self)
