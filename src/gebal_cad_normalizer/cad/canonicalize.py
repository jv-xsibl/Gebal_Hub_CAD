"""Read-only Stage 6 geometry canonicalization for supported 2D DXF entities."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal

import ezdxf
from ezdxf import units
from ezdxf.entities import DXFEntity

from gebal_cad_normalizer.exceptions import DxfReadError, OutputWriteError
from gebal_cad_normalizer.models import StrictModel


CanonicalStatus = Literal["canonicalized", "preserved_curve", "convertible_later", "review_required", "unsupported", "skipped_3d"]
CanonicalType = Literal["point", "line", "arc", "circle", "polyline", "ellipse", "spline_reference", "tessellated_polyline", "unsupported"]
IssueSeverity = Literal["info", "warning", "fail"]

DIRECT_2D_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "POINT"}
THREE_D_TYPES = {"3DFACE", "3DSOLID", "BODY", "SURFACE", "MESH", "POLYFACE", "POLYMESH"}
DEFAULT_Z_EPSILON = 1e-6


class CanonicalizationIssueCode(str, Enum):
    """Stable Stage 6 issue codes."""

    UNSUPPORTED_ENTITY_TYPE = "unsupported_entity_type"
    NONZERO_Z_GEOMETRY = "nonzero_z_geometry"
    UNSUPPORTED_3D_GEOMETRY = "unsupported_3d_geometry"
    BLOCK_CYCLE_DETECTED = "block_cycle_detected"
    INSERT_TRANSFORM_FAILED = "insert_transform_failed"
    INVALID_ENTITY_GEOMETRY = "invalid_entity_geometry"
    CURVE_PRESERVED_NOT_FLATTENED = "curve_preserved_not_flattened"
    TESSELLATION_FAILED = "tessellation_failed"
    CANONICAL_EXTENTS_UNAVAILABLE = "canonical_extents_unavailable"
    ENTITY_EXPANSION_CAP_EXCEEDED = "entity_expansion_cap_exceeded"
    INSERT_RECURSION_DEPTH_EXCEEDED = "insert_recursion_depth_exceeded"
    TESSELLATION_POINT_CAP_EXCEEDED = "tessellation_point_cap_exceeded"


class CanonicalizationError(DxfReadError):
    """Raised when a DXF cannot be opened for canonicalization."""


class CanonicalPoint(StrictModel):
    """A source point or point coordinate."""

    x: float
    y: float
    z: float = 0.0


class CanonicalLine(StrictModel):
    """Canonical line segment."""

    type: Literal["line"] = "line"
    start: CanonicalPoint
    end: CanonicalPoint


class CanonicalArc(StrictModel):
    """Canonical circular arc preserving DXF angles."""

    type: Literal["arc"] = "arc"
    center: CanonicalPoint
    radius: float
    start_angle: float
    end_angle: float
    direction: Literal["ccw"] = "ccw"


class CanonicalCircle(StrictModel):
    """Canonical circle."""

    type: Literal["circle"] = "circle"
    center: CanonicalPoint
    radius: float


class CanonicalPolylineVertex(StrictModel):
    """Canonical polyline vertex preserving bulge evidence."""

    point: CanonicalPoint
    start_width: float = 0.0
    end_width: float = 0.0
    bulge: float = 0.0


class CanonicalPolyline(StrictModel):
    """Canonical polyline preserving open/closed and bulge data."""

    type: Literal["polyline", "tessellated_polyline"] = "polyline"
    vertices: tuple[CanonicalPolylineVertex, ...]
    is_closed: bool
    elevation: float | None = None
    source_polyline_type: str | None = None
    tessellation_tolerance: float | None = None


class CanonicalEllipse(StrictModel):
    """Canonical ellipse where source parameters are preserved exactly enough for later conversion."""

    type: Literal["ellipse"] = "ellipse"
    center: CanonicalPoint
    major_axis: CanonicalPoint
    extrusion: CanonicalPoint | None
    ratio: float
    start_param: float
    end_param: float


class CanonicalSplineReference(StrictModel):
    """Preserved spline evidence; Stage 6 does not flatten splines by default."""

    type: Literal["spline_reference"] = "spline_reference"
    degree: int | None
    is_closed: bool
    control_points: tuple[CanonicalPoint, ...]
    fit_points: tuple[CanonicalPoint, ...]
    knot_values: tuple[float, ...]
    weights: tuple[float, ...]


CanonicalGeometry = CanonicalPoint | CanonicalLine | CanonicalArc | CanonicalCircle | CanonicalPolyline | CanonicalEllipse | CanonicalSplineReference


class CanonicalizationIssue(StrictModel):
    """Machine-readable canonicalization issue with source evidence."""

    code: str
    severity: IssueSeverity
    message: str
    source_handle: str | None = None
    original_dxf_type: str | None = None
    ancestry: tuple[str, ...] = ()
    evidence: dict[str, Any] = {}


class CanonicalEntity(StrictModel):
    """One immutable canonical entity plus preserved DXF evidence."""

    order_key: tuple[int, ...]
    source_handle: str | None
    original_dxf_type: str
    canonical_type: CanonicalType
    status: CanonicalStatus
    layer: str
    color: int | None
    linetype: str | None
    block_ancestry: tuple[str, ...] = ()
    insert_handles: tuple[str, ...] = ()
    is_closed: bool | None = None
    z_values: tuple[float, ...] = ()
    elevation: float | None = None
    geometry: CanonicalGeometry | None = None
    metadata: dict[str, Any] = {}
    confidence: float = 1.0


class CanonicalGeometryResult(StrictModel):
    """Complete Stage 6 canonicalization result."""

    source_path: Path | None
    source_sha256: str | None
    source_identity: str
    dxf_version: str
    insunits: int | None
    drawing_units: str
    total_source_entities_visited: int
    total_canonical_entities: int
    counts_by_original_dxf_type: dict[str, int]
    counts_by_canonical_type: dict[str, int]
    counts_by_status: dict[str, int]
    canonical_extents: dict[str, float] | None
    issues: tuple[CanonicalizationIssue, ...]
    entities: tuple[CanonicalEntity, ...]
    tessellation_enabled: bool
    tessellation_tolerance: float | None

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for reports and regression tests."""

        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def canonicalize_dxf(
    source: Path | str | ezdxf.document.Drawing,
    *,
    tessellate_curves: bool = False,
    tessellation_tolerance: float | None = None,
    z_epsilon: float = DEFAULT_Z_EPSILON,
    max_entity_expansion: int = 50000,
    max_insert_recursion_depth: int = 25,
    max_curve_tessellation_points: int = 5000,
) -> CanonicalGeometryResult:
    """Canonicalize modelspace DXF geometry without saving or mutating the source document."""

    z_epsilon = _validate_z_epsilon(z_epsilon)
    if max_entity_expansion <= 0:
        raise ValueError("max_entity_expansion must be positive")
    if max_insert_recursion_depth <= 0:
        raise ValueError("max_insert_recursion_depth must be positive")
    if max_curve_tessellation_points <= 0:
        raise ValueError("max_curve_tessellation_points must be positive")
    if tessellate_curves and (tessellation_tolerance is None or tessellation_tolerance <= 0):
        raise ValueError("tessellation_tolerance must be positive when tessellate_curves=True")

    source_path: Path | None = None
    source_sha256: str | None = None
    if isinstance(source, (str, Path)):
        source_path = Path(source)
        _validate_dxf_path(source_path)
        source_sha256 = _sha256(source_path)
        try:
            doc = ezdxf.readfile(source_path)
        except Exception as exc:
            raise CanonicalizationError(f"Invalid or unreadable DXF file: {source_path}") from exc
        source_identity = str(source_path)
    else:
        doc = source
        source_identity = f"ezdxf-document:{id(doc)}"

    issues: list[CanonicalizationIssue] = []
    entities: list[CanonicalEntity] = []
    visited = 0
    block_lookup = {str(block.name): block for block in doc.blocks}

    for index, entity in enumerate(list(doc.modelspace())):
        if len(entities) >= max_entity_expansion:
            issues.append(_issue(CanonicalizationIssueCode.ENTITY_EXPANSION_CAP_EXCEEDED, "fail", "Canonical entity expansion exceeded the configured cap.", evidence={"cap": max_entity_expansion, "visited_before_stop": visited}))
            break
        visited += _walk_entity(
            entity,
            entities,
            issues,
            block_lookup,
            order_key=(index,),
            block_ancestry=(),
            insert_handles=(),
            visiting_blocks=(),
            tessellate_curves=tessellate_curves,
            tessellation_tolerance=tessellation_tolerance,
            z_epsilon=z_epsilon,
            max_entity_expansion=max_entity_expansion,
            max_insert_recursion_depth=max_insert_recursion_depth,
            max_curve_tessellation_points=max_curve_tessellation_points,
        )

    entities = sorted(entities, key=lambda item: item.order_key)
    extents = _canonical_extents(entities, issues)
    original_counts = Counter(entity.original_dxf_type for entity in entities)
    canonical_counts = Counter(entity.canonical_type for entity in entities)
    status_counts = Counter(entity.status for entity in entities)

    return CanonicalGeometryResult(
        source_path=source_path,
        source_sha256=source_sha256,
        source_identity=source_identity,
        dxf_version=str(getattr(doc, "dxfversion", "") or ""),
        insunits=_read_insunits(doc, source_path),
        drawing_units=_decode_units(_read_insunits(doc, source_path)),
        total_source_entities_visited=visited,
        total_canonical_entities=len(entities),
        counts_by_original_dxf_type=dict(sorted(original_counts.items())),
        counts_by_canonical_type=dict(sorted(canonical_counts.items())),
        counts_by_status=dict(sorted(status_counts.items())),
        canonical_extents=extents,
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.source_handle or "", item.message))),
        entities=tuple(entities),
        tessellation_enabled=tessellate_curves,
        tessellation_tolerance=tessellation_tolerance if tessellate_curves else None,
    )


