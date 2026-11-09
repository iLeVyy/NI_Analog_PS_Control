"""
core/settings_editor.py
=======================
UI-independent logic of the Settings window (Project_Plan.md section 7).

The Settings window (ui/settings_window.py) only shows widgets and forwards
user input to this editor. All rules live here, so they are unit-tested
without Qt:

    * working copy of the configuration (original stays untouched until Save)
    * channel list built from the device scan (configured but missing
      channels stay visible, marked "not present")
    * channel assignment -> device name + device AO range taken from the scan
    * device AO range selection
    * used AO span presets from config/presets/ao_spans.json (D-05);
      "custom" enables free AO min/max
    * PS min/max (D-06), safety values and slew rates (D-07), backend id
    * scaling preview rows from config/presets/scaling_preview.json
    * validation (settings + hardware) and "unsaved changes" detection
    * restore defaults from config/defaults/settings.default.json

LabVIEW analogy:
    The "model" behind a dialog VI: the front panel only shows the cluster,
    this class holds and checks it.
"""

# Import copy to create independent working copies.
import copy
# Import dataclass to define the preview container.
from dataclasses import dataclass, field
# Import typing helpers.
from typing import Any, Dict, List, Optional, Tuple

# Import the catalog type.
from core.catalogs import Catalog
# Import scaling helpers and validation from the config manager.
from core.config_manager import channel_scale, recalculate_scaling, validate_config
# Import device data types and the hardware check.
from core.daq_backend import DeviceInfo, check_hardware, device_of_channel
# Import the configuration data classes.
from core.models import AppConfig, ChannelConfig

# Quantity name of the voltage channel.
VOLTAGE = "voltage"
# Quantity name of the current channel.
CURRENT = "current"
# Id of the free span preset (must exist in ao_spans.json with "custom": true).
CUSTOM_SPAN = "custom"
# Pseudo backend id meaning "try the auto order".
AUTO_BACKEND = "auto"


@dataclass
class ChannelOption:
    """
    One entry of a channel selector.

    Attributes:
        physical_channel (str): e.g. "cDAQ1Mod1/ao0" ("" = not assigned).
        device (str): Device name.
        label (str): Text shown in the selector.
        present (bool): False when the channel is configured but not found in the scan.
    """

    # Physical channel name.
    physical_channel: str
    # Device name.
    device: str
    # Display text.
    label: str
    # Present in the scan.
    present: bool = True


@dataclass
class ScalePreview:
    """
    Scaling preview of one channel.

    Attributes:
        valid (bool): True when slope/offset could be calculated.
        slope (float): AO volts per PS unit.
        offset (float): AO volts at PS = 0.
        rows (List[Tuple[float, float, float]]): (percent, PS value, AO value).
        errors (List[str]): Reasons when not valid.
        note (str): Info text, e.g. the offset hint when PS min is not 0.
    """

    # Scale valid.
    valid: bool
    # Slope.
    slope: float = 0.0
    # Offset.
    offset: float = 0.0
    # Table rows.
    rows: List[Tuple[float, float, float]] = field(default_factory=list)
    # Error messages.
    errors: List[str] = field(default_factory=list)
    # Info text.
    note: str = ""


def format_range(rng: Tuple[float, float]) -> str:
    """
    Format a voltage range for display.

    Inputs:
        rng (Tuple[float, float]): (min, max) in volts.

    Returns:
        str: e.g. "-10 .. 10 V".
    """
    # Compact number format without trailing zeros.
    return f"{rng[0]:g} .. {rng[1]:g} V"


