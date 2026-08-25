"""Path manager."""

from pathlib import Path


class LocalPathManager:
    """Builds consistent paths for STAC items and collections assuming a top level local catalog."""

    def __init__(self, model_root_dir: str):
        self._model_root_dir = model_root_dir

    @property
    def model_root_dir(self) -> str:
        """Model root directory."""
        return str(self._model_root_dir)

    @property
    def model_parent_dir(self) -> str:
        """Model parent directory."""
        return str(Path(self._model_root_dir).parent)

    @property
    def item_dir(self) -> str:
        """Duplicate of model_root, added for clarity in the calling code."""
        return self.model_root_dir

    def item_path(self, item_id: str) -> str:
        """Item path."""
        return str(Path(self._model_root_dir) / f"{item_id}.json")

    def derived_item_asset(self, filename: str) -> str:
        """Derive item asset path."""
        return str(Path(self._model_root_dir) / filename)