def write_canonical_json(result: CanonicalGeometryResult, output_path: Path | str) -> Path:
    """Write deterministic canonicalization JSON to an explicit path."""

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_deterministic_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write canonical geometry JSON report: {path}") from exc
    return path


def _walk_entity(
    entity: DXFEntity,
    entities: list[CanonicalEntity],
    issues: list[CanonicalizationIssue],
    block_lookup: dict[str, Any],
    *,
    order_key: tuple[int, ...],
    block_ancestry: tuple[str, ...],
    insert_handles: tuple[str, ...],
    visiting_blocks: tuple[str, ...],
    tessellate_curves: bool,
    tessellation_tolerance: float | None,
    z_epsilon: float,
    max_entity_expansion: int,
    max_insert_recursion_depth: int,
    max_curve_tessellation_points: int,
) -> int:
    dxf_type = _entity_type(entity)
    if dxf_type == "INSERT":
        return _walk_insert(
            entity,
            entities,
            issues,
            block_lookup,
            order_key=order_key,
            block_ancestry=block_ancestry,
            insert_handles=insert_handles,
            visiting_blocks=visiting_blocks,
            tessellate_curves=tessellate_curves,
            tessellation_tolerance=tessellation_tolerance,
            z_epsilon=z_epsilon,
            max_entity_expansion=max_entity_expansion,
            max_insert_recursion_depth=max_insert_recursion_depth,
            max_curve_tessellation_points=max_curve_tessellation_points,
        )

    if len(entities) >= max_entity_expansion:
        issues.append(_issue(CanonicalizationIssueCode.ENTITY_EXPANSION_CAP_EXCEEDED, "fail", "Canonical entity expansion exceeded the configured cap.", source_handle=_handle(entity), original_dxf_type=dxf_type, ancestry=block_ancestry, evidence={"cap": max_entity_expansion}))
        return 1
    entities.append(_canonical_entity(entity, order_key, block_ancestry, insert_handles, issues, tessellate_curves, tessellation_tolerance, z_epsilon, max_curve_tessellation_points))
    return 1


