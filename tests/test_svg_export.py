"""Tests for Stage 9.5 per-layer SVG export."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ezdxf
import pytest

from gebal_cad_normalizer.cad import SvgExportConfig, export_layer_svgs
from gebal_cad_normalizer.cad.oda import OdaConversionResult, OdaDirection
from gebal_cad_normalizer.cad.svg_export import SvgExportError


def _save(doc: ezdxf.document.Drawing, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _doc() -> ezdxf.document.Drawing:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 4
    return doc


def _layer(doc: ezdxf.document.Drawing, name: str) -> None:
    if name not in doc.layers:
        doc.layers.new(name)


def _export(source: Path, output: Path, **kwargs):
    return export_layer_svgs(source, output, **kwargs)


def _layer_manifest(result, name: str):
    return next(layer for layer in result.layers if layer.layer_name == name)


def _svg(output: Path, layer_manifest) -> str:
    return (output / layer_manifest.svg_filename).read_text(encoding="utf-8")


def test_line_layer_exports_svg(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "PRODUCT")
    doc.modelspace().add_line((0, 0), (10, 0), dxfattribs={"layer": "PRODUCT"})
    source = _save(doc, tmp_path / "line.dxf")

    result = _export(source, tmp_path / "svg")

    layer = _layer_manifest(result, "PRODUCT")
    assert layer.entity_count == 1
    assert layer.rendered_count == 1
    assert '<line x1="0" y1="0" x2="10" y2="0"' in _svg(tmp_path / "svg", layer)


def test_polyline_with_bulge_exports_arc_path(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "CURVE")
    doc.modelspace().add_lwpolyline([(0, 0, 0, 0, 1), (10, 0, 0, 0, 0)], format="xyseb", dxfattribs={"layer": "CURVE"})
    source = _save(doc, tmp_path / "bulge.dxf")

    result = _export(source, tmp_path / "svg")

    text = _svg(tmp_path / "svg", _layer_manifest(result, "CURVE"))
    assert " A " in text
    assert 'data-closed="false"' in text


def test_arc_and_circle_render(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "ROUND")
    msp = doc.modelspace()
    msp.add_arc((0, 0), 5, 0, 90, dxfattribs={"layer": "ROUND"})
    msp.add_circle((20, 0), 3, dxfattribs={"layer": "ROUND"})
    source = _save(doc, tmp_path / "round.dxf")

    result = _export(source, tmp_path / "svg")

    text = _svg(tmp_path / "svg", _layer_manifest(result, "ROUND"))
    assert "<path" in text
    assert "<circle" in text


def test_ellipse_and_spline_render(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "CURVES")
    msp = doc.modelspace()
    msp.add_ellipse((0, 0), (5, 0), 0.5, dxfattribs={"layer": "CURVES"})
    msp.add_spline([(10, 0), (12, 4), (15, 0)], dxfattribs={"layer": "CURVES"})
    source = _save(doc, tmp_path / "curves.dxf")

    result = _export(source, tmp_path / "svg")

    layer = _layer_manifest(result, "CURVES")
    assert layer.rendered_count == 2
    assert _svg(tmp_path / "svg", layer).count("<path") >= 2


def test_text_rendering_escapes_content_and_reports_fallback(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "TEXT")
    doc.modelspace().add_text("A&B <tag>", dxfattribs={"layer": "TEXT", "height": 2.5})
    source = _save(doc, tmp_path / "text.dxf")

    result = _export(source, tmp_path / "svg")

    layer = _layer_manifest(result, "TEXT")
    text = _svg(tmp_path / "svg", layer)
    assert "A&amp;B &lt;tag&gt;" in text
    assert "svg_text_fallback" in {issue.code for issue in layer.warnings}


def test_hatch_rendering_reports_simplification(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "HATCH")
    hatch = doc.modelspace().add_hatch(dxfattribs={"layer": "HATCH"})
    hatch.paths.add_polyline_path([(0, 0), (10, 0), (10, 5), (0, 5)], is_closed=True)
    source = _save(doc, tmp_path / "hatch.dxf")

    result = _export(source, tmp_path / "svg")

    layer = _layer_manifest(result, "HATCH")
    assert layer.rendered_count == 1
    assert "svg_hatch_simplified" in {issue.code for issue in layer.warnings}


def test_nested_insert_transform_renders_block_content(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "BLOCK_LAYER")
    child = doc.blocks.new("CHILD")
    child.add_line((0, 0), (2, 0), dxfattribs={"layer": "BLOCK_LAYER"})
    parent = doc.blocks.new("PARENT")
    parent.add_blockref("CHILD", (5, 0))
    doc.modelspace().add_blockref("PARENT", (10, 20), dxfattribs={"layer": "BLOCK_LAYER", "xscale": 2})
    source = _save(doc, tmp_path / "insert.dxf")

    result = _export(source, tmp_path / "svg")

    layer = _layer_manifest(result, "BLOCK_LAYER")
    assert layer.rendered_count >= 1
    assert layer.source_extents["min_x"] >= 10
    assert layer.source_extents["min_y"] == 20


def test_multiple_layers_produce_separate_svgs(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "PRODUCT"); _layer(doc, "SAFETY_ZONE")
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "PRODUCT"})
    doc.modelspace().add_line((0, 1), (1, 1), dxfattribs={"layer": "SAFETY_ZONE"})
    source = _save(doc, tmp_path / "layers.dxf")

    result = _export(source, tmp_path / "svg")

    names = {layer.layer_name: layer.svg_filename for layer in result.layers}
    assert names["PRODUCT"] != names["SAFETY_ZONE"]
    assert (tmp_path / "svg" / names["PRODUCT"]).exists()
    assert (tmp_path / "svg" / names["SAFETY_ZONE"]).exists()


def test_empty_layer_handling(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "EMPTY_REVIEW")
    source = _save(doc, tmp_path / "empty.dxf")

    result = _export(source, tmp_path / "svg")

    layer = _layer_manifest(result, "EMPTY_REVIEW")
    assert layer.entity_count == 0
    assert layer.rendered_count == 0
    assert "svg_empty_layer" in {issue.code for issue in layer.warnings}


def test_unsupported_content_reported(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "UNSUPPORTED")
    doc.modelspace().add_3dface([(0, 0, 0), (1, 0, 0), (1, 1, 1)], dxfattribs={"layer": "UNSUPPORTED"})
    source = _save(doc, tmp_path / "unsupported.dxf")

    result = _export(source, tmp_path / "svg")

    layer = _layer_manifest(result, "UNSUPPORTED")
    assert layer.skipped_count == 1
    assert "svg_entity_unsupported" in {issue.code for issue in result.warnings}


def test_deterministic_svg_and_manifest(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "PRODUCT")
    doc.modelspace().add_line((0, 0), (5, 0), dxfattribs={"layer": "PRODUCT"})
    source = _save(doc, tmp_path / "deterministic.dxf")

    first = _export(source, tmp_path / "first")
    second = _export(source, tmp_path / "second")

    first_layer = _layer_manifest(first, "PRODUCT")
    second_layer = _layer_manifest(second, "PRODUCT")
    assert _svg(tmp_path / "first", first_layer) == _svg(tmp_path / "second", second_layer)
    first_manifest = json.loads(first.to_manifest_json())
    second_manifest = json.loads(second.to_manifest_json())
    first_manifest["combined_svg"] = "OUT/combined.svg"
    second_manifest["combined_svg"] = "OUT/combined.svg"
    assert first_manifest == second_manifest


def test_source_checksum_unchanged(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "PRODUCT")
    doc.modelspace().add_line((0, 0), (5, 0), dxfattribs={"layer": "PRODUCT"})
    source = _save(doc, tmp_path / "source.dxf")
    before = _sha256(source)

    result = _export(source, tmp_path / "svg")

    assert _sha256(source) == before
    assert result.source_checksum_unchanged is True


def test_combined_svg_contains_all_renderable_layers(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "PRODUCT"); _layer(doc, "SAFETY_ZONE")
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "PRODUCT"})
    doc.modelspace().add_line((0, 1), (1, 1), dxfattribs={"layer": "SAFETY_ZONE"})
    source = _save(doc, tmp_path / "combined.dxf")

    result = _export(source, tmp_path / "svg", config=SvgExportConfig(include_combined=True))

    combined = (tmp_path / "svg" / "combined.svg").read_text(encoding="utf-8")
    assert result.combined_exported is True
    assert 'data-layer="PRODUCT"' in combined
    assert 'data-layer="SAFETY_ZONE"' in combined


def test_sanitized_duplicate_layer_names_remain_unique(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "A+B"); _layer(doc, "A B")
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "A+B"})
    doc.modelspace().add_line((0, 1), (1, 1), dxfattribs={"layer": "A B"})
    source = _save(doc, tmp_path / "dupes.dxf")

    result = _export(source, tmp_path / "svg")

    files = [layer.svg_filename for layer in result.layers if layer.layer_name in {"A+B", "A B"}]
    assert len(files) == 2
    assert len(set(files)) == 2
    assert all("GEBAL" not in name for name in files)


def test_dwg_input_routes_through_mocked_oda_conversion(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "PRODUCT")
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "PRODUCT"})
    converted_source = _save(doc, tmp_path / "converted.dxf")
    dwg = tmp_path / "source.dwg"
    dwg.write_bytes(b"mock-dwg")

    class FakeOda:
        def convert(self, request):
            request.destination_path.write_bytes(converted_source.read_bytes())
            return OdaConversionResult(source_path=request.source_path, destination_path=request.destination_path, direction=OdaDirection.DWG_TO_DXF, requested_cad_version=request.target_version, exit_code=0, stdout="", stderr="", elapsed_seconds=0.01, output_size_bytes=request.destination_path.stat().st_size)

    result = _export(dwg, tmp_path / "svg", oda_converter=FakeOda())

    assert result.source_file == dwg
    assert result.working_dxf_path.suffix == ".dxf"
    assert _layer_manifest(result, "PRODUCT").rendered_count == 1


def test_output_directory_failure_is_safe(tmp_path: Path) -> None:
    doc = _doc(); _layer(doc, "PRODUCT")
    doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "PRODUCT"})
    source = _save(doc, tmp_path / "source.dxf")
    output_file = tmp_path / "svg"
    output_file.write_text("existing", encoding="utf-8")

    with pytest.raises(SvgExportError):
        _export(source, output_file)

    assert output_file.read_text(encoding="utf-8") == "existing"


