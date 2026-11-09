"""
tests/conftest.py
=================
Shared pytest fixtures.

The "app_root" fixture builds a temporary copy of the application root with
the real shipped data files (config/defaults, config/presets, config/simulation,
resources/themes, version.txt), so tests never touch the real config folder.
"""

# Import shutil to copy the shipped data folders.
import shutil
# Import sys to make the project root importable.
import sys
# Import Path for file handling.
from pathlib import Path

# Import pytest to define fixtures.
import pytest

# Project root = parent folder of the tests folder.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Make "import core..." work when pytest is started from any folder.
if str(PROJECT_ROOT) not in sys.path:
    # Put the project root first on the import path.
    sys.path.insert(0, str(PROJECT_ROOT))

# Import AppPaths after the import path is set.
from core.app_paths import AppPaths  # noqa: E402


@pytest.fixture
def app_root(tmp_path: Path) -> AppPaths:
    """
    Create a temporary application root with the shipped data files.

    Inputs:
        tmp_path (Path): Temporary folder provided by pytest.

    Returns:
        AppPaths: Paths pointing into the temporary root.
    """
    # Copy the default template folder.
    shutil.copytree(PROJECT_ROOT / "config" / "defaults", tmp_path / "config" / "defaults")
    # Copy the catalog folder.
    shutil.copytree(PROJECT_ROOT / "config" / "presets", tmp_path / "config" / "presets")
    # Copy the simulated device list.
    shutil.copytree(PROJECT_ROOT / "config" / "simulation", tmp_path / "config" / "simulation")
    # Copy the themes folder.
    shutil.copytree(PROJECT_ROOT / "resources" / "themes", tmp_path / "resources" / "themes")
    # Copy the version file.
    shutil.copy(PROJECT_ROOT / "version.txt", tmp_path / "version.txt")
    # Return paths that point to the temporary root.
    return AppPaths(root=tmp_path)