class SettingsEditor:
    """
    Holds and validates the settings while the Settings window is open.

    Usage:
        editor = SettingsEditor(config, catalogs, defaults_dict)
        editor.set_devices(scan_result)
        editor.assign_channel("voltage", "cDAQ1Mod1/ao0")
        errors, warnings = editor.validate()
        if not errors: manager.save(editor.config)
    """

    def __init__(self, config: AppConfig, catalogs: Dict[str, Catalog],
                 defaults: Dict[str, Any]) -> None:
        """
        Create the editor with a working copy of the configuration.

        Inputs:
            config (AppConfig): Active configuration (not modified).
            catalogs (Dict[str, Catalog]): Loaded catalogs (ao_spans,
                scaling_preview, daq_backends are used).
            defaults (Dict[str, Any]): Content of settings.default.json.

        Returns:
            None.
        """
        # Working copy edited by the window.
        self.config: AppConfig = copy.deepcopy(config)
        # Snapshot used to detect unsaved changes.
        self._saved = copy.deepcopy(config.to_dict())
        # Catalogs.
        self._catalogs = catalogs
        # Default template.
        self._defaults = defaults
        # Last scan result.
        self.devices: List[DeviceInfo] = []
        # True after the first scan (before that, hardware checks are skipped).
        self.scanned = False

    # ------------------------------------------------------------------ helpers

    def channel(self, quantity: str) -> ChannelConfig:
        """
        Return the channel settings of a quantity.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            ChannelConfig: Channel in the working copy.

        Raises:
            ValueError: For an unknown quantity.
        """
        # Voltage channel.
        if quantity == VOLTAGE:
            # Return it.
            return self.config.voltage_channel
        # Current channel.
        if quantity == CURRENT:
            # Return it.
            return self.config.current_channel
        # Unknown quantity.
        raise ValueError(f"Unknown quantity '{quantity}'")

    def device(self, name: str) -> Optional[DeviceInfo]:
        """
        Find a scanned device by name.

        Inputs:
            name (str): Device name.

        Returns:
            Optional[DeviceInfo]: The device or None.
        """
        # Search the scan result.
        for device in self.devices:
            # Match by name.
            if device.name == name:
                # Found.
                return device
        # Not found.
        return None

    # ------------------------------------------------------------------ scan / channels

    def set_devices(self, devices: List[DeviceInfo]) -> None:
        """
        Store a scan result.

        Inputs:
            devices (List[DeviceInfo]): Devices with AO channels.

        Returns:
            None.
        """
        # Store the devices.
        self.devices = list(devices)
        # Mark that a scan happened.
        self.scanned = True

    def channel_options(self, quantity: str) -> List[ChannelOption]:
        """
        Build the entries of a channel selector.

        First entry is "not assigned". The configured channel is always
        included, marked "(not present)" when it is missing in the scan.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            List[ChannelOption]: Selector entries.
        """
        # Start with the "not assigned" entry.
        options = [ChannelOption("", "", "(not assigned)")]
        # Add every AO channel of every scanned device.
        for device in self.devices:
            # Loop over the device channels.
            for name in device.ao_channels:
                # Text: channel and product type.
                options.append(ChannelOption(name, device.name, f"{name}  [{device.product_type}]"))
        # Configured channel of this quantity.
        current = self.channel(quantity).physical_channel
        # Add it when it is configured but not in the scan.
        if current and all(option.physical_channel != current for option in options):
            # Marked entry so the user sees the missing hardware.
            options.append(ChannelOption(current, self.channel(quantity).device or device_of_channel(current),
                                         f"{current}  (not present)", present=False))
        # Return the entries.
        return options

    def assign_channel(self, quantity: str, physical_channel: str) -> None:
        """
        Assign a physical AO channel and take device name and device range from the scan.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            physical_channel (str): Channel name ("" = unassign).

        Returns:
            None.
        """
        # Channel settings to change.
        channel = self.channel(quantity)
        # Store the channel name.
        channel.physical_channel = physical_channel
        # Store the device name ("" when unassigned).
        channel.device = device_of_channel(physical_channel)
        # Find the device in the scan.
        device = self.device(channel.device)
        # Without scan data the device range stays as it is.
        if device is None or device.widest_range is None:
            # Nothing more to update.
            return
        # Keep the current device range if the device supports it.
        if (channel.device_ao_min, channel.device_ao_max) in device.ao_ranges:
            # Nothing to change.
            return
        # Otherwise use the widest supported range.
        channel.device_ao_min, channel.device_ao_max = device.widest_range

    def device_ranges(self, quantity: str) -> List[Tuple[float, float]]:
        """
        Return the AO ranges the assigned device supports.

        Falls back to the stored device range when the device was not scanned.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            List[Tuple[float, float]]: Supported ranges.
        """
        # Channel settings.
        channel = self.channel(quantity)
        # Scanned device of this channel.
        device = self.device(channel.device)
        # Use the scanned ranges when available.
        if device is not None and device.ao_ranges:
            # Ranges from the hardware.
            return list(device.ao_ranges)
        # Fall back to the stored range.
        return [(channel.device_ao_min, channel.device_ao_max)]

    def set_device_range(self, quantity: str, rng: Tuple[float, float]) -> None:
        """
        Select the device AO range.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            rng (Tuple[float, float]): (min, max) in volts.

        Returns:
            None.
        """
        # Store the range.
        self.channel(quantity).device_ao_min, self.channel(quantity).device_ao_max = rng

    # ------------------------------------------------------------------ span / ranges

    def span_presets(self) -> List[Dict[str, Any]]:
        """Return the span preset items from config/presets/ao_spans.json."""
        # Items in file order.
        return list(self._catalogs["ao_spans"].items)

    def is_custom_span(self, quantity: str) -> bool:
        """
        Check whether the channel uses the free (custom) span.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            bool: True for the custom preset.
        """
        # Find the selected preset item.
        item = self._catalogs["ao_spans"].get(self.channel(quantity).ao_span_preset)
        # Unknown preset ids are treated as custom (values stay editable).
        return item is None or bool(item.get("custom"))

    def apply_span_preset(self, quantity: str, preset_id: str) -> None:
        """
        Select a span preset; fixed presets set AO min/max, custom keeps the values.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            preset_id (str): Item id from ao_spans.json.

        Returns:
            None.

        Raises:
            ValueError: For an unknown preset id.
        """
        # Find the preset.
        item = self._catalogs["ao_spans"].get(preset_id)
        # Unknown id is a programming error.
        if item is None:
            # Report it.
            raise ValueError(f"Unknown AO span preset '{preset_id}'")
        # Channel settings.
        channel = self.channel(quantity)
        # Store the preset id.
        channel.ao_span_preset = preset_id
        # Fixed presets define the span.
        if not item.get("custom"):
            # Take min and max from the preset.
            channel.ao_min, channel.ao_max = float(item["ao_min"]), float(item["ao_max"])

    def set_custom_span(self, quantity: str, ao_min: float, ao_max: float) -> None:
        """
        Set a free AO span (switches the preset to custom).

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            ao_min (float): Span minimum.
            ao_max (float): Span maximum.

        Returns:
            None.
        """
        # Channel settings.
        channel = self.channel(quantity)
        # Free values always mean the custom preset.
        channel.ao_span_preset = CUSTOM_SPAN
        # Store the values.
        channel.ao_min, channel.ao_max = float(ao_min), float(ao_max)

    def set_ps_range(self, quantity: str, ps_min: float, ps_max: float) -> None:
        """
        Set the power supply range (D-06).

        Inputs:
            quantity (str): VOLTAGE or CURRENT.
            ps_min (float): PS minimum.
            ps_max (float): PS maximum.

        Returns:
            None.
        """
        # Store both values.
        self.channel(quantity).ps_min, self.channel(quantity).ps_max = float(ps_min), float(ps_max)

    # ------------------------------------------------------------------ safety / backend

    def set_safety(self, safe_startup: float, safe_exit: float, slew_enabled: bool,
                   slew_voltage: float, slew_current: float) -> None:
        """
        Set the safety values (sections 6.2, 13; D-07).

        Inputs:
            safe_startup (float): Safe value at start-up / Output OFF / error (PS units).
            safe_exit (float): Safe value at exit (PS units).
            slew_enabled (bool): Slew-rate limit on/off.
            slew_voltage (float): Voltage slew rate in V/s.
            slew_current (float): Current slew rate in A/s.

        Returns:
            None.
        """
        # Safety group of the working copy.
        safety = self.config.safety
        # Store every value.
        safety.safe_value_startup, safety.safe_value_exit = float(safe_startup), float(safe_exit)
        # Store the slew settings.
        safety.slew_limit_enabled = bool(slew_enabled)
        # Store the rates.
        safety.slew_rate_voltage, safety.slew_rate_current = float(slew_voltage), float(slew_current)

    def backend_options(self) -> List[Tuple[str, str]]:
        """
        Return the backend choices: "auto" plus every item of daq_backends.json.

        Returns:
            List[Tuple[str, str]]: (id, label) pairs.
        """
        # Start with the automatic choice.
        options = [(AUTO_BACKEND, "Auto (NI-DAQmx, else simulation)")]
        # Add every catalog item.
        options += [(item["id"], item.get("label", item["id"])) for item in self._catalogs["daq_backends"].items]
        # Return the list.
        return options

    def set_backend(self, backend_id: str) -> None:
        """
        Select the DAQ backend (applies at the next application start).

        Inputs:
            backend_id (str): "auto" or a catalog id.

        Returns:
            None.
        """
        # Store the id.
        self.config.daq.backend = backend_id

    # ------------------------------------------------------------------ preview / validation

    def preview(self, quantity: str) -> ScalePreview:
        """
        Build the scaling preview of one channel.

        Inputs:
            quantity (str): VOLTAGE or CURRENT.

        Returns:
            ScalePreview: Slope, offset, rows or errors.
        """
        # Channel settings.
        channel = self.channel(quantity)
        # Scale of the channel.
        scale = channel_scale(channel)
        # Invalid ranges: return the reasons.
        if not scale.is_valid:
            # Preview with errors only.
            return ScalePreview(valid=False, errors=scale.validate())
        # Percent points from the catalog.
        percents = [item["percent"] for item in self._catalogs["scaling_preview"].items]
        # Hint for a non-zero PS minimum (D-06).
        note = (f"PS min is {channel.ps_min:g} {channel.unit}: offset {scale.offset:.6g} V is applied."
                if channel.ps_min != 0 else "")
        # Full preview.
        return ScalePreview(valid=True, slope=scale.slope, offset=scale.offset,
                            rows=scale.preview(percents), note=note)

    def validate(self) -> Tuple[List[str], List[str]]:
        """
        Validate the working copy for Save (section 7.3).

        Unassigned channels are errors here (Save requires both channels).
        Hardware checks run only after a scan.

        Returns:
            Tuple[List[str], List[str]]: (errors, warnings).
        """
        # Recalculate slope/offset in the working copy.
        recalculate_scaling(self.config)
        # Settings checks.
        errors, warnings = validate_config(self.config)
        # Move "no AO channel assigned" from warnings to errors.
        missing = [w for w in warnings if "no AO channel assigned" in w]
        # Keep the other warnings.
        warnings = [w for w in warnings if w not in missing]
        # Add the missing assignments as errors.
        errors = missing + errors
        # Hardware checks after a scan.
        if self.scanned:
            # Compare with the scan.
            errors += check_hardware(self.config.voltage_channel, self.config.current_channel, self.devices)
        # Not scanned yet: hint only.
        else:
            # Tell the user that the hardware was not checked.
            warnings.append("Devices not scanned - hardware was not checked.")
        # Return both lists.
        return errors, warnings

    def is_dirty(self) -> bool:
        """Return True when the working copy differs from the last saved state."""
        # Recalculate first so slope/offset do not cause false differences.
        recalculate_scaling(self.config)
        # Compare everything except the meta group (changed by Save itself).
        current = {k: v for k, v in self.config.to_dict().items() if k != "meta"}
        # Same filter for the snapshot.
        saved = {k: v for k, v in self._saved.items() if k != "meta"}
        # Different means unsaved changes.
        return current != saved

    def mark_saved(self) -> None:
        """Take the working copy as the new saved state (call after a successful Save)."""
        # Recalculate first.
        recalculate_scaling(self.config)
        # Store the snapshot.
        self._saved = copy.deepcopy(self.config.to_dict())

    def restore_defaults(self) -> None:
        """
        Replace the working copy with the default template (not saved until Save).

        The meta group and the UI group (theme, window position) are kept.

        Returns:
            None.
        """
        # Build the default configuration.
        defaults = AppConfig.from_dict(copy.deepcopy(self._defaults))
        # Keep the meta information.
        defaults.meta = self.config.meta
        # Keep the UI preferences.
        defaults.ui = self.config.ui
        # Recalculate scaling.
        recalculate_scaling(defaults)
        # Use it as working copy.
        self.config = defaults
