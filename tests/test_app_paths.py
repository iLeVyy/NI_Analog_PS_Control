"""
tests/test_app_paths.py
=======================
Unit tests for core/app_paths.py.
"""

# Import the code under test.
from core.app_paths import AppPaths, detect_app_root


def test_dev_root_contains_main_py():
    """In development the detected root is the folder with main.py."""
    # Detect the root.
    root = detect_app_root()
    # main.py must be there.
    assert (root / "main.py").is_file()


def test_paths_are_relative_to_root(app_root):
    """All derived paths live under the root."""
    # Check every derived folder / file.
    for path in (app_root.config_dir, app_root.presets_dir, app_root.settings_file,
                 app_root.sequences_dir, app_root.logs_dir, app_root.themes_dir):
        # Each path must start with the root.
        assert app_root.root in path.parents


def test_ensure_writable_dirs_creates_folders(tmp_path):
    """ensure_writable_dirs() creates config, sequences and logs."""
    # Paths in an empty folder.
    paths = AppPaths(root=tmp_path)
    # Create the folders.
    paths.ensure_writable_dirs()
    # All three must exist.
    assert paths.config_dir.is_dir() and paths.sequences_dir.is_dir() and paths.logs_dir.is_dir()


def test_read_version(app_root):
    """read_version() returns the content of version.txt."""
    # The value must equal the text in the project's version.txt.
    assert app_root.read_version() == app_root.version_file.read_text(encoding="utf-8").strip()
    # And it must look like a version number (x.y.z).
    assert app_root.read_version().count(".") == 2
