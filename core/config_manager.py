"""
core/config_manager.py
======================
Loads, validates, migrates and saves config/settings.json
(Project_Plan.md section 9).

Start-up logic (section 9.3):
    * File missing           -> create from config/defaults/settings.default.json
    * File invalid/unreadable-> back up as settings.json.bak, load defaults, warn
    * Older config_version   -> migrate, save new version
    * Missing keys           -> filled from the default template (deep merge)

Slope and offset are always recalculated on load and save (section 8.3);
the stored values are informational only.

LabVIEW analogy:
    Equivalent to a configuration class / FGV with Init, Read, Validate, Write.
"""

# Import copy to duplicate nested dictionaries safely.
import copy
# Import json to read and write the settings file.
import json
# Import os for the atomic file replace.
import os
# Import dataclass to define the load result container.
from dataclasses import dataclass, field
# Import datetime to stamp the save time.
from datetime import datetime
# Import Path for file handling.
from pathlib import Path
# Import typing helpers.
from typing import Any, Callable, Dict, List, Tuple

# Import the path container.
from core.app_paths import AppPaths
# Import the module logger factory.
from core.logger import get_logger
# Import the configuration data classes.
from core.models import AppConfig, ChannelConfig
# Import the scaling helpers used for recalculation and validation.
from core.scaling import LinearScale, validate_span_in_device_range

# Logger for this module.
_log = get_logger("config_manager")

# Current structure version of settings.json; raise it when the structure changes.
CURRENT_CONFIG_VERSION = 1

# Load status: file existed and was valid.
STATUS_LOADED = "loaded"
# Load status: file was missing and has been created from defaults.
STATUS_CREATED = "created"
# Load status: file was invalid, backed up, and replaced by defaults.
STATUS_RESET_INVALID = "reset_invalid"
# Load status: file had an older structure version and was migrated.
STATUS_MIGRATED = "migrated"

# Migration functions by source version: each converts version N to N+1.
# Example for the future: MIGRATIONS[1] = _migrate_1_to_2
MIGRATIONS: Dict[int, Callable[[Dict[str, Any]], Dict[str, Any]]] = {}


class ConfigError(Exception):
    """Raised when the default template is missing or unusable (fatal)."""


@dataclass
class LoadResult:
    """
    Result of ConfigManager.load().

    Attributes:
        config (AppConfig): The loaded (or default) configuration.
        status (str): One of the STATUS_* constants.
        errors (List[str]): Validation errors (output must stay disabled while not empty).
        warnings (List[str]): Non-blocking hints for the user.
    """

    # Loaded configuration.
    config: AppConfig
    # Load status.
    status: str
    # Blocking validation errors.
    errors: List[str] = field(default_factory=list)
    # Non-blocking warnings.
    warnings: List[str] = field(default_factory=list)


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """
    Merge two nested dictionaries; values in override win.

    Used to fill keys missing in the user file with values from the template.

    Inputs:
        base (Dict[str, Any]): Template dictionary (not modified).
        override (Dict[str, Any]): User dictionary (not modified).

    Returns:
        Dict[str, Any]: New merged dictionary.
    """
    # Start from a deep copy so the template is never changed.
    result = copy.deepcopy(base)
    # Walk through every key of the override dictionary.
    for key, value in override.items():
        # If both sides hold a dictionary, merge them recursively.
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            # Recursive merge for nested groups.
            result[key] = deep_merge(result[key], value)
        # Otherwise the override value replaces the template value.
        else:
            # Copy the value so later edits do not affect the source.
            result[key] = copy.deepcopy(value)
    # Return the merged dictionary.
    return result


def channel_scale(channel: ChannelConfig) -> LinearScale:
    """
    Build the LinearScale for one channel.

    Inputs:
        channel (ChannelConfig): Channel configuration.

    Returns:
        LinearScale: Scale using the channel PS range and used AO span.
    """
    # Create the scale from the four range values.
    return LinearScale(ps_min=channel.ps_min, ps_max=channel.ps_max,
                       ao_min=channel.ao_min, ao_max=channel.ao_max)


