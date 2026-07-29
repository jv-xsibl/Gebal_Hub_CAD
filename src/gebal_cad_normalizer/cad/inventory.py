"""Read-only DXF inventory and data-quality audit using ezdxf."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Literal

import ezdxf
from ezdxf import bbox, units
from ezdxf.entities import DXFEntity

from gebal_cad_normalizer.exceptions import DxfReadError, OutputWriteError
from gebal_cad_normalizer.models import StrictModel


AuditSeverity = Literal["info", "warning", "fail"]
EntityCategory = Literal["known_supported", "convertible_later", "review_required", "ignored_non_operational", "unsupported"]

SUPPORTED_DXF_VERSIONS = {"AC1009", "AC1012", "AC1014", "AC1015", "AC1018", "AC1021", "AC1024", "AC1027", "AC1032"}
KNOWN_SUPPORTED_TYPES = {"LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "POINT"}
CONVERTIBLE_LATER_TYPES = {"REGION", "SPLINE", "ELLIPSE", "HATCH", "INSERT"}
IGNORED_NON_OPERATIONAL_TYPES = {"TEXT", "MTEXT", "DIMENSION", "LEADER", "MLEADER", "MULTILEADER", "ATTRIB", "ATTDEF", "VIEWPORT"}
REVIEW_REQUIRED_TYPES = {"3DSOLID", "BODY", "SURFACE", "WIPEOUT", "IMAGE", "UNDERLAY", "PDFUNDERLAY", "DGNUNDERLAY", "DWFUNDERLAY", "ACAD_PROXY_ENTITY"}
FLAGGED_TYPES = {
    "REGION", "3DSOLID", "BODY", "SURFACE", "SPLINE", "ELLIPSE", "HATCH", "INSERT", "WIPEOUT", "IMAGE", "UNDERLAY",
    "PDFUNDERLAY", "DGNUNDERLAY", "DWFUNDERLAY", "ACAD_PROXY_ENTITY",
}
TEXT_TYPES = {"TEXT", "MTEXT"}
DIMENSION_TYPES = {"DIMENSION"}
RASTER_UNDERLAY_TYPES = {"IMAGE", "UNDERLAY", "PDFUNDERLAY", "DGNUNDERLAY", "DWFUNDERLAY"}
THREE_D_TYPES = {"3DFACE", "3DSOLID", "BODY", "SURFACE", "MESH", "POLYFACE", "POLYMESH"}
DEFAULT_Z_EPSILON = 1e-6


class DxfInventoryIssueCode(str, Enum):
    """Stable Stage 5 DXF audit issue codes."""

    INVALID_DXF = "invalid_dxf"
    UNSUPPORTED_DXF_VERSION = "unsupported_dxf_version"
    UNITS_MISSING = "units_missing"
    UNITS_UNKNOWN = "units_unknown"
    EMPTY_MODELSPACE = "empty_modelspace"
    NO_2D_GEOMETRY = "no_2d_geometry"
    CONTAINS_REGION = "contains_region"
    CONTAINS_3D_GEOMETRY = "contains_3d_geometry"
    CONTAINS_PROXY_ENTITY = "contains_proxy_entity"
    CONTAINS_EXTERNAL_REFERENCE = "contains_external_reference"
    CONTAINS_RASTER_OR_UNDERLAY = "contains_raster_or_underlay"
    UNSUPPORTED_ENTITY_TYPE = "unsupported_entity_type"
    NONZERO_Z_GEOMETRY = "nonzero_z_geometry"
    UNRESOLVED_BLOCK_REFERENCE = "unresolved_block_reference"
    EXTENTS_UNAVAILABLE = "extents_unavailable"


class DxfInventoryError(DxfReadError):
    """Raised when a DXF cannot be safely opened for inventory."""

    def __init__(self, code: DxfInventoryIssueCode, message: str, *, path: Path | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.path = path


class DxfAuditIssue(StrictModel):
    """Machine-readable audit issue with preserved evidence."""

    code: str
    severity: AuditSeverity
    message: str
    evidence: dict[str, Any] = {}


class EntityTypeCount(StrictModel):
    """Count of one DXF entity type."""

    dxf_type: str
    count: int
    category: EntityCategory


class LayerInventory(StrictModel):
    """Layer table metadata and entity count."""

    name: str
    color: int | None
    linetype: str | None
    is_on: bool
    is_frozen: bool
    is_locked: bool
    is_plottable: bool
    entity_count: int


class BlockInventory(StrictModel):
    """Block definition metadata and INSERT usage."""

    name: str
    entity_count: int
    insert_count: int
    nested_insert_count: int
    is_xref: bool
    is_layout_block: bool
    unresolved_insert_references: tuple[str, ...] = ()


class DxfInventoryResult(StrictModel):
    """Complete read-only DXF inventory and audit result."""

    source_path: Path
    source_sha256: str
    dxf_version: str
    insunits: int | None
    drawing_units: str
    modelspace_present: bool
    paperspace_present: bool
    layers: tuple[LayerInventory, ...]
    blocks: tuple[BlockInventory, ...]
    entity_counts: tuple[EntityTypeCount, ...]
    entity_counts_by_layer: dict[str, tuple[EntityTypeCount, ...]]
    nested_entity_counts: tuple[EntityTypeCount, ...]
    modelspace_entity_count: int
    total_entity_count: int
    modelspace_extents: dict[str, float] | None
    layouts: tuple[str, ...]
    xref_indicators: tuple[str, ...]
    text_style_dimension_hatch_usage: dict[str, int]
    flagged_entity_presence: dict[str, int]
    has_nonzero_z_geometry: bool
    has_3d_geometry: bool
    unresolved_block_references: tuple[str, ...]
    issues: tuple[DxfAuditIssue, ...]

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for reports and regression tests."""

        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def inventory_dxf(path: Path | str, *, z_epsilon: float = DEFAULT_Z_EPSILON) -> DxfInventoryResult:
    """Open a DXF read-only and return structural inventory plus audit issues."""

    z_epsilon = _validate_z_epsilon(z_epsilon)
    source_path = Path(path)
    _validate_dxf_path(source_path)
    source_sha256 = _sha256(source_path)

    try:
        doc = ezdxf.readfile(source_path)
    except Exception as exc:
        raise DxfInventoryError(DxfInventoryIssueCode.INVALID_DXF, f"Invalid or unreadable DXF file: {source_path}", path=source_path) from exc

    issues: list[DxfAuditIssue] = []
    dxf_version = str(getattr(doc, "dxfversion", "") or "")
    if dxf_version not in SUPPORTED_DXF_VERSIONS:
        issues.append(_issue(DxfInventoryIssueCode.UNSUPPORTED_DXF_VERSION, "warning", f"DXF version is not in the supported audit list: {dxf_version}", {"dxf_version": dxf_version}))

    insunits = _read_insunits(doc, source_path)
    drawing_units = _decode_units(insunits)
    if insunits is None:
        issues.append(_issue(DxfInventoryIssueCode.UNITS_MISSING, "warning", "DXF header $INSUNITS is missing."))
    elif drawing_units == "unknown":
        issues.append(_issue(DxfInventoryIssueCode.UNITS_UNKNOWN, "warning", f"DXF header $INSUNITS is not recognized: {insunits}", {"insunits": insunits}))

    modelspace = doc.modelspace()
    layouts = tuple(sorted(doc.layouts.names()))
    paperspace_names = tuple(name for name in layouts if name.lower() != "model")
    layout_spaces = [doc.layouts.get(name) for name in paperspace_names]
    block_layouts = list(doc.blocks)
    content_blocks = [block for block in block_layouts if not _is_layout_block(block)]

    modelspace_entities = list(modelspace)
    layout_entities = [entity for layout in layout_spaces for entity in layout]
    block_entities = [entity for block in content_blocks for entity in block]
    layout_space_entities = modelspace_entities + layout_entities
    all_entities = layout_space_entities + block_entities

    if not modelspace_entities:
        issues.append(_issue(DxfInventoryIssueCode.EMPTY_MODELSPACE, "warning", "Modelspace contains no entities."))

    global_counts = Counter(_entity_type(entity) for entity in all_entities)
    layer_counts: dict[str, Counter[str]] = defaultdict(Counter)
    for entity in all_entities:
        layer_counts[_entity_layer(entity)][_entity_type(entity)] += 1

    insert_counts, nested_insert_counts, unresolved = _block_reference_counts(doc, layout_space_entities)
    nested_counts = _nested_entity_counts(doc, modelspace_entities, unresolved)
    layers = _layers(doc, layer_counts)
    blocks = _blocks(doc, insert_counts, nested_insert_counts, unresolved)
    xref_indicators = _xref_indicators(doc, blocks)

    flagged = {key: global_counts.get(key, 0) for key in sorted(FLAGGED_TYPES) if global_counts.get(key, 0) > 0}
    usage = {
        "text": sum(global_counts.get(key, 0) for key in TEXT_TYPES),
        "styles": len(list(doc.styles)),
        "dimensions": sum(global_counts.get(key, 0) for key in DIMENSION_TYPES),
        "hatches": global_counts.get("HATCH", 0),
    }

    nonzero_z_values = _nonzero_z_values(all_entities, z_epsilon)
    has_nonzero_z = bool(nonzero_z_values)
    has_3d_geometry = any(_entity_type(entity) in THREE_D_TYPES for entity in all_entities) or has_nonzero_z
    extents = _safe_modelspace_extents(modelspace, issues)

    _append_content_issues(issues, global_counts, flagged, has_nonzero_z, has_3d_geometry, unresolved, xref_indicators, nonzero_z_values, z_epsilon)
    if not _has_2d_geometry(global_counts):
        issues.append(_issue(DxfInventoryIssueCode.NO_2D_GEOMETRY, "warning", "No supported 2D geometry entity types were found."))

    return DxfInventoryResult(
        source_path=source_path,
        source_sha256=source_sha256,
        dxf_version=dxf_version,
        insunits=insunits,
        drawing_units=drawing_units,
        modelspace_present=True,
        paperspace_present=bool(paperspace_names),
        layers=tuple(sorted(layers, key=lambda item: item.name.lower())),
        blocks=tuple(sorted(blocks, key=lambda item: item.name.lower())),
        entity_counts=_count_models(global_counts),
        entity_counts_by_layer={layer: _count_models(counts) for layer, counts in sorted(layer_counts.items())},
        nested_entity_counts=_count_models(nested_counts),
        modelspace_entity_count=len(modelspace_entities),
        total_entity_count=sum(global_counts.values()),
        modelspace_extents=extents,
        layouts=layouts,
        xref_indicators=tuple(sorted(xref_indicators)),
        text_style_dimension_hatch_usage=usage,
        flagged_entity_presence=flagged,
        has_nonzero_z_geometry=has_nonzero_z,
        has_3d_geometry=has_3d_geometry,
        unresolved_block_references=tuple(sorted(unresolved)),
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.message))),
    )


