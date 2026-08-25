"""Unit tests for the flow-conditioned known-water-surface-elevation helpers.

These exercise the pure functions in ripple1d.ops.ras_run and do not require
HEC-RAS.
"""

import pandas as pd
import pytest

from ripple1d.consts import MIN_FLOW
from ripple1d.ops.ras_run import (
    ALLOWED_DEPTH_INCREMENTS,
    create_flow_wse_scenarios_matrix,
    stepwise_floor_lookup,
)

# tailwater lower-bound curve used across the tests: [discharge, wse]
CURVE = [[100, 20], [200, 24], [256, 26]]


def test_stepwise_floor_lookup_within_and_at_boundaries():
    # a flow lands in the [greatest tabulated discharge <= flow] band
    assert stepwise_floor_lookup(150, CURVE) == 20
    assert stepwise_floor_lookup(205, CURVE) == 24
    # a flow exactly on a tabulated discharge uses that row (<= is inclusive)
    assert stepwise_floor_lookup(100, CURVE) == 20
    assert stepwise_floor_lookup(200, CURVE) == 24
    assert stepwise_floor_lookup(256, CURVE) == 26


def test_stepwise_floor_lookup_clamps_below_and_holds_above():
    # below the lowest tabulated discharge -> clamp to the lowest floor
    assert stepwise_floor_lookup(0, CURVE) == 20
    assert stepwise_floor_lookup(60, CURVE) == 20
    # above the highest tabulated discharge -> hold the highest floor
    assert stepwise_floor_lookup(300, CURVE) == 26


def test_stepwise_floor_lookup_sorts_unordered_curve():
    unordered = [[256, 26], [100, 20], [200, 24]]
    assert stepwise_floor_lookup(205, unordered) == 24
    assert stepwise_floor_lookup(50, unordered) == 20


def test_create_flow_wse_scenarios_matrix_sweeps_each_flow_from_its_floor():
    ds_flows = pd.Series([150, 205])
    depths, flows, wses = create_flow_wse_scenarios_matrix(
        ds_flows,
        min_elevation_curve=CURVE,
        max_elevation=30,
        depth_increment=2,
        thalweg=10,
    )

    # flow 150 -> floor 20, swept 20..30; flow 205 -> floor 24, swept 24..30
    assert wses == [20, 22, 24, 26, 28, 30, 24, 26, 28, 30]
    assert flows == [150] * 6 + [205] * 4
    # depth is wse - thalweg
    assert depths == [10, 12, 14, 16, 18, 20, 14, 16, 18, 20]
    # parallel lists stay aligned
    assert len(depths) == len(flows) == len(wses)


def test_create_flow_wse_scenarios_matrix_keeps_all_flows_on_one_absolute_grid():
    # The grid is absolute multiples of depth_increment (whole feet for Δz=1), so two
    # flows with unrelated floors still share one lattice. Each flow's floor is rounded
    # to the nearest grid line: 21.5 rounds up to 22, not onto a private half-foot phase.
    ds_flows = pd.Series([150, 250])
    _, flows, wses = create_flow_wse_scenarios_matrix(
        ds_flows,
        min_elevation_curve=[[100, 20.0], [200, 21.5]],
        max_elevation=24,
        depth_increment=1,
        thalweg=0,
    )

    # every wse lands on the same absolute lattice (whole feet) -> not fragmented
    assert len({round(w % 1, 6) for w in wses}) == 1
    assert min(w for f, w in zip(flows, wses) if f == 250) == 22.0
    assert wses == [20, 21, 22, 23, 24, 22, 23, 24]


def test_create_flow_wse_scenarios_matrix_rounds_bounds_to_nearest_grid_line():
    # floor 20.3 -> nearest grid line 20; floor 20.6 -> nearest grid line 21 (round half up)
    ds_flows = pd.Series([150, 250])
    _, flows, wses = create_flow_wse_scenarios_matrix(
        ds_flows,
        min_elevation_curve=[[100, 20.3], [200, 20.6]],
        max_elevation=25,
        depth_increment=1,
        thalweg=0,
    )
    first = {}
    for f, w in zip(flows, wses):
        first.setdefault(f, w)
    assert first[150] == 20.0
    assert first[250] == 21.0


def test_create_flow_wse_scenarios_matrix_matches_worked_example():
    # d/s WSEL range 224.2 (floor) -> 227.1 (ceiling), swept at each allowed increment.
    ds_flows = pd.Series([150])

    def rungs(inc):
        _, _, wses = create_flow_wse_scenarios_matrix(
            ds_flows, min_elevation_curve=[[100, 224.2]], max_elevation=227.1, depth_increment=inc, thalweg=0
        )
        return [round(w, 1) for w in wses]

    assert rungs(0.5) == [224.0, 224.5, 225.0, 225.5, 226.0, 226.5, 227.0]
    assert rungs(1) == [224.0, 225.0, 226.0, 227.0]
    assert rungs(2) == [224.0, 226.0, 228.0]


def test_create_flow_wse_scenarios_matrix_grid_is_multiples_of_increment():
    ds_flows = pd.Series([150, 205, 300])
    for inc in ALLOWED_DEPTH_INCREMENTS:
        _, _, wses = create_flow_wse_scenarios_matrix(
            ds_flows, min_elevation_curve=CURVE, max_elevation=60, depth_increment=inc, thalweg=0
        )
        assert wses, f"expected rungs for increment {inc}"
        assert all(abs(w / inc - round(w / inc)) < 1e-6 for w in wses)


def test_create_flow_wse_scenarios_matrix_single_rung_when_floor_equals_ceiling():
    # when a flow's floor rounds to the same grid line as the ceiling, it yields one rung
    ds_flows = pd.Series([150])
    _, _, wses = create_flow_wse_scenarios_matrix(
        ds_flows, min_elevation_curve=[[100, 26.0]], max_elevation=26.0, depth_increment=1, thalweg=0
    )
    assert wses == [26.0]


def test_create_flow_wse_scenarios_matrix_emits_float_wses_for_integer_increment():
    # ras profile names do str(wse).replace(".", "_"); an int wse would name the library
    # "z_736" instead of "z_736_0", flows2fim wants z_736_0.
    ds_flows = pd.Series([150])
    _, _, wses = create_flow_wse_scenarios_matrix(
        ds_flows, min_elevation_curve=[[100, 735]], max_elevation=738, depth_increment=1, thalweg=0
    )
    assert all(isinstance(w, float) for w in wses)
    assert all("." in str(w) for w in wses)


def test_create_flow_wse_scenarios_matrix_rejects_disallowed_increment():
    ds_flows = pd.Series([150])
    with pytest.raises(ValueError, match="depth_increment must be one of"):
        create_flow_wse_scenarios_matrix(
            ds_flows, min_elevation_curve=CURVE, max_elevation=30, depth_increment=0.25, thalweg=0
        )


def test_create_flow_wse_scenarios_matrix_clamps_flow_to_min_flow():
    # a flow below MIN_FLOW is clamped up to MIN_FLOW in the emitted profiles
    ds_flows = pd.Series([0])
    _, flows, _ = create_flow_wse_scenarios_matrix(
        ds_flows,
        min_elevation_curve=CURVE,
        max_elevation=22,
        depth_increment=2,
        thalweg=0,
    )
    assert set(flows) == {MIN_FLOW}