def recalculate_scaling(config: AppConfig) -> None:
    """
    Recalculate slope and offset of both channels in place.

    Channels with an invalid scale keep slope = 0 and offset = 0.

    Inputs:
        config (AppConfig): Configuration to update.

    Returns:
        None.
    """
    # Handle both control channels the same way.
    for channel in (config.voltage_channel, config.current_channel):
        # Build the scale for this channel.
        scale = channel_scale(channel)
        # Only calculate when the ranges are valid.
        if scale.is_valid:
            # Store the calculated slope.
            channel.slope = scale.slope
            # Store the calculated offset.
            channel.offset = scale.offset
        # Invalid ranges: store neutral values (validation reports the error).
        else:
            # Neutral slope.
            channel.slope = 0.0
            # Neutral offset.
            channel.offset = 0.0


def validate_config(config: AppConfig) -> Tuple[List[str], List[str]]:
    """
    Validate a configuration (Project_Plan.md sections 7.3, 8, 9).

    Inputs:
        config (AppConfig): Configuration to check.

    Returns:
        Tuple[List[str], List[str]]: (errors, warnings).
    """
    # Blocking problems.
    errors: List[str] = []
    # Non-blocking hints.
    warnings: List[str] = []
    # Check both channels with a readable name prefix.
    for name, channel in (("Voltage", config.voltage_channel), ("Current", config.current_channel)):
        # An unassigned channel is not an error at start-up, but output stays disabled.
        if not channel.is_assigned:
            # Tell the user what is missing.
            warnings.append(f"{name} channel: no AO channel assigned.")
        # Check PS range and AO span widths.
        for message in channel_scale(channel).validate():
            # Prefix each message with the channel name.
            errors.append(f"{name} channel: {message}")
        # Check the used span against the device range.
        span_errors, span_warnings = validate_span_in_device_range(
            channel.ao_min, channel.ao_max, channel.device_ao_min, channel.device_ao_max)
        # Add span errors (skip the width error already reported above).
        errors.extend(f"{name} channel: {m}" for m in span_errors if "must be greater" not in m)
        # Add span warnings.
        warnings.extend(f"{name} channel: {m}" for m in span_warnings)
    # Voltage and current must not use the same physical channel.
    if (config.voltage_channel.is_assigned
            and config.voltage_channel.physical_channel == config.current_channel.physical_channel):
        # Describe the conflict.
        errors.append("Voltage and current channels must be different AO channels.")
    # Slew rates must be positive (D-07).
    if config.safety.slew_rate_voltage <= 0:
        # Describe the voltage rate problem.
        errors.append("Voltage slew rate must be greater than 0 V/s.")
    # Check the current slew rate.
    if config.safety.slew_rate_current <= 0:
        # Describe the current rate problem.
        errors.append("Current slew rate must be greater than 0 A/s.")
    # The ramp step interval must be at least 10 ms (D-07).
    if config.safety.slew_interval_ms < 10:
        # Describe the interval problem.
        errors.append("Slew ramp interval must be at least 10 ms.")
    # Ramp interval of sequences must be at least 10 ms.
    if config.sequence.ramp_interval_ms < 10:
        # Describe the ramp interval problem.
        errors.append("Sequence ramp interval must be at least 10 ms.")
    # Active-wait part must stay small (it uses CPU time).
    if not 0 <= config.sequence.spin_wait_ms <= 20:
        # Describe the active-wait problem.
        errors.append("Sequence spin wait must be between 0 and 20 ms.")
    # Monitor refresh must be reasonable.
    if config.sequence.status_poll_ms < 20:
        # Describe the refresh problem.
        errors.append("Sequence monitor refresh must be at least 20 ms.")
    # Minimum step duration must be positive (D-01).
    if config.sequence.min_step_duration_ms <= 0:
        # Describe the step duration problem.
        errors.append("Minimum step duration must be greater than 0 ms.")
    # The DAQ command timeout must be positive.
    if config.daq.write_timeout_s <= 0:
        # Describe the timeout problem.
        errors.append("DAQ write timeout must be greater than 0 s.")
    # Return both lists.
    return errors, warnings