def write_inventory_json(result: DxfInventoryResult, output_path: Path | str) -> Path:
    """Write deterministic JSON audit output to an explicit path."""

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_deterministic_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write DXF inventory JSON report: {path}") from exc
    return path


def write_inventory_markdown(result: DxfInventoryResult, output_path: Path | str) -> Path:
    """Write a concise Markdown audit report to an explicit path."""

    lines = [
        "# DXF Inventory Audit",
        "",
        f"- Source: `{result.source_path}`",
        f"- SHA-256: `{result.source_sha256}`",
        f"- DXF version: `{result.dxf_version}`",
        f"- Units: `{result.drawing_units}` (`$INSUNITS={result.insunits}`)",
        f"- Layers: {len(result.layers)}",
        f"- Total entities: {result.total_entity_count}",
        f"- Modelspace entities: {result.modelspace_entity_count}",
        f"- Extents: `{result.modelspace_extents}`",
        "",
        "## Issues",
    ]
    if result.issues:
        lines.extend(f"- `{issue.severity}` `{issue.code}`: {issue.message}" for issue in result.issues)
    else:
        lines.append("- None")
    lines.extend(["", "## Entity Counts"])
    lines.extend(f"- `{item.dxf_type}`: {item.count} ({item.category})" for item in result.entity_counts)
    lines.extend(["", "## Layers"])
    lines.extend(
        f"- `{layer.name}`: {layer.entity_count} entities, color={layer.color}, linetype={layer.linetype}, on={layer.is_on}, frozen={layer.is_frozen}, locked={layer.is_locked}, plot={layer.is_plottable}"
        for layer in result.layers
    )
    lines.extend(["", "## Blocks"])
    if result.blocks:
        lines.extend(f"- `{block.name}`: entities={block.entity_count}, inserts={block.insert_count}, nested_inserts={block.nested_insert_count}, xref={block.is_xref}" for block in result.blocks)
    else:
        lines.append("- None")

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write DXF inventory Markdown report: {path}") from exc
    return path


