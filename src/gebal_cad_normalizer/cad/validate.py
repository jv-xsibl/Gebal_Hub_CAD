"""Stage 11 deterministic JSON-vs-CAD validation."""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Mapping

from pydantic import Field

from gebal_cad_normalizer.cad.measure import MeasurementCandidate, MeasurementResult
from gebal_cad_normalizer.exceptions import OutputWriteError
from gebal_cad_normalizer.models import CadProcessingRequest, StrictModel


ValidationStatus = Literal["pass", "pass_with_warnings", "review_required", "fail", "not_verifiable"]
IssueSeverity = Literal["info", "warning", "fail", "not_verifiable"]

_REVIEW_MEASUREMENT_CODES = {
    "measurement_nonplanar_geometry",
    "measurement_unsupported_geometry",
    "measurement_opaque_region",
    "measurement_block_double_count_risk",
    "measurement_low_confidence",
}
_UNIT_REVIEW_STATUSES = {"unknown", "ambiguous"}
_NOT_TOP_VIEW_FIELDS = (
    "technical.dimensions.height_mm",
    "safety.cfh_mm",
    "safety.free_fall_height_mm",
    "technical.weight",
    "technical.materials",
    "technical.age_range",
)


class ValidationIssueCode(str, Enum):
    """Stable Stage 11 validation issue codes."""

    MISSING_JSON_VALUE = "validation_missing_json_value"
    INVALID_SOURCE_JSON_VALUE = "validation_invalid_source_json_value"
    MISSING_CAD_CANDIDATE = "validation_missing_cad_candidate"
    DIMENSION_MISMATCH = "validation_dimension_mismatch"
    AREA_MISMATCH = "validation_area_mismatch"
    CLOSE_MATCH = "validation_close_match"
    AMBIGUOUS_CANDIDATES = "validation_ambiguous_candidates"
    WEAK_CLASSIFICATION = "validation_weak_classification"
    UNITS_UNKNOWN = "validation_units_unknown"
    UNIT_INFERENCE_USED = "validation_unit_inference_used"
    UNIT_OVERRIDE_USED = "validation_unit_override_used"
    CAD_RISK_EVIDENCE = "validation_cad_risk_evidence"
    CONTAINMENT_MISMATCH = "validation_containment_mismatch"
    TOP_VIEW_NOT_VERIFIABLE = "validation_top_view_not_verifiable"


class ValidationIssue(StrictModel):
    """Machine-readable validation issue."""

    code: str
    severity: IssueSeverity
    message: str
    field_path: str | None = None
    candidate_id: str | None = None
    evidence: dict[str, Any] = {}


class ValidationConfig(StrictModel):
    """Configurable Stage 11 comparison tolerances."""

    dimension_abs_tolerance_mm: float = Field(default=5.0, ge=0.0)
    dimension_warning_abs_tolerance_mm: float = Field(default=10.0, ge=0.0)
    dimension_relative_tolerance: float = Field(default=0.005, ge=0.0)
    dimension_warning_relative_tolerance: float = Field(default=0.01, ge=0.0)
    area_abs_tolerance_m2: float = Field(default=0.1, ge=0.0)
    area_warning_abs_tolerance_m2: float = Field(default=0.25, ge=0.0)
    area_relative_tolerance: float = Field(default=0.01, ge=0.0)
    area_warning_relative_tolerance: float = Field(default=0.02, ge=0.0)
    weak_candidate_confidence: float = Field(default=0.55, ge=0.0, le=1.0)


class JsonCadExpectations(StrictModel):
    """Top-view-relevant product data extracted from normalized JSON."""

    sku: str | None = None
    product_length_mm: float | None = None
    product_width_mm: float | None = None
    product_height_mm: float | None = None
    safety_length_mm: float | None = None
    safety_width_mm: float | None = None
    falling_space_area_m2: float | None = None
    impact_area_m2: float | None = None
    free_fall_height_mm: float | None = None
    source_issues: tuple[ValidationIssue, ...] = ()


