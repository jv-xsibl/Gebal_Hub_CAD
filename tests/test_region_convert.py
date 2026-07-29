"""Tests for Stage 7 REGION to closed-polyline conversion."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import ezdxf
import pytest

from gebal_cad_normalizer.cad import attach_region_boundary_evidence, convert_regions
from gebal_cad_normalizer.exceptions import OutputWriteError


def _doc() -> ezdxf.document.Drawing:
    return ezdxf.new("R2010")


def _region(doc: ezdxf.document.Drawing, evidence: dict, *, layer: str = "REGIONS", color: int = 3, linetype: str = "CONTINUOUS"):
    if layer not in doc.layers:
        doc.layers.new(layer, dxfattribs={"color": color, "linetype": linetype})
    region = doc.modelspace().add_region(dxfattribs={"layer": layer, "color": color, "linetype": linetype})
    attach_region_boundary_evidence(region, evidence)
    return region


def _rect(x: float = 0, y: float = 0, w: float = 10, h: float = 5, **extra) -> dict:
    area = w * h
    perimeter = 2 * (w + h)
    return {
        "area": area,
        "perimeter": perimeter,
        "loops": [
            {
                "loop_id": "outer",
                "role": "outer",
                "closed": True,
                "segments": ["line", "line", "line", "line"],
                "vertices": [(x, y), (x + w, y), (x + w, y + h), (x, y + h)],
                "bulges": [0, 0, 0, 0],
            }
        ],
        **extra,
    }


def _circle(radius: float = 2.0, *, loop_id: str = "outer") -> dict:
    bulge = math.tan(math.pi / 4)
    return {
        "area": math.pi * radius * radius,
        "perimeter": 2 * math.pi * radius,
        "loops": [
            {
                "loop_id": loop_id,
                "role": "outer",
                "closed": True,
                "segments": ["arc", "arc"],
                "vertices": [(-radius, 0), (radius, 0)],
                "bulges": [bulge, bulge],
            }
        ],
    }


def _save(doc: ezdxf.document.Drawing, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _codes(result) -> set[str]:
    return {issue.code for issue in result.issues}


def test_simple_rectangular_region_converts_to_closed_loop() -> None:
    doc = _doc()
    region = _region(doc, _rect())

    result = convert_regions(doc)

    assert result.converted_count == 1
    converted = result.regions[0]
    assert converted.source_handle == region.dxf.handle
    assert converted.status == "converted"
    assert converted.loops[0].is_closed is True
    assert converted.loops[0].area == 50
    assert converted.loops[0].perimeter == 30


def test_circular_region_preserves_arcs_as_bulges() -> None:
    doc = _doc()
    _region(doc, _circle(3))

    result = convert_regions(doc, tolerance=1e-5)
    loop = result.regions[0].loops[0]

    assert result.converted_count == 1
    assert all(abs(value - 1.0) < 1e-12 for value in loop.bulges)
    assert loop.used_tessellation is False
    assert result.regions[0].area_deviation is not None
    assert result.regions[0].area_deviation < 1e-5


def test_region_with_mixed_line_and_arc_segments() -> None:
    doc = _doc()
    evidence = {
        "loops": [
            {
                "loop_id": "outer",
                "role": "outer",
                "closed": True,
                "segments": ["line", "arc", "line"],
                "vertices": [(0, 0), (4, 0), (2, 2)],
                "bulges": [0, math.tan(math.radians(45 / 4)), 0],
            }
        ],
    }
    _region(doc, evidence)

    result = convert_regions(doc)

    assert result.regions[0].status == "converted"
    assert result.regions[0].loops[0].bulges[1] != 0


def test_region_with_inner_hole_preserves_parent_child_loops() -> None:
    doc = _doc()
    evidence = _rect(0, 0, 10, 10)
    evidence["area"] = 84
    evidence["perimeter"] = 64
    evidence["loops"].append(
        {
            "loop_id": "hole",
            "role": "hole",
            "closed": True,
            "segments": ["line", "line", "line", "line"],
            "vertices": [(3, 3), (7, 3), (7, 7), (3, 7)],
            "bulges": [0, 0, 0, 0],
        }
    )
    _region(doc, evidence)

    result = convert_regions(doc)
    loops = {loop.loop_id: loop for loop in result.regions[0].loops}

    assert result.regions[0].converted_area == 84
    assert loops["hole"].role == "hole"
    assert loops["hole"].parent_loop_id == "outer"


def test_multiple_regions_per_layer_are_supported() -> None:
    doc = _doc()
    _region(doc, _rect(0, 0, 1, 1), layer="A")
    _region(doc, _rect(2, 0, 1, 1), layer="A")

    result = convert_regions(doc)

    assert result.region_count == 2
    assert result.converted_count == 2
    assert [region.layer for region in result.regions] == ["A", "A"]


def test_layer_style_and_elevation_are_preserved_on_write(tmp_path: Path) -> None:
    doc = _doc()
    _region(doc, _rect(), layer="STYLE", color=6, linetype="DASHED")
    output = tmp_path / "converted.dxf"

    result = convert_regions(doc, output_path=output)
    written = ezdxf.readfile(output)
    polyline = next(entity for entity in written.modelspace() if entity.dxftype() == "LWPOLYLINE")

    assert result.output_path == output
    assert polyline.dxf.layer == "STYLE"
    assert polyline.dxf.color == 6
    assert polyline.dxf.linetype == "DASHED"
    assert polyline.closed is True


def test_area_and_perimeter_tolerance_failures_are_reported() -> None:
    doc = _doc()
    _region(doc, _rect(area=999, perimeter=999))

    result = convert_regions(doc, tolerance=0.01)

    assert result.failed_count == 1
    assert "region_area_mismatch" in _codes(result)
    assert "region_perimeter_mismatch" in _codes(result)


def test_non_planar_region_is_rejected() -> None:
    doc = _doc()
    _region(doc, _rect(nonplanar=True))

    result = convert_regions(doc)

    assert result.failed_count == 1
    assert "region_nonplanar" in _codes(result)


def test_invalid_open_and_self_intersecting_topology_are_reported() -> None:
    doc = _doc()
    _region(doc, {"loops": [{"vertices": [(0, 0), (1, 1)], "closed": False}]})
    _region(doc, {"loops": [{"vertices": [(0, 0), (2, 2), (0, 2), (2, 0)], "bulges": [0, 0, 0, 0], "closed": True}]})

    result = convert_regions(doc)

    assert result.failed_count == 2
    assert "region_open_loop" in _codes(result)
    assert "region_self_intersection" in _codes(result)


def test_deterministic_output() -> None:
    doc = _doc()
    _region(doc, _rect())
    _region(doc, _circle())

    first = convert_regions(doc)
    second = convert_regions(doc)

    assert first.to_deterministic_json() == second.to_deterministic_json()


def test_source_file_checksum_is_unchanged(tmp_path: Path) -> None:
    doc = _doc()
    _region(doc, _rect())
    path = _save(doc, tmp_path / "source.dxf")
    before = _sha256(path)

    result = convert_regions(path)

    assert _sha256(path) == before
    assert result.source_sha256 == before


def test_explicit_output_dxf_preserves_unrelated_entities_and_replaces_regions(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((100, 100), (200, 200), dxfattribs={"layer": "KEEP"})
    _region(doc, _rect())
    source = _save(doc, tmp_path / "source.dxf")
    output = tmp_path / "output.dxf"

    convert_regions(source, output_path=output)
    written = ezdxf.readfile(output)
    types = [entity.dxftype() for entity in written.modelspace()]

    assert types.count("LINE") == 1
    assert types.count("LWPOLYLINE") == 1
    assert "REGION" not in types
    assert [entity.dxf.layer for entity in written.modelspace() if entity.dxftype() == "LINE"] == ["KEEP"]


def test_failed_write_preserves_existing_destination(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc()
    _region(doc, _rect())
    destination = tmp_path / "existing.dxf"
    destination.write_text("original", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr("gebal_cad_normalizer.cad.region_convert.os.replace", fail_replace)
    with pytest.raises(OutputWriteError):
        convert_regions(doc, output_path=destination)

    assert destination.read_text(encoding="utf-8") == "original"


def test_tessellation_only_when_required() -> None:
    doc = _doc()
    _region(doc, _rect())
    approximated = _rect(20, 0, 1, 1)
    approximated["loops"][0]["segments"] = ["spline"]
    approximated["loops"][0]["used_tessellation"] = True
    _region(doc, approximated)

    result = convert_regions(doc)

    assert result.converted_count == 1
    assert result.approximated_count == 1
    assert "region_curve_approximated" in _codes(result)


def test_opaque_region_without_boundary_evidence_fails_without_mutation() -> None:
    doc = _doc()
    doc.modelspace().add_region()

    result = convert_regions(doc)

    assert result.failed_count == 1
    assert "region_conversion_failed" in _codes(result)
    assert [entity.dxftype() for entity in doc.modelspace()] == ["REGION"]


def test_output_preserves_failed_regions_when_other_regions_convert(tmp_path: Path) -> None:
    doc = _doc()
    opaque = doc.modelspace().add_region(dxfattribs={"layer": "OPAQUE", "color": 2})
    opaque.sat = ("opaque acis fixture",)
    _region(doc, _rect(), layer="SUPPORTED", color=4)
    source = _save(doc, tmp_path / "mixed.dxf")
    before = _sha256(source)
    output = tmp_path / "mixed_converted.dxf"

    result = convert_regions(source, output_path=output)

    assert _sha256(source) == before
    assert result.converted_count == 1
    assert result.failed_count == 1

    written = ezdxf.readfile(output)
    entities = list(written.modelspace())
    regions = [entity for entity in entities if entity.dxftype() == "REGION"]
    polylines = [entity for entity in entities if entity.dxftype() == "LWPOLYLINE"]

    assert len(regions) == 1
    assert len(polylines) == 1
    assert regions[0].dxf.handle == opaque.dxf.handle
    assert regions[0].dxf.layer == "OPAQUE"
    assert regions[0].dxf.color == 2