def _validate_dxf_path(path: Path) -> None:
    if path.suffix.lower() != ".dxf":
        raise DxfInventoryError(DxfInventoryIssueCode.INVALID_DXF, f"Stage 5 inventory accepts .dxf files only: {path}", path=path)
    if not path.exists():
        raise DxfInventoryError(DxfInventoryIssueCode.INVALID_DXF, f"DXF file does not exist: {path}", path=path)
    if not path.is_file():
        raise DxfInventoryError(DxfInventoryIssueCode.INVALID_DXF, f"DXF path is not a file: {path}", path=path)
    try:
        if path.stat().st_size <= 0:
            raise DxfInventoryError(DxfInventoryIssueCode.INVALID_DXF, f"DXF file is empty: {path}", path=path)
    except OSError as exc:
        raise DxfInventoryError(DxfInventoryIssueCode.INVALID_DXF, f"DXF file cannot be read: {path}", path=path) from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise DxfInventoryError(DxfInventoryIssueCode.INVALID_DXF, f"DXF file cannot be read: {path}", path=path) from exc
    return digest.hexdigest()


def _read_insunits(doc: Any, source_path: Path) -> int | None:
    if not _raw_header_contains(source_path, "$INSUNITS"):
        return None
    value = doc.header.get("$INSUNITS")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None



