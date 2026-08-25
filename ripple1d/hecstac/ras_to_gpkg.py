"""Extract geospatial data from HEC-RAS files."""

import glob
import logging

from ripple1d.hecstac.ras.item import RASModelItem
from ripple1d.utils.ripple_utils import prj_is_ras


def gpkg_from_ras(source_model_directory: str, crs: str, metadata: dict):
    """Write geometry and flow data to a geopackage locally.

    Parameters
    ----------
    source_model_directory : str
        The path to the directory containing HEC-RAS project, plan, geometry,
        and flow files.
    crs : str
        This crs of the source model. This can be any string interpretable by the pyproj CRS function
        (https://pyproj4.github.io/pyproj/stable/api/crs/crs.html)
    metadata : dict
        A dictionary of miscellaneous metadata that will be appended to the
        non-spatial metadata table in the final geopackage.
    task_id : str, optional
        Task ID to use for logging, by default ""

    Raises
    ------
    FileNotFoundError
        Raises when no rar project (.prj) file is found

    Notes
    -----
    The gpkg_from_ras endpoint extracts data contained within the HEC-RAS
    geometry and flow files and exports them to a geopackage file.  When the
    directory containing a HEC-RAS project is submitted, ripple1d will scan the
    directory for the following files

    * **Project file (.prj)** ripple1d scans the directory for .prj files and identifies any that are HEC-RAS project files (*Note: if more than one valid project file is identified, one will be arbitrarily selected*).
    * **Plan file (.p0x)** ripple1d scans the project file for a list of plans and determine whether the plans contain encroachments. Since encroachments are often indicative of a floodway run as opposed to an existing condition run, the first listed plan without any encroachments is selected as the primary plan.
    * **Flow file (.f0x)** ripple1d checks to see if the flow file listed in the primary plan exists.  If no flow file is specified within the primary plan, or if the specified flow file does not exist, ripple1d will search the directory for any valid steady flow files in the directory and select an arbitrary one.  If no steady flow file is found, geopackage creation will continue with no flow metadata being recorded.
    * **Geometry file (.g0x)** ripple1d checks to see if the geometry file listed in the primary plan exists.  If no geometry file is specified within the primary plan, or if the specified geometry file does not exist , ripple1d will search the directory for any valid geometry files in the directory and select an arbitrary one.

    Once a set of HEC-RAS files are identified, ripple1d will extract
    cross-sectional geometry, reach centerlines, junction points, and structure
    extents and save them as individual layers within a geopackage.  Ripple1d
    creates an additional non-spatial table within the geopackage for metadata
    such as HEC-RAS version, project units, etc.  The geopackage will be saved
    to the project directory with the same base name as the HEC-RAS project
    file.
    """
    logging.info("gpkg_from_ras starting")
    prjs = glob.glob(f"{source_model_directory}/*.prj")
    ras_text_file_path = None

    for prj in prjs:
        if prj_is_ras(prj):
            ras_text_file_path = prj
            break

    if not ras_text_file_path:
        raise FileNotFoundError(f"No ras project file found in {source_model_directory}")

    item = RASModelItem.from_prj(ras_text_file_path, None, crs)
    metadata = item.add_model_geopackages(
        source_model_directory, geometries=[item._primary_geometry.name], metadata=metadata
    )
    logging.info("gpkg_from_ras complete")
    return metadata
