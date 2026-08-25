"""Constants."""

import datetime
import json

from shapely import Polygon, box, to_geojson

NULL_DATETIME = datetime.datetime(9999, 9, 9)
NULL_GEOMETRY = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
NULL_STAC_GEOMETRY = json.loads(to_geojson(NULL_GEOMETRY))
NULL_BBOX = box(0, 0, 1, 1)
NULL_STAC_BBOX = NULL_BBOX.bounds
PLACEHOLDER_ID = "id"
