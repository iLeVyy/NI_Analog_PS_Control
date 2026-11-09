"""
core/bootstrap.py
=================
Common start-up steps used by the console mode and the GUI (main.py):

    startup()             -> paths, version, logging, settings.json,
                             catalogs, themes, default template
    create_daq_service()  -> backend from settings.json (or --simulate),
                             minimum driver check, started DaqService

LabVIEW analogy:
    The initialization part of a launcher VI, shared by all start modes.
"""

# Import dataclass to define the context container.
from dataclasses import dataclass
# Import typing helpers.
from typing import Any, Dict, Optional, Tuple

# Import the path resolver.
from core.app_paths import AppPaths
# Import the catalog loaders and error type.
from core.catalogs import Catalog, CatalogError, discover_themes, load_all_catalogs
# Import the configuration manager.
from core.config_manager import ConfigError, ConfigManager, LoadResult
# Import the backend factory.
from core.daq_backend import DaqBackendError, DriverInfo, check_min_driver_version, create_backend
# Import the DAQ service.
from core.daq_service import DaqService
# Import the logging helpers.
from core.logger import get_logger, setup_logging
# Import the configuration type.
from core.models import AppConfig


class StartupError(Exception):
    """Raised when the installation is broken (default template or a catalog unusable)."""


@dataclass
class AppContext:
    """
    Everything the application needs after start-up.

    Attributes:
        paths (AppPaths): Application paths.
        version (str): Application version.
        manager (ConfigManager): Reads / writes settings.json.
        load_result (LoadResult): Result of the settings load at start-up.
        catalogs (Dict[str, Catalog]): Config-driven catalogs.
        themes (Dict[str, Dict[str, Any]]): Themes by id.
        defaults (Dict[str, Any]): Content of settings.default.json.
    """

    # Application paths.
    paths: AppPaths
    # Application version.
    version: str
    # Settings manager.
    manager: ConfigManager
    # Start-up load result.
    load_result: LoadResult
    # Catalogs.
    catalogs: Dict[str, Catalog]
    # Themes.
    themes: Dict[str, Dict[str, Any]]
    # Default template.
    defaults: Dict[str, Any]

    @property
    def config(self) -> AppConfig:
        """Return the active configuration (updated after every Save)."""
        # The configuration lives in the load result.
        return self.load_result.config

    def theme_colors(self) -> Dict[str, str]:
        """
        Return the color table of the configured theme (falls back to the first theme).

        Returns:
            Dict[str, str]: Color name -> hex text ({} when no theme exists).
        """
        # Configured theme, or the first one found.
        theme = self.themes.get(self.config.ui.theme) or next(iter(self.themes.values()), {})
        # Return its colors.
        return dict(theme.get("colors", {}))


def startup(paths: Optional[AppPaths] = None) -> AppContext:
    """
    Run all start-up steps that do not need hardware.

    Inputs:
        paths (Optional[AppPaths]): Paths to use (tests); None = detect.

    Returns:
        AppContext: Start-up result.

    Raises:
        StartupError: If the default template or a catalog is unusable.
    """
    # Resolve the paths when not given.
    paths = paths or AppPaths.from_environment()
    # Read the version.
    version = paths.read_version()
    # Start logging with defaults first, so config loading is logged.
    setup_logging(paths.logs_dir)
    # Logger for the start-up.
    log = get_logger("bootstrap")
    # Log the start.
    log.info("PS Control %s starting (root: %s)", version, paths.root)
    # Create the settings manager.
    manager = ConfigManager(paths, version)
    # Load settings and the default template.
    try:
        # Load / create / repair settings.json.
        load_result = manager.load()
        # Read the default template (used by Restore Defaults).
        defaults = manager.load_defaults_dict()
    # Broken installation.
    except ConfigError as exc:
        # Log and convert.
        log.critical("Cannot start: %s", exc)
        # Report to the caller.
        raise StartupError(str(exc)) from exc
    # Restart logging with the configured level and retention.
    setup_logging(paths.logs_dir, load_result.config.logging.level, load_result.config.logging.retention_days)
    # Load the catalogs.
    try:
        # Every config/presets/*.json file.
        catalogs = load_all_catalogs(paths.presets_dir)
    # Broken catalog file.
    except CatalogError as exc:
        # Log and convert.
        log.critical("Cannot start: %s", exc)
        # Report to the caller.
        raise StartupError(str(exc)) from exc
    # Find the themes.
    themes = discover_themes(paths.themes_dir)
    # Return the context.
    return AppContext(paths, version, manager, load_result, catalogs, themes, defaults)


def create_daq_service(ctx: AppContext, force_simulation: bool = False) -> Tuple[Optional[DaqService], DriverInfo]:
    """
    Create and start the DAQ service with the backend selected in settings.json.

    Inputs:
        ctx (AppContext): Start-up result.
        force_simulation (bool): True = use the simulation (command-line --simulate).

    Returns:
        Tuple[Optional[DaqService], DriverInfo]: Started service (None when no
        backend is usable) and the driver check result.
    """
    # Logger for this step.
    log = get_logger("bootstrap")
    # Backend id: forced simulation or the setting.
    backend_id = "simulation" if force_simulation else ctx.config.daq.backend
    # Try to create the backend.
    try:
        # Create backend and driver check.
        backend, driver = create_backend(ctx.catalogs["daq_backends"], backend_id, paths=ctx.paths)
    # No usable backend.
    except DaqBackendError as exc:
        # Log it.
        log.error("DAQ backend: %s", exc)
        # No service.
        return None, DriverInfo(available=False, message=str(exc))
    # Apply the minimum driver version.
    driver = check_min_driver_version(driver, ctx.config.daq.min_driver_version, backend.simulated)
    # Create the service.
    service = DaqService(backend, driver)
    # Start its worker thread.
    service.start()
    # Return service and driver info.
    return service, driver
