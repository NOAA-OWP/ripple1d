"""RASMap Contents."""

from .consts import TERRAIN_NAME, TERRAIN_PATH

RASMAP_631 = r"""<RASMapper>
  <Version>2.0.0</Version>
  <Features>
    <Layer Name="Profile Lines" Type="PolylineFeatureLayer" Filename=".\Features\Profile Lines.shp">
      <LabelFeatures Checked="True" PercentPosition="0" rows="1" cols="1" r0c0="Name" Position="3" Color="-16777216" FontSize="8.25" />
    </Layer>
  </Features>
  <Geometries>
    <Layer Name="geom_name_placeholder" Type="RASGeometry" Expanded="True" Filename=".\geom_hdf_placeholder">
      <Layer Type="RASXS" UnitsRiverStation="Feet" RiverStationDecimalPlaces="0" />
      <Layer Type="FinalNValueLayer">
        <ResampleMethod>near</ResampleMethod>
        <Surface On="True" />
      </Layer>
      <Layer Name="TEMP N-Value Rasterizer" Type="InterpretationRasterizerLayer">
        <ResampleMethod>near</ResampleMethod>
        <Surface On="True" />
      </Layer>
      <Layer Name="Final Values" Type="InterpretationRasterizerLayer">
        <ResampleMethod>near</ResampleMethod>
        <Surface On="True" />
      </Layer>
      <Layer Name="Final Values" Type="InterpretationRasterizerLayer">
        <ResampleMethod>near</ResampleMethod>
        <Surface On="True" />
      </Layer>
    </Layer>
  </Geometries>
  <EventConditions />
  <Results />
  <MapLayers />
  <Terrains />
  <CurrentView>
    <MaxX>3763908.23</MaxX>
    <MinX>3747983.37</MinX>
    <MaxY>10229616.82</MaxY>
    <MinY>10223235.59</MinY>
  </CurrentView>
  <VelocitySettings>
    <Density>1.5</Density>
    <Lifetime>100</Lifetime>
    <Radius>0.75</Radius>
    <Method>2</Method>
    <Timestep>1</Timestep>
    <StaticColor>Black</StaticColor>
    <SpeedRelativeToZoom>False</SpeedRelativeToZoom>
  </VelocitySettings>
  <AnimationSettings>
    <DelayTimer>0</DelayTimer>
  </AnimationSettings>
  <Units>US Customary</Units>
  <RenderMode>sloping</RenderMode>
  <ReduceShallowToHorizontal>true</ReduceShallowToHorizontal>
  <MarksWarpMethod>False</MarksWarpMethod>
  <CurrentSettings>
    <ProjectSettings>
      <RiverStationUnits>Feet</RiverStationUnits>
      <RiverStationDecimalPlaces>0</RiverStationDecimalPlaces>
      <HorizontalDecimalPlaces>1</HorizontalDecimalPlaces>
      <VerticalDecimalPlaces>2</VerticalDecimalPlaces>
      <XSMaxPoints>450</XSMaxPoints>
      <LSMaxPoints>1000</LSMaxPoints>
      <ProfilePointMinCount>0</ProfilePointMinCount>
      <ShowLegend>True</ShowLegend>
      <ShowNorthArrow>False</ShowNorthArrow>
      <ShowScaleBar>True</ShowScaleBar>
      <ShowGreaterThanInLegend>False</ShowGreaterThanInLegend>
      <MeshTolerance_MinFaceLength>0.05</MeshTolerance_MinFaceLength>
      <MeshTolerance_EnsureCellPoint>False</MeshTolerance_EnsureCellPoint>
    </ProjectSettings>
    <Folders />
  </CurrentSettings>
</RASMapper>"""

TERRAIN = rf"""  <Terrains Checked="True" Expanded="True">
    <Layer Name="{TERRAIN_NAME}" Type="TerrainLayer" Checked="True" Filename=".\{TERRAIN_PATH}">
      <Symbology>
        <SurfaceFill Colors="-10039894,-256,-16744448,-23296,-7667712,-5952982,-8355712,-1286" Values="-0.625,50.4063185155618,70.7601808393692,101.843506660136,135.379737550828,199.971461686389,225.529762277054,366.78125" Stretched="True" AlphaTag="255" UseDatasetMinMax="False" RegenerateForScreen="False" />
      </Symbology>
      <ResampleMethod>near</ResampleMethod>
      <Surface On="True" />
    </Layer>
  </Terrains>"""

