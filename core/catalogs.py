"""
core/catalogs.py
================
Loads the config-driven catalogs (lists of similar things) at run time:

    config/presets/<name>.json      -> AO span presets, step sizes, duration
                                       units, end states, sequence actions,
                                       scaling preview points, ...
    resources/themes/<id>/theme.json -> one folder per theme

Adding a new preset, unit or theme only needs a new JSON item or folder -
no code change (config-driven rule, same idea as AnyDataLogger language folders).

Catalog file format:
    {
      "catalog": "<name>",
      "description": "...",
      "default": "<item id>"   (or a dict of ids for grouped catalogs),
      "items": [ { "id": "...", ... }, ... ]
    }

LabVIEW analogy:
    Like reading ring/enum contents from a file at start-up instead of
    hard-coding them in a typedef.
"""

# Import json to read the data files.
import json
# Import dataclass to define the catalog container.
from dataclasses import dataclass, field
# Import Path for file handling.
from pathlib import Path
# Import typing helpers.
from typing import Any, Dict, List, Optional

# Import the module logger factory.
from core.logger import get_logger

# Logger for this module.
_log = get_logger("catalogs")


class CatalogError(Exception):
    """Raised when a catalog file is missing or has an invalid structure."""


@dataclass
class Catalog:
    """
    One loaded catalog.

    Attributes:
        name (str): Catalog name (file name without .json).
        items (List[Dict[str, Any]]): Catalog items; each has a unique "id".
        default (Any): Default item id (or dict of ids for grouped catalogs).
        description (str): Human-readable description from the file.
        raw (Dict[str, Any]): Complete file content, for catalog-specific
            extra keys (e.g. "auto_order" in daq_backends.json).
    """

    # Catalog name.
    name: str
    # Catalog items.
    items: List[Dict[str, Any]] = field(default_factory=list)
    # Default item id(s).
    default: Any = None
    # Description text.
    description: str = ""
    # Complete file content.
    raw: Dict[str, Any] = field(default_factory=dict)

    def ids(self) -> List[str]:
        """Return the ids of all items in file order."""
        # Build the list of ids.
        return [item["id"] for item in self.items]

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        """
        Find one item by id.

        Inputs:
            item_id (str): Item id to find.

        Returns:
            Optional[Dict[str, Any]]: The item, or None if not found.
        """
        # Search the items in order.
        for item in self.items:
            # Return the first item with a matching id.
            if item["id"] == item_id:
                # Found.
                return item
        # Not found.
        return None

    def filter(self, **criteria: Any) -> List[Dict[str, Any]]:
        """
        Return the items whose fields match all given criteria.

        Inputs:
            **criteria: Field/value pairs, e.g. quantity="voltage".

        Returns:
            List[Dict[str, Any]]: Matching items in file order.
        """
        # Keep an item only when every criterion matches.
        return [item for item in self.items
                if all(item.get(key) == value for key, value in criteria.items())]


def _read_json(path: Path) -> Dict[str, Any]:
    """
    Read a JSON file into a dictionary.

    Inputs:
        path (Path): File to read.

    Returns:
        Dict[str, Any]: Parsed content.

    Raises:
        CatalogError: If the file is missing, invalid JSON, or not a JSON object.
    """
    # The file must exist.
    if not path.is_file():
        # Report the missing file.
        raise CatalogError(f"Catalog file not found: {path}")
    # Try to parse the file.
    try:
        # Read the text as UTF-8 and parse it.
        data = json.loads(path.read_text(encoding="utf-8"))
    # Parsing failed.
    except json.JSONDecodeError as exc:
        # Report the position of the syntax error.
        raise CatalogError(f"Invalid JSON in {path}: {exc}") from exc
    # The top level must be a JSON object.
    if not isinstance(data, dict):
        # Report the wrong structure.
        raise CatalogError(f"Catalog file must contain a JSON object: {path}")
    # Return the parsed dictionary.
    return data


