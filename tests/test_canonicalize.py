"""Tests for Stage 6 read-only geometry canonicalization."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ezdxf

from gebal_cad_normalizer.cad import canonicalize_dxf, write_canonical_json
from gebal_cad_normalizer.cad.canonicalize import CanonicalArc, CanonicalCircle, CanonicalLine, CanonicalPoint, CanonicalPolyline, CanonicalSplineReference


def _doc(units: int | None = 4) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2010")
    if units is not None:
        doc.header["$INSUNITS"] = units
    return doc


def _save(doc: ezdxf.document.Drawing, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_line_canonicalization_preserves_source_metadata() -> None:
    doc = _doc()
    doc.layers.new("PRODUCT", dxfattribs={"color": 2, "linetype": "CONTINUOUS"})
    line = doc.modelspace().add_line((1, 2), (3, 4), dxfattribs={"layer": "PRODUCT", "color": 5, "linetype": "DASHED"})

    result = canonicalize_dxf(doc)
    entity = result.entities[0]

    assert result.total_canonical_entities == 1
    assert entity.source_handle == line.dxf.handle
    assert entity.original_dxf_type == "LINE"
    assert entity.canonical_type == "line"
    assert entity.status == "canonicalized"
    assert entity.layer == "PRODUCT"
    assert entity.color == 5
    assert entity.linetype == "DASHED"
    assert isinstance(entity.geometry, CanonicalLine)
    assert entity.geometry.start.x == 1
    assert entity.geometry.end.y == 4


def test_open_and_closed_lwpolyline_canonicalization() -> None:
    doc = _doc()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (1, 0), (1, 1)], close=False)
    msp.add_lwpolyline([(2, 0), (3, 0), (3, 1)], close=True)

    result = canonicalize_dxf(doc)

    assert [entity.is_closed for entity in result.entities] == [False, True]
    assert all(isinstance(entity.geometry, CanonicalPolyline) for entity in result.entities)
    assert result.counts_by_canonical_type == {"polyline": 2}


def test_lwpolyline_bulge_values_are_preserved() -> None:
    doc = _doc()
    doc.modelspace().add_lwpolyline([(0, 0, 0, 0, 0.5), (1, 0, 0, 0, 0.0)], format="xyseb")

    result = canonicalize_dxf(doc)
    polyline = result.entities[0].geometry

    assert isinstance(polyline, CanonicalPolyline)
    assert polyline.vertices[0].bulge == 0.5
    assert polyline.vertices[1].bulge == 0.0


def test_2d_polyline_canonicalization() -> None:
    doc = _doc()
    poly = doc.modelspace().add_polyline2d([(0, 0), (2, 0), (2, 2)], close=True)
    poly.vertices[0].dxf.bulge = 0.25

    result = canonicalize_dxf(doc)
    entity = result.entities[0]

    assert entity.original_dxf_type == "POLYLINE"
    assert entity.is_closed is True
    assert isinstance(entity.geometry, CanonicalPolyline)
    assert entity.geometry.source_polyline_type == "POLYLINE"
    assert entity.geometry.vertices[0].bulge == 0.25


def test_arc_direction_and_angles_are_preserved() -> None:
    doc = _doc()
    doc.modelspace().add_arc((5, 6), 3, 15, 125)

    result = canonicalize_dxf(doc)
    arc = result.entities[0].geometry

    assert result.entities[0].status == "convertible_later"
    assert isinstance(arc, CanonicalArc)
    assert arc.direction == "ccw"
    assert arc.start_angle == 15
    assert arc.end_angle == 125


def test_circle_canonicalization() -> None:
    doc = _doc()
    doc.modelspace().add_circle((2, 3), 4)

    result = canonicalize_dxf(doc)
    circle = result.entities[0].geometry

    assert result.entities[0].canonical_type == "circle"
    assert result.entities[0].is_closed is True
    assert isinstance(circle, CanonicalCircle)
    assert circle.center == CanonicalPoint(x=2, y=3, z=0)
    assert circle.radius == 4


def test_ellipse_is_preserved_without_default_flattening() -> None:
    doc = _doc()
    doc.modelspace().add_ellipse((0, 0), major_axis=(2, 0), ratio=0.5)

    result = canonicalize_dxf(doc)
    entity = result.entities[0]

    assert entity.canonical_type == "ellipse"
    assert entity.status == "preserved_curve"
    assert "curve_preserved_not_flattened" in _codes(result)


def test_spline_is_preserved_without_silent_flattening() -> None:
    doc = _doc()
    doc.modelspace().add_spline([(0, 0), (1, 2), (2, 0)])

    result = canonicalize_dxf(doc)
    entity = result.entities[0]

    assert entity.canonical_type == "spline_reference"
    assert entity.status == "preserved_curve"
    assert isinstance(entity.geometry, CanonicalSplineReference)
    assert len(entity.geometry.fit_points) == 3
    assert "curve_preserved_not_flattened" in _codes(result)


def test_nested_insert_transform_application() -> None:
    doc = _doc()
    inner = doc.blocks.new("INNER")
    inner.add_line((0, 0), (1, 0))
    outer = doc.blocks.new("OUTER")
    outer.add_blockref("INNER", (2, 0))
    doc.modelspace().add_blockref("OUTER", (10, 5))

    result = canonicalize_dxf(doc)
    line = result.entities[0].geometry

    assert isinstance(line, CanonicalLine)
    assert line.start == CanonicalPoint(x=12, y=5, z=0)
    assert line.end == CanonicalPoint(x=13, y=5, z=0)


def test_block_ancestry_and_insert_handles_are_preserved() -> None:
    doc = _doc()
    block = doc.blocks.new("CHAIR")
    source_line = block.add_line((0, 0), (1, 0))
    insert = doc.modelspace().add_blockref("CHAIR", (5, 5))

    result = canonicalize_dxf(doc)
    entity = result.entities[0]

    assert entity.source_handle == source_line.dxf.handle
    assert entity.block_ancestry == (f"CHAIR:{insert.dxf.handle}",)
    assert entity.insert_handles == (insert.dxf.handle,)


def test_circular_block_reference_handling() -> None:
    doc = _doc()
    block = doc.blocks.new("LOOP")
    block.add_blockref("LOOP", (1, 0))
    doc.modelspace().add_blockref("LOOP", (0, 0))

    result = canonicalize_dxf(doc)

    assert "block_cycle_detected" in _codes(result)
    assert result.total_source_entities_visited >= 2


def test_nonzero_z_geometry_is_flagged() -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 2), (1, 1, 2))

    result = canonicalize_dxf(doc)

    assert result.entities[0].z_values == (2.0, 2.0)
    assert "nonzero_z_geometry" in _codes(result)


def test_tiny_positive_z_below_default_epsilon_is_not_flagged() -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 9.7e-9), (1, 1, 9.7e-9))

    result = canonicalize_dxf(doc)

    assert result.entities[0].z_values == (9.7e-9, 9.7e-9)
    assert "nonzero_z_geometry" not in _codes(result)


def test_tiny_negative_z_below_default_epsilon_is_not_flagged() -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, -9.7e-9), (1, 1, -9.7e-9))

    result = canonicalize_dxf(doc)

    assert result.entities[0].z_values == (-9.7e-9, -9.7e-9)
    assert "nonzero_z_geometry" not in _codes(result)


def test_z_above_default_epsilon_is_flagged_with_raw_evidence() -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 2e-6), (1, 1, 2e-6))

    result = canonicalize_dxf(doc)
    issue = next(issue for issue in result.issues if issue.code == "nonzero_z_geometry")

    assert result.entities[0].z_values == (2e-6, 2e-6)
    assert issue.evidence["z_values"] == (2e-6, 2e-6)
    assert issue.evidence["z_epsilon"] == 1e-6


def test_configurable_z_epsilon_changes_canonicalization_behavior() -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 5e-7), (1, 1, 5e-7))

    tolerant = canonicalize_dxf(doc, z_epsilon=1e-6)
    strict = canonicalize_dxf(doc, z_epsilon=1e-8)

    assert "nonzero_z_geometry" not in _codes(tolerant)
    assert "nonzero_z_geometry" in _codes(strict)


def test_explicit_3d_entity_type_is_skipped_even_with_planar_coordinates() -> None:
    doc = _doc()
    doc.modelspace().add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])

    result = canonicalize_dxf(doc)
    entity = result.entities[0]

    assert entity.original_dxf_type == "3DFACE"
    assert entity.status == "skipped_3d"
    assert entity.geometry is None
    assert "unsupported_3d_geometry" in _codes(result)


def test_near_zero_z_source_file_checksum_unchanged_after_canonicalization(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, -9.7e-9), (1, 1, -9.7e-9))
    path = _save(doc, tmp_path / "near_zero_unchanged.dxf")
    before = _sha256(path)

    canonicalize_dxf(path)

    assert _sha256(path) == before


def test_3d_entity_is_skipped_and_reported() -> None:
    doc = _doc()
    doc.modelspace().add_polyline3d([(0, 0, 0), (1, 1, 1)])

    result = canonicalize_dxf(doc)
    entity = result.entities[0]

    assert entity.original_dxf_type == "POLYLINE"
    assert entity.status == "skipped_3d"
    assert entity.geometry is None
    assert "unsupported_3d_geometry" in _codes(result)


def test_unsupported_entity_is_reported() -> None:
    doc = _doc()
    doc.modelspace().add_text("label")

    result = canonicalize_dxf(doc)

    assert result.entities[0].original_dxf_type == "TEXT"
    assert result.entities[0].status == "unsupported"
    assert "unsupported_entity_type" in _codes(result)


def test_deterministic_ordering_and_json_output(tmp_path: Path) -> None:
    doc = _doc()
    msp = doc.modelspace()
    msp.add_line((0, 0), (1, 0))
    msp.add_circle((2, 0), 1)
    path = _save(doc, tmp_path / "ordered.dxf")

    first = canonicalize_dxf(path)
    second = canonicalize_dxf(path)
    output = write_canonical_json(first, tmp_path / "canonical.json")

    assert [entity.order_key for entity in first.entities] == [(0,), (1,)]
    assert first.to_deterministic_json() == second.to_deterministic_json()
    assert json.loads(output.read_text(encoding="utf-8")) == json.loads(first.to_deterministic_json())


def test_canonical_extents_are_calculated() -> None:
    doc = _doc()
    msp = doc.modelspace()
    msp.add_line((-1, -2), (3, 4))
    msp.add_lwpolyline([(10, 5), (11, 8)])

    result = canonicalize_dxf(doc)

    assert result.canonical_extents == {"min_x": -1.0, "min_y": -2.0, "min_z": 0.0, "max_x": 11.0, "max_y": 8.0, "max_z": 0.0}


def test_source_file_checksum_unchanged_after_canonicalization(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0), (1, 1))
    path = _save(doc, tmp_path / "source.dxf")
    before = _sha256(path)

    result = canonicalize_dxf(path)

    assert _sha256(path) == before
    assert result.source_sha256 == before


def test_source_ezdxf_document_is_not_mutated() -> None:
    doc = _doc()
    block = doc.blocks.new("B")
    block.add_line((0, 0), (1, 0))
    doc.modelspace().add_blockref("B", (5, 0))
    before_modelspace_types = [entity.dxftype() for entity in doc.modelspace()]
    before_block_types = [entity.dxftype() for entity in block]
    before_handles = [entity.dxf.handle for entity in block]

    canonicalize_dxf(doc)

    assert [entity.dxftype() for entity in doc.modelspace()] == before_modelspace_types
    assert [entity.dxftype() for entity in block] == before_block_types
    assert [entity.dxf.handle for entity in block] == before_handles


def test_optional_tessellation_is_deterministic_when_enabled(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_ellipse((0, 0), major_axis=(2, 0), ratio=0.5)
    path = _save(doc, tmp_path / "ellipse.dxf")

    first = canonicalize_dxf(path, tessellate_curves=True, tessellation_tolerance=0.25)
    second = canonicalize_dxf(path, tessellate_curves=True, tessellation_tolerance=0.25)

    assert first.tessellation_enabled is True
    assert first.entities[0].canonical_type == "tessellated_polyline"
    assert first.to_deterministic_json() == second.to_deterministic_json()


def test_tessellation_is_disabled_by_default() -> None:
    doc = _doc()
    doc.modelspace().add_ellipse((0, 0), major_axis=(2, 0), ratio=0.5)

    result = canonicalize_dxf(doc)

    assert result.tessellation_enabled is False
    assert result.tessellation_tolerance is None
    assert result.entities[0].canonical_type == "ellipse"
    assert result.entities[0].status == "preserved_curve"
