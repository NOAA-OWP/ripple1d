"""HEC-RAS STAC Item class."""

from __future__ import annotations

import datetime
import json
import logging
import traceback
from functools import cached_property
from pathlib import Path
from typing import Literal, Optional

import pystac
import pystac.errors
from pyproj import CRS
from pystac import Asset, Item
from pystac.extensions.projection import ProjectionExtension
from pystac.utils import datetime_to_str
from shapely import Polygon, simplify, to_geojson, union_all
from shapely.geometry import shape
from typing_extensions import Self

import ripple1d
from ripple1d.hecstac.common.asset_factory import AssetFactory
from ripple1d.hecstac.common.base_io import ModelFileReaderError
from ripple1d.hecstac.common.path_manager import LocalPathManager
from ripple1d.hecstac.ras.assets import (
    RAS_EXTENSION_MAPPING,
    GeometryAsset,
    GeometryHdfAsset,
    PlanAsset,
    ProjectAsset,
    QuasiUnsteadyFlowAsset,
    SteadyFlowAsset,
    UnsteadyFlowAsset,
)
from ripple1d.hecstac.ras.consts import NULL_DATETIME, NULL_STAC_BBOX, NULL_STAC_GEOMETRY
from ripple1d.hecstac.ras.parser import ProjectFile
from ripple1d.hecstac.ras.utils import find_model_files

ThumbnailLayers = Literal["mesh_areas", "breaklines", "bc_lines", "River", "XS", "Structure", "Junction"]


