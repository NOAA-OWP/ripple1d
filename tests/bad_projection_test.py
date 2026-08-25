import os
import shutil

from ripple1d.consts import MAP_DEM_UNCLIPPED_SRC_URL
from ripple1d.errors import RasTerrainFailure
from ripple1d.ops.ras_terrain import create_ras_terrain


def test_bad_prj():
    """Test if ripple will raise error when presented with an unsupported projection."""
    passed = False
    test_dir = os.path.join(os.path.dirname(__file__), "test-data", "3297968")
    try:
        result = create_ras_terrain(test_dir)
    except RasTerrainFailure:
        passed = True
    else:
        tpath = result["RAS Terrain"] + "." + os.path.basename(MAP_DEM_UNCLIPPED_SRC_URL).replace(".vrt", ".tif")
        if os.path.exists(tpath):
            raise RuntimeError("Terrain was generated even though test was set up for it to fail.")
    finally:
        shutil.rmtree(os.path.join(test_dir, "Terrain"))
        assert passed, "Ras did not raise an error even though terrain was not generated."


if __name__ == "__main__":
    test_bad_prj()
