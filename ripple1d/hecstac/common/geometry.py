"""Geometry utils."""

from pyproj import CRS, Transformer
from shapely import Geometry
from shapely.ops import transform


def reproject_to_wgs84(geom: Geometry, crs: str) -> Geometry:
    """Convert geometry CRS to EPSG:4326 for stac item geometry."""
    pyproj_crs = CRS.from_user_input(crs)
    wgs_crs = CRS.from_authority("EPSG", "4326")
    if pyproj_crs != wgs_crs:
        transformer = Transformer.from_crs(pyproj_crs, wgs_crs, True)
        return transform(transformer.transform, geom)
    return geom