PLAN = r"""  <Results Expanded="True">
    <Layer Name="plan_name_placeholder" Type="RASResults" Expanded="True" Filename=".\plan_hdf_placeholder">
      <Layer Name="Event Conditions" Type="RASEventConditions" Filename=".\plan_hdf_placeholder">
        <Layer Name="Wind Layer" Type="ResultWindLayer" Filename=".\plan_hdf_placeholder">
          <ResampleMethod>near</ResampleMethod>
          <Surface On="True" />
          <Metadata BandIndex="0" SubDataset="" />
        </Layer>
      </Layer>
      <Layer Type="RASGeometry" Filename=".\plan_hdf_placeholder">
        <Layer Type="RASXS" UnitsRiverStation="Feet" RiverStationDecimalPlaces="0" />
        <Layer Type="RASD2FlowArea">
          <DataColumnGroupVisibilities>
            <DataColumnGroupVisibility DisplayName="Feature Parameters" IsVisible="False" />
          </DataColumnGroupVisibilities>
        </Layer>
        <Layer Type="MeshPerimeterLayer">
          <DataColumnGroupVisibilities>
            <DataColumnGroupVisibility DisplayName="Feature Parameters" IsVisible="True" />
          </DataColumnGroupVisibilities>
        </Layer>
        <Layer Name="Culvert Groups" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="" />
        <Layer Name="Culvert Barrels" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Bridges/Culverts" />
        <Layer Name="Gate Groups" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="" />
        <Layer Name="Gate Openings" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Inline Structures" />
        <Layer Name="Culvert Groups" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="" />
        <Layer Name="Culvert Barrels" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Inline Structures" />
        <Layer Name="Rating Curve Outlets" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Inline Structures" />
        <Layer Name="Outlet Time Series" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Inline Structures" />
        <Layer Name="Gate Groups" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="" />
        <Layer Name="Gate Openings" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Lateral Structures" />
        <Layer Name="Culvert Groups" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="" />
        <Layer Name="Culvert Barrels" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Lateral Structures" />
        <Layer Name="Rating Curve Outlets" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Lateral Structures" />
        <Layer Name="Outlet Time Series" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="Lateral Structures" />
        <Layer Name="Gate Groups" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="" />
        <Layer Name="Gate Openings" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="SA/2D Connections" />
        <Layer Name="Culvert Groups" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="" />
        <Layer Name="Culvert Barrels" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="SA/2D Connections" />
        <Layer Name="Rating Curve Outlets" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="SA/2D Connections" />
        <Layer Name="Outlet Time Series" Type="VirtualGeometryFeatureLayer" ParentIdentifiers="SA/2D Connections" />
        <Layer Type="StructureLayer" UnitsRiverStation="Feet" RiverStationDecimalPlaces="0" />
        <Layer Type="FinalNValueLayer">
          <ResampleMethod>near</ResampleMethod>
          <Surface On="True" />
        </Layer>
        <Layer Name="TEMP N-Value Rasterizer" Type="InterpretationRasterizerLayer">
          <ResampleMethod>near</ResampleMethod>
          <Surface On="True" />
        </Layer>
        <Layer Name="Final Values" Type="InterpretationRasterizerLayer">
          <ResampleMethod>near</ResampleMethod>
          <Surface On="True" />
        </Layer>
        <Layer Name="Final Values" Type="InterpretationRasterizerLayer">
          <ResampleMethod>near</ResampleMethod>
          <Surface On="True" />
        </Layer>
      </Layer>
      <Layer Name="Depth" Type="RASResultsMap">
        <MapParameters MapType="depth" ProfileIndex="0" ProfileName="profile_placeholder" />
      </Layer>
      <Layer Name="Velocity" Type="RASResultsMap">
        <MapParameters MapType="velocity" ProfileIndex="0" ProfileName="profile_placeholder" />
      </Layer>
      <Layer Name="WSE" Type="RASResultsMap">
        <MapParameters MapType="elevation" ProfileIndex="0" ProfileName="profile_placeholder" />
      </Layer>
  </Results>"""