def _raw_header_contains(path: Path, variable: str) -> bool:
    try:
        return variable in path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _is_layout_block(block: Any) -> bool:
    name = str(getattr(block, "name", ""))
    return name.upper().startswith("*MODEL_SPACE") or name.upper().startswith("*PAPER_SPACE")
def _decode_units(insunits: int | None) -> str:
    if insunits is None:
        return "missing"
    try:
        decoded = units.decode(insunits)
    except Exception:
        return "unknown"
    return str(decoded) if decoded else "unknown"


def _layers(doc: Any, layer_counts: dict[str, Counter[str]]) -> list[LayerInventory]:
    layers: list[LayerInventory] = []
    for layer in doc.layers:
        name = str(layer.dxf.name)
        layers.append(
            LayerInventory(
                name=name,
                color=layer.dxf.get("color"),
                linetype=layer.dxf.get("linetype"),
                is_on=bool(layer.is_on()),
                is_frozen=bool(layer.is_frozen()),
                is_locked=bool(layer.is_locked()),
                is_plottable=bool(layer.dxf.get("plot", 1)),
                entity_count=sum(layer_counts.get(name, Counter()).values()),
            )
        )
    return layers


def _blocks(doc: Any, insert_counts: Counter[str], nested_insert_counts: Counter[str], unresolved: set[str]) -> list[BlockInventory]:
    block_names = {str(block.name) for block in doc.blocks}
    blocks: list[BlockInventory] = []
    for block in doc.blocks:
        name = str(block.name)
        unresolved_nested = sorted(ref for ref in unresolved if ref not in block_names)
        blocks.append(
            BlockInventory(
                name=name,
                entity_count=len(list(block)),
                insert_count=insert_counts.get(name, 0),
                nested_insert_count=nested_insert_counts.get(name, 0),
                is_xref=bool(getattr(block, "is_xref", False)),
                is_layout_block=_is_layout_block(block),
                unresolved_insert_references=tuple(unresolved_nested),
            )
        )
    return blocks


