import json
from dataclasses import dataclass
from pathlib import Path

import ripple1d


class BadConflation(Exception):
    """Raised when conflation files do not match."""

    def __init__(self, message: str):
        super().__init__(message)


class PathManager:
    """Generate paths to files for the test directory."""

    ras_model_name = "source_model"
    nwm_file_name = "nwm.parquet"

    def __init__(self, ras_dir: str):
        self.ras_dir = str(Path(__file__).parent / ras_dir)

    @property
    def ras_path(self) -> str:
        """Path to the ras gpkg."""
        return str(Path(self.ras_dir) / f"{self.ras_model_name}.gpkg")

    @property
    def nwm_path(self) -> str:
        """Path to the nwm network."""
        return str(Path(self.ras_dir) / self.nwm_file_name)

    @property
    def nwm_dict(self) -> dict:
        """Format information for conflate_model endpoint."""
        return {"file_name": self.nwm_path, "type": "nwm_hydrofabric", "version": f"{ripple1d.__version__}[testing]"}

    @property
    def conflation_file(self) -> str:
        """Path to the generated conflation file."""
        return str(Path(self.ras_dir) / f"{self.ras_model_name}.conflation.json")

    @property
    def rubric_file(self) -> str:
        """Path to the target conflation file."""
        return str(Path(self.ras_dir) / "validation.conflation.json")

    @property
    def test_id(self) -> str:
        """A short ID for the test."""
        return Path(self.ras_dir).name


class ConflationFile:

    def __init__(self, fpath: str):
        self.fpath = fpath
        with open(fpath) as f:
            self._dict = json.load(f)

    @property
    def reaches(self):
        return {k: ConflationReach(v) for k, v in self._dict["reaches"].items()}

    def __eq__(self, other) -> bool:
        """Check whether this conflation file matches another one."""
        if not isinstance(other, ConflationFile):
            return False
        if not self.reaches.keys() == other.reaches.keys():
            raise BadConflation(
                f"reach mismatch.\n{self.fpath} had reaches {list(self.reaches.keys())}\n{other.fpath} had reaches {list(other.reaches.keys())}"
            )
        for reach in self.reaches:
            if not reach in other.reaches:
                raise BadConflation(f"reach {reach} was present in {self.fpath} but not in {other.fpath}")
            r1 = self.reaches[reach]
            r2 = other.reaches[reach]
            if r1.eclipsed and not r2.eclipsed:
                raise BadConflation(f"{reach} was eclipsed in {self.fpath} but not in {other.fpath}")
            elif not r1.eclipsed and r2.eclipsed:
                raise BadConflation(f"{reach} was not eclipsed in {self.fpath} but was in {other.fpath}")
            elif r1.eclipsed and r2.eclipsed:
                return True
            if not r1.us_xs == r2.us_xs:
                raise BadConflation(
                    f"u/s XS incongruency for {reach}\n{self.fpath}: {r1.us_xs}\n{other.fpath}: {r2.us_xs}"
                )
            if not r1.ds_xs == r2.ds_xs:
                raise BadConflation(
                    f"d/s XS incongruency for {reach}\n{self.fpath}: {r1.ds_xs}\n{other.fpath}: {r2.ds_xs}"
                )
            return True


@dataclass
class ConflationReach:

    _dict: dict

    def val_to_str(self, val: dict) -> str:
        """Format a cross-section entry into text."""
        return f"{val['river']}_{val['reach']}_{val['xs_id']}"

    @property
    def eclipsed(self) -> bool:
        return self._dict["eclipsed"]

    @property
    def us_xs(self) -> str:
        return self.val_to_str(self._dict["us_xs"])

    @property
    def ds_xs(self) -> str:
        return self.val_to_str(self._dict["ds_xs"])
