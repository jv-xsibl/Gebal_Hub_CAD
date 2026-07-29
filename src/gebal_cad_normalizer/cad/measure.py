"""Stage 10 deterministic read-only CAD measurement engine."""

from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import ezdxf
from pydantic import Field

from gebal_cad_normalizer.cad.canonicalize import (
    CanonicalArc,
    CanonicalCircle,
    CanonicalEllipse,
    CanonicalEntity,
    CanonicalGeometryResult,
    CanonicalLine,
    CanonicalPoint,
    CanonicalPolyline,
    CanonicalSplineReference,
)
from gebal_cad_normalizer.cad.classify import ClassificationResult, LayerClassification
from gebal_cad_normalizer.cad.inventory import DxfInventoryResult
from gebal_cad_normalizer.exceptions import OutputWriteError
from gebal_cad_normalizer.models import StrictModel


MeasurementRole = Literal["product_geometry", "safety_zone", "foundation_or_installation", "review_required"]
UnitStatus = Literal["unknown", "explicit", "inferred", "ambiguous"]
IssueSeverity = Literal["info", "warning", "fail"]

UNIT_TO_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4}
SUPPORTED_ENTITY_TYPES = {"LWPOLYLINE", "POLYLINE", "LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE"}
OPAQUE_REGION_TYPES = {"REGION", "ACIS", "BODY", "3DSOLID", "SURFACE"}
PROXY_XREF_TYPES = {"ACAD_PROXY_ENTITY", "IMAGE", "UNDERLAY", "PDFUNDERLAY", "DGNUNDERLAY", "DWFUNDERLAY"}


class MeasurementIssueCode(str, Enum):
    """Stable Stage 10 measurement issue codes."""

    NO_CANDIDATE = "measurement_no_candidate"
    OPEN_GEOMETRY = "measurement_open_geometry"
    GAP_TOO_LARGE = "measurement_gap_too_large"
    SELF_INTERSECTION = "measurement_self_intersection"
    NONPLANAR_GEOMETRY = "measurement_nonplanar_geometry"
    UNSUPPORTED_GEOMETRY = "measurement_unsupported_geometry"
    OPAQUE_REGION = "measurement_opaque_region"
    BLOCK_DOUBLE_COUNT_RISK = "measurement_block_double_count_risk"
    CURVE_APPROXIMATED = "measurement_curve_approximated"
    HOLE_RELATIONSHIP_UNCERTAIN = "measurement_hole_relationship_uncertain"
    UNITS_UNKNOWN = "measurement_units_unknown"
    UNIT_INFERENCE_AMBIGUOUS = "measurement_unit_inference_ambiguous"
    UNIT_OVERRIDE_APPLIED = "measurement_unit_override_applied"
    MULTIPLE_CANDIDATES = "measurement_multiple_candidates"
    LOW_CONFIDENCE = "measurement_low_confidence"
    FAILED = "measurement_failed"


class MeasurementConfig(StrictModel):
    """Configuration for deterministic read-only measurement."""

    z_epsilon: float = Field(default=1e-6, ge=0.0)
    curve_flattening_tolerance: float = Field(default=1.0, gt=0.0)
    endpoint_join_tolerance: float = Field(default=1e-4, ge=0.0)
    max_join_gap: float = Field(default=10.0, gt=0.0)
    allow_review_required_candidates: bool = True
    low_confidence_threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    high_confidence_cap_for_review: float = Field(default=0.49, ge=0.0, le=1.0)
    explicit_unit: Literal["mm", "cm", "m", "in"] | None = None
    expected_width_mm: float | None = Field(default=None, gt=0)
    expected_depth_mm: float | None = Field(default=None, gt=0)
    unit_inference_max_residual_ratio: float = Field(default=0.03, ge=0.0)
    unit_inference_ambiguity_ratio: float = Field(default=0.015, ge=0.0)
    max_chain_combinations: int = Field(default=250000, gt=0)
    max_candidate_count: int = Field(default=500, gt=0)
    max_hole_containment_comparisons: int = Field(default=250000, gt=0)
    max_curve_tessellation_points: int = Field(default=5000, gt=0)
    max_self_intersection_comparisons: int = Field(default=250000, gt=0)


class MeasurementIssue(StrictModel):
    """Machine-readable measurement issue."""

    code: str
    severity: IssueSeverity
    message: str
    layer_name: str | None = None
    source_handle: str | None = None
    evidence: dict[str, Any] = {}


class MeasurementEvidence(StrictModel):
    """Evidence explaining one measurement candidate."""

    source_layer: str
    assigned_role: MeasurementRole
    classification_confidence: float
    source_handles: tuple[str, ...]
    block_ancestry: tuple[str, ...]
    insert_handles: tuple[str, ...]
    original_dxf_types: tuple[str, ...]
    z_values: tuple[float, ...]
    closed: bool
    curve_approximated: bool
    approximation_tolerance: float | None
    geometry_count: int
    hole_count: int
    join_gaps: tuple[float, ...] = ()
    ranking_reasons: tuple[str, ...] = ()
    review_reason: str | None = None


class MeasurementCandidate(StrictModel):
    """One plausible measured footprint or zone candidate."""

    candidate_id: str
    role: MeasurementRole
    source_layer: str
    bounding_box: dict[str, float]
    width: float
    depth: float
    area: float | None
    perimeter: float | None
    centroid: dict[str, float] | None
    confidence: float = Field(ge=0.0, le=1.0)
    alternatives: tuple[str, ...] = ()
    unit_status: UnitStatus
    unit: str | None = None
    scale_to_mm: float | None = None
    width_mm: float | None = None
    depth_mm: float | None = None
    unit_inference_confidence: float | None = None
    unit_inference_residual: float | None = None
    unit_alternatives: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
    review_reason: str | None = None
    evidence: MeasurementEvidence


class LayerMeasurement(StrictModel):
    """Measurement summary for a source layer."""

    layer_name: str
    assigned_role: str
    candidate_count: int
    issue_codes: tuple[str, ...]
    top_candidate_id: str | None = None