def load_catalog(presets_dir: Path, name: str) -> Catalog:
    """
    Load and validate config/presets/<name>.json.

    Inputs:
        presets_dir (Path): Folder with the catalog files.
        name (str): Catalog name (file name without .json).

    Returns:
        Catalog: Loaded catalog.

    Raises:
        CatalogError: If the file is missing or the structure is invalid.
    """
    # Build the file path.
    path = presets_dir / f"{name}.json"
    # Read the file.
    data = _read_json(path)
    # Get the item list (must be a list).
    items = data.get("items")
    # Reject a missing or wrong-typed item list.
    if not isinstance(items, list):
        # Report the structure problem.
        raise CatalogError(f"'items' must be a list in {path}")
    # Track ids to find duplicates.
    seen = set()
    # Check every item.
    for index, item in enumerate(items):
        # Each item must be an object with an "id".
        if not isinstance(item, dict) or "id" not in item:
            # Report the bad item with its position.
            raise CatalogError(f"Item {index} in {path} must be an object with an 'id'")
        # Ids must be unique.
        if item["id"] in seen:
            # Report the duplicate id.
            raise CatalogError(f"Duplicate id '{item['id']}' in {path}")
        # Remember the id.
        seen.add(item["id"])
    # Build the catalog object.
    catalog = Catalog(name=name, items=items, default=data.get("default"),
                      description=data.get("description", ""), raw=data)
    # Log the result.
    _log.debug("Catalog '%s' loaded with %d item(s)", name, len(items))
    # Return the catalog.
    return catalog


def load_all_catalogs(presets_dir: Path) -> Dict[str, Catalog]:
    """
    Load every *.json file in the presets folder as a catalog.

    New catalog files are picked up automatically.

    Inputs:
        presets_dir (Path): Folder with the catalog files.

    Returns:
        Dict[str, Catalog]: Catalogs by name.

    Raises:
        CatalogError: If any catalog file is invalid.
    """
    # Result dictionary.
    catalogs: Dict[str, Catalog] = {}
    # Loop over all JSON files in name order (stable order).
    for path in sorted(presets_dir.glob("*.json")):
        # Load each file using its name (without .json) as catalog name.
        catalogs[path.stem] = load_catalog(presets_dir, path.stem)
    # Log how many catalogs were found.
    _log.info("Loaded %d catalog(s): %s", len(catalogs), ", ".join(catalogs))
    # Return all catalogs.
    return catalogs


def discover_themes(themes_dir: Path) -> Dict[str, Dict[str, Any]]:
    """
    Find all themes: every sub-folder of resources/themes with a theme.json.

    Inputs:
        themes_dir (Path): resources/themes folder.

    Returns:
        Dict[str, Dict[str, Any]]: Theme data by theme id; each entry also
        gets "folder" (Path) so the stylesheet can be found later.
    """
    # Result dictionary.
    themes: Dict[str, Dict[str, Any]] = {}
    # Stop early if the folder does not exist.
    if not themes_dir.is_dir():
        # Log and return an empty result.
        _log.warning("Themes folder not found: %s", themes_dir)
        # No themes.
        return themes
    # Loop over sub-folders in name order.
    for folder in sorted(p for p in themes_dir.iterdir() if p.is_dir()):
        # Path of the theme description file.
        theme_file = folder / "theme.json"
        # Skip folders without a theme.json.
        if not theme_file.is_file():
            # Nothing to load in this folder.
            continue
        # Try to read the theme file; a broken theme must not stop the app.
        try:
            # Parse the theme description.
            data = _read_json(theme_file)
        # The theme file is broken.
        except CatalogError as exc:
            # Log and skip this theme.
            _log.warning("Theme skipped: %s", exc)
            # Continue with the next folder.
            continue
        # Use the "id" from the file, or the folder name if missing.
        theme_id = data.get("id", folder.name)
        # Remember the folder for later stylesheet loading.
        data["folder"] = folder
        # Store the theme.
        themes[theme_id] = data
    # Log the found themes.
    _log.info("Found %d theme(s): %s", len(themes), ", ".join(themes))
    # Return the themes.
    return themes
