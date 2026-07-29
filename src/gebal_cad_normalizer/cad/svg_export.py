"""Stage 9.5 read-only DXF/DWG layer SVG export."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
from collections import Counter, defaultdict
from enum import Enum
from html import escape
from pathlib import Path
from typing import Any, Iterable, Literal

import ezdxf
from pydantic import Field
from ezdxf import colors, units
from ezdxf.entities import DXFEntity

from gebal_cad_normalizer.cad.oda import OdaConversionRequest, OdaConverter
from gebal_cad_normalizer.exceptions import DxfReadError, OutputWriteError
from gebal_cad_normalizer.models import StrictModel

SvgBackground = Literal["transparent", "white"]
PROXY_TYPES = {"ACAD_PROXY_ENTITY"}
EXTERNAL_TYPES = {"IMAGE", "UNDERLAY", "PDFUNDERLAY", "DGNUNDERLAY", "DWFUNDERLAY"}


class SvgExportIssueCode(str, Enum):
    EMPTY_LAYER = "svg_empty_layer"
    EXTENTS_UNAVAILABLE = "svg_extents_unavailable"
    ENTITY_UNSUPPORTED = "svg_entity_unsupported"
    ENTITY_RENDER_FAILED = "svg_entity_render_failed"
    INSERT_RENDER_FAILED = "svg_insert_render_failed"
    TEXT_FALLBACK = "svg_text_fallback"
    HATCH_SIMPLIFIED = "svg_hatch_simplified"
    PROXY_SKIPPED = "svg_proxy_skipped"
    EXTERNAL_REFERENCE_SKIPPED = "svg_external_reference_skipped"
    OUTPUT_FAILED = "svg_output_failed"
    ENTITY_EXPANSION_CAP_EXCEEDED = "svg_entity_expansion_cap_exceeded"
    INSERT_RECURSION_DEPTH_EXCEEDED = "svg_insert_recursion_depth_exceeded"
    CURVE_POINT_CAP_EXCEEDED = "svg_curve_point_cap_exceeded"


class SvgExportConfig(StrictModel):
    include_combined: bool = True
    padding: float = 16.0
    background: SvgBackground = "transparent"
    monochrome: bool = False
    include_metadata: bool = False
    stroke_width_px: float = 1.5
    curve_segments: int = 64
    target_dxf_version: str = "R2013"
    timeout_seconds: float = 120.0
    max_entity_expansion: int = Field(default=50000, gt=0)
    max_insert_recursion_depth: int = Field(default=25, gt=0)
    max_curve_tessellation_points: int = Field(default=5000, gt=0)


class SvgIssue(StrictModel):
    code: str
    severity: Literal["info", "warning", "fail"]
    message: str
    layer_name: str | None = None
    entity_type: str | None = None
    source_handle: str | None = None
    evidence: dict[str, Any] = {}


class SvgDisplayTransform(StrictModel):
    translate_x: float
    translate_y: float
    flip_y: bool
    padding: float
    viewBox: str
    width: float
    height: float


class SvgLayerManifest(StrictModel):
    layer_name: str
    svg_filename: str
    entity_count: int
    rendered_count: int
    skipped_count: int
    entity_types: dict[str, int]
    source_extents: dict[str, float] | None
    display_transform: SvgDisplayTransform | None
    warnings: tuple[SvgIssue, ...]


class SvgExportResult(StrictModel):
    source_file: Path
    source_sha256: str
    working_dxf_path: Path
    output_dir: Path
    manifest_path: Path
    combined_svg_path: Path | None
    dxf_version: str
    insunits: int | None
    drawing_units: str
    layers: tuple[SvgLayerManifest, ...]
    warnings: tuple[SvgIssue, ...]
    source_checksum_unchanged: bool
    combined_exported: bool
    ai_used: bool = False

    def to_manifest_json(self) -> str:
        return json.dumps(_manifest_payload(self), sort_keys=True, separators=(",", ":"))


class SvgExportError(OutputWriteError):
    def __init__(self, code: SvgExportIssueCode, message: str) -> None:
        super().__init__(message)
        self.code = code


class _Drawable(StrictModel):
    layer: str
    entity_type: str
    source_handle: str | None
    svg: str
    points: tuple[tuple[float, float], ...]
    issue: SvgIssue | None = None


def export_layer_svgs(
    source_path: Path | str,
    output_dir: Path | str,
    *,
    config: SvgExportConfig | None = None,
    oda_executable_path: Path | str | None = None,
    inventory: Any | None = None,
    canonical_geometry: Any | None = None,
    oda_converter: OdaConverter | None = None,
) -> SvgExportResult:
    """Export one standalone SVG per source CAD layer without mutating the input."""

    del inventory, canonical_geometry
    cfg = config or SvgExportConfig()
    source = Path(source_path)
    destination = Path(output_dir)
    before_sha = _sha256(source)
    working_dxf, temp_conversion_root = _prepare_dxf(source, oda_executable_path, cfg, oda_converter)
    try:
        doc = ezdxf.readfile(working_dxf)
        result = _export_dxf_doc(doc, source, before_sha, working_dxf, destination, cfg)
    except Exception as exc:
        if isinstance(exc, SvgExportError):
            raise
        raise DxfReadError(f"Stage 9.5 could not read or export CAD source: {source}") from exc
    finally:
        if temp_conversion_root is not None:
            shutil.rmtree(temp_conversion_root, ignore_errors=True)
    return result.model_copy(update={"source_checksum_unchanged": before_sha == _sha256(source)})


def write_svg_manifest(result: SvgExportResult, output_path: Path | str) -> Path:
    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_manifest_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write SVG export manifest: {path}") from exc
    return path


def _prepare_dxf(source: Path, oda_executable_path: Path | str | None, config: SvgExportConfig, oda_converter: OdaConverter | None) -> tuple[Path, Path | None]:
    suffix = source.suffix.lower()
    if suffix == ".dxf":
        return source, None
    if suffix != ".dwg":
        raise DxfReadError(f"Stage 9.5 accepts .dxf directly or .dwg through ODA only: {source}")
    temp_root = Path(tempfile.mkdtemp(prefix="gebal_svg_dwg_"))
    converted = temp_root / f"{source.stem}.dxf"
    converter = oda_converter or OdaConverter(oda_executable_path)
    converter.convert(
        OdaConversionRequest(
            source_path=source,
            destination_path=converted,
            oda_executable_path=Path(oda_executable_path) if oda_executable_path is not None else None,
            target_version=config.target_dxf_version,
            timeout_seconds=config.timeout_seconds,
        )
    )
    return converted, temp_root


def _export_dxf_doc(doc: Any, source: Path, source_sha: str, working_dxf: Path, output_dir: Path, config: SvgExportConfig) -> SvgExportResult:
    if output_dir.exists() and not output_dir.is_dir():
        raise SvgExportError(SvgExportIssueCode.OUTPUT_FAILED, f"SVG output path exists and is not a directory: {output_dir}")
    layer_order = _ordered_layers(doc)
    layer_drawables: dict[str, list[_Drawable]] = {name: [] for name in layer_order}
    layer_entity_counts: Counter[str] = Counter()
    layer_type_counts: dict[str, Counter[str]] = defaultdict(Counter)
    warnings: list[SvgIssue] = []
    block_lookup = {str(block.name): block for block in doc.blocks}
    walk_state = {"visited": 0, "cap_reported": False}

    for index, entity in enumerate(list(doc.modelspace())):
        _collect_entity(entity, doc, block_lookup, layer_drawables, layer_entity_counts, layer_type_counts, warnings, order_key=(index,), visiting_blocks=(), config=config, walk_state=walk_state)

    staging_parent = output_dir.parent if str(output_dir.parent) else Path(".")
    staging = staging_parent / f".{output_dir.name}.svg_export_tmp"
    try:
        if staging.exists():
            shutil.rmtree(staging)
        (staging / "layers").mkdir(parents=True)
        layer_manifests = _write_layer_svgs(staging, source, source_sha, doc, layer_order, layer_drawables, layer_entity_counts, layer_type_counts, config)
        combined_path = _write_combined_svg(staging, source, source_sha, layer_drawables, config) if config.include_combined else None
        provisional = SvgExportResult(
            source_file=source,
            source_sha256=source_sha,
            working_dxf_path=working_dxf,
            output_dir=output_dir,
            manifest_path=output_dir / "manifest.json",
            combined_svg_path=output_dir / "combined.svg" if config.include_combined else None,
            dxf_version=str(getattr(doc, "dxfversion", "") or ""),
            insunits=_read_insunits(doc, working_dxf),
            drawing_units=_decode_units(_read_insunits(doc, working_dxf)),
            layers=tuple(layer_manifests),
            warnings=tuple(sorted(warnings, key=_issue_sort_key)),
            source_checksum_unchanged=True,
            combined_exported=combined_path is not None,
        )
        (staging / "manifest.json").write_text(provisional.to_manifest_json() + "\n", encoding="utf-8")
        os.replace(staging, output_dir)
        return provisional
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        if isinstance(exc, SvgExportError):
            raise
        raise SvgExportError(SvgExportIssueCode.OUTPUT_FAILED, f"Stage 9.5 failed to write SVG output directory: {output_dir}") from exc


def _collect_entity(entity: DXFEntity, doc: Any, block_lookup: dict[str, Any], layer_drawables: dict[str, list[_Drawable]], layer_entity_counts: Counter[str], layer_type_counts: dict[str, Counter[str]], warnings: list[SvgIssue], *, order_key: tuple[int, ...], visiting_blocks: tuple[str, ...], config: SvgExportConfig, walk_state: dict[str, Any]) -> None:
    del order_key
    walk_state["visited"] = int(walk_state.get("visited", 0)) + 1
    if walk_state["visited"] > config.max_entity_expansion:
        if not walk_state.get("cap_reported"):
            warnings.append(_issue(SvgExportIssueCode.ENTITY_EXPANSION_CAP_EXCEEDED, "fail", "SVG entity expansion exceeded the configured cap.", evidence={"cap": config.max_entity_expansion}))
            walk_state["cap_reported"] = True
        return
    dxf_type = _entity_type(entity)
    layer = _layer(entity)
    layer_drawables.setdefault(layer, [])
    layer_entity_counts[layer] += 1
    layer_type_counts[layer][dxf_type] += 1
    if dxf_type == "INSERT":
        _collect_insert(entity, doc, block_lookup, layer_drawables, layer_entity_counts, layer_type_counts, warnings, visiting_blocks=visiting_blocks, config=config, walk_state=walk_state)
        return
    drawable = _render_entity(entity, doc, layer, config)
    if drawable.issue is not None:
        warnings.append(drawable.issue)
    if drawable.svg:
        layer_drawables.setdefault(layer, []).append(drawable)


def _collect_insert(entity: DXFEntity, doc: Any, block_lookup: dict[str, Any], layer_drawables: dict[str, list[_Drawable]], layer_entity_counts: Counter[str], layer_type_counts: dict[str, Counter[str]], warnings: list[SvgIssue], *, visiting_blocks: tuple[str, ...], config: SvgExportConfig, walk_state: dict[str, Any]) -> None:
    name = str(entity.dxf.get("name", ""))
    if len(visiting_blocks) >= config.max_insert_recursion_depth:
        warnings.append(_issue(SvgExportIssueCode.INSERT_RECURSION_DEPTH_EXCEEDED, "fail", "SVG INSERT traversal exceeded the configured depth cap.", layer_name=_layer(entity), entity_type="INSERT", source_handle=_handle(entity), evidence={"cap": config.max_insert_recursion_depth, "block_name": name}))
        return
    if name in visiting_blocks or name not in block_lookup:
        warnings.append(_issue(SvgExportIssueCode.INSERT_RENDER_FAILED, "warning", f"INSERT could not be resolved safely: {name}", layer_name=_layer(entity), entity_type="INSERT", source_handle=_handle(entity)))
        return
    try:
        children = list(entity.virtual_entities())
    except Exception as exc:
        warnings.append(_issue(SvgExportIssueCode.INSERT_RENDER_FAILED, "warning", f"INSERT virtual entity traversal failed: {exc}", layer_name=_layer(entity), entity_type="INSERT", source_handle=_handle(entity), evidence={"block_name": name}))
        return
    for child in children:
        _collect_entity(child, doc, block_lookup, layer_drawables, layer_entity_counts, layer_type_counts, warnings, order_key=(), visiting_blocks=visiting_blocks + (name,), config=config, walk_state=walk_state)


def _render_entity(entity: DXFEntity, doc: Any, layer: str, config: SvgExportConfig) -> _Drawable:
    dxf_type = _entity_type(entity)
    try:
        if dxf_type in PROXY_TYPES:
            return _skipped(entity, layer, SvgExportIssueCode.PROXY_SKIPPED, "Proxy entity skipped.")
        if dxf_type in EXTERNAL_TYPES:
            return _skipped(entity, layer, SvgExportIssueCode.EXTERNAL_REFERENCE_SKIPPED, "External reference or raster content skipped.")
        color = _svg_color(entity, doc, layer, config)
        if dxf_type == "LINE":
            start, end = _xy(entity.dxf.start), _xy(entity.dxf.end)
            return _drawable(entity, layer, f'<line x1="{_n(start[0])}" y1="{_n(start[1])}" x2="{_n(end[0])}" y2="{_n(end[1])}" stroke="{color}" />', [start, end])
        if dxf_type == "POINT":
            point = _xy(entity.dxf.location)
            return _drawable(entity, layer, f'<circle cx="{_n(point[0])}" cy="{_n(point[1])}" r="1" fill="{color}" stroke="none" />', [point])
        if dxf_type == "ARC":
            return _render_arc(entity, layer, color)
        if dxf_type == "CIRCLE":
            center = _xy(entity.dxf.center); radius = float(entity.dxf.radius)
            return _drawable(entity, layer, f'<circle cx="{_n(center[0])}" cy="{_n(center[1])}" r="{_n(radius)}" fill="none" stroke="{color}" />', [(center[0] - radius, center[1] - radius), (center[0] + radius, center[1] + radius)])
        if dxf_type == "ELLIPSE":
            return _render_flattened(entity, layer, color, config, closed=_is_ellipse_closed(entity))
        if dxf_type == "SPLINE":
            return _render_flattened(entity, layer, color, config, closed=bool(getattr(entity, "closed", False)))
        if dxf_type == "LWPOLYLINE":
            return _render_lwpolyline(entity, layer, color)
        if dxf_type == "POLYLINE":
            return _render_polyline(entity, layer, color)
        if dxf_type in {"TEXT", "MTEXT"}:
            return _render_text(entity, layer, color)
        if dxf_type == "HATCH":
            return _render_hatch(entity, layer, color)
        return _skipped(entity, layer, SvgExportIssueCode.ENTITY_UNSUPPORTED, f"Unsupported entity type skipped: {dxf_type}")
    except Exception as exc:
        return _Drawable(layer=layer, entity_type=dxf_type, source_handle=_handle(entity), svg="", points=(), issue=_issue(SvgExportIssueCode.ENTITY_RENDER_FAILED, "warning", f"Entity render failed: {exc}", layer_name=layer, entity_type=dxf_type, source_handle=_handle(entity)))


def _render_arc(entity: DXFEntity, layer: str, color: str) -> _Drawable:
    center = _xy(entity.dxf.center); radius = float(entity.dxf.radius)
    start_angle = float(entity.dxf.start_angle); end_angle = float(entity.dxf.end_angle)
    start = (center[0] + radius * math.cos(math.radians(start_angle)), center[1] + radius * math.sin(math.radians(start_angle)))
    end = (center[0] + radius * math.cos(math.radians(end_angle)), center[1] + radius * math.sin(math.radians(end_angle)))
    delta = (end_angle - start_angle) % 360.0
    path = f'M {_n(start[0])} {_n(start[1])} A {_n(radius)} {_n(radius)} 0 {1 if delta > 180 else 0} 1 {_n(end[0])} {_n(end[1])}'
    return _drawable(entity, layer, f'<path d="{path}" fill="none" stroke="{color}" />', _arc_points(center, radius, start_angle, end_angle, 24))


def _render_lwpolyline(entity: DXFEntity, layer: str, color: str) -> _Drawable:
    vertices = [(float(x), float(y), float(bulge)) for x, y, _sw, _ew, bulge in entity.get_points("xyseb")]
    path, points = _polyline_path(vertices, bool(entity.closed))
    return _drawable(entity, layer, f'<path d="{path}" fill="none" stroke="{color}" data-closed="{str(bool(entity.closed)).lower()}" />', points)


def _render_polyline(entity: DXFEntity, layer: str, color: str) -> _Drawable:
    vertices = [(_xy(vertex.dxf.location)[0], _xy(vertex.dxf.location)[1], float(vertex.dxf.get("bulge", 0.0) or 0.0)) for vertex in entity.vertices]
    path, points = _polyline_path(vertices, bool(entity.is_closed))
    return _drawable(entity, layer, f'<path d="{path}" fill="none" stroke="{color}" data-closed="{str(bool(entity.is_closed)).lower()}" />', points)


def _render_flattened(entity: DXFEntity, layer: str, color: str, config: SvgExportConfig, *, closed: bool) -> _Drawable:
    segment_hint = min(max(1, config.curve_segments), config.max_curve_tessellation_points)
    try:
        points = [_xy(point) for point in entity.flattening(max(0.01, 1.0 / segment_hint))]
    except TypeError:
        points = [_xy(point) for point in entity.flattening(segment_hint)]
    if len(points) > config.max_curve_tessellation_points:
        issue = _issue(SvgExportIssueCode.CURVE_POINT_CAP_EXCEEDED, "fail", "SVG curve flattening exceeded the configured point cap.", layer_name=layer, entity_type=_entity_type(entity), source_handle=_handle(entity), evidence={"cap": config.max_curve_tessellation_points, "point_count": len(points)})
        points = points[: config.max_curve_tessellation_points]
    else:
        issue = None
    if len(points) < 2:
        raise ValueError("curve flattening produced fewer than two points")
    commands = [f"M {_n(points[0][0])} {_n(points[0][1])}"] + [f"L {_n(x)} {_n(y)}" for x, y in points[1:]]
    if closed:
        commands.append("Z")
    drawable = _drawable(entity, layer, f'<path d="{" ".join(commands)}" fill="none" stroke="{color}" data-closed="{str(closed).lower()}" />', points)
    return drawable if issue is None else drawable.model_copy(update={"issue": issue})


def _render_text(entity: DXFEntity, layer: str, color: str) -> _Drawable:
    text = entity.plain_text() if hasattr(entity, "plain_text") else str(_dxf_get(entity, "text", ""))
    location_value = _dxf_get(entity, "insert", None) or _dxf_get(entity, "location", None) or (0, 0, 0)
    location = _xy(location_value)
    height = float(_dxf_get(entity, "height", 2.5) or 2.5)
    issue = _issue(SvgExportIssueCode.TEXT_FALLBACK, "info", "Text rendered with generic SVG text fallback.", layer_name=layer, entity_type=_entity_type(entity), source_handle=_handle(entity))
    return _Drawable(layer=layer, entity_type=_entity_type(entity), source_handle=_handle(entity), svg=f'<text x="{_n(location[0])}" y="{_n(location[1])}" fill="{color}" font-family="sans-serif" font-size="{_n(height)}">{escape(text)}</text>', points=(location,), issue=issue)


def _render_hatch(entity: DXFEntity, layer: str, color: str) -> _Drawable:
    paths: list[str] = []
    points: list[tuple[float, float]] = []
    for hatch_path in getattr(entity, "paths", []):
        if not hasattr(hatch_path, "vertices"):
            continue
        vertices = [(float(item[0]), float(item[1]), float(item[2]) if len(item) > 2 else 0.0) for item in hatch_path.vertices]
        if vertices:
            path, path_points = _polyline_path(vertices, True)
            paths.append(path); points.extend(path_points)
    if not paths:
        raise ValueError("hatch boundary path is unavailable")
    issue = _issue(SvgExportIssueCode.HATCH_SIMPLIFIED, "info", "HATCH rendered from available boundary paths.", layer_name=layer, entity_type="HATCH", source_handle=_handle(entity))
    return _Drawable(layer=layer, entity_type="HATCH", source_handle=_handle(entity), svg=f'<path d="{" ".join(paths)}" fill="{color}" fill-opacity="0.12" stroke="{color}" data-simplified="hatch" />', points=tuple(points), issue=issue)


def _polyline_path(vertices: list[tuple[float, float, float]], closed: bool) -> tuple[str, tuple[tuple[float, float], ...]]:
    if not vertices:
        raise ValueError("polyline has no vertices")
    commands = [f"M {_n(vertices[0][0])} {_n(vertices[0][1])}"]
    points = [(vertices[0][0], vertices[0][1])]
    segment_count = len(vertices) if closed else len(vertices) - 1
    for index in range(segment_count):
        current = vertices[index]; nxt = vertices[(index + 1) % len(vertices)]; bulge = current[2]
        if abs(bulge) > 1e-12:
            center, radius, start_angle, end_angle, sweep = _bulge_arc(current, nxt, bulge)
            commands.append(f"A {_n(radius)} {_n(radius)} 0 {1 if abs(sweep) > 180 else 0} {1 if bulge > 0 else 0} {_n(nxt[0])} {_n(nxt[1])}")
            points.extend(_arc_points(center, radius, start_angle, end_angle, 16))
        else:
            commands.append(f"L {_n(nxt[0])} {_n(nxt[1])}")
            points.append((nxt[0], nxt[1]))
    if closed:
        commands.append("Z")
    return " ".join(commands), tuple(points)


def _bulge_arc(start: tuple[float, float, float], end: tuple[float, float, float], bulge: float) -> tuple[tuple[float, float], float, float, float, float]:
    x1, y1 = start[0], start[1]; x2, y2 = end[0], end[1]
    chord = math.hypot(x2 - x1, y2 - y1)
    if chord <= 1e-12:
        raise ValueError("zero-length bulge segment")
    theta = 4.0 * math.atan(bulge); radius = chord / (2.0 * abs(math.sin(theta / 2.0)))
    mx, my = (x1 + x2) / 2.0, (y1 + y2) / 2.0
    ux, uy = -(y2 - y1) / chord, (x2 - x1) / chord
    distance = radius * math.cos(theta / 2.0); sign = 1.0 if bulge > 0 else -1.0
    center = (mx + ux * distance * sign, my + uy * distance * sign)
    start_angle = math.degrees(math.atan2(y1 - center[1], x1 - center[0])); sweep = math.degrees(theta)
    return center, radius, start_angle, start_angle + sweep, sweep


def _arc_points(center: tuple[float, float], radius: float, start_angle: float, end_angle: float, segments: int) -> list[tuple[float, float]]:
    sweep = (end_angle - start_angle) % 360.0
    if sweep == 0.0 and abs(end_angle - start_angle) > 1e-9:
        sweep = 360.0
    if end_angle < start_angle and abs(end_angle - start_angle) < 360.0:
        sweep = end_angle - start_angle
    count = max(2, segments)
    return [(center[0] + radius * math.cos(math.radians(start_angle + sweep * i / (count - 1))), center[1] + radius * math.sin(math.radians(start_angle + sweep * i / (count - 1)))) for i in range(count)]


def _write_layer_svgs(staging: Path, source: Path, source_sha: str, doc: Any, layer_order: list[str], layer_drawables: dict[str, list[_Drawable]], layer_entity_counts: Counter[str], layer_type_counts: dict[str, Counter[str]], config: SvgExportConfig) -> list[SvgLayerManifest]:
    del doc
    filenames = _layer_filenames(layer_order)
    manifests: list[SvgLayerManifest] = []
    for layer in layer_order:
        drawables = layer_drawables.get(layer, [])
        layer_warnings = [drawable.issue for drawable in drawables if drawable.issue is not None]
        if not drawables:
            layer_warnings.append(_issue(SvgExportIssueCode.EMPTY_LAYER, "info", "Layer has no renderable SVG geometry.", layer_name=layer))
        extents = _extents(drawables)
        if extents is None and layer_entity_counts[layer] > 0:
            layer_warnings.append(_issue(SvgExportIssueCode.EXTENTS_UNAVAILABLE, "warning", "Layer extents are unavailable.", layer_name=layer))
        svg, transform = _svg_document(source, source_sha, layer, drawables, extents, config)
        filename = filenames[layer]
        (staging / "layers" / filename).write_text(svg, encoding="utf-8")
        rendered = len([drawable for drawable in drawables if drawable.svg])
        manifests.append(SvgLayerManifest(layer_name=layer, svg_filename=f"layers/{filename}", entity_count=int(layer_entity_counts[layer]), rendered_count=rendered, skipped_count=max(0, int(layer_entity_counts[layer]) - rendered), entity_types=dict(sorted(layer_type_counts[layer].items())), source_extents=extents, display_transform=transform, warnings=tuple(sorted(layer_warnings, key=_issue_sort_key))))
    return manifests


def _write_combined_svg(staging: Path, source: Path, source_sha: str, layer_drawables: dict[str, list[_Drawable]], config: SvgExportConfig) -> Path:
    drawables = [drawable for layer in sorted(layer_drawables) for drawable in layer_drawables[layer]]
    svg, _ = _svg_document(source, source_sha, "combined", drawables, _extents(drawables), config)
    path = staging / "combined.svg"
    path.write_text(svg, encoding="utf-8")
    return path


def _svg_document(source: Path, source_sha: str, layer: str, drawables: list[_Drawable], extents: dict[str, float] | None, config: SvgExportConfig) -> tuple[str, SvgDisplayTransform | None]:
    width = max(1.0, (extents["max_x"] - extents["min_x"]) if extents else 1.0)
    height = max(1.0, (extents["max_y"] - extents["min_y"]) if extents else 1.0)
    svg_width = width + config.padding * 2.0; svg_height = height + config.padding * 2.0
    transform = SvgDisplayTransform(translate_x=-(extents["min_x"] if extents else 0.0) + config.padding, translate_y=(extents["max_y"] if extents else 0.0) + config.padding, flip_y=True, padding=config.padding, viewBox=f"0 0 {_n(svg_width)} {_n(svg_height)}", width=svg_width, height=svg_height)
    metadata = ""
    if config.include_metadata:
        metadata = f"<metadata>{escape(json.dumps({'source_file': str(source), 'source_sha256': source_sha, 'layer_name': layer}, sort_keys=True))}</metadata><title>{escape(layer)}</title>"
    background = '<rect width="100%" height="100%" fill="white" />' if config.background == "white" else ""
    body = "\n".join(f'<g data-layer="{escape(drawable.layer)}" data-entity-type="{drawable.entity_type}">{drawable.svg}</g>' for drawable in drawables if drawable.svg)
    content_transform = f"translate({_n(transform.translate_x)} {_n(transform.translate_y)}) scale(1 -1)"
    svg = '<?xml version="1.0" encoding="UTF-8"?>\n' + f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" viewBox="{transform.viewBox}" fill="none" stroke-linecap="round" stroke-linejoin="round" style="background:transparent" data-source-layer="{escape(layer)}">\n{metadata}\n{background}\n<g vector-effect="non-scaling-stroke" stroke-width="{_n(config.stroke_width_px)}" transform="{content_transform}">\n{body}\n</g>\n</svg>\n'
    return svg, transform if extents is not None else None


def _manifest_payload(result: SvgExportResult) -> dict[str, Any]:
    return {"source": {"file": str(result.source_file), "sha256": result.source_sha256, "working_dxf_path": str(result.working_dxf_path), "checksum_unchanged": result.source_checksum_unchanged}, "dxf_version": result.dxf_version, "insunits": result.insunits, "drawing_units": result.drawing_units, "combined_svg": str(result.combined_svg_path) if result.combined_svg_path else None, "combined_exported": result.combined_exported, "ai_used": result.ai_used, "layers": [layer.model_dump(mode="json") for layer in result.layers], "warnings": [issue.model_dump(mode="json") for issue in result.warnings]}


def _ordered_layers(doc: Any) -> list[str]:
    names = [str(layer.dxf.name) for layer in doc.layers]
    present = set(names)
    for entity in doc.modelspace():
        layer = _layer(entity)
        if layer not in present:
            names.append(layer); present.add(layer)
    return sorted(names, key=lambda value: value.casefold())


def _layer_filenames(layer_order: list[str]) -> dict[str, str]:
    used: Counter[str] = Counter(); filenames: dict[str, str] = {}
    for index, layer in enumerate(layer_order, start=1):
        base = _sanitize_layer_name(layer); used[base] += 1
        filenames[layer] = f"{index:03d}_{base}{'_' + str(used[base]) if used[base] > 1 else ''}.svg"
    return filenames


def _sanitize_layer_name(name: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", name.strip())
    clean = re.sub(r"_+", "_", clean).strip("._-")
    return clean[:80] or "layer"


def _extents(drawables: Iterable[_Drawable]) -> dict[str, float] | None:
    points = [point for drawable in drawables for point in drawable.points]
    if not points:
        return None
    return {"min_x": min(point[0] for point in points), "min_y": min(point[1] for point in points), "max_x": max(point[0] for point in points), "max_y": max(point[1] for point in points)}


def _drawable(entity: DXFEntity, layer: str, svg: str, points: Iterable[tuple[float, float]]) -> _Drawable:
    return _Drawable(layer=layer, entity_type=_entity_type(entity), source_handle=_handle(entity), svg=svg, points=tuple(points))


def _skipped(entity: DXFEntity, layer: str, code: SvgExportIssueCode, message: str) -> _Drawable:
    return _Drawable(layer=layer, entity_type=_entity_type(entity), source_handle=_handle(entity), svg="", points=(), issue=_issue(code, "warning", message, layer_name=layer, entity_type=_entity_type(entity), source_handle=_handle(entity)))


def _svg_color(entity: DXFEntity, doc: Any, layer: str, config: SvgExportConfig) -> str:
    if config.monochrome:
        return "#111111"
    aci = _dxf_get(entity, "color", 256)
    if aci in (256, None):
        try:
            aci = doc.layers.get(layer).dxf.get("color", 7)
        except Exception:
            aci = 7
    try:
        rgb = colors.aci2rgb(abs(int(aci)))
        return f"#{rgb.r:02x}{rgb.g:02x}{rgb.b:02x}"
    except Exception:
        return "#111111"


def _read_insunits(doc: Any, source_path: Path | None) -> int | None:
    if source_path is not None and not _raw_header_contains(source_path, "$INSUNITS"):
        return None
    try:
        value = doc.header.get("$INSUNITS")
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


def _is_ellipse_closed(entity: DXFEntity) -> bool:
    return abs(float(entity.dxf.end_param) - float(entity.dxf.start_param)) >= 6.283185307179586 - 1e-9


def _xy(value: Any) -> tuple[float, float]:
    return float(value[0]), float(value[1])



def _dxf_get(entity: DXFEntity, key: str, default: Any = None) -> Any:
    try:
        value = entity.dxf.get(key)
    except Exception:
        return default
    return default if value is None else value
def _entity_type(entity: DXFEntity) -> str:
    try:
        return str(entity.dxftype()).upper()
    except Exception:
        return "UNKNOWN"


def _layer(entity: DXFEntity) -> str:
    try:
        return str(entity.dxf.get("layer", "0"))
    except Exception:
        return "0"


def _handle(entity: DXFEntity) -> str | None:
    try:
        value = entity.dxf.get("handle")
    except Exception:
        value = None
    return str(value) if value is not None else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DxfReadError(f"CAD file cannot be read: {path}") from exc
    return digest.hexdigest()


def _issue(code: SvgExportIssueCode, severity: Literal["info", "warning", "fail"], message: str, *, layer_name: str | None = None, entity_type: str | None = None, source_handle: str | None = None, evidence: dict[str, Any] | None = None) -> SvgIssue:
    return SvgIssue(code=code.value, severity=severity, message=message, layer_name=layer_name, entity_type=entity_type, source_handle=source_handle, evidence=evidence or {})


def _issue_sort_key(issue: SvgIssue) -> tuple[str, str, str, str]:
    return (issue.severity, issue.code, issue.layer_name or "", issue.message)


def _n(value: float) -> str:
    if abs(value) < 1e-12:
        value = 0.0
    return f"{value:.6f}".rstrip("0").rstrip(".")