def _block_reference_counts(doc: Any, entities: Iterable[DXFEntity]) -> tuple[Counter[str], Counter[str], set[str]]:
    block_names = {str(block.name) for block in doc.blocks}
    insert_counts: Counter[str] = Counter()
    nested_insert_counts: Counter[str] = Counter()
    unresolved: set[str] = set()
    for entity in entities:
        if _entity_type(entity) != "INSERT":
            continue
        name = str(entity.dxf.get("name", ""))
        if name:
            insert_counts[name] += 1
            if name not in block_names:
                unresolved.add(name)
    for block in doc.blocks:
        for entity in block:
            if _entity_type(entity) == "INSERT":
                name = str(entity.dxf.get("name", ""))
                if name:
                    nested_insert_counts[name] += 1
                    if name not in block_names:
                        unresolved.add(name)
    return insert_counts, nested_insert_counts, unresolved


def _nested_entity_counts(doc: Any, modelspace_entities: Iterable[DXFEntity], unresolved: set[str]) -> Counter[str]:
    counts: Counter[str] = Counter()
    block_lookup = {str(block.name): block for block in doc.blocks}
    visiting: set[str] = set()

    def walk_block(name: str) -> None:
        if name in visiting:
            return
        block = block_lookup.get(name)
        if block is None:
            unresolved.add(name)
            return
        visiting.add(name)
        try:
            for nested in block:
                counts[_entity_type(nested)] += 1
                if _entity_type(nested) == "INSERT":
                    walk_block(str(nested.dxf.get("name", "")))
        finally:
            visiting.remove(name)

    for entity in modelspace_entities:
        if _entity_type(entity) == "INSERT":
            walk_block(str(entity.dxf.get("name", "")))
    return counts


def _xref_indicators(doc: Any, blocks: Iterable[BlockInventory]) -> list[str]:
    indicators = [block.name for block in blocks if block.is_xref]
    for block in doc.blocks:
        if not hasattr(block, "block"):
            continue
        for attrib in ("xref_path", "path", "filename"):
            try:
                value = block.block.dxf.get(attrib)
            except Exception:
                value = None
            if value:
                indicators.append(f"{block.name}:{value}")
    return indicators


def _safe_modelspace_extents(modelspace: Any, issues: list[DxfAuditIssue]) -> dict[str, float] | None:
    try:
        extents = bbox.extents(modelspace, fast=True)
    except Exception as exc:
        issues.append(_issue(DxfInventoryIssueCode.EXTENTS_UNAVAILABLE, "warning", f"Modelspace extents could not be calculated: {exc}"))
        return None
    if not extents.has_data:
        issues.append(_issue(DxfInventoryIssueCode.EXTENTS_UNAVAILABLE, "warning", "Modelspace extents are unavailable."))
        return None
    return {
        "min_x": float(extents.extmin.x),
        "min_y": float(extents.extmin.y),
        "min_z": float(extents.extmin.z),
        "max_x": float(extents.extmax.x),
        "max_y": float(extents.extmax.y),
        "max_z": float(extents.extmax.z),
    }


def _append_content_issues(
    issues: list[DxfAuditIssue],
    global_counts: Counter[str],
    flagged: dict[str, int],
    has_nonzero_z: bool,
    has_3d_geometry: bool,
    unresolved: set[str],
    xref_indicators: Iterable[str],
    nonzero_z_values: tuple[float, ...],
    z_epsilon: float,
) -> None:
    if flagged.get("REGION", 0) > 0:
        issues.append(_issue(DxfInventoryIssueCode.CONTAINS_REGION, "warning", "DXF contains REGION entities.", {"count": flagged["REGION"]}))
    if has_3d_geometry:
        issues.append(_issue(DxfInventoryIssueCode.CONTAINS_3D_GEOMETRY, "warning", "DXF contains 3D geometry indicators."))
    if global_counts.get("ACAD_PROXY_ENTITY", 0) > 0:
        issues.append(_issue(DxfInventoryIssueCode.CONTAINS_PROXY_ENTITY, "warning", "DXF contains ACAD_PROXY_ENTITY entities.", {"count": global_counts["ACAD_PROXY_ENTITY"]}))
    refs = list(xref_indicators)
    if refs:
        issues.append(_issue(DxfInventoryIssueCode.CONTAINS_EXTERNAL_REFERENCE, "warning", "DXF contains external-reference indicators.", {"indicators": refs}))
    raster_count = sum(global_counts.get(key, 0) for key in RASTER_UNDERLAY_TYPES)
    if raster_count > 0:
        issues.append(_issue(DxfInventoryIssueCode.CONTAINS_RASTER_OR_UNDERLAY, "warning", "DXF contains raster image or underlay entities.", {"count": raster_count}))
    unsupported = {key: count for key, count in global_counts.items() if _entity_category(key) == "unsupported"}
    if unsupported:
        issues.append(_issue(DxfInventoryIssueCode.UNSUPPORTED_ENTITY_TYPE, "warning", "DXF contains entity types outside the current audit category map.", {"entity_counts": dict(sorted(unsupported.items()))}))
    if has_nonzero_z:
        issues.append(
            _issue(
                DxfInventoryIssueCode.NONZERO_Z_GEOMETRY,
                "warning",
                "DXF contains non-zero Z or elevation indicators.",
                {"z_values": nonzero_z_values, "z_epsilon": z_epsilon},
            )
        )
    if unresolved:
        issues.append(_issue(DxfInventoryIssueCode.UNRESOLVED_BLOCK_REFERENCE, "warning", "DXF contains INSERT references whose block definitions were not found.", {"block_names": sorted(unresolved)}))


