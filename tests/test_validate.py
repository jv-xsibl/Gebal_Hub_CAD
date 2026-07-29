"""Tests for Stage 11 JSON-vs-CAD validation."""

from __future__ import annotations

import copy
import json

import pytest

from gebal_cad_normalizer.cad.measure import MeasurementCandidate, MeasurementConfig, MeasurementEvidence, MeasurementIssue, MeasurementResult
from gebal_cad_normalizer.cad.validate import JsonCadExpectations, ValidationConfig, validate_json_against_cad


def _candidate(
    candidate_id: str,
    role: str,
    width_mm: float,
    depth_mm: float,
    *,
    area_raw: float | None = None,
    confidence: float = 0.9,
    unit_status: str = "explicit",
    unit: str | None = "mm",
    scale: float | None = 1.0,
    layer: str | None = None,
    warnings: tuple[str, ...] = (),
    review_reason: str | None = None,
    bbox: dict[str, float] | None = None,
) -> MeasurementCandidate:
    bbox = bbox or {"min_x": 0.0, "min_y": 0.0, "max_x": width_mm, "max_y": depth_mm}
    evidence = MeasurementEvidence(
        source_layer=layer or role,
        assigned_role=role if role in {"product_geometry", "safety_zone", "foundation_or_installation", "review_required"} else "review_required",
        classification_confidence=confidence,
        source_handles=(candidate_id,),
        block_ancestry=(),
        insert_handles=(),
        original_dxf_types=("LWPOLYLINE",),
        z_values=(),
        closed=True,
        curve_approximated=False,
        approximation_tolerance=None,
        geometry_count=1,
        hole_count=0,
        review_reason=review_reason,
    )
    return MeasurementCandidate(
        candidate_id=candidate_id,
        role=role,
        source_layer=layer or role,
        bounding_box=bbox,
        width=width_mm / (scale or 1.0),
        depth=depth_mm / (scale or 1.0),
        area=area_raw if area_raw is not None else (width_mm / (scale or 1.0)) * (depth_mm / (scale or 1.0)),
        perimeter=None,
        centroid=None,
        confidence=confidence,
        unit_status=unit_status,
        unit=unit,
        scale_to_mm=scale,
        width_mm=width_mm if scale is not None else None,
        depth_mm=depth_mm if scale is not None else None,
        warnings=warnings,
        review_reason=review_reason,
        evidence=evidence,
    )


def _measurement(*candidates: MeasurementCandidate, unit_status: str = "explicit", unit_evidence: dict | None = None, issues: tuple[MeasurementIssue, ...] = ()) -> MeasurementResult:
    return MeasurementResult(
        source_identity="fixture.dxf",
        source_sha256="abc",
        source_checksum_unchanged=True,
        drawing_units="mm",
        insunits=4,
        unit_status=unit_status,
        inferred_unit="mm" if unit_status in {"explicit", "inferred"} else None,
        scale_to_mm=1.0 if unit_status in {"explicit", "inferred"} else None,
        unit_evidence=unit_evidence or {"source": "$INSUNITS"},
        candidates=tuple(candidates),
        layer_measurements=(),
        issues=issues,
        config=MeasurementConfig(),
    )


def _expected(**kwargs) -> JsonCadExpectations:
    defaults = dict(product_length_mm=100.0, product_width_mm=50.0, safety_length_mm=200.0, safety_width_mm=150.0, falling_space_area_m2=30.0)
    defaults.update(kwargs)
    return JsonCadExpectations(sku="SKU1", **defaults)


def _check(result, field: str):
    return next(check for check in result.checks if check.field_path == field)


def test_exact_rotated_and_tolerance_matches() -> None:
    exact = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 100, 50), _candidate("S1", "safety_zone", 200, 150)))
    rotated = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 50, 100), _candidate("S1", "safety_zone", 150, 200)))
    tolerant = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 100.4, 50), _candidate("S1", "safety_zone", 200, 150)))

    assert _check(exact, "technical.dimensions.length_mm").status == "pass"
    assert _check(rotated, "technical.dimensions.width_mm").status == "pass"
    assert _check(tolerant, "technical.dimensions.length_mm").status == "pass"


def test_dimension_mismatch_fails() -> None:
    result = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 140, 50)))
    assert _check(result, "technical.dimensions.length_mm").status == "fail"
    assert result.overall_status == "fail"


def test_area_match_and_mismatch() -> None:
    ok = validate_json_against_cad(_expected(), _measurement(_candidate("S1", "safety_zone", 200, 150, area_raw=30_000_000)))
    bad = validate_json_against_cad(_expected(), _measurement(_candidate("S1", "safety_zone", 200, 150, area_raw=40_000_000)))

    assert _check(ok, "safety.falling_space_area_m2").status == "pass"
    assert _check(bad, "safety.falling_space_area_m2").status == "fail"