def _walk_insert(
    insert: DXFEntity,
    entities: list[CanonicalEntity],
    issues: list[CanonicalizationIssue],
    block_lookup: dict[str, Any],
    *,
    order_key: tuple[int, ...],
    block_ancestry: tuple[str, ...],
    insert_handles: tuple[str, ...],
    visiting_blocks: tuple[str, ...],
    tessellate_curves: bool,
    tessellation_tolerance: float | None,
    z_epsilon: float,
    max_entity_expansion: int,
    max_insert_recursion_depth: int,
    max_curve_tessellation_points: int,
) -> int:
    name = str(insert.dxf.get("name", ""))
    handle = _handle(insert)
    if len(visiting_blocks) >= max_insert_recursion_depth:
        issues.append(_issue(CanonicalizationIssueCode.INSERT_RECURSION_DEPTH_EXCEEDED, "fail", "INSERT recursion exceeded the configured depth cap.", source_handle=handle, original_dxf_type="INSERT", ancestry=block_ancestry + (name,), evidence={"cap": max_insert_recursion_depth, "block_name": name}))
        return 1
    if name in visiting_blocks:
        issues.append(
            _issue(
                CanonicalizationIssueCode.BLOCK_CYCLE_DETECTED,
                "warning",
                f"Recursive block reference detected for INSERT block {name}.",
                source_handle=handle,
                original_dxf_type="INSERT",
                ancestry=block_ancestry + (name,),
                evidence={"block_name": name},
            )
        )
        return 1

    block = block_lookup.get(name)
    if block is None:
        entities.append(_unsupported_entity(insert, order_key, block_ancestry, insert_handles, "review_required", {"block_name": name}))
        issues.append(
            _issue(
                CanonicalizationIssueCode.UNSUPPORTED_ENTITY_TYPE,
                "warning",
                f"INSERT references missing block definition: {name}.",
                source_handle=handle,
                original_dxf_type="INSERT",
                ancestry=block_ancestry,
                evidence={"block_name": name},
            )
        )
        return 1

    visited = 1
    try:
        matrix = insert.matrix44()
    except Exception as exc:
        entities.append(_unsupported_entity(insert, order_key, block_ancestry, insert_handles, "review_required", {"block_name": name, "error": str(exc)}))
        issues.append(
            _issue(
                CanonicalizationIssueCode.INSERT_TRANSFORM_FAILED,
                "warning",
                f"INSERT transform could not be calculated for block {name}: {exc}",
                source_handle=handle,
                original_dxf_type="INSERT",
                ancestry=block_ancestry,
                evidence={"block_name": name},
            )
        )
        return visited

    next_ancestry = block_ancestry + (f"{name}:{handle or ''}",)
    next_insert_handles = insert_handles + ((handle or ""),)
    for child_index, child in enumerate(list(block)):
        if len(entities) >= max_entity_expansion:
            issues.append(_issue(CanonicalizationIssueCode.ENTITY_EXPANSION_CAP_EXCEEDED, "fail", "Canonical entity expansion exceeded the configured cap while walking INSERT content.", source_handle=handle, original_dxf_type="INSERT", ancestry=next_ancestry, evidence={"cap": max_entity_expansion, "block_name": name}))
            return visited
        visited += 1
        try:
            transformed = child.copy()
            if _handle(child) is not None:
                transformed.dxf.handle = _handle(child)
            transformed.transform(matrix)
        except Exception as exc:
            issues.append(
                _issue(
                    CanonicalizationIssueCode.INSERT_TRANSFORM_FAILED,
                    "warning",
                    f"Nested entity transform failed in block {name}: {exc}",
                    source_handle=_handle(child),
                    original_dxf_type=_entity_type(child),
                    ancestry=next_ancestry,
                    evidence={"block_name": name, "insert_handle": handle},
                )
            )
            entities.append(_unsupported_entity(child, order_key + (child_index,), next_ancestry, next_insert_handles, "review_required", {"transform_error": str(exc)}))
            continue
        visited += _walk_entity(
            transformed,
            entities,
            issues,
            block_lookup,
            order_key=order_key + (child_index,),
            block_ancestry=next_ancestry,
            insert_handles=next_insert_handles,
            visiting_blocks=visiting_blocks + (name,),
            tessellate_curves=tessellate_curves,
            tessellation_tolerance=tessellation_tolerance,
            z_epsilon=z_epsilon,
            max_entity_expansion=max_entity_expansion,
            max_insert_recursion_depth=max_insert_recursion_depth,
            max_curve_tessellation_points=max_curve_tessellation_points,
        ) - 1
    return visited


