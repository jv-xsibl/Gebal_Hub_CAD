"""Tests for Stage 12 integrated reporting packages."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path

import ezdxf
import pytest

from gebal_cad_normalizer.cad.oda import OdaConversionResult, OdaDirection
from gebal_cad_normalizer.reporting import run_reporting_pipeline


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save_product_dxf(path: Path, *, units: bool = True, filename: str | None = None) -> Path:
    target = path if filename is None else path.with_name(filename)
    doc = ezdxf.new("R2010")
    if units:
        doc.header["$INSUNITS"] = 4
    for layer in ("Lg_prod", "Lg_area"):
        if layer not in doc.layers:
            doc.layers.new(layer)
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (100, 0), (100, 50), (0, 50)], close=True, dxfattribs={"layer": "Lg_prod"})
    msp.add_lwpolyline([(-50, -50), (150, -50), (150, 100), (-50, 100)], close=True, dxfattribs={"layer": "Lg_area"})
    target.parent.mkdir(parents=True, exist_ok=True)
    doc.saveas(target)
    return target


def _json(path: Path, sku: str = "SKU100M", *, length: float = 100, width: float = 50) -> Path:
    payload = {
        "sku": sku,
        "name": "Fixture Product",
        "media": {"top_view_cad_file": {"file_name": f"{sku}_Top_View.dwg", "url": "https://example.invalid/cad.dwg", "file_type": "dwg", "purpose": "top view"}},
        "technical": {"dimensions": {"length_mm": length, "width_mm": width, "height_mm": 25}},
        "safety": {"safety_zone": {"length_mm": 200, "width_mm": 150}, "cfh_mm": 30},
    }
    path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
    return path


def _package(out: Path) -> Path:
    packages = [item for item in out.iterdir() if item.is_dir() and not item.name.startswith(".")]
    assert len(packages) == 1
    return packages[0]


def _normalized_manifest(manifest: dict) -> dict:
    clone = copy.deepcopy(manifest)
    clone["elapsed_seconds"] = 0
    clone["timings_seconds"] = {key: 0 for key in clone["timings_seconds"]}
    for artifact in clone.get("artifacts", {}).values():
        artifact["sha256"] = "sha"
    for row in clone.get("files", []):
        row["sha256"] = "sha"
        row["size_bytes"] = 0
    return clone


def _normalized_report(text: str) -> str:
    return re.sub(r"\([0-9.e-]+s\)", "(0s)", text)


def test_successful_package_contains_core_outputs(tmp_path: Path) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json")

    manifest = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")
    package = _package(tmp_path / "out")

    assert manifest["overall_status"] in {"pass", "pass_with_warnings", "review_required"}
    assert (package / "manifest.json").exists()
    assert (package / "report.md").exists()
    assert (package / "normalized" / "SKU100M_normalized.dxf").exists()
    assert (package / "svg" / "manifest.json").exists()
    assert (package / "reports" / "inventory.json").exists()
    assert (package / "reports" / "classification.json").exists()
    assert (package / "reports" / "measurement.json").exists()
    assert (package / "reports" / "validation.json").exists()
    assert manifest["source_checksums_unchanged"] is True
    assert manifest["artifacts"]["normalized_dxf"]["path"] == "normalized/SKU100M_normalized.dxf"


def test_review_or_fail_package_records_validation_issues(tmp_path: Path) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json", length=999, width=50)

    manifest = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")

    assert manifest["overall_status"] == "fail"
    assert any(issue["code"] == "validation_dimension_mismatch" for issue in manifest["issues"]["validation"])
    assert (_package(tmp_path / "out") / "report.md").read_text(encoding="utf-8").count("validation_dimension_mismatch") >= 1


def test_partial_stage_failure_still_produces_manifest_and_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json")

    def boom(*_args, **_kwargs):
        raise RuntimeError("measurement failed deliberately")

    monkeypatch.setattr("gebal_cad_normalizer.reporting.measure_geometry", boom)
    manifest = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")
    package = _package(tmp_path / "out")

    assert manifest["overall_status"] == "fail"
    assert manifest["stage_statuses"]["measurement"] == "fail"
    assert (package / "manifest.json").exists()
    assert (package / "report.md").exists()


def test_atomic_safety_refuses_silent_overwrite(tmp_path: Path) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json")
    run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")
    package = _package(tmp_path / "out")
    marker = package / "marker.txt"
    marker.write_text("keep", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")

    assert marker.read_text(encoding="utf-8") == "keep"


def test_manifest_and_report_are_deterministic_after_normalizing_timings(tmp_path: Path) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json")
    first = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")
    report_a = (_package(tmp_path / "out") / "report.md").read_text(encoding="utf-8")
    second = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm", allow_overwrite=True)
    report_b = (_package(tmp_path / "out") / "report.md").read_text(encoding="utf-8")

    assert _normalized_manifest(first) == _normalized_manifest(second)
    assert _normalized_report(report_a) == _normalized_report(report_b)


def test_checksum_preservation_and_input_immutability(tmp_path: Path) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json")
    before = (_sha256(cad), _sha256(js), cad.read_bytes(), js.read_text(encoding="utf-8"))

    manifest = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")

    assert (_sha256(cad), _sha256(js), cad.read_bytes(), js.read_text(encoding="utf-8")) == before
    assert manifest["source_checksums"]["cad_sha256"] == before[0]
    assert manifest["source_checksums_unchanged"] is True


def test_repeated_sku_occurrence_identity_uses_paths(tmp_path: Path) -> None:
    cad1 = _save_product_dxf(tmp_path / "one" / "source.dxf")
    cad2 = _save_product_dxf(tmp_path / "two" / "source.dxf")
    js1 = _json(tmp_path / "one" / "product.json", sku="SKU100M")
    js2 = _json(tmp_path / "two" / "product.json", sku="SKU100M")

    first = run_reporting_pipeline(json_path=js1, input_path=cad1, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")
    second = run_reporting_pipeline(json_path=js2, input_path=cad2, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")

    assert first["sku"] == second["sku"] == "SKU100M"
    assert first["occurrence_id"] != second["occurrence_id"]
    assert len([item for item in (tmp_path / "out").iterdir() if item.is_dir()]) == 2


def test_filename_mismatch_evidence_is_preserved(tmp_path: Path) -> None:
    cad = _save_product_dxf(tmp_path / "top_view_175332M.dxf")
    js = _json(tmp_path / "product.json", sku="175532M")

    manifest = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm")

    assert manifest["filename_mismatch_evidence"]["mismatch"] is True
    assert manifest["filename_mismatch_evidence"]["sku_tokens_in_filename"] == ["175332M"]


def test_optional_dwg_export_uses_existing_oda_wrapper(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json")

    class FakeOda:
        def __init__(self, *_args, **_kwargs):
            pass

        def convert(self, request):
            request.destination_path.write_bytes(b"mock dwg")
            return OdaConversionResult(source_path=request.source_path, destination_path=request.destination_path, direction=OdaDirection.DXF_TO_DWG, requested_cad_version=request.target_version, exit_code=0, stdout="", stderr="", elapsed_seconds=0.01, output_size_bytes=request.destination_path.stat().st_size)

    monkeypatch.setattr("gebal_cad_normalizer.reporting.OdaConverter", FakeOda)
    manifest = run_reporting_pipeline(json_path=js, input_path=cad, output_dir=tmp_path / "out", vendor_profile="bluestone_playground", unit="mm", export_dwg=True)
    package = _package(tmp_path / "out")

    assert manifest["stage_statuses"]["dwg_export"] == "pass"
    assert (package / "normalized" / "SKU100M_normalized.dwg").read_bytes() == b"mock dwg"
    assert manifest["artifacts"]["normalized_dwg"]["path"] == "normalized/SKU100M_normalized.dwg"

def test_stage_timeout_still_produces_partial_manifest_and_report(tmp_path: Path) -> None:
    cad = _save_product_dxf(tmp_path / "source.dxf")
    js = _json(tmp_path / "product.json")

    manifest = run_reporting_pipeline(
        json_path=js,
        input_path=cad,
        output_dir=tmp_path / "out",
        vendor_profile="bluestone_playground",
        unit="mm",
        stage_timeouts_seconds={"canonicalization": 0.01},
    )
    package = _package(tmp_path / "out")

    assert manifest["overall_status"] == "fail"
    assert manifest["stage_statuses"]["canonicalization"] == "fail"
    assert any(issue["code"] == "stage_timeout" for issue in manifest["issues"]["cad"])
    assert (package / "manifest.json").exists()
    assert (package / "report.md").exists()

