import json
from collections import defaultdict
from typing import OrderedDict

import contextily as ctx
import fiona
import geopandas as gpd
import matplotlib.patheffects as path_effects
import matplotlib.pyplot as plt
import numpy as np
import requests
import shapely
from flask import request
from pyproj import CRS
from shapely import LineString, Point, distance

from ripple1d.ops.subset_gpkg import RippleGeopackageSubsetter


class Plotter:
    """Generates plots for conflated HEC-RAS models."""

    custom_colors: list[str] = [
        "#01FF70",
        "#FFDC00",
        "#FF851B",
        "#FF4136",
        "#F012BE",
        "#B10DC9",
    ]
    # colors from CSS at https://robert-96.github.io/gimp-color-palettes/
    layer_colors: OrderedDict = OrderedDict(
        {
            "Junction": {"color": "red", "label": "HEC-RAS Junction", "lw": 2, "zorder": 3},
            "River": {"color": "#03c2fc", "label": "HEC-RAS River", "lw": 2},
            "XS": {"color": "#0ffc03", "label": "HEC-RAS Cross Sections", "lw": 2},
            "nwm": {"lw": 2},
        }
    )
    width: float = 6.0
    rows: int = 3

    def __init__(self, ras_path: str, nwm_path: str, conflation_path: str):
        self.ras_path = ras_path
        self.nwm_path = nwm_path
        self.conflation_path = conflation_path
        self.fig, self.axs = plt.subplots(nrows=self.rows, sharex=True, sharey=True)

        self.gdfs: dict[gpd.GeoDataFrame] = None
        self.crs: CRS = None
        self.nwm: gpd.GeoDataFrame = None
        self.nwm_endpts: gpd.GeoDataFrame = None
        self.conflation: dict = None
        self.load_data()

    def load_data(self):
        self.gdfs = {l: gpd.read_file(self.ras_path, layer=l) for l in fiona.listlayers(self.ras_path)}
        self.crs = self.gdfs[next(iter(self.gdfs))].crs
        self.nwm = gpd.read_parquet(self.nwm_path).to_crs(self.crs)
        self.nwm_endpts = self.nwm.copy()
        self.nwm_endpts["geometry"] = shapely.get_point(self.nwm.geometry, 0)
        with open(self.conflation_path) as f:
            self.conflation = json.load(f)

    def make_plot(self):
        self.add_default_ras_data()
        self.add_default_nwm_data()
        self.add_conflated_data()
        self.apply_formatting()

    @property
    def nwm_reaches(self) -> list:
        return self.nwm["ID"].astype(str).values[::-1]

    @property
    def out_path(self) -> str:
        return self.conflation_path.replace(".json", ".jpg")

    def add_default_ras_data(self):
        for layer, kwargs in self.layer_colors.items():
            if layer in self.gdfs.keys():
                self.gdfs[layer].plot(ax=self.axs[0], **kwargs)
        self.gdfs["River"].plot(ax=self.axs[2], color="k", lw=1, label="HEC-RAS River")

    def add_default_nwm_data(self):
        for ind, r in enumerate(self.nwm["ID"].astype(str).values[::-1]):
            color = self.custom_colors[ind]
            sub_nwm = self.nwm[self.nwm["ID"] == int(r)]
            sub_nwm_endpts = self.nwm_endpts[self.nwm_endpts["ID"] == int(r)]
            sub_nwm.plot(ax=self.axs[1], **self.layer_colors["nwm"], label=f"{r}", color=color)
            sub_nwm_endpts.plot(ax=self.axs[1], **self.layer_colors["nwm"], color=color)

    def divide_section(self, line, splits):
        subs = []
        dists = np.linspace(0, line.length, splits + 1, endpoint=True)[1:]
        pt1 = Point(line.coords[0])
        d = 0
        segment = 0
        cur_coords = [pt1]
        ind = 1
        while ind <= len(line.coords):
            pt2 = Point(line.coords[ind])
            inc_dist = distance(pt1, pt2)
            new_d = d + inc_dist
            if new_d - dists[segment] > 1:
                new_pt = Point(line.interpolate(dists[segment]))
                cur_coords.append(new_pt)
                sub = LineString(cur_coords)
                subs.append(sub)
                cur_coords = [new_pt]
                pt1 = new_pt
                d = dists[segment]
                segment += 1
            elif abs(new_d - dists[segment]) < 1:
                cur_coords.append(pt2)
                sub = LineString(cur_coords)
                subs.append(sub)
                cur_coords = [pt2]
                pt1 = pt2
                d = dists[segment]
                segment += 1
                ind += 2
            else:
                cur_coords.append(pt2)
                pt1 = pt2
                d += inc_dist
                ind += 1
        return subs

    def clip_xs(self, subset, overlaps, r):
        for xs_id in subset["source_river_reach_rs"]:
            if xs_id in list(overlaps.keys()):
                splits = len(overlaps[xs_id])
                position = overlaps[xs_id].index(r)
                geom = subset.loc[subset["source_river_reach_rs"] == xs_id, "geometry"].values[0]
                subset.loc[subset["source_river_reach_rs"] == xs_id, "geometry"] = self.divide_section(geom, splits)[
                    position
                ]
        return subset

    def format_source_id(self, row: dict) -> str:
        return " ".join([str(row["source_river"]), str(row["source_reach"]), str(row["source_river_station"])])

    def generate_subset_gdfs(self) -> dict:
        # Create combo gdf
        subset_gdfs = {}
        overlaps = defaultdict(list)
        for r in self.conflation["reaches"]:
            if self.conflation["reaches"][r]["eclipsed"]:
                continue
            subsetter = RippleGeopackageSubsetter(self.ras_path, self.conflation_path, None, r)
            subset = subsetter.subset_xs
            subset["source_river_reach_rs"] = subset.apply(self.format_source_id, axis=1)
            subset_gdfs[r] = subset

            # log xs for duplicate detection
            for xs_id in subset["source_river_reach_rs"]:
                overlaps[xs_id].append(r)

        # split duplicated section geometry
        overlaps = {k: v for k, v in overlaps.items() if len(v) > 1}
        for r in subset_gdfs:
            subset_gdfs[r] = self.clip_xs(subset_gdfs[r], overlaps, r)

        return subset_gdfs

    def add_conflated_data(self):
        subset_gdfs = self.generate_subset_gdfs()
        for ind, r in enumerate(self.nwm_reaches):
            if r not in self.conflation["reaches"]:
                continue
            color = self.custom_colors[ind]
            if self.conflation["reaches"][r]["eclipsed"]:
                ls = "dashed"
                label = f"{r} (eclipsed)"
            else:
                ls = "solid"
                label = f"{r}"

                subset = subset_gdfs[r]
                subset.plot(ax=self.axs[2], color="k", lw=3, zorder=2)
                subset.plot(ax=self.axs[2], color=color, lw=2, zorder=2)

            sub_nwm = self.nwm[self.nwm["ID"] == int(r)]
            sub_nwm_endpts = self.nwm_endpts[self.nwm_endpts["ID"] == int(r)]
            sub_nwm.plot(ax=self.axs[2], **self.layer_colors["nwm"], label=label, ls=ls, color=color, zorder=1)
            sub_nwm_endpts.plot(ax=self.axs[2], **self.layer_colors["nwm"], color=color, zorder=1)

    def apply_formatting(self):
        # Add labels
        txt = self.axs[0].text(
            0.05, 0.95, "HEC-RAS Source Model", transform=self.axs[0].transAxes, va="top", ha="left", fontsize=8
        )
        txt.set_path_effects([path_effects.withStroke(linewidth=3, foreground="white")])
        txt = self.axs[1].text(
            0.05, 0.95, "NWM Reaches", transform=self.axs[1].transAxes, va="top", ha="left", fontsize=8
        )
        txt.set_path_effects([path_effects.withStroke(linewidth=3, foreground="white")])
        txt = self.axs[2].text(
            0.05, 0.95, "Conflated Sub Models", transform=self.axs[2].transAxes, va="top", ha="left", fontsize=8
        )
        txt.set_path_effects([path_effects.withStroke(linewidth=3, foreground="white")])

        # Format
        y1 = self.axs[0].get_ylim()[0]
        y2 = self.axs[0].get_ylim()[1]
        x1 = self.axs[0].get_ylim()[0]
        x2 = self.axs[0].get_ylim()[1]
        width = x2 - x1
        self.axs[0].set_ylim(y1 - (1 * (width / self.width)), y2)
        for ax in self.axs:
            # Add an extra 10% for the legend
            ax.legend(fontsize="x-small", loc="lower right", ncols=len(self.nwm_reaches) + 1)
            ax.set_axis_off()
            try:
                ctx.add_basemap(ax, crs=self.crs, source=ctx.providers.USGS.USImagery, attribution=False)
            except requests.exceptions.HTTPError as e:
                try:
                    ctx.add_basemap(ax, crs=self.crs, source=ctx.providers.Esri.WorldStreetMap, attribution=False)
                except requests.exceptions.HTTPError as e:
                    ctx.add_basemap(ax, crs=self.crs, source=ctx.providers.OpenStreetMap.Mapnik, attribution=False)

        height = abs(self.axs[0].get_ylim()[0] - self.axs[0].get_ylim()[1])
        width = abs(self.axs[0].get_xlim()[0] - self.axs[0].get_xlim()[1])
        ar = (height) / width
        self.fig.set_size_inches(self.width, 3 * ar * self.width, forward=True)
        self.fig.tight_layout()

    def export(self) -> str:
        self.fig.savefig(self.out_path, dpi=300)
        plt.close(self.fig)
        return self.out_path


def plot_conflation(ras_path: str, nwm_path: str, conflation_path: str) -> str:
    """Create summary png for conflation results."""
    plotter = Plotter(ras_path, nwm_path, conflation_path)
    plotter.make_plot()
    return plotter.export()