def _canonical_entity(
    entity: DXFEntity,
    order_key: tuple[int, ...],
    ancestry: tuple[str, ...],
    insert_handles: tuple[str, ...],
    issues: list[CanonicalizationIssue],
    tessellate_curves: bool,
    tessellation_tolerance: float | None,
    z_epsilon: float,
    max_curve_tessellation_points: int,
) -> CanonicalEntity:
    dxf_type = _entity_type(entity)
    if dxf_type in THREE_D_TYPES or (dxf_type == "POLYLINE" and bool(getattr(entity, "is_3d_polyline", False))):
        issues.append(_issue(CanonicalizationIssueCode.UNSUPPORTED_3D_GEOMETRY, "warning", f"3D entity skipped: {dxf_type}.", source_handle=_handle(entity), original_dxf_type=dxf_type, ancestry=ancestry))
        return _unsupported_entity(entity, order_key, ancestry, insert_handles, "skipped_3d")

    try:
        geometry, canonical_type, status, is_closed = _geometry(entity, tessellate_curves, tessellation_tolerance, max_curve_tessellation_points)
    except Exception as exc:
        point_cap_exceeded = "point cap" in str(exc).lower()
        issue_code = CanonicalizationIssueCode.TESSELLATION_POINT_CAP_EXCEEDED if point_cap_exceeded else (CanonicalizationIssueCode.TESSELLATION_FAILED if tessellate_curves and "tessellation" in str(exc).lower() else CanonicalizationIssueCode.INVALID_ENTITY_GEOMETRY)
        severity = "fail" if point_cap_exceeded else "warning"
        issues.append(_issue(issue_code, severity, f"Entity geometry could not be canonicalized: {exc}", source_handle=_handle(entity), original_dxf_type=dxf_type, ancestry=ancestry))
        return _unsupported_entity(entity, order_key, ancestry, insert_handles, "review_required", {"error": str(exc)})

    if status == "preserved_curve":
        issues.append(_issue(CanonicalizationIssueCode.CURVE_PRESERVED_NOT_FLATTENED, "info", f"{dxf_type} preserved as curve/reference data.", source_handle=_handle(entity), original_dxf_type=dxf_type, ancestry=ancestry))
    if dxf_type not in DIRECT_2D_TYPES:
        issues.append(_issue(CanonicalizationIssueCode.UNSUPPORTED_ENTITY_TYPE, "warning", f"Unsupported entity type reported without conversion: {dxf_type}.", source_handle=_handle(entity), original_dxf_type=dxf_type, ancestry=ancestry))

    z_values = _z_values(geometry)
    elevation = _elevation(entity)
    if any(_numeric_nonzero(z, z_epsilon) for z in z_values) or _numeric_nonzero(elevation, z_epsilon):
        issues.append(
            _issue(
                CanonicalizationIssueCode.NONZERO_Z_GEOMETRY,
                "warning",
                "Entity contains non-zero Z/elevation information.",
                source_handle=_handle(entity),
                original_dxf_type=dxf_type,
                ancestry=ancestry,
                evidence={"z_values": tuple(z_values), "elevation": elevation, "z_epsilon": z_epsilon},
            )
        )

    return CanonicalEntity(
        order_key=order_key,
        source_handle=_handle(entity),
        original_dxf_type=dxf_type,
        canonical_type=canonical_type,
        status=status,
        layer=_layer(entity),
        color=_dxf_get(entity, "color"),
        linetype=_dxf_get(entity, "linetype"),
        block_ancestry=ancestry,
        insert_handles=insert_handles,
        is_closed=is_closed,
        z_values=tuple(z_values),
        elevation=elevation,
        geometry=geometry,
        metadata=_metadata(entity),
        confidence=1.0 if status != "unsupported" else 0.0,
    )


