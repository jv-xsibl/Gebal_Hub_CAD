"""Tests for Stage 5 read-only DXF inventory."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ezdxf
import pytest

from gebal_cad_normalizer.cad import DxfInventoryError, DxfInventoryIssueCode, inventory_dxf
from gebal_cad_normalizer.cad.inventory import write_inventory_json, write_inventory_markdown


def _save(doc: ezdxf.document.Drawing, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _doc(units: int | None = 4) -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2010")
    if units is not None:
        doc.header["$INSUNITS"] = units
    return doc


def _issue_codes(path: Path) -> set[str]:
    return {issue.code for issue in inventory_dxf(path).issues}


def _counts_by_type(result) -> dict[str, int]:
    return {item.dxf_type: item.count for item in result.entity_counts}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _remove_header_variable(path: Path, variable: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    output: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == "9" and index + 1 < len(lines) and lines[index + 1].strip() == variable:
            index += 4
            continue
        output.append(lines[index])
        index += 1
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


def test_missing_dxf_fails_clearly(tmp_path: Path) -> None:
    with pytest.raises(DxfInventoryError) as excinfo:
        inventory_dxf(tmp_path / "missing.dxf")

    assert excinfo.value.code == DxfInventoryIssueCode.INVALID_DXF


def test_invalid_corrupt_dxf_fails_clearly(tmp_path: Path) -> None:
    path = tmp_path / "corrupt.dxf"
    path.write_text("not a dxf", encoding="utf-8")

    with pytest.raises(DxfInventoryError) as excinfo:
        inventory_dxf(path)

    assert excinfo.value.code == DxfInventoryIssueCode.INVALID_DXF


def test_valid_simple_2d_dxf_inventory_and_extents(tmp_path: Path) -> None:
    doc = _doc()
    msp = doc.modelspace()
    msp.add_line((0, 0), (10, 5), dxfattribs={"layer": "0"})
    path = _save(doc, tmp_path / "simple.dxf")

    result = inventory_dxf(path)

    assert result.dxf_version == "AC1024"
    assert result.insunits == 4
    assert result.drawing_units == "mm"
    assert result.modelspace_present is True
    assert result.paperspace_present is True
    assert result.modelspace_entity_count == 1
    assert _counts_by_type(result)["LINE"] == 1
    assert result.modelspace_extents == {"min_x": 0.0, "min_y": 0.0, "min_z": 0.0, "max_x": 10.0, "max_y": 5.0, "max_z": 0.0}


def test_layers_and_entity_counts(tmp_path: Path) -> None:
    doc = _doc()
    doc.layers.new("PRODUCT", dxfattribs={"color": 2, "linetype": "CONTINUOUS"})
    doc.layers.new("REFERENCE", dxfattribs={"color": 3})
    doc.layers.get("REFERENCE").lock()
    msp = doc.modelspace()
    msp.add_line((0, 0), (1, 0), dxfattribs={"layer": "PRODUCT"})
    msp.add_circle((2, 2), 1, dxfattribs={"layer": "REFERENCE"})
    path = _save(doc, tmp_path / "layers.dxf")

    result = inventory_dxf(path)
    layers = {layer.name: layer for layer in result.layers}

    assert layers["PRODUCT"].color == 2
    assert layers["PRODUCT"].entity_count == 1
    assert layers["REFERENCE"].is_locked is True
    assert layers["REFERENCE"].entity_count == 1
    assert _counts_by_type(result) == {"CIRCLE": 1, "LINE": 1}
    assert result.entity_counts_by_layer["PRODUCT"][0].dxf_type == "LINE"


def test_block_definition_insert_and_nested_counting(tmp_path: Path) -> None:
    doc = _doc()
    nested = doc.blocks.new("NESTED")
    nested.add_circle((0, 0), 1)
    block = doc.blocks.new("CHAIR")
    block.add_line((0, 0), (1, 0))
    block.add_blockref("NESTED", (0, 0))
    doc.modelspace().add_blockref("CHAIR", (5, 5))
    path = _save(doc, tmp_path / "blocks.dxf")

    result = inventory_dxf(path)
    blocks = {block.name: block for block in result.blocks}
    nested_counts = {item.dxf_type: item.count for item in result.nested_entity_counts}

    assert blocks["CHAIR"].entity_count == 2
    assert blocks["CHAIR"].insert_count == 1
    assert blocks["NESTED"].nested_insert_count == 1
    assert nested_counts["LINE"] == 1
    assert nested_counts["INSERT"] == 1
    assert nested_counts["CIRCLE"] == 1


def test_region_detection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "region.dxf"
    path.write_text("0\nSECTION\n9\n$INSUNITS\n70\n4\n0\nENDSEC\n0\nEOF\n", encoding="utf-8")

    class FakeDxf:
        name = "0"

        def get(self, key: str, default=None):
            return {"layer": "0"}.get(key, default)

    class FakeEntity:
        dxf = FakeDxf()

        def dxftype(self) -> str:
            return "REGION"

    class FakeLayer:
        dxf = type("LayerDxf", (), {"name": "0", "get": lambda self, key, default=None: {"color": 7, "linetype": "CONTINUOUS"}.get(key, default)})()

        def is_on(self) -> bool:
            return True

        def is_frozen(self) -> bool:
            return False

        def is_locked(self) -> bool:
            return False

    class FakeLayouts:
        def names(self):
            return ["Model"]

    class FakeDoc:
        dxfversion = "AC1024"
        header = {"$INSUNITS": 4}
        layers = [FakeLayer()]
        layouts = FakeLayouts()
        blocks = []
        styles = []

        def modelspace(self):
            return [FakeEntity()]

    monkeypatch.setattr("gebal_cad_normalizer.cad.inventory.ezdxf.readfile", lambda source: FakeDoc())
    result = inventory_dxf(path)

    assert result.flagged_entity_presence["REGION"] == 1
    assert "contains_region" in {issue.code for issue in result.issues}


def test_spline_ellipse_hatch_detection(tmp_path: Path) -> None:
    doc = _doc()
    msp = doc.modelspace()
    msp.add_spline([(0, 0), (1, 2), (3, 0)])
    msp.add_ellipse((4, 4), major_axis=(2, 0), ratio=0.5)
    hatch = msp.add_hatch(color=1)
    hatch.paths.add_polyline_path([(0, 0), (1, 0), (1, 1), (0, 1)], is_closed=True)
    path = _save(doc, tmp_path / "curves_hatch.dxf")

    result = inventory_dxf(path)
    counts = _counts_by_type(result)

    assert counts["SPLINE"] == 1
    assert counts["ELLIPSE"] == 1
    assert counts["HATCH"] == 1
    assert result.flagged_entity_presence["SPLINE"] == 1
    assert result.flagged_entity_presence["ELLIPSE"] == 1
    assert result.flagged_entity_presence["HATCH"] == 1
    assert result.text_style_dimension_hatch_usage["hatches"] == 1


def test_nonzero_z_detection(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 2), (1, 1, 2))
    path = _save(doc, tmp_path / "z.dxf")

    result = inventory_dxf(path)

    assert result.has_nonzero_z_geometry is True
    assert "nonzero_z_geometry" in {issue.code for issue in result.issues}
    assert "contains_3d_geometry" in {issue.code for issue in result.issues}


def test_tiny_positive_z_below_default_epsilon_is_not_flagged(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 9.7e-9), (1, 1, 9.7e-9))
    path = _save(doc, tmp_path / "tiny_positive_z.dxf")

    result = inventory_dxf(path)

    assert result.has_nonzero_z_geometry is False
    assert result.has_3d_geometry is False
    assert "nonzero_z_geometry" not in {issue.code for issue in result.issues}
    assert "contains_3d_geometry" not in {issue.code for issue in result.issues}


def test_tiny_negative_z_below_default_epsilon_is_not_flagged(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, -9.7e-9), (1, 1, -9.7e-9))
    path = _save(doc, tmp_path / "tiny_negative_z.dxf")

    result = inventory_dxf(path)

    assert result.has_nonzero_z_geometry is False
    assert result.has_3d_geometry is False
    assert "nonzero_z_geometry" not in {issue.code for issue in result.issues}


def test_z_above_default_epsilon_is_flagged_with_raw_evidence(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 2e-6), (1, 1, 2e-6))
    path = _save(doc, tmp_path / "above_epsilon_z.dxf")

    result = inventory_dxf(path)
    issue = next(issue for issue in result.issues if issue.code == "nonzero_z_geometry")

    assert result.has_nonzero_z_geometry is True
    assert result.has_3d_geometry is True
    assert issue.evidence["z_values"] == (2e-6,)
    assert issue.evidence["z_epsilon"] == 1e-6


def test_configurable_z_epsilon_changes_inventory_behavior(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, 5e-7), (1, 1, 5e-7))
    path = _save(doc, tmp_path / "configured_z.dxf")

    tolerant = inventory_dxf(path, z_epsilon=1e-6)
    strict = inventory_dxf(path, z_epsilon=1e-8)

    assert tolerant.has_nonzero_z_geometry is False
    assert strict.has_nonzero_z_geometry is True


def test_explicit_3d_entity_type_is_flagged_even_with_planar_coordinates(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)])
    path = _save(doc, tmp_path / "face.dxf")

    result = inventory_dxf(path)

    assert result.has_nonzero_z_geometry is False
    assert result.has_3d_geometry is True
    assert "contains_3d_geometry" in {issue.code for issue in result.issues}


def test_near_zero_z_source_file_checksum_unchanged_after_audit(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0, -9.7e-9), (1, 1, -9.7e-9))
    path = _save(doc, tmp_path / "near_zero_unchanged.dxf")
    before = _sha256(path)

    inventory_dxf(path)

    assert _sha256(path) == before


def test_empty_modelspace(tmp_path: Path) -> None:
    path = _save(_doc(), tmp_path / "empty.dxf")

    codes = _issue_codes(path)

    assert "empty_modelspace" in codes
    assert "no_2d_geometry" in codes
    assert "extents_unavailable" in codes


def test_missing_and_unknown_units(tmp_path: Path) -> None:
    missing = _doc(units=None)
    unknown = _doc(units=999)
    missing_path = _save(missing, tmp_path / "missing_units.dxf")
    _remove_header_variable(missing_path, "$INSUNITS")
    unknown_path = _save(unknown, tmp_path / "unknown_units.dxf")

    assert "units_missing" in _issue_codes(missing_path)
    assert "units_unknown" in _issue_codes(unknown_path)


def test_proxy_style_entity_handling(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().new_entity("ACAD_PROXY_ENTITY", dxfattribs={"layer": "0"})
    path = _save(doc, tmp_path / "proxy.dxf")

    result = inventory_dxf(path)

    assert result.flagged_entity_presence["ACAD_PROXY_ENTITY"] == 1
    assert "contains_proxy_entity" in {issue.code for issue in result.issues}


def test_raster_underlay_indicators(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().new_entity("PDFUNDERLAY", dxfattribs={"layer": "0"})
    path = _save(doc, tmp_path / "underlay.dxf")

    result = inventory_dxf(path)

    assert result.flagged_entity_presence["PDFUNDERLAY"] == 1
    assert "contains_raster_or_underlay" in {issue.code for issue in result.issues}


def test_extents_failure_is_non_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0), (1, 1))
    path = _save(doc, tmp_path / "extents_failure.dxf")

    def fail_extents(*args, **kwargs):
        raise RuntimeError("bbox failed")

    monkeypatch.setattr("gebal_cad_normalizer.cad.inventory.bbox.extents", fail_extents)
    result = inventory_dxf(path)

    assert result.modelspace_extents is None
    assert "extents_unavailable" in {issue.code for issue in result.issues}
    assert _counts_by_type(result)["LINE"] == 1


def test_json_serialization_is_deterministic_and_report_helpers(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0), (1, 1))
    path = _save(doc, tmp_path / "serial.dxf")
    result = inventory_dxf(path)

    first = result.to_deterministic_json()
    second = result.to_deterministic_json()
    json.loads(first)

    assert first == second
    assert write_inventory_json(result, tmp_path / "report.json").read_text(encoding="utf-8").strip() == first
    assert "# DXF Inventory Audit" in write_inventory_markdown(result, tmp_path / "report.md").read_text(encoding="utf-8")


def test_source_file_checksum_unchanged_after_audit(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_line((0, 0), (1, 1))
    path = _save(doc, tmp_path / "unchanged.dxf")
    before = _sha256(path)

    result = inventory_dxf(path)

    assert _sha256(path) == before
    assert result.source_sha256 == before


def test_unresolved_block_reference_detected(tmp_path: Path) -> None:
    doc = _doc()
    doc.modelspace().add_blockref("MISSING_BLOCK", (0, 0))
    path = _save(doc, tmp_path / "missing_block.dxf")

    result = inventory_dxf(path)

    assert result.unresolved_block_references == ("MISSING_BLOCK",)
    assert "unresolved_block_reference" in {issue.code for issue in result.issues}