class RASModelItem(Item):
    """An object representation of a HEC-RAS model."""

    PROJECT_KEY = "HEC-RAS:project"
    PROJECT_TITLE_KEY = "HEC-RAS:project_title"
    MODEL_UNITS_KEY = "HEC-RAS:unit_system"
    MODEL_GAGES_KEY = "HEC-RAS:gages"  # TODO: Is this deprecated?
    PROJECT_VERSION_KEY = "HEC-RAS:version"
    PROJECT_DESCRIPTION_KEY = "HEC-RAS:description"
    PROJECT_STATUS_KEY = "HEC-RAS:status"
    RAS_HAS_1D_KEY = "HEC-RAS:has_1d"
    RAS_HAS_2D_KEY = "HEC-RAS:has_2d"
    RAS_DATETIME_SOURCE_KEY = "HEC-RAS:datetime_source"
    HECSTAC_VERSION_KEY = "HEC-RAS:hecstac_version"

    def __init__(self, *args, **kwargs):
        """Add a few default properties to the base class."""
        super().__init__(*args, **kwargs)
        self.simplify_geometry = True

    @classmethod
    def from_prj(
        cls,
        ras_project_file: str,
        stac_id: str = None,
        crs: str = None,
        simplify_geometry: bool = True,
        assets: list = None,
    ) -> Self:
        """Create a STAC item from a HEC-RAS .prj file.

        Parameters
        ----------
        ras_project_file : str
            Path to the HEC-RAS project file (.prj).
        stac_id : str
            ID for the STAC item. If none, ID is set to the .prj file stem (e.g., Muncie.prj -> Muncie).
        crs : str, optional
            Coordinate reference system (CRS) to apply to the item. If None, the CRS will be extracted from the geometry .hdf file.
        simplify_geometry : bool, optional
            Whether to simplify geometry. Defaults to True.
        assets : list, optional
            List of assets to include in the STAC item. If None, all model files will be included.

        Returns
        -------
        stac : RASModelItem
            An instance of the class representing the STAC item.

        """
        if not assets:
            assets = {Path(i).name: Asset(i, Path(i).name) for i in find_model_files(ras_project_file)}
        else:
            assets = {Path(i).name: Asset(i, Path(i).name) for i in assets}

        if not stac_id:
            stac_id = Path(ras_project_file).stem

        stac = cls(
            stac_id,
            NULL_STAC_GEOMETRY,
            NULL_STAC_BBOX,
            NULL_DATETIME,
            {cls.PROJECT_KEY: Path(ras_project_file).name},
            href=ras_project_file.replace(".prj", ".json").replace(".PRJ", ".json"),
            assets=assets,
        )
        if crs:
            stac.crs = crs
        stac.simplify_geometry = simplify_geometry
        stac.update_properties()

        return stac

    @classmethod
    def from_dict(cls, stac: dict) -> Self:
        """Load a model from a stac item dictionary."""
        item = super().from_dict(stac)
        item.update_properties()
        return item

    @cached_property
    def factory(self) -> AssetFactory:
        """Return AssetFactory for this item."""
        return AssetFactory(RAS_EXTENSION_MAPPING)

    @cached_property
    def pm(self) -> LocalPathManager:
        """Get the path manager rooted at project file's href."""
        return LocalPathManager(str(Path(self.project_asset.href).parent))

    @cached_property
    def project_asset(self) -> ProjectAsset:
        """Find the project file for this model."""
        return [i for i in self.assets.values() if isinstance(i, ProjectAsset)][0]

    @cached_property
    def pf(self) -> ProjectFile:
        """Get a ProjectFile instance for the RAS Model .prj file."""
        return self.project_asset.file

    @cached_property
    def has_2d(self) -> bool:
        """Whether any geometry file has 2D elements."""
        return any([a.has_2d for a in self.geometry_assets])

    @cached_property
    def has_1d(self) -> bool:
        """Whether any geometry file has 2D elements."""
        return any([a.has_1d for a in self.geometry_assets])

    @cached_property
    def geometry_assets(self) -> list[GeometryHdfAsset | GeometryAsset]:
        """Return any RasGeomHdf in assets."""
        return [a for a in self.assets.values() if isinstance(a, (GeometryHdfAsset, GeometryAsset))]

    @cached_property
    def plan_assets(self) -> list[PlanAsset]:
        """Return any RasGeomHdf in assets."""
        return [a for a in self.assets.values() if isinstance(a, PlanAsset)]

    @property
    def crs(self) -> CRS:
        """Get the authority code for the model CRS."""
        try:
            return CRS(self.ext.proj.wkt2)
        except pystac.errors.ExtensionNotImplemented:
            return None

    @crs.setter
    def crs(self, crs):
        """Apply the projection extension to this item given a CRS."""
        prj_ext = ProjectionExtension.ext(self, add_if_missing=True)
        crs = CRS(crs)
        if crs.to_authority() is not None:
            auth = ":".join(crs.to_authority())
        else:
            auth = None
        prj_ext.apply(code=auth, wkt2=crs.to_wkt())

    @property
    def geometry(self) -> dict:
        """Return footprint of model as a geojson."""
        if hasattr(self, "_geometry_cached"):
            return self._geometry_cached

        if self.crs is None:
            self._geometry_cached = NULL_STAC_GEOMETRY
            return self._geometry_cached

        if len(self.geometry_assets) == 0:
            self._geometry_cached = NULL_STAC_GEOMETRY
            return self._geometry_cached

        geometries = []
        for i in self.geometry_assets:
            try:
                geometries.append(i.geometry_wgs84)
            except Exception:
                continue

        unioned_geometry = union_all(geometries)
        if self.simplify_geometry:
            unioned_geometry = simplify(unioned_geometry, 0.001)
            if isinstance(unioned_geometry, Polygon):
                if unioned_geometry.interiors:
                    unioned_geometry = Polygon(list(unioned_geometry.exterior.coords))

        self._geometry_cached = json.loads(to_geojson(unioned_geometry))
        return self._geometry_cached

    @geometry.setter
    def geometry(self, val):
        """Ignore external setting of geometry."""
        pass

    @property
    def bbox(self) -> list[float]:
        """Get the bounding box of the model geometry."""
        return list(shape(self.geometry).bounds)

    @bbox.setter
    def bbox(self, val):
        """Ignore external setting of bbox."""
        pass

    def to_dict(self, *args, lightweight=True, **kwargs):
        """Preload fields before serializing to dict.

        If lightweight=True, skip loading heavy geometry assets.
        """
        if not lightweight:
            _ = self.geometry
            _ = self.bbox
        _ = self.datetime
        _ = self.properties
        return super().to_dict(*args, **kwargs)

    def to_file(self, *args, out_path: str = None, lightweight=True, **kwargs) -> None:
        """Save the item to it's self href."""
        d = self.to_dict(*args, lightweight=lightweight, **kwargs)
        if out_path is None:
            out_path = self.get_self_href()
        with open(out_path, mode="w") as f:
            json.dump(d, f, indent=4)
        return out_path

    def update_properties(self) -> dict:
        """Force recalculation of HEC-RAS properties."""
        self.properties[self.PROJECT_KEY] = self.project_asset.name
        self.properties[self.RAS_HAS_1D_KEY] = self.has_1d
        self.properties[self.RAS_HAS_2D_KEY] = self.has_2d
        self.properties[self.PROJECT_TITLE_KEY] = self.pf.project_title
        self.properties[self.PROJECT_VERSION_KEY] = self.project_version
        self.properties[self.PROJECT_DESCRIPTION_KEY] = self.pf.project_description
        self.properties[self.PROJECT_STATUS_KEY] = self.pf.project_status
        self.properties[self.MODEL_UNITS_KEY] = self.pf.project_units
        self.properties[self.HECSTAC_VERSION_KEY] = ripple1d.__version__

        datetimes = self.model_datetime
        if len(datetimes) > 1:
            self.properties["start_datetime"] = datetime_to_str(min(datetimes))
            self.properties["end_datetime"] = datetime_to_str(max(datetimes))
            self.properties[self.RAS_DATETIME_SOURCE_KEY] = "model_geometry"
            self.datetime = None
        elif len(datetimes) == 1:
            self.datetime = datetimes[0]
            self.properties[self.RAS_DATETIME_SOURCE_KEY] = "model_geometry"
        else:
            self.datetime = datetime.datetime.now()
            self.properties[self.RAS_DATETIME_SOURCE_KEY] = "processing_time"

    @cached_property
    def project_version(self):
        """Attempt to return the geometry used to perform the last update on the primary geometry file."""
        if self._primary_geometry is not None:
            return self._primary_geometry.file.file_version
        else:
            return None

    @cached_property
    def model_datetime(self) -> list[datetime.datetime]:
        """Parse datetime from model geometry and return result."""
        datetimes = []
        for i in self.geometry_assets:
            dt = i.file.geometry_time
            if dt is None:
                continue
            if isinstance(dt, list):
                datetimes.extend([t for t in dt if t])
            elif isinstance(dt, datetime.datetime):
                datetimes.append(dt)

        return list(set(datetimes))

    def add_model_thumbnails(
        self,
        layers: list[ThumbnailLayers],
        thumbnail_dest: str,
        title_prefix: str = "Model_Thumbnail",
        make_public: bool = True,
    ):
        """Generate model thumbnail asset for each geometry file.

        Parameters
        ----------
        layers : list
            List of geometry layers to be included in the plot. Options include 'mesh_areas', 'breaklines', 'bc_lines',
            'River', 'XS', 'Structure', and 'Junction'
        thumbnail_dest : str, optional
            Directory for created thumbnails.
        title_prefix : str, optional
            Thumbnail title prefix, by default "Model_Thumbnail".
        make_public : bool, optional
            Whether to use public-style url for created assets.

        """
        for geom in self.geometry_assets:
            if (not geom.title.startswith(self.id)) and (not geom.title.lower().startswith("backup")):
                continue
            # Conditions
            is_hdf = isinstance(geom, GeometryHdfAsset)
            is_text = isinstance(geom, GeometryAsset)
            has_hdf = any([i.href == geom.href + ".hdf" for i in self.geometry_assets])
            has_text = is_hdf and any([i.href == geom.href.replace(".hdf", "") for i in self.geometry_assets])
            has_1d = geom.has_1d

            if is_text and has_hdf:
                # TODO: right now since hdf thumbs don't have 1D elements, we need to run all 1D through regular geom.
                if has_1d:
                    make = True
                else:
                    make = False
            elif is_text and not has_hdf:
                make = True
            elif is_hdf:
                if has_1d and has_text:
                    make = False
                else:
                    make = True

            if is_hdf and make:
                if not any([i in layers for i in ["mesh_areas", "breaklines", "bc_lines"]]):
                    continue
                self.assets[f"{geom.href.rsplit('/')[-1][:-4]}_thumbnail"] = geom.thumbnail(
                    layers=layers, title=title_prefix, thumbnail_dest=thumbnail_dest, make_public=make_public
                )
            elif is_text and make:
                if not any([i in layers for i in ["River", "XS", "Structure", "Junction"]]):
                    continue
                self.assets[f"{geom.href.rsplit('/')[-1]}_thumbnail"] = geom.thumbnail(
                    layers=layers, title=title_prefix, thumbnail_dest=thumbnail_dest, make_public=make_public
                )

    def add_model_geopackages(self, dst: str, geometries: list = None, make_public: bool = True, metadata: dict = {}):
        """Generate model geopackage asset for each geometry file.

        Parameters
        ----------
        dst : str, optional
            Directory for created geopackages.
        geometries : list, optional
            A list of geometry file names to make the gpkg for.
        make_public : bool, optional
            Whether to use public-style url for created assets.

        """
        metadata = self.gpkg_metadata | metadata
        for geom in self.geometry_assets:
            if geometries is not None and geom.name not in geometries:
                continue
            asset_name = f"{geom.href.rsplit('/')[-1]}_geopackage"
            if isinstance(geom, GeometryAsset):
                try:
                    if isinstance(self._primary_flow, SteadyFlowAsset):
                        flow_file = self._primary_flow.file
                    else:
                        flow_file = None
                    self.assets[asset_name] = geom.geopackage(dst, metadata, flow_file, make_public=make_public)
                except Exception as e:
                    logging.error(f"Error on {geom.name}: {str(e)}")
                    logging.error(str(traceback.format_exc()))
        return metadata

    @cached_property
    def _primary_plan(self) -> Optional[PlanAsset]:
        """Primary plan for use in Ripple1D."""  # TODO: develop test for this logic. easily tested
        if len(self.plan_assets) == 0:
            return None
        elif len(self.plan_assets) == 1:
            return self.plan_assets[0]

        candidate_plans = [i for i in self.plan_assets if not i.file.is_encroached]

        if len(candidate_plans) > 1:
            cur_plan = [i for i in candidate_plans if i.name == self.pf.plan_current]
            if len(cur_plan) == 1:
                return cur_plan[0]
            else:
                return candidate_plans[0]
        elif len(candidate_plans) == 0:
            return self.plan_assets[0]
        else:
            return candidate_plans[0]

    @cached_property
    def _primary_flow(self) -> Optional[SteadyFlowAsset | UnsteadyFlowAsset | QuasiUnsteadyFlowAsset]:
        """Flow asset listed in the primary plan."""
        for i in self.assets.values():
            if isinstance(i, (SteadyFlowAsset, UnsteadyFlowAsset, QuasiUnsteadyFlowAsset)):
                if i.name == self._primary_plan.file.flow_file:
                    return i
        return None

    @cached_property
    def _primary_geometry(self) -> Optional[GeometryAsset | GeometryHdfAsset]:
        """Geometry asset listed in the primary plan."""
        for i in self.assets.values():
            if isinstance(i, (GeometryAsset, GeometryHdfAsset)):
                if self._primary_plan is not None:
                    if i.name.startswith(self._primary_plan.file.geometry_file):
                        return i
                else:
                    return i
        return None

    @cached_property
    def gpkg_metadata(self) -> dict:
        """Generate metadata for the geopackage metadata table."""
        metadata = {}
        metadata["plans_files"] = "\n".join([i.name for i in self.assets.values() if isinstance(i, PlanAsset)])
        metadata["geom_files"] = "\n".join([i.name for i in self.geometry_assets])
        metadata["steady_flow_files"] = "\n".join(
            [i.name for i in self.assets.values() if isinstance(i, SteadyFlowAsset)]
        )
        metadata["unsteady_flow_files"] = "\n".join(
            [i.name for i in self.assets.values() if isinstance(i, UnsteadyFlowAsset)]
        )
        metadata["ras_project_file"] = self.properties[self.PROJECT_KEY]
        metadata["ras_project_title"] = self.pf.project_title
        metadata["plans_titles"] = "\n".join([i.title for i in self.assets if isinstance(i, PlanAsset)])
        metadata["geom_titles"] = "\n".join([i.title for i in self.geometry_assets])
        metadata["steady_flow_titles"] = "\n".join([i.title for i in self.assets if isinstance(i, SteadyFlowAsset)])
        metadata["active_plan"] = self.pf.plan_current
        metadata["primary_plan_file"] = self._primary_plan.name
        metadata["primary_plan_title"] = self._primary_plan.file.plan_title
        metadata["primary_flow_file"] = self._primary_flow.name
        metadata["primary_flow_title"] = self._primary_flow.file.flow_title
        metadata["primary_geom_file"] = self._primary_geometry.name
        metadata["primary_geom_title"] = self._primary_geometry.file.geom_title
        metadata["ras_version"] = self._primary_geometry.file.geom_version
        metadata["hecstac_version"] = ripple1d.__version__
        if isinstance(self._primary_flow, SteadyFlowAsset):
            metadata["profile_names"] = "\n".join(self._primary_flow.file.profile_names)
        else:
            metadata["profile_names"] = None
        metadata["units"] = self.pf.project_units
        return metadata

    def add_asset(self, key, asset):
        """Subclass asset then add, eagerly load metadata safely."""
        subclass = self.factory.asset_from_dict(asset)
        if subclass is None:
            return

        # Eager load extra fields
        try:
            _ = subclass.extra_fields
        except ModelFileReaderError as e:
            return

        # Safely load file only if __file_class__ is not None
        if getattr(subclass, "__file_class__", None) is not None:
            _ = subclass.file

        if self.crs is None and isinstance(subclass, GeometryHdfAsset) and subclass.file.projection is not None:
            self.crs = subclass.file.projection
        return super().add_asset(key, subclass)

    def _process_and_add_pq_asset(self, gdf, path, asset_key, title, description):
        if gdf is not None and not gdf.empty:
            gdf.to_parquet(path)
            self.add_asset(
                asset_key,
                Asset(
                    href=path,
                    title=title,
                    description=description,
                    media_type="application/x-parquet",
                    roles=["data"],
                ),
            )

    def add_geospatial_assets(self, output_prefix: str):
        """Extract geospatial data from geometry hdf asset and adds them as Parquet assets.

        Args:
            output_prefix (str): Path prefix where the Parquet files will be saved.

        """
        for i in self.geometry_assets:
            if isinstance(i, GeometryHdfAsset):
                self._process_and_add_pq_asset(
                    i.reference_lines_spatial(),
                    f"{output_prefix}/ref_lines.pq",
                    "ref_lines",
                    "Reference Lines",
                    "Parquet file containing model reference lines and their geometry.",
                )
                self._process_and_add_pq_asset(
                    i.reference_points_spatial(),
                    f"{output_prefix}/ref_points.pq",
                    "ref_points",
                    "Reference Points",
                    "Parquet file containing model reference points and their geometry.",
                )
                self._process_and_add_pq_asset(
                    i.bc_lines_spatial(),
                    f"{output_prefix}/bc_lines.pq",
                    "bc_lines",
                    "Boundary Condition Lines",
                    "Parquet file containing model boundary condition lines and their geometry.",
                )
                self._process_and_add_pq_asset(
                    i.model_perimeter(),
                    f"{output_prefix}/model_geometry.pq",
                    "model_geometry",
                    "Model Geometry",
                    "Parquet file containing model geometry.",
                )
                break