def _geometry(entity: DXFEntity, tessellate_curves: bool, tessellation_tolerance: float | None, max_curve_tessellation_points: int) -> tuple[CanonicalGeometry | None, CanonicalType, CanonicalStatus, bool | None]:
    dxf_type = _entity_type(entity)
    if dxf_type == "LINE":
        return CanonicalLine(start=_point(entity.dxf.start), end=_point(entity.dxf.end)), "line", "canonicalized", False
    if dxf_type == "POINT":
        return _point(entity.dxf.location), "point", "canonicalized", None
    if dxf_type == "ARC":
        return (
            CanonicalArc(center=_point(entity.dxf.center), radius=float(entity.dxf.radius), start_angle=float(entity.dxf.start_angle), end_angle=float(entity.dxf.end_angle)),
            "arc",
            "convertible_later",
            False,
        )
    if dxf_type == "CIRCLE":
        return CanonicalCircle(center=_point(entity.dxf.center), radius=float(entity.dxf.radius)), "circle", "convertible_later", True
    if dxf_type == "LWPOLYLINE":
        vertices = tuple(CanonicalPolylineVertex(point=CanonicalPoint(x=float(x), y=float(y), z=float(entity.dxf.get("elevation", 0.0) or 0.0)), start_width=float(sw), end_width=float(ew), bulge=float(bulge)) for x, y, sw, ew, bulge in entity.get_points("xyseb"))
        return CanonicalPolyline(vertices=vertices, is_closed=bool(entity.closed), elevation=_elevation(entity), source_polyline_type="LWPOLYLINE"), "polyline", "canonicalized", bool(entity.closed)
    if dxf_type == "POLYLINE" and bool(getattr(entity, "is_2d_polyline", False)):
        vertices = tuple(CanonicalPolylineVertex(point=_point(vertex.dxf.location), bulge=float(vertex.dxf.get("bulge", 0.0) or 0.0)) for vertex in entity.vertices)
        return CanonicalPolyline(vertices=vertices, is_closed=bool(entity.is_closed), elevation=_elevation(entity), source_polyline_type="POLYLINE"), "polyline", "canonicalized", bool(entity.is_closed)
    if dxf_type == "ELLIPSE":
        if tessellate_curves:
            return _tessellated(entity, tessellation_tolerance, max_curve_tessellation_points), "tessellated_polyline", "canonicalized", bool(_ellipse_closed(entity))
        return (
            CanonicalEllipse(
                center=_point(entity.dxf.center),
                major_axis=_point(entity.dxf.major_axis),
                extrusion=_point(entity.dxf.extrusion) if entity.dxf.hasattr("extrusion") else None,
                ratio=float(entity.dxf.ratio),
                start_param=float(entity.dxf.start_param),
                end_param=float(entity.dxf.end_param),
            ),
            "ellipse",
            "preserved_curve",
            _ellipse_closed(entity),
        )
    if dxf_type == "SPLINE":
        if tessellate_curves:
            return _tessellated(entity, tessellation_tolerance, max_curve_tessellation_points), "tessellated_polyline", "canonicalized", bool(getattr(entity, "closed", False))
        return (
            CanonicalSplineReference(
                degree=int(entity.dxf.degree) if entity.dxf.hasattr("degree") else None,
                is_closed=bool(getattr(entity, "closed", False)),
                control_points=tuple(_point(point) for point in getattr(entity, "control_points", [])),
                fit_points=tuple(_point(point) for point in getattr(entity, "fit_points", [])),
                knot_values=tuple(float(value) for value in getattr(entity, "knots", [])),
                weights=tuple(float(value) for value in getattr(entity, "weights", [])),
            ),
            "spline_reference",
            "preserved_curve",
            bool(getattr(entity, "closed", False)),
        )
    return None, "unsupported", "unsupported", None


