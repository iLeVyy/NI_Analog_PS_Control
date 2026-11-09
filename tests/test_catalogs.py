"""
tests/test_catalogs.py
======================
Unit tests for core/catalogs.py (config-driven presets and themes).
"""

# Import json to write test catalog files.
import json

# Import pytest for exception checks.
import pytest

# Import the code under test.
from core.catalogs import CatalogError, discover_themes, load_all_catalogs, load_catalog


def test_shipped_catalogs_load(app_root):
    """All shipped catalog files load without errors."""
    # Load everything from the presets folder.
    catalogs = load_all_catalogs(app_root.presets_dir)
    # The catalogs required by the plan must exist.
    for name in ("ao_spans", "step_sizes", "duration_units", "end_states",
                 "sequence_actions", "scaling_preview"):
        # Each one must be present.
        assert name in catalogs


def test_ao_span_default_is_0_10(app_root):
    """D-05: the default span preset is 0-10 V."""
    # Load the span catalog.
    spans = load_catalog(app_root.presets_dir, "ao_spans")
    # Check the default id.
    assert spans.default == "0_10"
    # Check the values of the default item.
    assert spans.get("0_10")["ao_max"] == 10.0


def test_step_sizes_filter_by_quantity(app_root):
    """filter() returns only the voltage step sizes."""
    # Load the step size catalog.
    steps = load_catalog(app_root.presets_dir, "step_sizes")
    # Filter by quantity.
    voltage = steps.filter(quantity="voltage")
    # Every result is a voltage item.
    assert voltage and all(item["quantity"] == "voltage" for item in voltage)


def test_new_catalog_item_needs_no_code_change(app_root):
    """Adding an item to a JSON file makes it available immediately."""
    # Path of the span catalog in the temporary root.
    path = app_root.presets_dir / "ao_spans.json"
    # Read the current file.
    data = json.loads(path.read_text(encoding="utf-8"))
    # Add a new 1-5 V preset.
    data["items"].append({"id": "1_5", "label": "1 - 5 V", "ao_min": 1.0, "ao_max": 5.0})
    # Write the file back.
    path.write_text(json.dumps(data), encoding="utf-8")
    # Load again.
    spans = load_catalog(app_root.presets_dir, "ao_spans")
    # The new preset is available.
    assert "1_5" in spans.ids()


def test_duplicate_id_is_rejected(app_root):
    """Duplicate ids raise CatalogError."""
    # Write a broken catalog.
    (app_root.presets_dir / "broken.json").write_text(
        json.dumps({"items": [{"id": "a"}, {"id": "a"}]}), encoding="utf-8")
    # Loading must fail.
    with pytest.raises(CatalogError):
        # Load the broken catalog.
        load_catalog(app_root.presets_dir, "broken")


def test_themes_are_discovered_from_folders(app_root):
    """Every resources/themes/<id>/theme.json is found; new folders are picked up."""
    # Create an extra theme folder.
    extra = app_root.themes_dir / "contrast"
    # Make the folder.
    extra.mkdir()
    # Write its theme.json.
    (extra / "theme.json").write_text(json.dumps({"id": "contrast", "label": "High contrast"}),
                                      encoding="utf-8")
    # Discover all themes.
    themes = discover_themes(app_root.themes_dir)
    # Shipped and new themes are present.
    assert {"dark", "light", "contrast"} <= set(themes)