class ConfigManager:
    """
    Reads and writes config/settings.json.

    Usage:
        manager = ConfigManager(paths, app_version)
        result = manager.load()
        manager.save(result.config)
    """

    def __init__(self, paths: AppPaths, app_version: str) -> None:
        """
        Create the manager.

        Inputs:
            paths (AppPaths): Application paths.
            app_version (str): Version written into the meta group on save.

        Returns:
            None.
        """
        # Store the paths.
        self._paths = paths
        # Store the application version.
        self._app_version = app_version

    def load_defaults_dict(self) -> Dict[str, Any]:
        """
        Read the default template as a dictionary.

        Inputs:
            None.

        Returns:
            Dict[str, Any]: Template content.

        Raises:
            ConfigError: If the template is missing or invalid (installation broken).
        """
        # Path of the template.
        template = self._paths.default_settings_file
        # The template must exist; it is part of the installation.
        if not template.is_file():
            # Report the broken installation.
            raise ConfigError(f"Default settings template missing: {template}")
        # Try to parse the template.
        try:
            # Read and parse the template.
            return json.loads(template.read_text(encoding="utf-8"))
        # The template is not valid JSON.
        except json.JSONDecodeError as exc:
            # Report the broken template.
            raise ConfigError(f"Default settings template invalid: {exc}") from exc

    def _backup_invalid_file(self) -> Path:
        """
        Rename the broken settings.json to settings.json.bak.

        Inputs:
            None.

        Returns:
            Path: Path of the backup file.
        """
        # Build the backup path next to the settings file.
        backup = self._paths.settings_file.with_name(self._paths.settings_file.name + ".bak")
        # Replace an older backup if there is one.
        os.replace(self._paths.settings_file, backup)
        # Log the backup.
        _log.warning("Invalid settings file backed up to %s", backup)
        # Return the backup path.
        return backup

    @staticmethod
    def _migrate(data: Dict[str, Any]) -> Tuple[Dict[str, Any], bool]:
        """
        Upgrade an older settings structure step by step to the current version.

        Inputs:
            data (Dict[str, Any]): Settings content from the file.

        Returns:
            Tuple[Dict[str, Any], bool]: (upgraded data, True if anything was migrated).
        """
        # Read the version stored in the file (missing = 1).
        version = int(data.get("meta", {}).get("config_version", 1))
        # Remember whether a migration step ran.
        migrated = False
        # Apply migration steps until the current version is reached.
        while version < CURRENT_CONFIG_VERSION:
            # Find the step function for this version.
            step = MIGRATIONS.get(version)
            # A missing step is a programming error; stop migrating safely.
            if step is None:
                # Log the gap and leave the loop.
                _log.error("No migration defined from config version %d", version)
                # Stop the loop.
                break
            # Run the step.
            data = step(data)
            # Go to the next version.
            version += 1
            # Mark that a migration happened.
            migrated = True
        # Store the reached version in the meta group.
        data.setdefault("meta", {})["config_version"] = max(version, CURRENT_CONFIG_VERSION)
        # Return the result.
        return data, migrated

    def load(self) -> LoadResult:
        """
        Load the configuration following the start-up logic of section 9.3.

        Inputs:
            None.

        Returns:
            LoadResult: Configuration, status, errors and warnings.

        Raises:
            ConfigError: Only if the default template itself is unusable.
        """
        # Make sure the config, sequences and logs folders exist.
        self._paths.ensure_writable_dirs()
        # Read the default template (fatal if broken).
        defaults = self.load_defaults_dict()
        # Assume a normal load until something else happens.
        status = STATUS_LOADED
        # Case 1: the user file does not exist yet.
        if not self._paths.settings_file.is_file():
            # Log the first start.
            _log.info("settings.json not found - creating it from defaults")
            # Use an empty user dictionary (all values come from the template).
            user_data: Dict[str, Any] = {}
            # Mark the status.
            status = STATUS_CREATED
        # Case 2: the user file exists.
        else:
            # Try to read and parse it.
            try:
                # Parse the user file.
                user_data = json.loads(self._paths.settings_file.read_text(encoding="utf-8"))
                # The top level must be an object.
                if not isinstance(user_data, dict):
                    # Treat a wrong structure like invalid JSON.
                    raise ValueError("settings.json must contain a JSON object")
            # Invalid JSON or wrong structure.
            except (ValueError, OSError) as exc:
                # Log the problem.
                _log.warning("settings.json invalid (%s) - using defaults", exc)
                # Keep the broken file for analysis.
                self._backup_invalid_file()
                # Start from the template only.
                user_data = {}
                # Mark the status.
                status = STATUS_RESET_INVALID
        # Upgrade older structures.
        user_data, migrated = self._migrate(user_data) if user_data else (user_data, False)
        # Report a migration only for an otherwise normal load.
        if migrated and status == STATUS_LOADED:
            # Mark the status.
            status = STATUS_MIGRATED
        # Fill missing keys from the template.
        merged = deep_merge(defaults, user_data)
        # Try to build the typed configuration.
        try:
            # Build the configuration object.
            config = AppConfig.from_dict(merged)
        # A group has the wrong structure (e.g. a list instead of an object).
        except (TypeError, KeyError, AttributeError) as exc:
            # Log the structure problem.
            _log.warning("settings.json structure invalid (%s) - using defaults", exc)
            # Back up the file if it exists on disk.
            if self._paths.settings_file.is_file():
                # Keep the broken file.
                self._backup_invalid_file()
            # Build the configuration from the template only.
            config = AppConfig.from_dict(copy.deepcopy(defaults))
            # Mark the status.
            status = STATUS_RESET_INVALID
        # Recalculate slope and offset (stored values are informational only).
        recalculate_scaling(config)
        # Validate the result.
        errors, warnings = validate_config(config)
        # Write the file when it was created, reset or migrated.
        if status != STATUS_LOADED:
            # Save the cleaned-up configuration.
            self.save(config)
        # Log a short summary.
        _log.info("Config %s (%d error(s), %d warning(s))", status, len(errors), len(warnings))
        # Return everything to the caller.
        return LoadResult(config=config, status=status, errors=errors, warnings=warnings)

    def save(self, config: AppConfig) -> Path:
        """
        Save the configuration to settings.json (atomic write).

        Slope/offset are recalculated and the meta group is updated first.

        Inputs:
            config (AppConfig): Configuration to save.

        Returns:
            Path: Path of the written file.
        """
        # Make sure the config folder exists.
        self._paths.config_dir.mkdir(parents=True, exist_ok=True)
        # Recalculate slope and offset before writing.
        recalculate_scaling(config)
        # Store the current structure version.
        config.meta.config_version = CURRENT_CONFIG_VERSION
        # Store the application version.
        config.meta.app_version = self._app_version
        # Store the save time (ISO format, seconds precision).
        config.meta.last_saved = datetime.now().isoformat(timespec="seconds")
        # Temporary file next to the target (same drive, so replace is atomic).
        temp_file = self._paths.settings_file.with_suffix(".json.tmp")
        # Write pretty-printed JSON to the temporary file.
        temp_file.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
        # Replace the real file in one step (no half-written file on power loss).
        os.replace(temp_file, self._paths.settings_file)
        # Log the save.
        _log.info("Settings saved to %s", self._paths.settings_file)
        # Return the file path.
        return self._paths.settings_file