def _tessellated(entity: DXFEntity, tolerance: float | None, max_curve_tessellation_points: int) -> CanonicalPolyline:
    try:
        points = tuple(_point(point) for point in entity.flattening(float(tolerance)))
    except Exception as exc:
        raise RuntimeError(f"tessellation failed: {exc}") from exc
    if len(points) < 2:
        raise RuntimeError("tessellation returned fewer than two points")
    if len(points) > max_curve_tessellation_points:
        raise RuntimeError(f"tessellation point cap exceeded: {len(points)} > {max_curve_tessellation_points}")
    vertices = tuple(CanonicalPolylineVertex(point=point) for point in points)
    return CanonicalPolyline(type="tessellated_polyline", vertices=vertices, is_closed=points[0] == points[-1], tessellation_tolerance=tolerance)


def _unsupported_entity(entity: DXFEntity, order_key: tuple[int, ...], ancestry: tuple[str, ...], insert_handles: tuple[str, ...], status: CanonicalStatus, metadata: dict[str, Any] | None = None) -> CanonicalEntity:
    return CanonicalEntity(
        order_key=order_key,
        source_handle=_handle(entity),
        original_dxf_type=_entity_type(entity),
        canonical_type="unsupported",
        status=status,
        layer=_layer(entity),
        color=_dxf_get(entity, "color"),
        linetype=_dxf_get(entity, "linetype"),
        block_ancestry=ancestry,
        insert_handles=insert_handles,
        geometry=None,
        metadata={**_metadata(entity), **(metadata or {})},
        confidence=0.0,
    )


def _canonical_extents(entities: Iterable[CanonicalEntity], issues: list[CanonicalizationIssue]) -> dict[str, float] | None:
    points: list[CanonicalPoint] = []
    for entity in entities:
        if entity.geometry is not None:
            points.extend(_geometry_points(entity.geometry))
    if not points:
        issues.append(_issue(CanonicalizationIssueCode.CANONICAL_EXTENTS_UNAVAILABLE, "warning", "Canonical extents are unavailable because no point-bearing canonical geometry exists."))
        return None
    return {
        "min_x": min(point.x for point in points),
        "min_y": min(point.y for point in points),
        "min_z": min(point.z for point in points),
        "max_x": max(point.x for point in points),
        "max_y": max(point.y for point in points),
        "max_z": max(point.z for point in points),
    }


