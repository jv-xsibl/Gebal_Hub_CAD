"""Tests for Stage 9 normalized DXF layer rewriting."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import ezdxf
import pytest

from gebal_cad_normalizer.cad import (
    LayerRewriteConfig,
    canonicalize_dxf,
    classify_layers,
    inventory_dxf,
    rewrite_layers,
)
from gebal_cad_normalizer.exceptions import OutputWriteError


def _save(doc: ezdxf.document.Drawing, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(path)
    return path


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _rect(doc: ezdxf.document.Drawing, layer: str, x: float = 0.0, y: float = 0.0):
    if layer not in doc.layers:
        doc.layers.new(layer)
    return doc.modelspace().add_lwpolyline([(x, y), (x + 10, y), (x + 10, y + 5), (x, y + 5)], close=True, dxfattribs={"layer": layer})


def _line(doc: ezdxf.document.Drawing, layer: str, start=(0, 0), end=(1, 0)):
    if layer not in doc.layers:
        doc.layers.new(layer)
    return doc.modelspace().add_line(start, end, dxfattribs={"layer": layer})


def _classification(source: Path):
    return classify_layers(inventory_dxf(source), canonicalize_dxf(source))


def _rewrite(source: Path, output: Path, *, config: LayerRewriteConfig | None = None):
    return rewrite_layers(source, _classification(source), output, config)


def _layers(path: Path) -> list[str]:
    return [entity.dxf.layer for entity in ezdxf.readfile(path).modelspace()]


def _force_layer(classification, layer_name: str, *, role: str, confidence: float = 1.0):
    layers = []
    for layer in classification.layers:
        if layer.original_layer_name == layer_name:
            layers.append(layer.model_copy(update={"assigned_role": role, "confidence": confidence, "issues": (), "review_reason": None}))
        else:
            layers.append(layer)
    return classification.model_copy(update={"layers": tuple(layers)})


def test_confident_product_mapping(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_FOOTPRINT")
    source = _save(doc, tmp_path / "source.dxf")
    result = _rewrite(source, tmp_path / "out.dxf")

    assert _layers(result.output_path) == ["PRODUCT"]
    mapping = {row.original_layer_name: row for row in result.original_to_target_layer_mapping}
    assert mapping["PRODUCT_FOOTPRINT"].target_layer_name == "PRODUCT"


def test_safety_zone_mapping(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "FALL_IMPACT_AREA")
    source = _save(doc, tmp_path / "source.dxf")

    result = _rewrite(source, tmp_path / "out.dxf")

    assert _layers(result.output_path) == ["SAFETY_ZONE"]


def test_dimensions_text_hatch_foundation_mapping(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    doc.layers.new("DIMENSIONS")
    doc.layers.new("NOTES")
    doc.layers.new("SURFACE_HATCH")
    doc.layers.new("INSTALL_ANCHORS")
    msp = doc.modelspace()
    msp.add_linear_dim(base=(0, 2), p1=(0, 0), p2=(4, 0), dxfattribs={"layer": "DIMENSIONS"}).render()
    msp.add_text("note", dxfattribs={"layer": "NOTES"})
    msp.add_hatch(dxfattribs={"layer": "SURFACE_HATCH"})
    _rect(doc, "INSTALL_ANCHORS", 20, 0)
    source = _save(doc, tmp_path / "source.dxf")
    classification = _force_layer(_classification(source), "DIMENSIONS", role="dimensions")

    result = rewrite_layers(source, classification, tmp_path / "out.dxf")

    assert {"DIMENSIONS", "TEXT", "HATCH", "FOUNDATION"} <= set(_layers(result.output_path))


def test_several_source_layers_to_one_target(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    doc.layers.new("NOTES")
    doc.layers.new("ANNOTATION_TEXT")
    doc.modelspace().add_text("a", dxfattribs={"layer": "NOTES"})
    doc.modelspace().add_mtext("b", dxfattribs={"layer": "ANNOTATION_TEXT"})
    source = _save(doc, tmp_path / "source.dxf")

    result = _rewrite(source, tmp_path / "out.dxf")

    assert _layers(result.output_path) == ["TEXT", "TEXT"]
    entity_mappings = [row for row in result.original_to_target_layer_mapping if row.entity_count > 0]
    assert {row.target_layer_name for row in entity_mappings} == {"TEXT"}


def test_low_confidence_to_review(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _line(doc, "MISC")
    source = _save(doc, tmp_path / "source.dxf")

    result = _rewrite(source, tmp_path / "out.dxf")

    assert _layers(result.output_path) == ["REVIEW_REQUIRED"]
    assert {"low_confidence_classification", "ambiguous_layer_mapping"} & {issue.code for issue in result.issues}


def test_mixed_content_preserved_or_reviewed(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_WITH_NOTES")
    doc.modelspace().add_text("mixed", dxfattribs={"layer": "PRODUCT_WITH_NOTES"})
    source = _save(doc, tmp_path / "source.dxf")

    reviewed = _rewrite(source, tmp_path / "reviewed.dxf")
    preserved = _rewrite(source, tmp_path / "preserved.dxf", config=LayerRewriteConfig(uncertain_layer_action="preserve"))

    assert _layers(reviewed.output_path) == ["REVIEW_REQUIRED", "REVIEW_REQUIRED"]
    assert _layers(preserved.output_path) == ["PRODUCT_WITH_NOTES", "PRODUCT_WITH_NOTES"]
    assert "mixed_content_preserved" in {issue.code for issue in reviewed.issues}


def test_proxy_xref_preserved(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _line(doc, "XREF_VENDOR")
    _line(doc, "PROXY_VENDOR", (2, 0), (3, 0))
    source = _save(doc, tmp_path / "source.dxf")
    classification = _classification(source)
    layers = []
    for layer in classification.layers:
        if layer.original_layer_name == "XREF_VENDOR":
            layers.append(layer.model_copy(update={"assigned_role": "external_or_proxy", "confidence": 0.96, "evidence": layer.evidence.model_copy(update={"has_external_reference": True})}))
        elif layer.original_layer_name == "PROXY_VENDOR":
            layers.append(layer.model_copy(update={"assigned_role": "external_or_proxy", "confidence": 0.96, "evidence": layer.evidence.model_copy(update={"has_proxy": True})}))
        else:
            layers.append(layer)
    classification = classification.model_copy(update={"layers": tuple(layers)})

    result = rewrite_layers(source, classification, tmp_path / "out.dxf")

    assert _layers(result.output_path) == ["EXTERNAL", "REVIEW_REQUIRED"]
    assert {"external_reference_preserved", "proxy_entity_preserved"} <= {issue.code for issue in result.issues}


def test_blocks_and_inserts_preserved(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    doc.layers.new("PRODUCT_FOOTPRINT")
    block = doc.blocks.new("PRODUCT_BLOCK")
    block.add_line((0, 0), (2, 0), dxfattribs={"layer": "PRODUCT_FOOTPRINT"})
    doc.modelspace().add_blockref("PRODUCT_BLOCK", (10, 20), dxfattribs={"layer": "PRODUCT_FOOTPRINT", "rotation": 30, "xscale": 2})
    source = _save(doc, tmp_path / "source.dxf")
    classification = _force_layer(_classification(source), "PRODUCT_FOOTPRINT", role="product_geometry")

    result = rewrite_layers(source, classification, tmp_path / "out.dxf")
    written = ezdxf.readfile(result.output_path)
    written_insert = next(entity for entity in written.modelspace() if entity.dxftype() == "INSERT")

    assert written_insert.dxf.layer == "PRODUCT"
    assert written_insert.dxf.insert.x == 10
    assert written_insert.dxf.insert.y == 20
    assert written_insert.dxf.rotation == 30
    assert written_insert.dxf.xscale == 2
    assert next(iter(written.blocks.get("PRODUCT_BLOCK"))).dxf.layer == "PRODUCT"


def test_original_layer_evidence_retained(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_FOOTPRINT")
    source = _save(doc, tmp_path / "source.dxf")

    result = _rewrite(source, tmp_path / "out.dxf")
    entity = next(iter(ezdxf.readfile(result.output_path).modelspace()))
    payload = json.loads(entity.get_xdata("GEBAL_STAGE9_REWRITE_AUDIT")[0].value)

    assert payload["original_layer"] == "PRODUCT_FOOTPRINT"
    assert any(row.original_layer_name == "PRODUCT_FOOTPRINT" for row in result.original_to_target_layer_mapping)


def test_configurable_target_names(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_FOOTPRINT")
    source = _save(doc, tmp_path / "source.dxf")
    targets = dict(LayerRewriteConfig().target_layers)
    targets["product_geometry"] = "EQUIPMENT"

    result = _rewrite(source, tmp_path / "out.dxf", config=LayerRewriteConfig(target_layers=targets))

    assert _layers(result.output_path) == ["EQUIPMENT"]


def test_entity_count_and_extents_preserved(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_FOOTPRINT")
    _rect(doc, "FALL_IMPACT_AREA", -5, -5)
    source = _save(doc, tmp_path / "source.dxf")

    result = _rewrite(source, tmp_path / "out.dxf")

    assert result.preservation_checks.entity_count_preserved is True
    assert result.preservation_checks.modelspace_extents_preserved is True
    assert result.entity_totals_before == result.entity_totals_after
    assert result.modelspace_extents_before == result.modelspace_extents_after


def test_source_unchanged(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_FOOTPRINT")
    source = _save(doc, tmp_path / "source.dxf")
    before = _sha256(source)

    result = _rewrite(source, tmp_path / "out.dxf")

    assert _sha256(source) == before
    assert result.preservation_checks.source_checksum_unchanged is True


def test_atomic_failure_safety(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_FOOTPRINT")
    source = _save(doc, tmp_path / "source.dxf")
    destination = tmp_path / "existing.dxf"
    destination.write_text("original", encoding="utf-8")

    def fail_replace(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr("gebal_cad_normalizer.cad.rewrite.os.replace", fail_replace)
    with pytest.raises(OutputWriteError):
        _rewrite(source, destination)

    assert destination.read_text(encoding="utf-8") == "original"


def test_deterministic_output_and_mapping(tmp_path: Path) -> None:
    doc = ezdxf.new("R2010")
    _rect(doc, "PRODUCT_FOOTPRINT")
    _rect(doc, "FALL_IMPACT_AREA", 20, 0)
    source = _save(doc, tmp_path / "source.dxf")
    classification = _classification(source)

    first = rewrite_layers(source, classification, tmp_path / "first.dxf")
    second = rewrite_layers(source, classification, tmp_path / "second.dxf")

    assert first.original_to_target_layer_mapping == second.original_to_target_layer_mapping
    assert [entity.dxf.layer for entity in ezdxf.readfile(first.output_path).modelspace()] == [entity.dxf.layer for entity in ezdxf.readfile(second.output_path).modelspace()]
    assert not any(name.startswith("GEBAL") for name in LayerRewriteConfig().target_layers.values())
    assert first.preservation_checks.ai_used is False