class CandidateValidation(StrictModel):
    """Validation score for one Stage 10 candidate."""

    candidate_id: str
    role: str
    source_layer: str
    status: ValidationStatus
    confidence: float
    compared_fields: tuple[str, ...]
    dimension_delta_mm: float | None = None
    dimension_delta_ratio: float | None = None
    area_delta_m2: float | None = None
    area_delta_ratio: float | None = None
    ranking_score: tuple[float, ...]
    issues: tuple[ValidationIssue, ...] = ()


class FieldValidation(StrictModel):
    """Validation result for one top-view or explicitly unverifiable field."""

    field_path: str
    status: ValidationStatus
    message: str
    candidate_id: str | None = None
    issues: tuple[ValidationIssue, ...] = ()
    alternatives: tuple[CandidateValidation, ...] = ()


class JsonCadValidationResult(StrictModel):
    """Complete Stage 11 JSON-vs-CAD validation result."""

    sku: str | None
    overall_status: ValidationStatus
    unit_status: str
    unit_evidence: dict[str, Any]
    checks: tuple[FieldValidation, ...]
    candidate_rankings: tuple[CandidateValidation, ...]
    issues: tuple[ValidationIssue, ...]
    config: ValidationConfig

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for reports and regression tests."""

        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def validate_json_against_cad(
    product: CadProcessingRequest | Mapping[str, Any] | JsonCadExpectations,
    measurement: MeasurementResult,
    config: ValidationConfig | None = None,
) -> JsonCadValidationResult:
    """Compare normalized JSON expectations to Stage 10 CAD measurements."""

    cfg = config or ValidationConfig()
    expected = extract_json_expectations(product)
    global_issues = list(expected.source_issues)
    global_issues.extend(_unit_issues(measurement))
    global_issues.extend(_cad_risk_issues(measurement))

    rankings: list[CandidateValidation] = []
    checks: list[FieldValidation] = []

    product_rankings = _rank_dimension_candidates(
        measurement.candidates,
        "product_geometry",
        ("technical.dimensions.length_mm", "technical.dimensions.width_mm"),
        expected.product_length_mm,
        expected.product_width_mm,
        measurement,
        cfg,
    )
    rankings.extend(product_rankings)
    checks.append(_dimension_check("technical.dimensions.length_mm", product_rankings, expected.product_length_mm, expected.product_width_mm, "product footprint"))
    checks.append(_dimension_check("technical.dimensions.width_mm", product_rankings, expected.product_length_mm, expected.product_width_mm, "product footprint"))

    safety_rankings = _rank_dimension_candidates(
        measurement.candidates,
        "safety_zone",
        ("safety.safety_zone.length_mm", "safety.safety_zone.width_mm"),
        expected.safety_length_mm,
        expected.safety_width_mm,
        measurement,
        cfg,
    )
    rankings.extend(safety_rankings)
    checks.append(_dimension_check("safety.safety_zone.length_mm", safety_rankings, expected.safety_length_mm, expected.safety_width_mm, "safety zone"))
    checks.append(_dimension_check("safety.safety_zone.width_mm", safety_rankings, expected.safety_length_mm, expected.safety_width_mm, "safety zone"))

    if expected.falling_space_area_m2 is not None:
        area_rankings = _rank_area_candidates(measurement.candidates, expected.falling_space_area_m2, "safety.falling_space_area_m2", measurement, cfg)
        rankings.extend(area_rankings)
        checks.append(_area_check("safety.falling_space_area_m2", area_rankings))
    else:
        checks.append(_missing_json_check("safety.falling_space_area_m2"))
    if expected.impact_area_m2 is not None:
        area_rankings = _rank_area_candidates(measurement.candidates, expected.impact_area_m2, "safety.impact_area_m2", measurement, cfg)
        rankings.extend(area_rankings)
        checks.append(_area_check("safety.impact_area_m2", area_rankings))
    else:
        checks.append(_missing_json_check("safety.impact_area_m2"))

    checks.append(_containment_check(measurement, cfg))
    checks.extend(_top_view_unverifiable_checks(expected))

    all_issues = global_issues + [issue for check in checks for issue in check.issues] + [issue for ranking in rankings for issue in ranking.issues]
    overall = _overall_status(tuple(checks), tuple(all_issues))
    return JsonCadValidationResult(
        sku=expected.sku,
        overall_status=overall,
        unit_status=measurement.unit_status,
        unit_evidence=measurement.unit_evidence,
        checks=tuple(sorted(checks, key=lambda item: item.field_path)),
        candidate_rankings=tuple(sorted(rankings, key=lambda item: (item.role, item.ranking_score, item.source_layer, item.candidate_id))),
        issues=tuple(sorted(all_issues, key=lambda item: (item.severity, item.code, item.field_path or "", item.candidate_id or "", item.message))),
        config=cfg,
    )


def extract_json_expectations(product: CadProcessingRequest | Mapping[str, Any] | JsonCadExpectations) -> JsonCadExpectations:
    """Extract Stage 11 values without mutating caller-owned JSON."""

    if isinstance(product, JsonCadExpectations):
        return product
    if isinstance(product, CadProcessingRequest):
        dims = product.expected_dimensions
        safety = product.expected_safety
        return JsonCadExpectations(
            sku=product.product.sku,
            product_length_mm=dims.length_mm if dims else None,
            product_width_mm=dims.width_mm if dims else None,
            product_height_mm=dims.height_mm if dims else None,
            safety_length_mm=safety.safety_zone_length_mm if safety else None,
            safety_width_mm=safety.safety_zone_width_mm if safety else None,
            falling_space_area_m2=safety.falling_space_area_m2 if safety else None,
            impact_area_m2=safety.impact_area_m2 if safety else None,
            free_fall_height_mm=safety.free_fall_height_mm if safety else None,
        )
    issues: list[ValidationIssue] = []
    return JsonCadExpectations(
        sku=_text(product, "sku"),
        product_length_mm=_positive(product, "technical.dimensions.length_mm", issues),
        product_width_mm=_positive(product, "technical.dimensions.width_mm", issues),
        product_height_mm=_positive(product, "technical.dimensions.height_mm", issues, issue_invalid=False),
        safety_length_mm=_positive(product, "safety.safety_zone.length_mm", issues),
        safety_width_mm=_positive(product, "safety.safety_zone.width_mm", issues),
        falling_space_area_m2=_positive(product, "safety.falling_space_area_m2", issues),
        impact_area_m2=_positive(product, "safety.impact_area_m2", issues),
        free_fall_height_mm=_positive(product, "safety.cfh_mm", issues, issue_invalid=False),
        source_issues=tuple(issues),
    )


def write_validation_json(result: JsonCadValidationResult, output_path: Path | str) -> Path:
    """Write deterministic validation JSON to an explicit path."""

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_deterministic_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write validation JSON report: {path}") from exc
    return path


def _rank_dimension_candidates(
    candidates: tuple[MeasurementCandidate, ...],
    role: str,
    fields: tuple[str, str],
    expected_a: float | None,
    expected_b: float | None,
    measurement: MeasurementResult,
    config: ValidationConfig,
) -> list[CandidateValidation]:
    role_candidates = [candidate for candidate in candidates if candidate.role == role]
    if expected_a is None or expected_b is None:
        return []
    ranked = [_dimension_candidate(candidate, fields, expected_a, expected_b, measurement, config) for candidate in role_candidates]
    return sorted(ranked, key=lambda item: (item.ranking_score, item.source_layer, item.candidate_id))


def _dimension_candidate(
    candidate: MeasurementCandidate,
    fields: tuple[str, str],
    expected_a: float,
    expected_b: float,
    measurement: MeasurementResult,
    config: ValidationConfig,
) -> CandidateValidation:
    issues = _candidate_gate_issues(candidate, fields[0], measurement, config)
    if candidate.width_mm is None or candidate.depth_mm is None or candidate.unit_status in _UNIT_REVIEW_STATUSES:
        status: ValidationStatus = "not_verifiable"
        delta = ratio = None
    else:
        delta, ratio = _dimension_delta((candidate.width_mm, candidate.depth_mm), (expected_a, expected_b))
        status = _tolerance_status(delta, ratio, config.dimension_abs_tolerance_mm, config.dimension_relative_tolerance, config.dimension_warning_abs_tolerance_mm, config.dimension_warning_relative_tolerance)
        if status == "fail":
            issues.append(_issue(ValidationIssueCode.DIMENSION_MISMATCH, "fail", "CAD dimensions differ from JSON beyond configured tolerance.", fields[0], candidate))
        elif status == "pass_with_warnings":
            issues.append(_issue(ValidationIssueCode.CLOSE_MATCH, "warning", "CAD dimensions are a close match within warning tolerance.", fields[0], candidate))
    status = _gate_status(status, issues)
    return CandidateValidation(
        candidate_id=candidate.candidate_id,
        role=candidate.role,
        source_layer=candidate.source_layer,
        status=status,
        confidence=candidate.confidence,
        compared_fields=fields,
        dimension_delta_mm=delta,
        dimension_delta_ratio=ratio,
        ranking_score=_score(status, delta, ratio, candidate),
        issues=tuple(issues),
    )


def _rank_area_candidates(
    candidates: tuple[MeasurementCandidate, ...],
    expected_area_m2: float,
    field: str,
    measurement: MeasurementResult,
    config: ValidationConfig,
) -> list[CandidateValidation]:
    ranked = []
    for candidate in candidates:
        if candidate.role != "safety_zone":
            continue
        issues = _candidate_gate_issues(candidate, field, measurement, config)
        cad_area_m2 = candidate.area * (candidate.scale_to_mm or 0) ** 2 / 1_000_000 if candidate.area is not None and candidate.scale_to_mm else None
        if cad_area_m2 is None or candidate.unit_status in _UNIT_REVIEW_STATUSES:
            status: ValidationStatus = "not_verifiable"
            delta = ratio = None
        else:
            delta = abs(cad_area_m2 - expected_area_m2)
            ratio = delta / expected_area_m2
            status = _tolerance_status(delta, ratio, config.area_abs_tolerance_m2, config.area_relative_tolerance, config.area_warning_abs_tolerance_m2, config.area_warning_relative_tolerance)
            if status == "fail":
                issues.append(_issue(ValidationIssueCode.AREA_MISMATCH, "fail", "CAD safety area differs from JSON beyond configured tolerance.", field, candidate))
            elif status == "pass_with_warnings":
                issues.append(_issue(ValidationIssueCode.CLOSE_MATCH, "warning", "CAD safety area is a close match within warning tolerance.", field, candidate))
        status = _gate_status(status, issues)
        ranked.append(
            CandidateValidation(
                candidate_id=candidate.candidate_id,
                role=candidate.role,
                source_layer=candidate.source_layer,
                status=status,
                confidence=candidate.confidence,
                compared_fields=(field,),
                area_delta_m2=delta,
                area_delta_ratio=ratio,
                ranking_score=_score(status, delta, ratio, candidate),
                issues=tuple(issues),
            )
        )
    return sorted(ranked, key=lambda item: (item.ranking_score, item.source_layer, item.candidate_id))


def _dimension_check(field: str, rankings: list[CandidateValidation], expected_a: float | None, expected_b: float | None, label: str) -> FieldValidation:
    if expected_a is None or expected_b is None:
        return _missing_json_check(field)
    if not rankings:
        issue = ValidationIssue(code=ValidationIssueCode.MISSING_CAD_CANDIDATE.value, severity="fail", message=f"No CAD {label} candidate was available.", field_path=field)
        return FieldValidation(field_path=field, status="fail", message=issue.message, issues=(issue,))
    return _ranked_check(field, rankings, f"Best CAD {label} candidate compared against JSON.")


def _area_check(field: str, rankings: list[CandidateValidation]) -> FieldValidation:
    if not rankings:
        issue = ValidationIssue(code=ValidationIssueCode.MISSING_CAD_CANDIDATE.value, severity="fail", message="No CAD safety-zone area candidate was available.", field_path=field)
        return FieldValidation(field_path=field, status="fail", message=issue.message, issues=(issue,))
    return _ranked_check(field, rankings, "Best CAD safety-zone area candidate compared against JSON.")


def _ranked_check(field: str, rankings: list[CandidateValidation], message: str) -> FieldValidation:
    best = rankings[0]
    issues = list(best.issues)
    if len(rankings) > 1 and rankings[1].ranking_score[:3] == best.ranking_score[:3]:
        issues.append(ValidationIssue(code=ValidationIssueCode.AMBIGUOUS_CANDIDATES.value, severity="warning", message="Multiple CAD candidates have equivalent validation rank.", field_path=field, candidate_id=best.candidate_id))
        status = _worse_status(best.status, "review_required")
    else:
        status = best.status
    return FieldValidation(field_path=field, status=status, message=message, candidate_id=best.candidate_id, issues=tuple(issues), alternatives=tuple(rankings))


def _containment_check(measurement: MeasurementResult, config: ValidationConfig) -> FieldValidation:
    products = [c for c in measurement.candidates if c.role == "product_geometry"]
    safeties = [c for c in measurement.candidates if c.role == "safety_zone"]
    field = "cad.top_view.product_inside_safety"
    if not products or not safeties:
        issue = ValidationIssue(code=ValidationIssueCode.MISSING_CAD_CANDIDATE.value, severity="not_verifiable", message="Product-inside-safety containment needs product and safety CAD candidates.", field_path=field)
        return FieldValidation(field_path=field, status="not_verifiable", message=issue.message, issues=(issue,))
    product = sorted(products, key=lambda c: (-c.confidence, c.source_layer, c.candidate_id))[0]
    safety = sorted(safeties, key=lambda c: (-c.confidence, c.source_layer, c.candidate_id))[0]
    issues = _candidate_gate_issues(product, field, measurement, config) + _candidate_gate_issues(safety, field, measurement, config)
    inside = _bbox_contains(safety.bounding_box, product.bounding_box)
    if not inside:
        issues.append(ValidationIssue(code=ValidationIssueCode.CONTAINMENT_MISMATCH.value, severity="fail", message="Best safety-zone bounds do not contain best product bounds.", field_path=field, candidate_id=safety.candidate_id))
        status: ValidationStatus = "fail"
    else:
        status = "pass"
    status = _gate_status(status, issues)
    return FieldValidation(field_path=field, status=status, message="Product footprint is checked against safety-zone containment.", candidate_id=safety.candidate_id, issues=tuple(issues))


def _top_view_unverifiable_checks(expected: JsonCadExpectations) -> list[FieldValidation]:
    checks = []
    present = {
        "technical.dimensions.height_mm": expected.product_height_mm,
        "safety.cfh_mm": expected.free_fall_height_mm,
        "safety.free_fall_height_mm": expected.free_fall_height_mm,
    }
    for field in _NOT_TOP_VIEW_FIELDS:
        evidence = {"json_value_present": present.get(field) is not None} if field in present else {}
        issue = ValidationIssue(code=ValidationIssueCode.TOP_VIEW_NOT_VERIFIABLE.value, severity="not_verifiable", message="Field is not verifiable from top-view CAD.", field_path=field, evidence=evidence)
        checks.append(FieldValidation(field_path=field, status="not_verifiable", message=issue.message, issues=(issue,)))
    return checks


def _candidate_gate_issues(candidate: MeasurementCandidate, field: str, measurement: MeasurementResult, config: ValidationConfig) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    if candidate.unit_status == "unknown" or measurement.unit_status == "unknown":
        issues.append(_issue(ValidationIssueCode.UNITS_UNKNOWN, "not_verifiable", "CAD units are unknown; millimetre comparison is not authoritative.", field, candidate, measurement.unit_evidence))
    elif candidate.unit_status == "ambiguous" or measurement.unit_status == "ambiguous":
        issues.append(_issue(ValidationIssueCode.UNITS_UNKNOWN, "not_verifiable", "CAD unit inference is ambiguous.", field, candidate, measurement.unit_evidence))
    elif candidate.unit_status == "inferred":
        issues.append(_issue(ValidationIssueCode.UNIT_INFERENCE_USED, "warning", "CAD units were inferred from expected dimensions.", field, candidate, measurement.unit_evidence))
    elif measurement.unit_evidence.get("source") == "explicit_override":
        issues.append(_issue(ValidationIssueCode.UNIT_OVERRIDE_USED, "warning", "CAD units came from an explicit override.", field, candidate, measurement.unit_evidence))
    if candidate.role == "review_required" or candidate.confidence < config.weak_candidate_confidence:
        issues.append(_issue(ValidationIssueCode.WEAK_CLASSIFICATION, "warning", "Candidate classification is weak or review-required.", field, candidate, {"confidence": candidate.confidence, "review_reason": candidate.review_reason}))
    if any(warning in {"non-planar Z evidence", "classification requires review"} for warning in candidate.warnings):
        issues.append(_issue(ValidationIssueCode.CAD_RISK_EVIDENCE, "warning", "Candidate carries CAD evidence that requires review.", field, candidate, {"warnings": candidate.warnings}))
    risk_codes = sorted({issue.code for issue in measurement.issues if issue.code in _REVIEW_MEASUREMENT_CODES and (issue.layer_name in {None, candidate.source_layer})})
    if risk_codes:
        issues.append(_issue(ValidationIssueCode.CAD_RISK_EVIDENCE, "warning", "Measurement result contains CAD risk evidence.", field, candidate, {"measurement_issue_codes": risk_codes}))
    return issues


def _unit_issues(measurement: MeasurementResult) -> list[ValidationIssue]:
    if measurement.unit_status in _UNIT_REVIEW_STATUSES:
        return [ValidationIssue(code=ValidationIssueCode.UNITS_UNKNOWN.value, severity="not_verifiable", message="CAD units are not authoritative for JSON comparison.", evidence=measurement.unit_evidence)]
    if measurement.unit_status == "inferred":
        return [ValidationIssue(code=ValidationIssueCode.UNIT_INFERENCE_USED.value, severity="warning", message="CAD units were inferred and recorded as evidence.", evidence=measurement.unit_evidence)]
    if measurement.unit_evidence.get("source") == "explicit_override":
        return [ValidationIssue(code=ValidationIssueCode.UNIT_OVERRIDE_USED.value, severity="warning", message="CAD unit override was applied and recorded as evidence.", evidence=measurement.unit_evidence)]
    return []


def _cad_risk_issues(measurement: MeasurementResult) -> list[ValidationIssue]:
    codes = sorted({issue.code for issue in measurement.issues if issue.code in _REVIEW_MEASUREMENT_CODES})
    if not codes:
        return []
    return [ValidationIssue(code=ValidationIssueCode.CAD_RISK_EVIDENCE.value, severity="warning", message="CAD measurement includes evidence that prevents forced pass decisions.", evidence={"measurement_issue_codes": codes})]


def _missing_json_check(field: str) -> FieldValidation:
    issue = ValidationIssue(code=ValidationIssueCode.MISSING_JSON_VALUE.value, severity="not_verifiable", message="JSON value is missing; CAD comparison was not attempted.", field_path=field)
    return FieldValidation(field_path=field, status="not_verifiable", message=issue.message, issues=(issue,))


def _positive(data: Mapping[str, Any], path: str, issues: list[ValidationIssue], *, issue_invalid: bool = True) -> float | None:
    value = _get(data, path)
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        if issue_invalid:
            issues.append(ValidationIssue(code=ValidationIssueCode.INVALID_SOURCE_JSON_VALUE.value, severity="fail", message="JSON value is not numeric.", field_path=path, evidence={"value": repr(value)}))
        return None
    if number <= 0:
        if issue_invalid:
            issues.append(ValidationIssue(code=ValidationIssueCode.INVALID_SOURCE_JSON_VALUE.value, severity="fail", message="JSON value must be greater than zero; it was not corrected.", field_path=path, evidence={"value": number}))
        return None
    return number


def _get(data: Mapping[str, Any], path: str) -> Any:
    current: Any = data
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return None
        current = current[key]
    return current


def _text(data: Mapping[str, Any], path: str) -> str | None:
    value = _get(data, path)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dimension_delta(actual: tuple[float, float], expected: tuple[float, float]) -> tuple[float, float]:
    actual_sorted = sorted(actual)
    expected_sorted = sorted(expected)
    deltas = [abs(a - e) for a, e in zip(actual_sorted, expected_sorted)]
    ratios = [delta / expected_sorted[index] for index, delta in enumerate(deltas)]
    return round(max(deltas), 9), round(max(ratios), 9)


def _tolerance_status(delta: float, ratio: float, abs_pass: float, rel_pass: float, abs_warn: float, rel_warn: float) -> ValidationStatus:
    if delta <= abs_pass or ratio <= rel_pass:
        return "pass"
    if delta <= abs_warn or ratio <= rel_warn:
        return "pass_with_warnings"
    return "fail"


def _gate_status(status: ValidationStatus, issues: list[ValidationIssue]) -> ValidationStatus:
    if status == "fail":
        return status
    if any(issue.severity == "not_verifiable" for issue in issues):
        return "not_verifiable"
    if any(issue.code in {ValidationIssueCode.WEAK_CLASSIFICATION.value, ValidationIssueCode.CAD_RISK_EVIDENCE.value} for issue in issues):
        return "review_required"
    if any(issue.severity == "warning" for issue in issues) and status == "pass":
        return "pass_with_warnings"
    return status


def _score(status: ValidationStatus, delta: float | None, ratio: float | None, candidate: MeasurementCandidate) -> tuple[float, ...]:
    status_rank = {"pass": 0.0, "pass_with_warnings": 1.0, "review_required": 2.0, "not_verifiable": 3.0, "fail": 4.0}[status]
    return (status_rank, round(ratio if ratio is not None else 999999.0, 9), round(delta if delta is not None else 999999.0, 9), round(1.0 - candidate.confidence, 9))


def _worse_status(left: ValidationStatus, right: ValidationStatus) -> ValidationStatus:
    rank = {"pass": 0, "pass_with_warnings": 1, "review_required": 2, "not_verifiable": 3, "fail": 4}
    return left if rank[left] >= rank[right] else right


def _overall_status(checks: tuple[FieldValidation, ...], issues: tuple[ValidationIssue, ...]) -> ValidationStatus:
    statuses = [check.status for check in checks if check.field_path not in _NOT_TOP_VIEW_FIELDS]
    if any(issue.code == ValidationIssueCode.INVALID_SOURCE_JSON_VALUE.value for issue in issues):
        return "fail"
    if any(status == "fail" for status in statuses):
        return "fail"
    if any(status == "review_required" for status in statuses):
        return "review_required"
    if statuses and all(status == "not_verifiable" for status in statuses):
        return "not_verifiable"
    if any(status in {"pass_with_warnings", "not_verifiable"} for status in statuses) or any(issue.severity == "warning" for issue in issues):
        return "pass_with_warnings"
    return "pass"


def _bbox_contains(outer: dict[str, float], inner: dict[str, float]) -> bool:
    return outer["min_x"] <= inner["min_x"] and outer["min_y"] <= inner["min_y"] and outer["max_x"] >= inner["max_x"] and outer["max_y"] >= inner["max_y"]


def _issue(code: ValidationIssueCode, severity: IssueSeverity, message: str, field: str, candidate: MeasurementCandidate | None = None, evidence: dict[str, Any] | None = None) -> ValidationIssue:
    return ValidationIssue(code=code.value, severity=severity, message=message, field_path=field, candidate_id=candidate.candidate_id if candidate else None, evidence=evidence or {})