def _geometry_points(geometry: CanonicalGeometry) -> list[CanonicalPoint]:
    if isinstance(geometry, CanonicalPoint):
        return [geometry]
    if isinstance(geometry, CanonicalLine):
        return [geometry.start, geometry.end]
    if isinstance(geometry, CanonicalArc):
        return [geometry.center]
    if isinstance(geometry, CanonicalCircle):
        return [geometry.center]
    if isinstance(geometry, CanonicalPolyline):
        return [vertex.point for vertex in geometry.vertices]
    if isinstance(geometry, CanonicalEllipse):
        return [geometry.center, CanonicalPoint(x=geometry.center.x + geometry.major_axis.x, y=geometry.center.y + geometry.major_axis.y, z=geometry.center.z + geometry.major_axis.z)]
    if isinstance(geometry, CanonicalSplineReference):
        return list(geometry.control_points or geometry.fit_points)
    return []


def _point(value: Any) -> CanonicalPoint:
    return CanonicalPoint(x=float(value[0]), y=float(value[1]), z=float(value[2]) if len(value) > 2 else 0.0)


def _z_values(geometry: CanonicalGeometry | None) -> list[float]:
    if geometry is None:
        return []
    return [point.z for point in _geometry_points(geometry)]


def _metadata(entity: DXFEntity) -> dict[str, Any]:
    return {
        "handle": _handle(entity),
        "owner": _dxf_get(entity, "owner"),
        "paperspace": _dxf_get(entity, "paperspace"),
        "true_color": _dxf_get(entity, "true_color"),
        "color": _dxf_get(entity, "color"),
        "linetype": _dxf_get(entity, "linetype"),
    }


def _validate_dxf_path(path: Path) -> None:
    if path.suffix.lower() != ".dxf":
        raise CanonicalizationError(f"Stage 6 canonicalization accepts .dxf files only: {path}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise CanonicalizationError(f"DXF file is missing, invalid, or empty: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_insunits(doc: Any, source_path: Path | None) -> int | None:
    if source_path is not None and not _raw_header_contains(source_path, "$INSUNITS"):
        return None
    value = doc.header.get("$INSUNITS")
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _raw_header_contains(path: Path, variable: str) -> bool:
    try:
        return variable in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _decode_units(insunits: int | None) -> str:
    if insunits is None:
        return "missing"
    try:
        decoded = units.decode(insunits)
    except Exception:
        return "unknown"
    return str(decoded) if decoded else "unknown"


def _ellipse_closed(entity: DXFEntity) -> bool:
    return abs(float(entity.dxf.end_param) - float(entity.dxf.start_param)) >= 6.283185307179586 - 1e-9


def _entity_type(entity: DXFEntity) -> str:
    try:
        return str(entity.dxftype()).upper()
    except Exception:
        return "UNKNOWN"


def _handle(entity: DXFEntity) -> str | None:
    value = _dxf_get(entity, "handle")
    return str(value) if value is not None else None


def _layer(entity: DXFEntity) -> str:
    value = _dxf_get(entity, "layer")
    return str(value) if value is not None else "0"


def _elevation(entity: DXFEntity) -> float | None:
    value = _dxf_get(entity, "elevation")
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _dxf_get(entity: DXFEntity, key: str) -> Any:
    try:
        return entity.dxf.get(key)
    except Exception:
        return None


def _numeric_nonzero(value: Any, z_epsilon: float) -> bool:
    try:
        return abs(float(value)) > z_epsilon
    except (TypeError, ValueError):
        return False


def _validate_z_epsilon(value: float) -> float:
    epsilon = float(value)
    if epsilon < 0:
        raise ValueError("z_epsilon must be non-negative")
    return epsilon


def _issue(
    code: CanonicalizationIssueCode,
    severity: IssueSeverity,
    message: str,
    *,
    source_handle: str | None = None,
    original_dxf_type: str | None = None,
    ancestry: tuple[str, ...] = (),
    evidence: dict[str, Any] | None = None,
) -> CanonicalizationIssue:
    return CanonicalizationIssue(code=code.value, severity=severity, message=message, source_handle=source_handle, original_dxf_type=original_dxf_type, ancestry=ancestry, evidence=evidence or {})