def _has_2d_geometry(counts: Counter[str]) -> bool:
    return any(counts.get(entity_type, 0) > 0 for entity_type in KNOWN_SUPPORTED_TYPES | {"SPLINE", "ELLIPSE", "HATCH"})


def _nonzero_z_values(entities: Iterable[DXFEntity], z_epsilon: float) -> tuple[float, ...]:
    values: set[float] = set()
    for entity in entities:
        values.update(value for value in _raw_z_values(entity) if _numeric_nonzero(value, z_epsilon))
    return tuple(sorted(values))


def _raw_z_values(entity: DXFEntity) -> tuple[float, ...]:
    values: list[float] = []
    for attr in ("elevation", "thickness"):
        try:
            value = entity.dxf.get(attr)
        except Exception:
            value = None
        numeric = _to_float(value)
        if numeric is not None:
            values.append(numeric)
    for attr in ("insert", "location", "center", "start", "end"):
        try:
            value = entity.dxf.get(attr)
        except Exception:
            value = None
        numeric = _point_z(value)
        if numeric is not None:
            values.append(numeric)
    return tuple(values)


def _point_z(value: Any) -> float | None:
    if value is None or not hasattr(value, "z"):
        return None
    return _to_float(value.z)


def _numeric_nonzero(value: Any, z_epsilon: float) -> bool:
    numeric = _to_float(value)
    return numeric is not None and abs(numeric) > z_epsilon


def _to_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_z_epsilon(value: float) -> float:
    epsilon = float(value)
    if epsilon < 0:
        raise ValueError("z_epsilon must be non-negative")
    return epsilon


def _entity_type(entity: DXFEntity) -> str:
    try:
        return str(entity.dxftype()).upper()
    except Exception:
        return "UNKNOWN"


def _entity_layer(entity: DXFEntity) -> str:
    try:
        return str(entity.dxf.get("layer", "0"))
    except Exception:
        return "0"


def _count_models(counts: Counter[str]) -> tuple[EntityTypeCount, ...]:
    return tuple(EntityTypeCount(dxf_type=name, count=count, category=_entity_category(name)) for name, count in sorted(counts.items(), key=lambda item: item[0]))


def _entity_category(dxf_type: str) -> EntityCategory:
    name = dxf_type.upper()
    if name in KNOWN_SUPPORTED_TYPES:
        return "known_supported"
    if name in CONVERTIBLE_LATER_TYPES:
        return "convertible_later"
    if name in IGNORED_NON_OPERATIONAL_TYPES:
        return "ignored_non_operational"
    if name in REVIEW_REQUIRED_TYPES:
        return "review_required"
    return "unsupported"


def _issue(code: DxfInventoryIssueCode, severity: AuditSeverity, message: str, evidence: dict[str, Any] | None = None) -> DxfAuditIssue:
    return DxfAuditIssue(code=code.value, severity=severity, message=message, evidence=evidence or {})



