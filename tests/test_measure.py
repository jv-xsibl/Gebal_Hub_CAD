"""Tests for Stage 10 deterministic CAD measurement."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import ezdxf
import pytest

from gebal_cad_normalizer.cad import (
    LayerClassificationConfig,
    MeasurementConfig,
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    measure_geometry,
    write_measurement_json,
)


def _doc(units: int | None = 0) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2010")
    if units is not None:
        doc.header["$INSUNITS"] = units
    return doc


def _save(doc: ezdxf.document.Drawing, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _measure(path: Path, **config_kwargs):
    inventory = inventory_dxf(path)
    canonical = canonicalize_dxf(path, tessellate_curves=True, tessellation_tolerance=config_kwargs.pop("curve_flattening_tolerance", 1.0))
    classification = classify_layers(inventory, canonical, LayerClassificationConfig(vendor_profile="bluestone_playground"))
    return measure_geometry(inventory, canonical, classification, MeasurementConfig(**config_kwargs))


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_closed_rectangular_product_footprint(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "rect.dxf"))
    candidate = result.candidates[0]

    assert candidate.role == "product_geometry"
    assert candidate.width == 100
    assert candidate.depth == 50
    assert candidate.area == 5000
    assert candidate.evidence.closed is True


def test_rotated_rectangle_width_depth_uses_axis_aligned_bounds(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    angle = math.radians(30)
    pts = []
    for x, y in [(-50, -20), (50, -20), (50, 20), (-50, 20)]:
        pts.append((x * math.cos(angle) - y * math.sin(angle), x * math.sin(angle) + y * math.cos(angle)))
    doc.modelspace().add_lwpolyline(pts, close=True, dxfattribs={"layer": "Lg_prod"})
    candidate = _measure(_save(doc, tmp_path / "rotated.dxf")).candidates[0]

    assert candidate.width > 100
    assert candidate.depth > 40
    assert candidate.area == pytest.approx(4000)


def test_circular_footprint(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_circle((10, 20), 5, dxfattribs={"layer": "Lg_prod"})
    candidate = _measure(_save(doc, tmp_path / "circle.dxf"), curve_flattening_tolerance=0.1).candidates[0]

    assert candidate.width == pytest.approx(10, abs=0.02)
    assert candidate.depth == pytest.approx(10, abs=0.02)
    assert candidate.area == pytest.approx(math.pi * 25, rel=0.002)
    assert "measurement_curve_approximated" in _codes(_measure(_save(doc, tmp_path / "circle2.dxf"), curve_flattening_tolerance=0.1))


def test_ellipse_and_spline_approximation(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_ellipse((0, 0), major_axis=(10, 0), ratio=0.5, dxfattribs={"layer": "Lg_prod"})
    doc.modelspace().add_spline([(30, 0), (35, 5), (40, 0), (35, -5)], dxfattribs={"layer": "Lg_prod"}).closed = True
    result = _measure(_save(doc, tmp_path / "curves.dxf"), curve_flattening_tolerance=0.25)

    assert len(result.candidates) >= 1
    assert "measurement_curve_approximated" in _codes(result)


def test_line_arc_chain_closure(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "Lg_prod"})
    msp.add_arc((10, 5), 5, 270, 90, dxfattribs={"layer": "Lg_prod"})
    msp.add_line((10, 10), (0, 10), dxfattribs={"layer": "Lg_prod"})
    msp.add_line((0, 10), (0, 0), dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "chain.dxf"), curve_flattening_tolerance=0.25)

    assert result.candidates[0].width == pytest.approx(15, abs=0.1)
    assert "measurement_curve_approximated" in _codes(result)


def test_gap_below_tolerance_joins(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "Lg_prod"})
    msp.add_line((10.05, 0), (10, 10), dxfattribs={"layer": "Lg_prod"})
    msp.add_line((10, 10), (0, 10), dxfattribs={"layer": "Lg_prod"})
    msp.add_line((0, 10), (0, 0), dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "small_gap.dxf"), endpoint_join_tolerance=0.1, max_join_gap=0.1)

    assert result.candidates
    assert "measurement_gap_too_large" not in _codes(result)


def test_gap_above_tolerance_fails(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 0), dxfattribs={"layer": "Lg_prod"})
    msp.add_line((12, 0), (10, 10), dxfattribs={"layer": "Lg_prod"})
    msp.add_line((10, 10), (0, 10), dxfattribs={"layer": "Lg_prod"})
    msp.add_line((0, 10), (0, 0), dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "large_gap.dxf"), max_join_gap=0.1)

    assert "measurement_gap_too_large" in _codes(result)


def test_polygon_with_hole(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_area")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "Lg_area"})
    msp.add_lwpolyline([(40, 40), (60, 40), (60, 60), (40, 60)], close=True, dxfattribs={"layer": "Lg_area"})
    candidate = _measure(_save(doc, tmp_path / "hole.dxf")).candidates[0]

    assert candidate.role == "safety_zone"
    assert candidate.area == 9600
    assert candidate.evidence.hole_count == 1

def test_hatch_polyline_boundary_measured_without_rewriting(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "Lg_prod"})
    hatch.paths.add_polyline_path([(0, 0), (20, 0), (20, 10), (0, 10)], is_closed=True)
    result = _measure(_save(doc, tmp_path / "hatch_boundary.dxf"))
    candidate = result.candidates[0]

    assert candidate.role == "review_required"
    assert candidate.width == 20
    assert candidate.depth == 10
    assert candidate.area == 200
    assert candidate.evidence.original_dxf_types == ("HATCH",)

def test_nested_insert_transform(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    inner = doc.blocks.new("INNER")
    inner.add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True, dxfattribs={"layer": "Lg_prod"})
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (20, 10))
    doc.modelspace().add_blockref("OUTER", (100, 50))
    candidate = _measure(_save(doc, tmp_path / "nested_insert.dxf")).candidates[0]

    assert candidate.bounding_box == {"min_x": 120.0, "min_y": 60.0, "max_x": 130.0, "max_y": 65.0}
    assert candidate.evidence.block_ancestry


def test_repeated_inserts_measured_independently_without_definition_double_count(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    block = doc.blocks.new("UNIT")
    block.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "Lg_prod"})
    doc.modelspace().add_blockref("UNIT", (0, 0))
    doc.modelspace().add_blockref("UNIT", (30, 0))
    result = _measure(_save(doc, tmp_path / "repeat_insert.dxf"))

    assert len(result.candidates) == 2
    assert all(candidate.area == 100 for candidate in result.candidates)


def test_product_and_larger_containing_safety_zone(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.layers.new("Lg_area")
    msp = doc.modelspace()
    msp.add_lwpolyline([(20, 20), (80, 20), (80, 70), (20, 70)], close=True, dxfattribs={"layer": "Lg_prod"})
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 100), (0, 100)], close=True, dxfattribs={"layer": "Lg_area"})
    result = _measure(_save(doc, tmp_path / "product_safety.dxf"))

    assert {candidate.role for candidate in result.candidates} == {"product_geometry", "safety_zone"}
    assert max(candidate.area for candidate in result.candidates if candidate.role == "safety_zone") > max(candidate.area for candidate in result.candidates if candidate.role == "product_geometry")


def test_several_plausible_candidates_retained(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "Lg_prod"})
    msp.add_lwpolyline([(20, 0), (35, 0), (35, 10), (20, 10)], close=True, dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "many.dxf"))

    assert len(result.candidates) == 2
    assert "measurement_multiple_candidates" in _codes(result)
    assert result.candidates[0].alternatives


def test_low_confidence_classification_remains_review(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Mystery")
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "Mystery"})
    result = _measure(_save(doc, tmp_path / "review.dxf"))

    assert result.candidates[0].role == "review_required"
    assert result.candidates[0].confidence <= 0.55


def test_unknown_units_preserved(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "unknown_units.dxf"))

    assert result.unit_status == "unknown"
    assert result.candidates[0].width_mm is None
    assert "measurement_units_unknown" in _codes(result)


def test_explicit_unit_override(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 5), (0, 5)], close=True, dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "unit_override.dxf"), explicit_unit="cm")

    assert result.unit_status == "explicit"
    assert result.candidates[0].width_mm == 100
    assert "measurement_unit_override_applied" in _codes(result)


def test_json_assisted_mm_inference(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_lwpolyline([(0, 0), (4000, 0), (4000, 2000), (0, 2000)], close=True, dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "infer_mm.dxf"), expected_width_mm=4000, expected_depth_mm=2000)

    assert result.unit_status == "inferred"
    assert result.inferred_unit == "mm"
    assert result.candidates[0].width_mm == 4000


def test_ambiguous_unit_inference(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_lwpolyline([(0, 0), (40, 0), (40, 20), (0, 20)], close=True, dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "ambiguous_units.dxf"), expected_width_mm=500, expected_depth_mm=250)

    assert result.unit_status == "ambiguous"
    assert "measurement_unit_inference_ambiguous" in _codes(result)


def test_non_planar_geometry_rejected_for_high_confidence(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "Lg_prod", "elevation": 2})
    result = _measure(_save(doc, tmp_path / "nonplanar.dxf"))

    assert "measurement_nonplanar_geometry" in _codes(result)
    assert result.candidates[0].confidence <= 0.52


def test_opaque_region_reported(tmp_path: Path) -> None:
    doc = _doc()
    path = _save(doc, tmp_path / "region_evidence.dxf")
    inventory = inventory_dxf(path)
    inventory = inventory.model_copy(update={"flagged_entity_presence": {"REGION": 1}})
    canonical = canonicalize_dxf(path)
    result = measure_geometry(inventory, canonical)
    assert "measurement_opaque_region" in _codes(result)


def test_deterministic_serialization(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    path = _save(doc, tmp_path / "deterministic.dxf")
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "Lg_prod"})
    path = _save(doc, path)

    first = _measure(path)
    second = _measure(path)
    output = write_measurement_json(first, tmp_path / "measure.json")

    assert first.to_deterministic_json() == second.to_deterministic_json()
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(first.to_deterministic_json())


def test_source_inputs_checksum_unchanged(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    doc.modelspace().add_lwpolyline([(0, 0), (10, 0), (10, 10), (0, 10)], close=True, dxfattribs={"layer": "Lg_prod"})
    path = _save(doc, tmp_path / "unchanged.dxf")
    before = hashlib.sha256(path.read_bytes()).hexdigest()

    result = _measure(path)

    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    assert result.source_checksum_unchanged is True

def test_chain_combination_cap_reports_fail_issue(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("Lg_prod")
    msp = doc.modelspace()
    for index in range(8):
        msp.add_line((index * 10, 0), (index * 10 + 1, 0), dxfattribs={"layer": "Lg_prod"})
    result = _measure(_save(doc, tmp_path / "chain_cap.dxf"), max_chain_combinations=1)

    assert "measurement_failed" in _codes(result)
    issue = next(issue for issue in result.issues if issue.code == "measurement_failed")
    assert issue.severity == "fail"
    assert issue.evidence["cap"] == 1

