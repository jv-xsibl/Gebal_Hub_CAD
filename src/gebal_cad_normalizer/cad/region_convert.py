"""Stage 7 REGION to closed-polyline conversion.

ezdxf exposes DXF REGION entities as ACIS containers. This module converts only
REGIONs that provide auditable planar boundary-loop evidence; opaque ACIS data is
retained and reported instead of guessed.
"""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import ezdxf
from ezdxf.entities import DXFEntity

from gebal_cad_normalizer.exceptions import DxfReadError, OutputWriteError, RegionConversionError
from gebal_cad_normalizer.models import StrictModel


REGION_EVIDENCE_APPID = "GEBAL_REGION_BOUNDARY"
REGION_OUTPUT_APPID = "GEBAL_REGION_CONVERSION"

RegionConversionStatus = Literal["converted", "approximated", "review_required", "failed"]
IssueSeverity = Literal["info", "warning", "fail"]


class RegionConversionIssueCode(str, Enum):
    """Stable Stage 7 issue codes."""

    REGION_NONPLANAR = "region_nonplanar"
    REGION_INVALID = "region_invalid"
    REGION_OPEN_LOOP = "region_open_loop"
    REGION_SELF_INTERSECTION = "region_self_intersection"
    REGION_TOPOLOGY_FAILED = "region_topology_failed"
    REGION_CURVE_APPROXIMATED = "region_curve_approximated"
    REGION_AREA_MISMATCH = "region_area_mismatch"
    REGION_PERIMETER_MISMATCH = "region_perimeter_mismatch"
    REGION_CONVERSION_FAILED = "region_conversion_failed"


class RegionPoint(StrictModel):
    """2D point on a converted REGION loop."""

    x: float
    y: float


class ConvertedLoop(StrictModel):
    """Closed output loop converted from a REGION boundary loop."""

    loop_id: str
    parent_loop_id: str | None = None
    role: Literal["outer", "hole"] = "outer"
    vertices: tuple[RegionPoint, ...]
    bulges: tuple[float, ...]
    is_closed: bool
    elevation: float
    area: float | None
    perimeter: float | None
    used_tessellation: bool = False
    tessellation_tolerance: float | None = None
    source_evidence: dict[str, Any] = {}


class RegionConversionIssue(StrictModel):
    """Machine-readable REGION conversion issue."""

    code: str
    severity: IssueSeverity
    message: str
    source_handle: str | None = None
    layer: str | None = None
    evidence: dict[str, Any] = {}


class ConvertedRegion(StrictModel):
    """Conversion result for one source REGION entity."""

    order_index: int
    source_handle: str | None
    layer: str
    color: int | None
    linetype: str | None
    elevation: float
    status: RegionConversionStatus
    loops: tuple[ConvertedLoop, ...]
    issue_codes: tuple[str, ...]
    source_area: float | None
    converted_area: float | None
    area_deviation: float | None
    source_perimeter: float | None
    converted_perimeter: float | None
    perimeter_deviation: float | None
    conversion_evidence: dict[str, Any] = {}


