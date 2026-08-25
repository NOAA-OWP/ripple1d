"""Constants used throughout."""

from collections import OrderedDict

SUPPRESS_LOGS = ["boto3", "botocore", "geopandas", "fiona", "rasterio", "pyogrio", "shapely"]

# MAP_DEM_UNCLIPPED_SRC_URL = (
#     "https://rockyweb.usgs.gov/vdelivery/Datasets/Staged/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"
# )
MAP_DEM_UNCLIPPED_SRC_URL = "https://prd-tnm.s3.amazonaws.com/StagedProducts/Elevation/13/TIFF/USGS_Seamless_DEM_13.vrt"

MAP_DEM_BUFFER_DIST_FT = 1000.0
MAP_DEM_DIRNAME = "MapTerrain"
MAP_DEM_HDF_NAME = "MapTerrain.hdf"
MAP_DEM_VERT_UNITS = "Meters"

METERS_PER_FOOT = 1200.0 / 3937.0

MINDEPTH = 0.1  # ft
MIN_FLOW = 1  # cfs

DEFAULT_EPSG = 4269
DEFAULT_ND_SLOPE = 0.001
MIN_ND_SLOPE = 1e-6  # slope lower bound
MAX_ND_SLOPE = 0.1  # slope upper bound
ND_SLOPE_SIG_FIGS = 6  # significant figures for ds_slope in conflation JSON

STAC_API_URL = "https://stac2.dewberryanalytics.com"

TERRAIN_NAME = "Terrain_Name"
TERRAIN_PATH = "Terrain_Path"
SUPPORTED_LAYERS = ["River", "XS"]

WSE_HDF_PATH = "/Results/Steady/Output/Output Blocks/Base Output/Steady Profiles/Cross Sections/Water Surface"
FLOW_HDF_PATH = "/Results/Steady/Output/Output Blocks/Base Output/Steady Profiles/Cross Sections/Flow"
XS_NAMES_HDF_PATH = "/Results/Steady/Output/Geometry Info/Cross Section Only"
PROFILE_NAMES_HDF_PATH = "/Results/Steady/Output/Output Blocks/Base Output/Steady Profiles/Profile Names"

LAYER_COLORS = OrderedDict(
    {
        "Banks": "red",
        "Junction": "red",
        "BCLines": "brown",
        "BreakLines": "black",
        "Connections": "cyan",
        "Structure": "black",
        "Mesh": "yellow",
        "River": "blue",
        "StorageAreas": "orange",
        "TwoDAreas": "purple",
        "XS": "green",
    }
)

SHOW_RAS = False

HYDROFABRIC_CRS = 5070

TERRAIN_AGREEMENT_PRECISION = {
    "inundation_overlap": 3,
    "flow_area_overlap": 3,
    "top_width_agreement": 3,
    "flow_area_agreement": 3,
    "hydraulic_radius_agreement": 3,
    "mean": 2,
    "std": 2,
    "max": 2,
    "min": 2,
    "p_25": 2,
    "p_50": 2,
    "p_75": 2,
    "rmse": 2,
    "normalized_rmse": 3,
    "r_squared": 3,
    "spectral_angle": 3,
    "spectral_correlation": 3,
    "correlation": 3,
    "max_cross_correlation": 3,
    "thalweg_elevation_difference": 2,
}
keys = list(TERRAIN_AGREEMENT_PRECISION.keys())
for k in keys:
    TERRAIN_AGREEMENT_PRECISION[f"avg_{k}"] = TERRAIN_AGREEMENT_PRECISION[k]
    TERRAIN_AGREEMENT_PRECISION[f"max_el_residuals_{k}"] = TERRAIN_AGREEMENT_PRECISION[k]

DEFAULT_MAX_WALK = 3e4
