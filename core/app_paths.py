"""
core/app_paths.py
=================
Resolves the application root folder and every relative sub-folder the
application uses (config, presets, sequences, logs, resources, themes).

Why this module exists:
    All files must live relative to the application root (NFR-02). The root
    is different in development (folder that contains main.py) and in a
    Nuitka standalone build (folder that contains the EXE). This module is
    the ONLY place that knows how to find it.

LabVIEW analogy:
    Equivalent to the "Application Directory" VI plus a typedef cluster of
    all derived paths.
"""

# Import the standard "sys" module to read the program path (argv / executable).
import sys
# Import Path for object-oriented, OS-independent path handling.
from pathlib import Path
# Import dataclass to build a simple, typed container for all paths.
from dataclasses import dataclass


def _is_nuitka_compiled() -> bool:
    """
    Check whether the program runs as a Nuitka-compiled build.

    Inputs:
        None.

    Returns:
        bool: True when running from a Nuitka build, otherwise False.
    """
    # Nuitka defines a global name "__compiled__" inside every compiled module.
    return "__compiled__" in globals()


def detect_app_root() -> Path:
    """
    Detect the application root folder for the current way of running.

    Inputs:
        None.

    Returns:
        Path: Absolute path of the folder that holds main.py (development)
              or the EXE (Nuitka / other frozen builds).
    """
    # Case 1: Nuitka build -> the root is the folder of the started EXE.
    if _is_nuitka_compiled():
        # sys.argv[0] is the path of the started EXE; take its parent folder.
        return Path(sys.argv[0]).resolve().parent
    # Case 2: other frozen builds (e.g. PyInstaller) set sys.frozen = True.
    if getattr(sys, "frozen", False):
        # sys.executable is the EXE path in frozen builds; take its folder.
        return Path(sys.executable).resolve().parent
    # Case 3: development -> this file is <root>/core/app_paths.py, so go up two levels.
    return Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class AppPaths:
    """
    Immutable container with every folder and file path used by the app.

    Attributes:
        root (Path): Application root folder.
    All other paths are derived properties of root.
    """

    # The application root folder; everything else is derived from it.
    root: Path

    @classmethod
    def from_environment(cls) -> "AppPaths":
        """
        Build an AppPaths object using the automatically detected root.

        Inputs:
            None.

        Returns:
            AppPaths: Paths based on detect_app_root().
        """
        # Create the object with the detected root folder.
        return cls(root=detect_app_root())

    @property
    def config_dir(self) -> Path:
        """Return <root>/config - folder with the user settings file."""
        # Join the root with the "config" folder name.
        return self.root / "config"

    @property
    def defaults_dir(self) -> Path:
        """Return <root>/config/defaults - shipped default templates (never edited by the app)."""
        # Join the config folder with the "defaults" folder name.
        return self.config_dir / "defaults"

    @property
    def presets_dir(self) -> Path:
        """Return <root>/config/presets - config-driven catalogs (spans, units, ...)."""
        # Join the config folder with the "presets" folder name.
        return self.config_dir / "presets"

    @property
    def simulation_dir(self) -> Path:
        """Return <root>/config/simulation - data files of the simulation backend."""
        # Join the config folder with the "simulation" folder name.
        return self.config_dir / "simulation"

    @property
    def settings_file(self) -> Path:
        """Return <root>/config/settings.json - the active user configuration."""
        # Join the config folder with the settings file name.
        return self.config_dir / "settings.json"

    @property
    def default_settings_file(self) -> Path:
        """Return <root>/config/defaults/settings.default.json - the default template."""
        # Join the defaults folder with the default template file name.
        return self.defaults_dir / "settings.default.json"

    @property
    def sequences_dir(self) -> Path:
        """Return <root>/sequences - user sequence files (*.json)."""
        # Join the root with the "sequences" folder name.
        return self.root / "sequences"

    @property
    def logs_dir(self) -> Path:
        """Return <root>/logs - daily application log files."""
        # Join the root with the "logs" folder name.
        return self.root / "logs"

    @property
    def resources_dir(self) -> Path:
        """Return <root>/resources - icons and themes."""
        # Join the root with the "resources" folder name.
        return self.root / "resources"

    @property
    def themes_dir(self) -> Path:
        """Return <root>/resources/themes - one sub-folder per theme (theme.json + style.qss)."""
        # Join the resources folder with the "themes" folder name.
        return self.resources_dir / "themes"

    @property
    def version_file(self) -> Path:
        """Return <root>/version.txt - single source of the application version."""
        # Join the root with the version file name.
        return self.root / "version.txt"

    def ensure_writable_dirs(self) -> None:
        """
        Create the folders the application writes to, if they are missing.

        Inputs:
            None.

        Returns:
            None.
        """
        # Loop over every folder the application writes into at run time.
        for folder in (self.config_dir, self.sequences_dir, self.logs_dir):
            # Create the folder (and parents); do nothing if it already exists.
            folder.mkdir(parents=True, exist_ok=True)

    def read_version(self) -> str:
        """
        Read the application version from version.txt.

        Inputs:
            None.

        Returns:
            str: Version string such as "0.1.0", or "0.0.0" if the file is missing.
        """
        # If the version file does not exist, return a neutral fallback version.
        if not self.version_file.is_file():
            # Fallback keeps the app running even if the file was deleted.
            return "0.0.0"
        # Read the file, strip spaces / line breaks and return the text.
        return self.version_file.read_text(encoding="utf-8").strip()