def test_product_and_safety_separation_and_containment() -> None:
    result = validate_json_against_cad(
        _expected(),
        _measurement(
            _candidate("P1", "product_geometry", 100, 50, bbox={"min_x": 25, "min_y": 25, "max_x": 125, "max_y": 75}),
            _candidate("S1", "safety_zone", 200, 150, bbox={"min_x": 0, "min_y": 0, "max_x": 200, "max_y": 150}),
        ),
    )
    assert _check(result, "technical.dimensions.length_mm").candidate_id == "P1"
    assert _check(result, "safety.safety_zone.length_mm").candidate_id == "S1"
    assert _check(result, "cad.top_view.product_inside_safety").status == "pass"


def test_containment_mismatch_fails() -> None:
    result = validate_json_against_cad(
        _expected(),
        _measurement(
            _candidate("P1", "product_geometry", 100, 50, bbox={"min_x": 0, "min_y": 0, "max_x": 100, "max_y": 50}),
            _candidate("S1", "safety_zone", 200, 150, bbox={"min_x": 10, "min_y": 10, "max_x": 210, "max_y": 160}),
        ),
    )
    assert _check(result, "cad.top_view.product_inside_safety").status == "fail"


def test_ambiguous_candidates_preserved_for_review() -> None:
    result = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 100, 50), _candidate("P2", "product_geometry", 100, 50)))
    check = _check(result, "technical.dimensions.length_mm")
    assert check.status == "review_required"
    assert [item.candidate_id for item in check.alternatives] == ["P1", "P2"]


def test_unknown_inferred_and_overridden_units_record_evidence() -> None:
    unknown = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 100, 50, unit_status="unknown", unit=None, scale=None), unit_status="unknown"))
    inferred = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 100, 50, unit_status="inferred"), unit_status="inferred", unit_evidence={"source": "expected_dimensions"}))
    override = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 100, 50), unit_evidence={"source": "explicit_override", "unit": "mm"}))

    assert _check(unknown, "technical.dimensions.length_mm").status == "not_verifiable"
    assert any(issue.code == "validation_unit_inference_used" for issue in inferred.issues)
    assert any(issue.code == "validation_unit_override_used" for issue in override.issues)


def test_invalid_zero_safety_json_is_source_data_issue() -> None:
    source = {"sku": "SKU1", "technical": {"dimensions": {"length_mm": 100, "width_mm": 50}}, "safety": {"safety_zone": {"length_mm": 0, "width_mm": 0}}}
    original = copy.deepcopy(source)
    result = validate_json_against_cad(source, _measurement(_candidate("S1", "safety_zone", 200, 150)))

    assert source == original
    assert result.overall_status == "fail"
    assert any(issue.code == "validation_invalid_source_json_value" for issue in result.issues)


def test_missing_values_and_candidates() -> None:
    no_json = validate_json_against_cad(JsonCadExpectations(sku="SKU1"), _measurement())
    no_candidate = validate_json_against_cad(_expected(), _measurement())

    assert _check(no_json, "technical.dimensions.length_mm").status == "not_verifiable"
    assert _check(no_candidate, "technical.dimensions.length_mm").status == "fail"


@pytest.mark.parametrize("code", ["measurement_opaque_region", "measurement_nonplanar_geometry", "measurement_unsupported_geometry"])
def test_region_nonplanar_proxy_xref_evidence_prevents_forced_pass(code: str) -> None:
    issue = MeasurementIssue(code=code, severity="warning", message=code, layer_name="product_geometry")
    result = validate_json_against_cad(_expected(), _measurement(_candidate("P1", "product_geometry", 100, 50), issues=(issue,)))
    assert _check(result, "technical.dimensions.length_mm").status == "review_required"


def test_non_top_view_fields_are_not_verifiable() -> None:
    result = validate_json_against_cad(_expected(product_height_mm=70, free_fall_height_mm=40), _measurement(_candidate("P1", "product_geometry", 100, 50)))
    assert _check(result, "technical.dimensions.height_mm").status == "not_verifiable"
    assert _check(result, "safety.cfh_mm").status == "not_verifiable"


def test_deterministic_output_and_input_immutability() -> None:
    source = {"sku": "SKU1", "technical": {"dimensions": {"length_mm": 100, "width_mm": 50}}, "safety": {"safety_zone": {"length_mm": 200, "width_mm": 150}}}
    original = copy.deepcopy(source)
    measurement = _measurement(_candidate("P1", "product_geometry", 100, 50), _candidate("S1", "safety_zone", 200, 150))
    first = validate_json_against_cad(source, measurement)
    second = validate_json_against_cad(source, measurement)

    assert source == original
    assert first.to_deterministic_json() == second.to_deterministic_json()
    assert json.loads(first.to_deterministic_json())["overall_status"] in {"pass", "pass_with_warnings"}