class MeasurementResult(StrictModel):
    """Complete Stage 10 measurement result."""

    source_identity: str
    source_sha256: str | None
    source_checksum_unchanged: bool
    drawing_units: str
    insunits: int | None
    unit_status: UnitStatus
    inferred_unit: str | None
    scale_to_mm: float | None
    unit_evidence: dict[str, Any]
    candidates: tuple[MeasurementCandidate, ...]
    layer_measurements: tuple[LayerMeasurement, ...]
    issues: tuple[MeasurementIssue, ...]
    config: MeasurementConfig

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for reports and regression tests."""

        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


class _Loop(StrictModel):
    layer: str
    role: MeasurementRole
    classification_confidence: float
    points: tuple[tuple[float, float], ...]
    closed: bool
    curve_approximated: bool
    approximation_tolerance: float | None
    source_handles: tuple[str, ...]
    block_ancestry: tuple[str, ...]
    insert_handles: tuple[str, ...]
    original_dxf_types: tuple[str, ...]
    z_values: tuple[float, ...]
    geometry_count: int
    join_gaps: tuple[float, ...] = ()
    review_reason: str | None = None


def measure_geometry(
    inventory: DxfInventoryResult,
    canonical: CanonicalGeometryResult,
    classification: ClassificationResult | None = None,
    config: MeasurementConfig | None = None,
) -> MeasurementResult:
    """Measure plausible product and safety candidates without mutating CAD input."""

    cfg = config or MeasurementConfig()
    role_by_layer = _role_by_layer(classification, cfg)
    issues: list[MeasurementIssue] = []
    issues.extend(_inventory_guard_issues(inventory))

    loops: list[_Loop] = []
    by_layer: defaultdict[str, list[CanonicalEntity]] = defaultdict(list)
    for entity in canonical.entities:
        by_layer[entity.layer].append(entity)
        issues.extend(_entity_guard_issues(entity, cfg))

    for layer, entities in sorted(by_layer.items()):
        role, class_conf, review_reason = role_by_layer.get(layer, ("review_required", 0.2, "unclassified layer"))
        if role == "skip":
            continue
        loops.extend(_closed_entity_loops(layer, role, class_conf, review_reason, tuple(sorted(entities, key=lambda item: item.order_key)), cfg, issues))
        loops.extend(_chain_loops(layer, role, class_conf, review_reason, tuple(sorted(entities, key=lambda item: item.order_key)), cfg, issues))
    loops.extend(_hatch_loops(inventory, role_by_layer, cfg, issues))

    candidates = _candidates_from_loops(loops, cfg, issues)
    unit_status, unit, scale, unit_evidence, unit_issue = _unit_resolution(candidates, inventory, cfg)
    if unit_issue is not None:
        issues.append(unit_issue)
    candidates = tuple(_apply_units(candidate, unit_status, unit, scale, unit_evidence, cfg) for candidate in candidates)
    candidates = _attach_alternatives(tuple(sorted(candidates, key=lambda item: (-item.confidence, item.role, item.source_layer, item.candidate_id))))

    if not candidates:
        issues.append(_issue(MeasurementIssueCode.NO_CANDIDATE, "fail", "No measurable closed candidate was found."))
    if len(candidates) > 1:
        issues.append(_issue(MeasurementIssueCode.MULTIPLE_CANDIDATES, "info", "Multiple plausible measurement candidates were retained.", evidence={"count": len(candidates)}))
    for candidate in candidates:
        if candidate.confidence < cfg.low_confidence_threshold:
            issues.append(_issue(MeasurementIssueCode.LOW_CONFIDENCE, "warning", "Measurement candidate is below the configured confidence threshold.", layer_name=candidate.source_layer, evidence={"candidate_id": candidate.candidate_id, "confidence": candidate.confidence}))

    layer_measurements = _layer_measurements(candidates, issues, role_by_layer)
    return MeasurementResult(
        source_identity=canonical.source_identity,
        source_sha256=canonical.source_sha256,
        source_checksum_unchanged=bool(inventory.source_sha256 == canonical.source_sha256) if canonical.source_sha256 else True,
        drawing_units=inventory.drawing_units,
        insunits=inventory.insunits,
        unit_status=unit_status,
        inferred_unit=unit,
        scale_to_mm=scale,
        unit_evidence=unit_evidence,
        candidates=candidates,
        layer_measurements=layer_measurements,
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.layer_name or "", item.source_handle or "", item.message))),
        config=cfg,
    )


def write_measurement_json(result: MeasurementResult, output_path: Path | str) -> Path:
    """Write deterministic measurement JSON to an explicit path."""

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_deterministic_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write measurement JSON report: {path}") from exc
    return path


def _role_by_layer(classification: ClassificationResult | None, config: MeasurementConfig) -> dict[str, tuple[MeasurementRole | Literal["skip"], float, str | None]]:
    if classification is None:
        return {}
    mapped: dict[str, tuple[MeasurementRole | Literal["skip"], float, str | None]] = {}
    allowed = {"product_geometry", "safety_zone", "foundation_or_installation"}
    for layer in classification.layers:
        role = layer.assigned_role
        if role in allowed:
            mapped[layer.original_layer_name] = (role, layer.confidence, layer.review_reason)  # type: ignore[assignment]
        elif role in {"ambiguous", "review_required"} and config.allow_review_required_candidates:
            mapped[layer.original_layer_name] = ("review_required", min(layer.confidence, config.high_confidence_cap_for_review), layer.review_reason or "classification requires review")
        else:
            mapped[layer.original_layer_name] = ("skip", layer.confidence, layer.review_reason)
    return mapped


def _inventory_guard_issues(inventory: DxfInventoryResult) -> list[MeasurementIssue]:
    issues: list[MeasurementIssue] = []
    if inventory.drawing_units in {"missing", "unknown"} or inventory.insunits in {None, 0}:
        issues.append(_issue(MeasurementIssueCode.UNITS_UNKNOWN, "warning", "Drawing units are unknown; raw drawing-unit measurements are preserved.", evidence={"drawing_units": inventory.drawing_units, "insunits": inventory.insunits}))
    for entity_type, count in inventory.flagged_entity_presence.items():
        if entity_type in OPAQUE_REGION_TYPES and count:
            issues.append(_issue(MeasurementIssueCode.OPAQUE_REGION, "warning", "Opaque REGION/ACIS-style geometry is not reconstructed by measurement.", evidence={"entity_type": entity_type, "count": count}))
        if entity_type in PROXY_XREF_TYPES and count:
            issues.append(_issue(MeasurementIssueCode.UNSUPPORTED_GEOMETRY, "warning", "Proxy, XREF, raster, or underlay content is not authoritative measurement geometry.", evidence={"entity_type": entity_type, "count": count}))
    if inventory.xref_indicators:
        issues.append(_issue(MeasurementIssueCode.UNSUPPORTED_GEOMETRY, "warning", "External-reference indicators are not authoritative measurement geometry.", evidence={"xref_indicators": inventory.xref_indicators}))
    return issues


def _entity_guard_issues(entity: CanonicalEntity, config: MeasurementConfig) -> list[MeasurementIssue]:
    issues: list[MeasurementIssue] = []
    if any(abs(z) > config.z_epsilon for z in entity.z_values) or (entity.elevation is not None and abs(entity.elevation) > config.z_epsilon):
        issues.append(_issue(MeasurementIssueCode.NONPLANAR_GEOMETRY, "warning", "Entity contains meaningful non-planar Z evidence.", layer_name=entity.layer, source_handle=entity.source_handle, evidence={"z_values": entity.z_values, "elevation": entity.elevation, "z_epsilon": config.z_epsilon}))
    if entity.original_dxf_type in OPAQUE_REGION_TYPES:
        issues.append(_issue(MeasurementIssueCode.OPAQUE_REGION, "warning", "Opaque REGION/ACIS-style entity is not measurable without deterministic boundaries.", layer_name=entity.layer, source_handle=entity.source_handle))
    if entity.original_dxf_type not in SUPPORTED_ENTITY_TYPES and entity.geometry is None:
        issues.append(_issue(MeasurementIssueCode.UNSUPPORTED_GEOMETRY, "warning", "Unsupported entity is not measured as authoritative geometry.", layer_name=entity.layer, source_handle=entity.source_handle, evidence={"original_dxf_type": entity.original_dxf_type}))
    return issues


def _closed_entity_loops(layer: str, role: MeasurementRole, class_conf: float, review_reason: str | None, entities: tuple[CanonicalEntity, ...], config: MeasurementConfig, issues: list[MeasurementIssue]) -> list[_Loop]:
    loops: list[_Loop] = []
    for entity in entities:
        geometry = entity.geometry
        if isinstance(geometry, CanonicalPolyline):
            points = _polyline_points(geometry, config, issues, layer, entity.source_handle)
            if not geometry.is_closed and _distance(points[0], points[-1]) > config.endpoint_join_tolerance:
                issues.append(_issue(MeasurementIssueCode.OPEN_GEOMETRY, "warning", "Open polyline was not measured as a closed candidate.", layer_name=layer, source_handle=entity.source_handle))
                continue
            loops.append(_loop_from_entity(layer, role, class_conf, review_reason, entity, points, True, geometry.type == "tessellated_polyline" or any(abs(v.bulge) > 1e-12 for v in geometry.vertices), config.curve_flattening_tolerance))
        elif isinstance(geometry, CanonicalCircle):
            loops.append(_loop_from_entity(layer, role, class_conf, review_reason, entity, _circle_points(geometry, config, issues, layer, entity.source_handle), True, True, config.curve_flattening_tolerance))
        elif isinstance(geometry, CanonicalEllipse):
            if not _ellipse_is_closed(geometry):
                issues.append(_issue(MeasurementIssueCode.OPEN_GEOMETRY, "warning", "Open ellipse segment was not measured as a closed candidate.", layer_name=layer, source_handle=entity.source_handle))
                continue
            loops.append(_loop_from_entity(layer, role, class_conf, review_reason, entity, _ellipse_points(geometry, config, issues, layer, entity.source_handle), True, True, config.curve_flattening_tolerance))
        elif isinstance(geometry, CanonicalSplineReference):
            if not geometry.is_closed:
                issues.append(_issue(MeasurementIssueCode.OPEN_GEOMETRY, "warning", "Open spline reference was not measured as a closed candidate.", layer_name=layer, source_handle=entity.source_handle))
                continue
            points = tuple((point.x, point.y) for point in (geometry.fit_points or geometry.control_points))
            if len(points) >= 3:
                loops.append(_loop_from_entity(layer, role, class_conf, review_reason, entity, points, True, True, config.curve_flattening_tolerance))
    return loops


def _chain_loops(layer: str, role: MeasurementRole, class_conf: float, review_reason: str | None, entities: tuple[CanonicalEntity, ...], config: MeasurementConfig, issues: list[MeasurementIssue]) -> list[_Loop]:
    chain_entities = [entity for entity in entities if isinstance(entity.geometry, (CanonicalLine, CanonicalArc))]
    if len(chain_entities) < 3:
        return []
    unused = list(chain_entities)
    loops: list[_Loop] = []
    combinations = 0
    while unused:
        first = unused.pop(0)
        first_points = _entity_segment_points(first.geometry, config, issues, layer, first.source_handle)
        chain = [first]
        points = list(first_points)
        gaps: list[float] = []
        changed = True
        while changed and unused:
            changed = False
            end = points[-1]
            for index, entity in enumerate(unused):
                combinations += 1
                if combinations > config.max_chain_combinations:
                    issues.append(_issue(MeasurementIssueCode.FAILED, "fail", "Line/arc chain search exceeded the configured combination cap.", layer_name=layer, evidence={"cap": config.max_chain_combinations, "chain_entity_count": len(chain_entities)}))
                    return loops
                seg = list(_entity_segment_points(entity.geometry, config, issues, layer, entity.source_handle))
                direct = _distance(end, seg[0])
                reverse = _distance(end, seg[-1])
                best = min(direct, reverse)
                if best <= config.max_join_gap:
                    if best > config.endpoint_join_tolerance:
                        gaps.append(best)
                    if reverse < direct:
                        seg.reverse()
                    points.extend(seg[1:])
                    chain.append(entity)
                    unused.pop(index)
                    changed = True
                    break
        close_gap = _distance(points[-1], points[0])
        if close_gap <= config.max_join_gap:
            if close_gap > config.endpoint_join_tolerance:
                gaps.append(close_gap)
            source_handles = tuple(_strs(entity.source_handle for entity in chain))
            loops.append(
                _Loop(
                    layer=layer,
                    role=role,
                    classification_confidence=class_conf,
                    points=tuple(points),
                    closed=True,
                    curve_approximated=any(isinstance(entity.geometry, CanonicalArc) for entity in chain),
                    approximation_tolerance=config.curve_flattening_tolerance if any(isinstance(entity.geometry, CanonicalArc) for entity in chain) else None,
                    source_handles=source_handles,
                    block_ancestry=tuple(sorted({ancestor for entity in chain for ancestor in entity.block_ancestry})),
                    insert_handles=tuple(sorted({handle for entity in chain for handle in entity.insert_handles if handle})),
                    original_dxf_types=tuple(sorted({entity.original_dxf_type for entity in chain})),
                    z_values=tuple(sorted(z for entity in chain for z in entity.z_values)),
                    geometry_count=len(chain),
                    join_gaps=tuple(round(gap, 9) for gap in sorted(gaps)),
                    review_reason=review_reason,
                )
            )
            if gaps:
                issues.append(_issue(MeasurementIssueCode.OPEN_GEOMETRY, "info", "Endpoint gaps within tolerance were joined for measurement evidence only.", layer_name=layer, evidence={"gaps": tuple(round(gap, 9) for gap in sorted(gaps))}))
        else:
            issues.append(_issue(MeasurementIssueCode.GAP_TOO_LARGE, "warning", "Line/arc chain gap exceeds configured maximum; closure was not invented.", layer_name=layer, evidence={"gap": close_gap, "max_join_gap": config.max_join_gap}))
    return loops

def _hatch_loops(inventory: DxfInventoryResult, role_by_layer: dict[str, tuple[MeasurementRole | Literal["skip"], float, str | None]], config: MeasurementConfig, issues: list[MeasurementIssue]) -> list[_Loop]:
    if inventory.source_path is None:
        return []
    try:
        doc = ezdxf.readfile(inventory.source_path)
    except Exception as exc:
        issues.append(_issue(MeasurementIssueCode.UNSUPPORTED_GEOMETRY, "warning", "HATCH boundaries could not be inspected from source DXF.", evidence={"error": str(exc)}))
        return []
    loops: list[_Loop] = []
    for hatch in doc.modelspace().query("HATCH"):
        layer = str(hatch.dxf.get("layer", "0"))
        role, class_conf, review_reason = role_by_layer.get(layer, ("review_required", 0.2, "hatch boundary requires review"))
        if role == "skip":
            role, class_conf, review_reason = "review_required", min(class_conf, config.high_confidence_cap_for_review), review_reason or "hatch boundary not classified as operational"
        for boundary in hatch.paths:
            vertices = getattr(boundary, "vertices", None)
            if not vertices:
                continue
            points: list[tuple[float, float]] = []
            curve = False
            for index, vertex in enumerate(vertices):
                x, y = float(vertex[0]), float(vertex[1])
                if not points:
                    points.append((x, y))
                    continue
                previous = vertices[index - 1]
                bulge = float(previous[2]) if len(previous) > 2 else 0.0
                if abs(bulge) > 1e-12:
                    curve = True
                    points.extend(_bulge_points(points[-1], (x, y), bulge, config, issues, layer, str(hatch.dxf.handle))[1:])
                else:
                    points.append((x, y))
            closed = bool(getattr(boundary, "is_closed", False))
            if not closed and points and _distance(points[0], points[-1]) > config.endpoint_join_tolerance:
                issues.append(_issue(MeasurementIssueCode.OPEN_GEOMETRY, "warning", "Open HATCH boundary was not measured as a closed candidate.", layer_name=layer, source_handle=str(hatch.dxf.handle)))
                continue
            loops.append(
                _Loop(
                    layer=layer,
                    role=role,  # type: ignore[arg-type]
                    classification_confidence=class_conf,
                    points=tuple(points),
                    closed=True,
                    curve_approximated=curve,
                    approximation_tolerance=config.curve_flattening_tolerance if curve else None,
                    source_handles=(str(hatch.dxf.handle),),
                    block_ancestry=(),
                    insert_handles=(),
                    original_dxf_types=("HATCH",),
                    z_values=(0.0,),
                    geometry_count=1,
                    review_reason=review_reason,
                )
            )
    return loops

def _loop_from_entity(layer: str, role: MeasurementRole, class_conf: float, review_reason: str | None, entity: CanonicalEntity, points: tuple[tuple[float, float], ...], closed: bool, curve: bool, tolerance: float | None) -> _Loop:
    return _Loop(
        layer=layer,
        role=role,
        classification_confidence=class_conf,
        points=points,
        closed=closed,
        curve_approximated=curve,
        approximation_tolerance=tolerance if curve else None,
        source_handles=tuple(_strs((entity.source_handle,))),
        block_ancestry=entity.block_ancestry,
        insert_handles=tuple(handle for handle in entity.insert_handles if handle),
        original_dxf_types=(entity.original_dxf_type,),
        z_values=tuple(entity.z_values),
        geometry_count=1,
        review_reason=review_reason,
    )


def _candidates_from_loops(loops: list[_Loop], config: MeasurementConfig, issues: list[MeasurementIssue]) -> tuple[MeasurementCandidate, ...]:
    candidates: list[MeasurementCandidate] = []
    grouped: defaultdict[tuple[str, MeasurementRole], list[_Loop]] = defaultdict(list)
    for loop in loops:
        grouped[(loop.layer, loop.role)].append(loop)
    for (layer, role), layer_loops in sorted(grouped.items()):
        outer_loops = sorted(layer_loops, key=lambda item: abs(_area(item.points)), reverse=True)
        consumed_holes: set[int] = set()
        containment_comparisons = 0
        for index, outer in enumerate(outer_loops):
            if index in consumed_holes:
                continue
            if len(candidates) >= config.max_candidate_count:
                issues.append(_issue(MeasurementIssueCode.FAILED, "fail", "Measurement candidate count exceeded the configured cap.", layer_name=layer, evidence={"cap": config.max_candidate_count, "loop_count": len(loops)}))
                return tuple(candidates)
            area = _area(outer.points)
            if abs(area) <= 0:
                continue
            holes = []
            for h_index, hole in enumerate(outer_loops):
                if h_index == index or h_index in consumed_holes:
                    continue
                containment_comparisons += 1
                if containment_comparisons > config.max_hole_containment_comparisons:
                    issues.append(_issue(MeasurementIssueCode.FAILED, "fail", "Hole-containment processing exceeded the configured comparison cap.", layer_name=layer, evidence={"cap": config.max_hole_containment_comparisons, "loop_count": len(outer_loops)}))
                    return tuple(candidates)
                if abs(_area(hole.points)) < abs(area) and _contains_point(outer.points, _centroid_raw(hole.points)):
                    holes.append(hole)
                    consumed_holes.add(h_index)
            candidate = _candidate(outer, holes, len(candidates) + 1, config, issues)
            if candidate is not None:
                candidates.append(candidate)
    return tuple(candidates)


def _candidate(outer: _Loop, holes: list[_Loop], sequence: int, config: MeasurementConfig, issues: list[MeasurementIssue]) -> MeasurementCandidate | None:
    points = _dedupe_closed(outer.points)
    if len(points) < 3:
        return None
    segment_count = len(_dedupe_closed(points))
    self_intersection_comparisons = max(0, segment_count * max(0, segment_count - 3) // 2)
    if self_intersection_comparisons > config.max_self_intersection_comparisons:
        issues.append(_issue(MeasurementIssueCode.FAILED, "fail", "Self-intersection processing exceeded the configured comparison cap.", layer_name=outer.layer, evidence={"cap": config.max_self_intersection_comparisons, "segment_count": segment_count, "comparison_count": self_intersection_comparisons, "source_handles": outer.source_handles}))
    elif _self_intersects(points):
        issues.append(_issue(MeasurementIssueCode.SELF_INTERSECTION, "warning", "Self-intersecting loop was retained for review but area confidence is reduced.", layer_name=outer.layer, evidence={"source_handles": outer.source_handles}))
    bbox = {"min_x": min(x for x, _ in points), "min_y": min(y for _, y in points), "max_x": max(x for x, _ in points), "max_y": max(y for _, y in points)}
    bbox["min_x"], bbox["min_y"], bbox["max_x"], bbox["max_y"] = (round(bbox["min_x"], 9), round(bbox["min_y"], 9), round(bbox["max_x"], 9), round(bbox["max_y"], 9))
    outer_area = abs(_area(points))
    hole_area = sum(abs(_area(_dedupe_closed(hole.points))) for hole in holes)
    net_area = max(outer_area - hole_area, 0.0)
    centroid = _centroid_with_holes(points, [_dedupe_closed(hole.points) for hole in holes])
    confidence, reasons, warnings = _confidence(outer, holes, net_area, config)
    evidence = MeasurementEvidence(
        source_layer=outer.layer,
        assigned_role=outer.role,
        classification_confidence=round(outer.classification_confidence, 4),
        source_handles=outer.source_handles + tuple(handle for hole in holes for handle in hole.source_handles),
        block_ancestry=tuple(sorted(set(outer.block_ancestry + tuple(ancestor for hole in holes for ancestor in hole.block_ancestry)))),
        insert_handles=tuple(sorted(set(outer.insert_handles + tuple(handle for hole in holes for handle in hole.insert_handles)))),
        original_dxf_types=tuple(sorted(set(outer.original_dxf_types + tuple(kind for hole in holes for kind in hole.original_dxf_types)))),
        z_values=tuple(sorted(outer.z_values + tuple(z for hole in holes for z in hole.z_values))),
        closed=outer.closed,
        curve_approximated=outer.curve_approximated or any(hole.curve_approximated for hole in holes),
        approximation_tolerance=outer.approximation_tolerance if outer.curve_approximated else next((hole.approximation_tolerance for hole in holes if hole.curve_approximated), None),
        geometry_count=outer.geometry_count + sum(hole.geometry_count for hole in holes),
        hole_count=len(holes),
        join_gaps=tuple(sorted(outer.join_gaps + tuple(gap for hole in holes for gap in hole.join_gaps))),
        ranking_reasons=tuple(reasons),
        review_reason=outer.review_reason,
    )
    if evidence.curve_approximated:
        issues.append(_issue(MeasurementIssueCode.CURVE_APPROXIMATED, "info", "Curved geometry was measured from deterministic flattened points.", layer_name=outer.layer, evidence={"candidate_id": f"MC{sequence:04d}", "tolerance": evidence.approximation_tolerance}))
    if len(holes) > 1:
        issues.append(_issue(MeasurementIssueCode.HOLE_RELATIONSHIP_UNCERTAIN, "info", "Multiple inner loops were treated as holes based on containment.", layer_name=outer.layer, evidence={"candidate_id": f"MC{sequence:04d}", "hole_count": len(holes)}))
    return MeasurementCandidate(
        candidate_id=f"MC{sequence:04d}",
        role=outer.role,
        source_layer=outer.layer,
        bounding_box=bbox,
        width=round(bbox["max_x"] - bbox["min_x"], 9),
        depth=round(bbox["max_y"] - bbox["min_y"], 9),
        area=round(net_area, 9),
        perimeter=round(_perimeter(points) + sum(_perimeter(_dedupe_closed(hole.points)) for hole in holes), 9),
        centroid={"x": round(centroid[0], 9), "y": round(centroid[1], 9)} if centroid is not None else None,
        confidence=round(confidence, 4),
        unit_status="unknown",
        warnings=tuple(warnings),
        review_reason=outer.review_reason,
        evidence=evidence,
    )


def _confidence(loop: _Loop, holes: list[_Loop], area: float, config: MeasurementConfig) -> tuple[float, list[str], list[str]]:
    score = min(max(loop.classification_confidence, 0.0), 1.0)
    cap = 1.0
    reasons = [f"classification confidence {score:.4f}"]
    warnings: list[str] = []
    if loop.role == "review_required":
        score = min(score, config.high_confidence_cap_for_review)
        cap = config.high_confidence_cap_for_review
        warnings.append("classification requires review")
    if loop.closed:
        score += 0.12
        reasons.append("closed-loop geometry")
    if loop.curve_approximated:
        score -= 0.04
        warnings.append("curve approximation used")
    if loop.join_gaps:
        score -= min(0.12, 0.02 * len(loop.join_gaps))
        warnings.append("endpoint gaps joined within tolerance")
    if area > 0:
        score += 0.06
        reasons.append("positive enclosed area")
    if holes:
        score -= 0.02
        reasons.append("inner-loop holes subtracted")
    if loop.z_values and any(abs(z) > config.z_epsilon for z in loop.z_values):
        cap = min(cap, 0.52)
        warnings.append("non-planar Z evidence")
    return max(0.0, min(score, cap)), reasons, warnings


def _unit_resolution(candidates: tuple[MeasurementCandidate, ...], inventory: DxfInventoryResult, config: MeasurementConfig) -> tuple[UnitStatus, str | None, float | None, dict[str, Any], MeasurementIssue | None]:
    if config.explicit_unit:
        return "explicit", config.explicit_unit, UNIT_TO_MM[config.explicit_unit], {"source": "explicit_override", "unit": config.explicit_unit}, _issue(MeasurementIssueCode.UNIT_OVERRIDE_APPLIED, "info", "Explicit unit override was applied.", evidence={"unit": config.explicit_unit, "scale_to_mm": UNIT_TO_MM[config.explicit_unit]})
    known = _known_unit(inventory.drawing_units, inventory.insunits)
    if known is not None:
        return "explicit", known, UNIT_TO_MM[known], {"source": "$INSUNITS", "drawing_units": inventory.drawing_units, "insunits": inventory.insunits}, None
    if not (config.expected_width_mm and config.expected_depth_mm) or not candidates:
        return "unknown", None, None, {"source": "raw_drawing_units_only"}, None
    ranked: list[dict[str, Any]] = []
    reference = max(candidates, key=lambda item: item.confidence)
    for unit, scale in UNIT_TO_MM.items():
        residual = _dimension_residual((reference.width * scale, reference.depth * scale), (config.expected_width_mm, config.expected_depth_mm))
        ranked.append({"unit": unit, "scale_to_mm": scale, "residual": round(residual, 9)})
    ranked.sort(key=lambda item: (item["residual"], item["unit"]))
    best = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    evidence = {"source": "expected_dimensions", "candidate_id": reference.candidate_id, "expected_width_mm": config.expected_width_mm, "expected_depth_mm": config.expected_depth_mm, "alternatives": ranked}
    ambiguous = best["residual"] > config.unit_inference_max_residual_ratio or (second is not None and abs(second["residual"] - best["residual"]) <= config.unit_inference_ambiguity_ratio)
    if ambiguous:
        return "ambiguous", None, None, evidence, _issue(MeasurementIssueCode.UNIT_INFERENCE_AMBIGUOUS, "warning", "Expected dimensions did not produce a high-confidence unit inference.", evidence=evidence)
    confidence = max(0.0, min(1.0, 1.0 - best["residual"] / max(config.unit_inference_max_residual_ratio, 1e-9)))
    evidence["confidence"] = round(confidence, 4)
    return "inferred", str(best["unit"]), float(best["scale_to_mm"]), evidence, None


def _apply_units(candidate: MeasurementCandidate, status: UnitStatus, unit: str | None, scale: float | None, evidence: dict[str, Any], config: MeasurementConfig) -> MeasurementCandidate:
    payload = candidate.model_dump()
    payload["unit_status"] = status
    payload["unit"] = unit
    payload["scale_to_mm"] = scale
    payload["unit_alternatives"] = tuple(evidence.get("alternatives", ()))
    if scale is not None:
        payload["width_mm"] = round(candidate.width * scale, 9)
        payload["depth_mm"] = round(candidate.depth * scale, 9)
        if config.expected_width_mm and config.expected_depth_mm:
            payload["unit_inference_residual"] = _dimension_residual((candidate.width * scale, candidate.depth * scale), (config.expected_width_mm, config.expected_depth_mm))
            payload["unit_inference_confidence"] = evidence.get("confidence")
    return MeasurementCandidate(**payload)


def _attach_alternatives(candidates: tuple[MeasurementCandidate, ...]) -> tuple[MeasurementCandidate, ...]:
    by_role: defaultdict[str, list[str]] = defaultdict(list)
    for candidate in candidates:
        by_role[candidate.role].append(candidate.candidate_id)
    updated: list[MeasurementCandidate] = []
    for candidate in candidates:
        payload = candidate.model_dump()
        payload["alternatives"] = tuple(item for item in by_role[candidate.role] if item != candidate.candidate_id)
        updated.append(MeasurementCandidate(**payload))
    return tuple(updated)


def _layer_measurements(candidates: tuple[MeasurementCandidate, ...], issues: list[MeasurementIssue], role_by_layer: dict[str, tuple[Any, float, str | None]]) -> tuple[LayerMeasurement, ...]:
    candidate_by_layer: defaultdict[str, list[MeasurementCandidate]] = defaultdict(list)
    issue_by_layer: defaultdict[str, set[str]] = defaultdict(set)
    for candidate in candidates:
        candidate_by_layer[candidate.source_layer].append(candidate)
    for issue in issues:
        if issue.layer_name:
            issue_by_layer[issue.layer_name].add(issue.code)
    layers = set(candidate_by_layer) | set(issue_by_layer) | set(role_by_layer)
    summaries: list[LayerMeasurement] = []
    for layer in sorted(layers):
        layer_candidates = sorted(candidate_by_layer.get(layer, ()), key=lambda item: (-item.confidence, item.candidate_id))
        role = role_by_layer.get(layer, ("review_required", 0, None))[0]
        summaries.append(LayerMeasurement(layer_name=layer, assigned_role=str(role), candidate_count=len(layer_candidates), issue_codes=tuple(sorted(issue_by_layer.get(layer, set()))), top_candidate_id=layer_candidates[0].candidate_id if layer_candidates else None))
    return tuple(summaries)


def _polyline_points(polyline: CanonicalPolyline, config: MeasurementConfig, issues: list[MeasurementIssue] | None = None, layer: str | None = None, source_handle: str | None = None) -> tuple[tuple[float, float], ...]:
    vertices = list(polyline.vertices)
    if not vertices:
        return ()
    points: list[tuple[float, float]] = []
    count = len(vertices)
    limit = count if polyline.is_closed else count - 1
    for index in range(max(limit, 0)):
        current = vertices[index]
        nxt = vertices[(index + 1) % count]
        start = (current.point.x, current.point.y)
        end = (nxt.point.x, nxt.point.y)
        if not points:
            points.append(start)
        if abs(current.bulge) > 1e-12:
            points.extend(_bulge_points(start, end, current.bulge, config, issues, layer, source_handle)[1:])
        else:
            points.append(end)
    if not polyline.is_closed and count == 1:
        points.append((vertices[0].point.x, vertices[0].point.y))
    return tuple(points)


def _entity_segment_points(geometry: Any, config: MeasurementConfig, issues: list[MeasurementIssue] | None = None, layer: str | None = None, source_handle: str | None = None) -> tuple[tuple[float, float], ...]:
    if isinstance(geometry, CanonicalLine):
        return ((geometry.start.x, geometry.start.y), (geometry.end.x, geometry.end.y))
    if isinstance(geometry, CanonicalArc):
        return _arc_points(geometry.center.x, geometry.center.y, geometry.radius, math.radians(geometry.start_angle), math.radians(geometry.end_angle), config, issues, layer, source_handle)
    return ()


def _circle_points(circle: CanonicalCircle, config: MeasurementConfig, issues: list[MeasurementIssue] | None = None, layer: str | None = None, source_handle: str | None = None) -> tuple[tuple[float, float], ...]:
    circumference = 2 * math.pi * circle.radius
    count = max(96, int(math.ceil(circumference / config.curve_flattening_tolerance)) * 4)
    count = _bounded_curve_count(count, config, issues, layer, source_handle, "circle")
    return tuple((circle.center.x + circle.radius * math.cos(2 * math.pi * index / count), circle.center.y + circle.radius * math.sin(2 * math.pi * index / count)) for index in range(count))


def _ellipse_points(ellipse: CanonicalEllipse, config: MeasurementConfig, issues: list[MeasurementIssue] | None = None, layer: str | None = None, source_handle: str | None = None) -> tuple[tuple[float, float], ...]:
    major = math.hypot(ellipse.major_axis.x, ellipse.major_axis.y)
    minor = major * ellipse.ratio
    rotation = math.atan2(ellipse.major_axis.y, ellipse.major_axis.x)
    circumference = math.pi * (3 * (major + minor) - math.sqrt((3 * major + minor) * (major + 3 * minor)))
    count = max(32, int(math.ceil(circumference / config.curve_flattening_tolerance)))
    count = _bounded_curve_count(count, config, issues, layer, source_handle, "ellipse")
    points = []
    start = ellipse.start_param
    end = ellipse.end_param
    for index in range(count):
        t = start + (end - start) * index / count
        x = major * math.cos(t)
        y = minor * math.sin(t)
        points.append((ellipse.center.x + x * math.cos(rotation) - y * math.sin(rotation), ellipse.center.y + x * math.sin(rotation) + y * math.cos(rotation)))
    return tuple(points)


def _arc_points(cx: float, cy: float, radius: float, start: float, end: float, config: MeasurementConfig, issues: list[MeasurementIssue] | None = None, layer: str | None = None, source_handle: str | None = None) -> tuple[tuple[float, float], ...]:
    if end < start:
        end += 2 * math.pi
    arc_len = abs(end - start) * radius
    count = max(2, int(math.ceil(arc_len / config.curve_flattening_tolerance)) + 1)
    count = _bounded_curve_count(count, config, issues, layer, source_handle, "arc")
    return tuple((cx + radius * math.cos(start + (end - start) * index / (count - 1)), cy + radius * math.sin(start + (end - start) * index / (count - 1))) for index in range(count))


def _bulge_points(start: tuple[float, float], end: tuple[float, float], bulge: float, config: MeasurementConfig, issues: list[MeasurementIssue] | None = None, layer: str | None = None, source_handle: str | None = None) -> tuple[tuple[float, float], ...]:
    chord = _distance(start, end)
    if chord <= 0:
        return (start, end)
    theta = 4 * math.atan(bulge)
    radius = abs(chord / (2 * math.sin(theta / 2)))
    midpoint = ((start[0] + end[0]) / 2, (start[1] + end[1]) / 2)
    ux, uy = ((end[0] - start[0]) / chord, (end[1] - start[1]) / chord)
    h = radius * math.cos(theta / 2)
    sign = 1 if bulge >= 0 else -1
    center = (midpoint[0] - sign * uy * h, midpoint[1] + sign * ux * h)
    a1 = math.atan2(start[1] - center[1], start[0] - center[0])
    a2 = math.atan2(end[1] - center[1], end[0] - center[0])
    if bulge >= 0 and a2 < a1:
        a2 += 2 * math.pi
    if bulge < 0 and a1 < a2:
        a1 += 2 * math.pi
    return _arc_points(center[0], center[1], radius, a1, a2, config, issues, layer, source_handle)


def _bounded_curve_count(count: int, config: MeasurementConfig, issues: list[MeasurementIssue] | None, layer: str | None, source_handle: str | None, curve_type: str) -> int:
    if count <= config.max_curve_tessellation_points:
        return count
    if issues is not None:
        issues.append(_issue(MeasurementIssueCode.FAILED, "fail", "Curve tessellation exceeded the configured point cap.", layer_name=layer, source_handle=source_handle, evidence={"curve_type": curve_type, "requested_points": count, "cap": config.max_curve_tessellation_points}))
    return config.max_curve_tessellation_points


def _ellipse_is_closed(ellipse: CanonicalEllipse) -> bool:
    return abs(ellipse.end_param - ellipse.start_param) >= 2 * math.pi - 1e-9


def _known_unit(drawing_units: str, insunits: int | None) -> str | None:
    normalized = drawing_units.casefold()
    if normalized in {"mm", "millimeter", "millimeters"} or insunits == 4:
        return "mm"
    if normalized in {"cm", "centimeter", "centimeters"} or insunits == 5:
        return "cm"
    if normalized in {"m", "meter", "meters"} or insunits == 6:
        return "m"
    if normalized in {"in", "inch", "inches"} or insunits == 1:
        return "in"
    return None


def _dimension_residual(actual: tuple[float, float], expected: tuple[float, float]) -> float:
    a = sorted(actual)
    e = sorted(expected)
    return max(abs(a[0] - e[0]) / e[0], abs(a[1] - e[1]) / e[1])


def _area(points: tuple[tuple[float, float], ...]) -> float:
    pts = _dedupe_closed(points)
    return sum((x1 * y2) - (x2 * y1) for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])) / 2.0


def _perimeter(points: tuple[tuple[float, float], ...]) -> float:
    pts = _dedupe_closed(points)
    return sum(_distance(a, b) for a, b in zip(pts, pts[1:] + pts[:1]))


def _centroid_raw(points: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    centroid = _centroid_with_holes(_dedupe_closed(points), [])
    return centroid if centroid is not None else (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))


def _centroid_with_holes(outer: tuple[tuple[float, float], ...], holes: list[tuple[tuple[float, float], ...]]) -> tuple[float, float] | None:
    pieces = [(outer, 1.0)] + [(hole, -1.0) for hole in holes]
    total_area = 0.0
    cx_total = 0.0
    cy_total = 0.0
    for points, sign in pieces:
        area = abs(_area(points)) * sign
        if abs(area) <= 1e-12:
            continue
        cx, cy = _polygon_centroid(points)
        total_area += area
        cx_total += cx * area
        cy_total += cy * area
    if abs(total_area) <= 1e-12:
        return None
    return (cx_total / total_area, cy_total / total_area)


def _polygon_centroid(points: tuple[tuple[float, float], ...]) -> tuple[float, float]:
    pts = _dedupe_closed(points)
    signed_area = _area(pts)
    if abs(signed_area) <= 1e-12:
        return (sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts))
    cx = sum((x1 + x2) * ((x1 * y2) - (x2 * y1)) for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])) / (6 * signed_area)
    cy = sum((y1 + y2) * ((x1 * y2) - (x2 * y1)) for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1])) / (6 * signed_area)
    return (cx, cy)


def _contains_point(poly: tuple[tuple[float, float], ...], point: tuple[float, float]) -> bool:
    x, y = point
    inside = False
    pts = _dedupe_closed(poly)
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        if (y1 > y) != (y2 > y):
            xinters = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
            if x < xinters:
                inside = not inside
    return inside


def _self_intersects(points: tuple[tuple[float, float], ...]) -> bool:
    pts = _dedupe_closed(points)
    segments = list(zip(pts, pts[1:] + pts[:1]))
    for i, (a, b) in enumerate(segments):
        for j, (c, d) in enumerate(segments):
            if abs(i - j) <= 1 or {i, j} == {0, len(segments) - 1}:
                continue
            if _segments_intersect(a, b, c, d):
                return True
    return False


def _segments_intersect(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float], d: tuple[float, float]) -> bool:
    def orient(p: tuple[float, float], q: tuple[float, float], r: tuple[float, float]) -> float:
        return (q[1] - p[1]) * (r[0] - q[0]) - (q[0] - p[0]) * (r[1] - q[1])

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    return o1 * o2 < 0 and o3 * o4 < 0


def _dedupe_closed(points: tuple[tuple[float, float], ...]) -> tuple[tuple[float, float], ...]:
    pts = tuple((round(float(x), 12), round(float(y), 12)) for x, y in points)
    if len(pts) > 1 and _distance(pts[0], pts[-1]) <= 1e-12:
        pts = pts[:-1]
    return pts


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _strs(values: Any) -> list[str]:
    return sorted(str(value) for value in values if value is not None and str(value))


def _issue(code: MeasurementIssueCode, severity: IssueSeverity, message: str, *, layer_name: str | None = None, source_handle: str | None = None, evidence: dict[str, Any] | None = None) -> MeasurementIssue:
    return MeasurementIssue(code=code.value, severity=severity, message=message, layer_name=layer_name, source_handle=source_handle, evidence=evidence or {})