class RegionConversionResult(StrictModel):
    """Complete Stage 7 conversion result."""

    source_path: Path | None
    source_sha256: str | None
    source_identity: str
    dxf_version: str
    tolerance: float
    region_count: int
    converted_count: int
    failed_count: int
    review_required_count: int
    approximated_count: int
    loop_count: int
    counts_by_status: dict[str, int]
    issues: tuple[RegionConversionIssue, ...]
    regions: tuple[ConvertedRegion, ...]
    output_path: Path | None = None

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for reports and regression tests."""

        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def convert_regions(
    source: Path | str | ezdxf.document.Drawing,
    *,
    output_path: Path | str | None = None,
    tolerance: float = 1e-6,
    allow_overwrite_input: bool = False,
) -> RegionConversionResult:
    """Convert supported planar REGION entities without mutating the source.

    If ``output_path`` is omitted, only in-memory converted-loop geometry is
    returned. If ``output_path`` is provided, a new DXF copy is written
    atomically with successfully converted REGIONs replaced by closed
    LWPOLYLINEs; failed/review-required REGIONs are preserved.
    """

    if tolerance <= 0:
        raise ValueError("tolerance must be positive")

    source_path: Path | None = None
    source_sha256: str | None = None
    if isinstance(source, (str, Path)):
        source_path = Path(source)
        _validate_dxf_path(source_path)
        source_sha256 = _sha256(source_path)
        try:
            doc = ezdxf.readfile(source_path)
        except Exception as exc:
            raise RegionConversionError(f"Invalid or unreadable DXF file: {source_path}") from exc
        source_identity = str(source_path)
    else:
        doc = source
        source_identity = f"ezdxf-document:{id(doc)}"

    regions, issues = _convert_document_regions(doc, tolerance)
    written_output: Path | None = None
    if output_path is not None:
        destination = Path(output_path)
        if source_path is not None and destination.resolve() == source_path.resolve() and not allow_overwrite_input:
            raise OutputWriteError("Refusing to overwrite input DXF without allow_overwrite_input=True")
        write_doc = ezdxf.readfile(source_path) if source_path is not None else copy.deepcopy(doc)
        _write_converted_copy(write_doc, regions, destination)
        written_output = destination

    counts = Counter(region.status for region in regions)
    return RegionConversionResult(
        source_path=source_path,
        source_sha256=source_sha256,
        source_identity=source_identity,
        dxf_version=str(getattr(doc, "dxfversion", "") or ""),
        tolerance=float(tolerance),
        region_count=len(regions),
        converted_count=counts.get("converted", 0),
        failed_count=counts.get("failed", 0),
        review_required_count=counts.get("review_required", 0),
        approximated_count=counts.get("approximated", 0),
        loop_count=sum(len(region.loops) for region in regions),
        counts_by_status=dict(sorted(counts.items())),
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.source_handle or "", item.message))),
        regions=tuple(sorted(regions, key=lambda item: (item.order_index, item.source_handle or ""))),
        output_path=written_output,
    )


def attach_region_boundary_evidence(region: DXFEntity, evidence: dict[str, Any]) -> None:
    """Attach deterministic boundary-loop evidence to a REGION fixture/entity."""

    doc = getattr(region, "doc", None)
    if doc is not None and REGION_EVIDENCE_APPID not in doc.appids:
        doc.appids.add(REGION_EVIDENCE_APPID)
    if hasattr(region, "sat") and not getattr(region, "sat", ()):
        region.sat = ("Gebal boundary evidence carrier",)
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    region.set_xdata(REGION_EVIDENCE_APPID, [(1000, payload)])


def _convert_document_regions(doc: ezdxf.document.Drawing, tolerance: float) -> tuple[list[ConvertedRegion], list[RegionConversionIssue]]:
    issues: list[RegionConversionIssue] = []
    converted: list[ConvertedRegion] = []
    for index, entity in enumerate(list(doc.modelspace())):
        if _entity_type(entity) != "REGION":
            continue
        converted.append(_convert_region(entity, len(converted), issues, tolerance))
    return converted, issues


def _convert_region(entity: DXFEntity, order_index: int, issues: list[RegionConversionIssue], tolerance: float) -> ConvertedRegion:
    handle = _handle(entity)
    layer = _layer(entity)
    elevation = _elevation(entity)
    region_issues: list[RegionConversionIssue] = []
    evidence = _read_boundary_evidence(entity)

    if evidence is None:
        region_issues.append(
            _issue(
                RegionConversionIssueCode.REGION_CONVERSION_FAILED,
                "fail",
                "REGION boundary loops are unavailable; opaque ACIS data was not guessed.",
                source_handle=handle,
                layer=layer,
                evidence={"supported_evidence_appid": REGION_EVIDENCE_APPID},
            )
        )
        issues.extend(region_issues)
        return _region_model(entity, order_index, "failed", (), region_issues, None, None, None, None, evidence={})

    if bool(evidence.get("nonplanar")) or _nondefault_extrusion(entity):
        region_issues.append(_issue(RegionConversionIssueCode.REGION_NONPLANAR, "fail", "REGION is not planar in the supported XY conversion plane.", source_handle=handle, layer=layer, evidence=evidence))
    if bool(evidence.get("invalid")):
        region_issues.append(_issue(RegionConversionIssueCode.REGION_INVALID, "fail", "REGION boundary evidence is marked invalid.", source_handle=handle, layer=layer, evidence=evidence))

    loops: list[ConvertedLoop] = []
    raw_loops = evidence.get("loops")
    if not isinstance(raw_loops, list) or not raw_loops:
        region_issues.append(_issue(RegionConversionIssueCode.REGION_TOPOLOGY_FAILED, "fail", "REGION has no supported boundary loops.", source_handle=handle, layer=layer, evidence=evidence))
    else:
        for loop_index, raw_loop in enumerate(raw_loops):
            loop = _parse_loop(raw_loop, loop_index, elevation, tolerance, handle, layer, region_issues)
            if loop is not None:
                loops.append(loop)

    if loops and not any(loop.role == "outer" for loop in loops):
        region_issues.append(_issue(RegionConversionIssueCode.REGION_TOPOLOGY_FAILED, "fail", "REGION topology has no outer loop.", source_handle=handle, layer=layer, evidence=evidence))
    loops = _assign_hole_parents(loops)

    source_area = _optional_float(evidence.get("area"))
    source_perimeter = _optional_float(evidence.get("perimeter"))
    converted_area = _signed_region_area(loops) if loops else None
    converted_perimeter = sum((loop.perimeter or 0.0) for loop in loops) if loops else None
    area_dev = _deviation(source_area, converted_area)
    perimeter_dev = _deviation(source_perimeter, converted_perimeter)

    if source_area is not None and converted_area is not None and area_dev is not None and area_dev > tolerance:
        region_issues.append(_issue(RegionConversionIssueCode.REGION_AREA_MISMATCH, "fail", "Converted REGION area exceeds tolerance.", source_handle=handle, layer=layer, evidence={"source_area": source_area, "converted_area": converted_area, "deviation": area_dev, "tolerance": tolerance}))
    if source_perimeter is not None and converted_perimeter is not None and perimeter_dev is not None and perimeter_dev > tolerance:
        region_issues.append(_issue(RegionConversionIssueCode.REGION_PERIMETER_MISMATCH, "fail", "Converted REGION perimeter exceeds tolerance.", source_handle=handle, layer=layer, evidence={"source_perimeter": source_perimeter, "converted_perimeter": converted_perimeter, "deviation": perimeter_dev, "tolerance": tolerance}))

    used_tessellation = any(loop.used_tessellation for loop in loops)
    if used_tessellation:
        region_issues.append(_issue(RegionConversionIssueCode.REGION_CURVE_APPROXIMATED, "warning", "REGION contains curve segments approximated by tessellation.", source_handle=handle, layer=layer, evidence={"tolerance": tolerance}))

    issues.extend(region_issues)
    fail_codes = {issue.code for issue in region_issues if issue.severity == "fail"}
    if fail_codes:
        status: RegionConversionStatus = "failed"
    elif not loops:
        status = "review_required"
    elif used_tessellation:
        status = "approximated"
    else:
        status = "converted"
    return _region_model(entity, order_index, status, tuple(loops), region_issues, source_area, source_perimeter, area_dev, perimeter_dev, evidence=evidence, converted_area=converted_area, converted_perimeter=converted_perimeter)


def _parse_loop(
    raw_loop: Any,
    loop_index: int,
    elevation: float,
    tolerance: float,
    source_handle: str | None,
    layer: str,
    issues: list[RegionConversionIssue],
) -> ConvertedLoop | None:
    if not isinstance(raw_loop, dict):
        issues.append(_issue(RegionConversionIssueCode.REGION_TOPOLOGY_FAILED, "fail", "REGION loop evidence is not an object.", source_handle=source_handle, layer=layer))
        return None
    role = str(raw_loop.get("role", "outer"))
    if role not in {"outer", "hole"}:
        role = "outer"
    vertices_raw = raw_loop.get("vertices")
    if not isinstance(vertices_raw, list) or len(vertices_raw) < 2:
        issues.append(_issue(RegionConversionIssueCode.REGION_OPEN_LOOP, "fail", "REGION loop has fewer than three vertices.", source_handle=source_handle, layer=layer, evidence={"loop_index": loop_index}))
        return None

    try:
        vertices = tuple(RegionPoint(x=float(point[0]), y=float(point[1])) for point in vertices_raw)
    except Exception:
        issues.append(_issue(RegionConversionIssueCode.REGION_INVALID, "fail", "REGION loop contains invalid vertex coordinates.", source_handle=source_handle, layer=layer, evidence={"loop_index": loop_index}))
        return None
    vertices = _drop_duplicate_closing_vertex(vertices, tolerance)

    bulges_raw = raw_loop.get("bulges", [0.0] * len(vertices))
    try:
        bulges = tuple(float(value) for value in bulges_raw)
    except Exception:
        issues.append(_issue(RegionConversionIssueCode.REGION_INVALID, "fail", "REGION loop contains invalid bulge values.", source_handle=source_handle, layer=layer, evidence={"loop_index": loop_index}))
        return None
    if len(bulges) != len(vertices):
        issues.append(_issue(RegionConversionIssueCode.REGION_INVALID, "fail", "REGION loop bulge count does not match vertex count.", source_handle=source_handle, layer=layer, evidence={"loop_index": loop_index}))
        return None

    closed = bool(raw_loop.get("closed", True)) and len(vertices) >= 2 and not _has_zero_segments(vertices, tolerance)
    if not closed:
        issues.append(_issue(RegionConversionIssueCode.REGION_OPEN_LOOP, "fail", "REGION loop is open or contains zero-length segments.", source_handle=source_handle, layer=layer, evidence={"loop_index": loop_index}))
    if _self_intersects(vertices, tolerance):
        issues.append(_issue(RegionConversionIssueCode.REGION_SELF_INTERSECTION, "fail", "REGION loop self-intersects.", source_handle=source_handle, layer=layer, evidence={"loop_index": loop_index}))

    area = _loop_area(vertices, bulges)
    perimeter = _loop_perimeter(vertices, bulges)
    used_tessellation = bool(raw_loop.get("used_tessellation", False)) or any(str(segment).lower() not in {"line", "arc"} for segment in raw_loop.get("segments", []))
    return ConvertedLoop(
        loop_id=str(raw_loop.get("loop_id") or f"loop-{loop_index:04d}"),
        parent_loop_id=None,
        role=role,  # type: ignore[arg-type]
        vertices=vertices,
        bulges=bulges,
        is_closed=closed,
        elevation=elevation,
        area=abs(area),
        perimeter=perimeter,
        used_tessellation=used_tessellation,
        tessellation_tolerance=float(raw_loop.get("tessellation_tolerance", tolerance)) if used_tessellation else None,
        source_evidence={key: value for key, value in sorted(raw_loop.items()) if key not in {"vertices", "bulges"}},
    )


def _assign_hole_parents(loops: list[ConvertedLoop]) -> list[ConvertedLoop]:
    outers = [loop for loop in loops if loop.role == "outer"]
    assigned: list[ConvertedLoop] = []
    for loop in loops:
        if loop.role != "hole" or loop.parent_loop_id is not None or not outers:
            assigned.append(loop)
            continue
        point = loop.vertices[0]
        containing = [outer for outer in outers if _point_in_polygon(point, outer.vertices)]
        parent = min(containing, key=lambda item: item.area or float("inf")) if containing else outers[0]
        assigned.append(loop.model_copy(update={"parent_loop_id": parent.loop_id}))
    return assigned


def _write_converted_copy(doc: ezdxf.document.Drawing, regions: list[ConvertedRegion], destination: Path) -> None:
    if destination.suffix.lower() != ".dxf":
        raise OutputWriteError(f"Stage 7 output accepts .dxf files only: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if REGION_OUTPUT_APPID not in doc.appids:
        doc.appids.add(REGION_OUTPUT_APPID)
    msp = doc.modelspace()
    by_handle = {region.source_handle: region for region in regions if region.status in {"converted", "approximated"}}
    for entity in list(msp):
        if _entity_type(entity) != "REGION" or _handle(entity) not in by_handle:
            continue
        region = by_handle[_handle(entity)]
        insert_attribs = {"layer": region.layer}
        if region.color is not None:
            insert_attribs["color"] = region.color
        if region.linetype is not None:
            insert_attribs["linetype"] = region.linetype
        for loop in sorted(region.loops, key=lambda item: (item.role != "outer", item.parent_loop_id or "", item.loop_id)):
            points = [(vertex.x, vertex.y, 0.0, 0.0, loop.bulges[index]) for index, vertex in enumerate(loop.vertices)]
            polyline = msp.add_lwpolyline(points, format="xyseb", close=True, dxfattribs={**insert_attribs, "elevation": loop.elevation})
            polyline.set_xdata(
                REGION_OUTPUT_APPID,
                [
                    (1000, json.dumps({"source_handle": region.source_handle, "loop_id": loop.loop_id, "parent_loop_id": loop.parent_loop_id, "role": loop.role}, sort_keys=True, separators=(",", ":"))),
                ],
            )
        msp.delete_entity(entity)

    temp_path = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        doc.saveas(temp_path)
        os.replace(temp_path, destination)
    except Exception as exc:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass
        raise OutputWriteError(f"Failed to write Stage 7 converted DXF: {destination}") from exc


def _region_model(
    entity: DXFEntity,
    order_index: int,
    status: RegionConversionStatus,
    loops: tuple[ConvertedLoop, ...],
    issues: list[RegionConversionIssue],
    source_area: float | None,
    source_perimeter: float | None,
    area_deviation: float | None,
    perimeter_deviation: float | None,
    *,
    evidence: dict[str, Any],
    converted_area: float | None = None,
    converted_perimeter: float | None = None,
) -> ConvertedRegion:
    return ConvertedRegion(
        order_index=order_index,
        source_handle=_handle(entity),
        layer=_layer(entity),
        color=_dxf_get(entity, "color"),
        linetype=_dxf_get(entity, "linetype"),
        elevation=_elevation(entity),
        status=status,
        loops=loops,
        issue_codes=tuple(sorted({issue.code for issue in issues})),
        source_area=source_area,
        converted_area=converted_area,
        area_deviation=area_deviation,
        source_perimeter=source_perimeter,
        converted_perimeter=converted_perimeter,
        perimeter_deviation=perimeter_deviation,
        conversion_evidence={key: evidence[key] for key in sorted(evidence) if key != "loops"},
    )


def _read_boundary_evidence(entity: DXFEntity) -> dict[str, Any] | None:
    try:
        tags = entity.get_xdata(REGION_EVIDENCE_APPID)
    except Exception:
        return None
    payload = "".join(str(tag.value) for tag in tags if int(tag.code) == 1000)
    if not payload:
        return None
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return {"invalid": True, "parse_error": "boundary evidence is not valid JSON"}
    return data if isinstance(data, dict) else {"invalid": True, "parse_error": "boundary evidence root is not an object"}


def _loop_area(vertices: tuple[RegionPoint, ...], bulges: tuple[float, ...]) -> float:
    total = 0.0
    for index, start in enumerate(vertices):
        end = vertices[(index + 1) % len(vertices)]
        total += start.x * end.y - end.x * start.y
        total += _arc_segment_area(_distance(start, end), bulges[index])
    return total / 2.0


def _signed_region_area(loops: list[ConvertedLoop]) -> float:
    total = 0.0
    for loop in loops:
        value = loop.area or 0.0
        total += value if loop.role == "outer" else -value
    return total


def _loop_perimeter(vertices: tuple[RegionPoint, ...], bulges: tuple[float, ...]) -> float:
    total = 0.0
    for index, start in enumerate(vertices):
        chord = _distance(start, vertices[(index + 1) % len(vertices)])
        bulge = bulges[index]
        if abs(bulge) <= 1e-12:
            total += chord
            continue
        theta = abs(4.0 * math.atan(bulge))
        radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
        total += radius * theta
    return total


def _arc_segment_area(chord: float, bulge: float) -> float:
    if abs(bulge) <= 1e-12 or chord <= 0:
        return 0.0
    theta = 4.0 * math.atan(bulge)
    radius = chord * (1.0 + bulge * bulge) / (4.0 * abs(bulge))
    return math.copysign(radius * radius * (abs(theta) - math.sin(abs(theta))), theta)


def _self_intersects(vertices: tuple[RegionPoint, ...], tolerance: float) -> bool:
    segments = [(vertices[i], vertices[(i + 1) % len(vertices)]) for i in range(len(vertices))]
    for i, first in enumerate(segments):
        for j, second in enumerate(segments):
            if abs(i - j) <= 1 or {i, j} == {0, len(segments) - 1}:
                continue
            if _segments_intersect(first[0], first[1], second[0], second[1], tolerance):
                return True
    return False


def _segments_intersect(a: RegionPoint, b: RegionPoint, c: RegionPoint, d: RegionPoint, tolerance: float) -> bool:
    def orient(p: RegionPoint, q: RegionPoint, r: RegionPoint) -> float:
        return (q.x - p.x) * (r.y - p.y) - (q.y - p.y) * (r.x - p.x)

    o1 = orient(a, b, c)
    o2 = orient(a, b, d)
    o3 = orient(c, d, a)
    o4 = orient(c, d, b)
    return (o1 * o2 < -tolerance) and (o3 * o4 < -tolerance)


def _point_in_polygon(point: RegionPoint, vertices: tuple[RegionPoint, ...]) -> bool:
    inside = False
    j = len(vertices) - 1
    for i, vertex in enumerate(vertices):
        previous = vertices[j]
        if ((vertex.y > point.y) != (previous.y > point.y)) and (point.x < (previous.x - vertex.x) * (point.y - vertex.y) / ((previous.y - vertex.y) or 1e-30) + vertex.x):
            inside = not inside
        j = i
    return inside


def _drop_duplicate_closing_vertex(vertices: tuple[RegionPoint, ...], tolerance: float) -> tuple[RegionPoint, ...]:
    if len(vertices) > 1 and _distance(vertices[0], vertices[-1]) <= tolerance:
        return vertices[:-1]
    return vertices


def _has_zero_segments(vertices: tuple[RegionPoint, ...], tolerance: float) -> bool:
    return any(_distance(vertices[index], vertices[(index + 1) % len(vertices)]) <= tolerance for index in range(len(vertices)))


def _distance(first: RegionPoint, second: RegionPoint) -> float:
    return math.hypot(first.x - second.x, first.y - second.y)


def _deviation(expected: float | None, actual: float | None) -> float | None:
    if expected is None or actual is None:
        return None
    return abs(float(expected) - float(actual))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_dxf_path(path: Path) -> None:
    if path.suffix.lower() != ".dxf":
        raise RegionConversionError(f"Stage 7 REGION conversion accepts .dxf files only: {path}")
    if not path.is_file() or path.stat().st_size <= 0:
        raise RegionConversionError(f"DXF file is missing, invalid, or empty: {path}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DxfReadError(f"DXF file cannot be read: {path}") from exc
    return digest.hexdigest()


def _nondefault_extrusion(entity: DXFEntity) -> bool:
    extrusion = _dxf_get(entity, "extrusion")
    if extrusion is None:
        return False
    try:
        return abs(float(extrusion[0])) > 1e-9 or abs(float(extrusion[1])) > 1e-9 or abs(float(extrusion[2]) - 1.0) > 1e-9
    except Exception:
        return True


def _handle(entity: DXFEntity) -> str | None:
    value = _dxf_get(entity, "handle")
    return str(value) if value is not None else None


def _layer(entity: DXFEntity) -> str:
    value = _dxf_get(entity, "layer")
    return str(value) if value is not None else "0"


def _elevation(entity: DXFEntity) -> float:
    value = _dxf_get(entity, "elevation")
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _dxf_get(entity: DXFEntity, key: str) -> Any:
    try:
        return entity.dxf.get(key)
    except Exception:
        return None


def _entity_type(entity: DXFEntity) -> str:
    try:
        return str(entity.dxftype()).upper()
    except Exception:
        return "UNKNOWN"


def _issue(
    code: RegionConversionIssueCode,
    severity: IssueSeverity,
    message: str,
    *,
    source_handle: str | None = None,
    layer: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> RegionConversionIssue:
    return RegionConversionIssue(code=code.value, severity=severity, message=message, source_handle=source_handle, layer=layer, evidence=evidence or {})




