"""Stage 9 normalized DXF layer rewriting."""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from enum import Enum
from pathlib import Path
from typing import Any, Literal

import ezdxf
from ezdxf import bbox
from ezdxf.entities import DXFEntity
from pydantic import Field

from gebal_cad_normalizer.cad.classify import ClassificationResult, LayerClassification
from gebal_cad_normalizer.exceptions import DxfReadError, OutputWriteError
from gebal_cad_normalizer.models import StrictModel


LayerRewriteAction = Literal["move", "preserve", "review_required"]
UncertainLayerAction = Literal["review_required", "preserve"]
IssueSeverity = Literal["info", "warning", "fail"]

REWRITE_XDATA_APPID = "GEBAL_STAGE9_REWRITE_AUDIT"
DEFAULT_TARGET_LAYERS: dict[str, str] = {
    "product_geometry": "PRODUCT",
    "safety_zone": "SAFETY_ZONE",
    "foundation_or_installation": "FOUNDATION",
    "dimensions": "DIMENSIONS",
    "text_annotation": "TEXT",
    "hatch_or_fill": "HATCH",
    "construction_or_reference": "REFERENCE",
    "external_or_proxy": "EXTERNAL",
    "non_operational": "NON_OPERATIONAL",
    "ambiguous": "REVIEW_REQUIRED",
    "review_required": "REVIEW_REQUIRED",
}
DEFAULT_LAYER_STYLES: dict[str, dict[str, Any]] = {
    "PRODUCT": {"color": 3, "linetype": "CONTINUOUS"},
    "SAFETY_ZONE": {"color": 1, "linetype": "DASHED"},
    "FOUNDATION": {"color": 2, "linetype": "CONTINUOUS"},
    "DIMENSIONS": {"color": 5, "linetype": "CONTINUOUS"},
    "TEXT": {"color": 7, "linetype": "CONTINUOUS"},
    "HATCH": {"color": 8, "linetype": "CONTINUOUS"},
    "REFERENCE": {"color": 9, "linetype": "CENTER"},
    "EXTERNAL": {"color": 6, "linetype": "CONTINUOUS"},
    "NON_OPERATIONAL": {"color": 250, "linetype": "CONTINUOUS"},
    "REVIEW_REQUIRED": {"color": 30, "linetype": "DASHED"},
}
_UNSUPPORTED_OR_REVIEW_TYPES = {
    "3DSOLID",
    "ACAD_PROXY_ENTITY",
    "BODY",
    "DGNUNDERLAY",
    "DWFUNDERLAY",
    "IMAGE",
    "MESH",
    "PDFUNDERLAY",
    "POLYFACE",
    "POLYMESH",
    "SURFACE",
    "UNDERLAY",
    "WIPEOUT",
}


class LayerRewriteIssueCode(str, Enum):
    """Stable Stage 9 issue codes."""

    LOW_CONFIDENCE_CLASSIFICATION = "low_confidence_classification"
    AMBIGUOUS_LAYER_MAPPING = "ambiguous_layer_mapping"
    MIXED_CONTENT_PRESERVED = "mixed_content_preserved"
    UNSUPPORTED_ENTITY_PRESERVED = "unsupported_entity_preserved"
    PROXY_ENTITY_PRESERVED = "proxy_entity_preserved"
    EXTERNAL_REFERENCE_PRESERVED = "external_reference_preserved"
    ENTITY_MOVE_FAILED = "entity_move_failed"
    ENTITY_COUNT_MISMATCH = "entity_count_mismatch"
    GEOMETRY_EXTENTS_MISMATCH = "geometry_extents_mismatch"
    OUTPUT_WRITE_FAILED = "output_write_failed"
    OUTPUT_VALIDATION_FAILED = "output_validation_failed"


class LayerRewriteIssue(StrictModel):
    """Machine-readable Stage 9 issue."""

    code: str
    severity: IssueSeverity
    message: str
    layer_name: str | None = None
    entity_handle: str | None = None
    evidence: dict[str, Any] = {}


class LayerRewriteConfig(StrictModel):
    """Configuration for Stage 9 layer rewriting."""

    confidence_threshold: float = Field(default=0.62, ge=0.0, le=1.0)
    uncertain_layer_action: UncertainLayerAction = "review_required"
    target_layers: dict[str, str] = DEFAULT_TARGET_LAYERS
    target_layer_styles: dict[str, dict[str, Any]] = DEFAULT_LAYER_STYLES
    preserve_original_layer_xdata: bool = True
    extents_tolerance: float = Field(default=1e-6, gt=0.0)
    allow_overwrite_input: bool = False


class LayerRewriteMapping(StrictModel):
    """One original source layer mapping decision."""

    original_layer_name: str
    assigned_role: str
    confidence: float
    target_layer_name: str
    action: LayerRewriteAction
    entity_count: int
    moved_entity_count: int = 0
    unchanged_entity_count: int = 0
    review_required_entity_count: int = 0
    reason: str


class EntityTotals(StrictModel):
    """Entity totals before and after rewrite."""

    modelspace: int
    all_layouts_and_blocks: int


class PreservationChecks(StrictModel):
    """Validation checks proving copy preservation."""

    source_checksum_unchanged: bool
    entity_count_preserved: bool
    modelspace_extents_preserved: bool
    handles_preserved_where_possible: bool
    no_entities_deleted: bool
    no_geometry_transformation: bool
    target_layers_use_gebal_prefix: bool
    ai_used: bool = False


class LayerRewriteResult(StrictModel):
    """Complete Stage 9 rewrite result."""

    source_path: Path
    output_path: Path
    source_sha256_before: str
    source_sha256_after: str
    output_sha256: str
    entity_totals_before: EntityTotals
    entity_totals_after: EntityTotals
    original_to_target_layer_mapping: tuple[LayerRewriteMapping, ...]
    moved_entity_count: int
    unchanged_entity_count: int
    review_required_entity_count: int
    issues: tuple[LayerRewriteIssue, ...]
    modelspace_extents_before: dict[str, float] | None
    modelspace_extents_after: dict[str, float] | None
    preservation_checks: PreservationChecks

    def to_deterministic_json(self) -> str:
        """Serialize deterministically for reports and regression tests."""

        return json.dumps(self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))


def rewrite_layers(
    source_path: Path | str,
    classification: ClassificationResult,
    output_path: Path | str,
    config: LayerRewriteConfig | None = None,
) -> LayerRewriteResult:
    """Write a normalized DXF copy with deterministic layer reassignment."""

    cfg = config or LayerRewriteConfig()
    source = Path(source_path)
    destination = Path(output_path)
    _validate_paths(source, destination, cfg)
    before_sha = _sha256(source)
    try:
        doc = ezdxf.readfile(source)
    except Exception as exc:
        raise DxfReadError(f"Invalid or unreadable DXF file: {source}") from exc

    before_totals = _entity_totals(doc)
    before_extents = _modelspace_extents(doc)
    classification_by_layer = {layer.original_layer_name: layer for layer in classification.layers}
    _ensure_target_layers(doc, cfg)
    if cfg.preserve_original_layer_xdata and REWRITE_XDATA_APPID not in doc.appids:
        doc.appids.add(REWRITE_XDATA_APPID)

    moved_by_source: Counter[str] = Counter()
    unchanged_by_source: Counter[str] = Counter()
    review_by_source: Counter[str] = Counter()
    issues: list[LayerRewriteIssue] = []

    for entity in _all_rewrite_entities(doc):
        original_layer = _entity_layer(entity)
        layer_classification = classification_by_layer.get(original_layer)
        action, target_layer, reason, decision_issues = _mapping_decision(original_layer, layer_classification, entity, cfg)
        issues.extend(decision_issues)
        if action == "preserve":
            unchanged_by_source[original_layer] += 1
            continue
        if action == "review_required":
            review_by_source[original_layer] += 1
        else:
            moved_by_source[original_layer] += 1
        if not _move_entity(entity, target_layer, original_layer, cfg, issues):
            unchanged_by_source[original_layer] += 1
            if action == "review_required":
                review_by_source[original_layer] -= 1
            else:
                moved_by_source[original_layer] -= 1

    mappings = _build_mappings(classification.layers, cfg, moved_by_source, unchanged_by_source, review_by_source)
    _write_validated_copy(doc, source, destination, before_totals, before_extents, before_sha, cfg, issues)

    after_sha = _sha256(source)
    try:
        written = ezdxf.readfile(destination)
    except Exception as exc:
        raise OutputWriteError(f"Stage 9 output validation failed after promotion: {destination}") from exc
    after_totals = _entity_totals(written)
    after_extents = _modelspace_extents(written)
    output_sha = _sha256(destination)
    entity_count_preserved = before_totals == after_totals
    extents_preserved = _extents_match(before_extents, after_extents, cfg.extents_tolerance)
    source_unchanged = before_sha == after_sha
    no_gebal_targets = not any(name.upper().startswith("GEBAL") for name in set(cfg.target_layers.values()))
    checks = PreservationChecks(
        source_checksum_unchanged=source_unchanged,
        entity_count_preserved=entity_count_preserved,
        modelspace_extents_preserved=extents_preserved,
        handles_preserved_where_possible=entity_count_preserved,
        no_entities_deleted=entity_count_preserved,
        no_geometry_transformation=extents_preserved,
        target_layers_use_gebal_prefix=not no_gebal_targets,
    )

    return LayerRewriteResult(
        source_path=source,
        output_path=destination,
        source_sha256_before=before_sha,
        source_sha256_after=after_sha,
        output_sha256=output_sha,
        entity_totals_before=before_totals,
        entity_totals_after=after_totals,
        original_to_target_layer_mapping=mappings,
        moved_entity_count=sum(moved_by_source.values()),
        unchanged_entity_count=sum(unchanged_by_source.values()),
        review_required_entity_count=sum(review_by_source.values()),
        issues=tuple(sorted(issues, key=lambda item: (item.severity, item.code, item.layer_name or "", item.entity_handle or "", item.message))),
        modelspace_extents_before=before_extents,
        modelspace_extents_after=after_extents,
        preservation_checks=checks,
    )


def write_rewrite_json(result: LayerRewriteResult, output_path: Path | str) -> Path:
    """Write deterministic Stage 9 rewrite JSON to an explicit path."""

    path = Path(output_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(result.to_deterministic_json() + "\n", encoding="utf-8")
    except OSError as exc:
        raise OutputWriteError(f"Failed to write Stage 9 rewrite JSON report: {path}") from exc
    return path


def _validate_paths(source: Path, destination: Path, config: LayerRewriteConfig) -> None:
    if source.suffix.lower() != ".dxf":
        raise DxfReadError(f"Stage 9 input accepts .dxf files only: {source}")
    if destination.suffix.lower() != ".dxf":
        raise OutputWriteError(f"Stage 9 output accepts .dxf files only: {destination}")
    if not source.exists() or not source.is_file():
        raise DxfReadError(f"DXF file does not exist: {source}")
    if source.resolve() == destination.resolve() and not config.allow_overwrite_input:
        raise OutputWriteError("Refusing to overwrite input DXF without allow_overwrite_input=True")
    gebal_targets = [name for name in set(config.target_layers.values()) if name.upper().startswith("GEBAL")]
    if gebal_targets:
        raise ValueError(f"Stage 9 target layers must not start with GEBAL: {gebal_targets}")


def _mapping_decision(
    original_layer: str,
    classification: LayerClassification | None,
    entity: DXFEntity,
    config: LayerRewriteConfig,
) -> tuple[LayerRewriteAction, str, str, list[LayerRewriteIssue]]:
    if classification is None:
        target = config.target_layers["review_required"]
        return "review_required", target, "missing Stage 8 classification", [
            _issue(LayerRewriteIssueCode.AMBIGUOUS_LAYER_MAPPING, "warning", "Entity layer has no Stage 8 classification.", original_layer, entity)
        ]

    target = config.target_layers.get(classification.assigned_role, config.target_layers["review_required"])
    issues: list[LayerRewriteIssue] = []
    entity_type = _entity_type(entity)
    codes = {issue.code for issue in classification.issues}

    if entity_type == "ACAD_PROXY_ENTITY" or classification.evidence.has_proxy:
        issues.append(_issue(LayerRewriteIssueCode.PROXY_ENTITY_PRESERVED, "warning", "Proxy content is routed for review without geometry interpretation.", original_layer, entity))
        return _uncertain_action(config), config.target_layers["review_required"], "proxy content requires review", issues
    if classification.evidence.has_external_reference:
        issues.append(_issue(LayerRewriteIssueCode.EXTERNAL_REFERENCE_PRESERVED, "warning", "External reference evidence is preserved.", original_layer, entity))
        return "move", config.target_layers.get("external_or_proxy", "EXTERNAL"), "external reference layer preserved as external", issues
    if entity_type in _UNSUPPORTED_OR_REVIEW_TYPES:
        issues.append(_issue(LayerRewriteIssueCode.UNSUPPORTED_ENTITY_PRESERVED, "warning", "Unsupported or review-required entity is routed for review.", original_layer, entity, {"dxf_type": entity_type}))
        return _uncertain_action(config), config.target_layers["review_required"], "unsupported entity requires review", issues
    if "mixed_operational_content" in codes:
        issues.append(_issue(LayerRewriteIssueCode.MIXED_CONTENT_PRESERVED, "warning", "Mixed operational/non-operational content is routed by uncertainty policy.", original_layer, entity))
        return _uncertain_action(config), config.target_layers["review_required"], "mixed content requires review", issues
    if classification.assigned_role in {"ambiguous", "review_required"}:
        issues.append(_issue(LayerRewriteIssueCode.AMBIGUOUS_LAYER_MAPPING, "warning", "Ambiguous or review-required classification is routed by uncertainty policy.", original_layer, entity))
        return _uncertain_action(config), config.target_layers["review_required"], classification.review_reason or "ambiguous classification", issues
    if classification.confidence < config.confidence_threshold:
        issues.append(_issue(LayerRewriteIssueCode.LOW_CONFIDENCE_CLASSIFICATION, "warning", "Classification confidence is below Stage 9 rewrite threshold.", original_layer, entity, {"confidence": classification.confidence, "threshold": config.confidence_threshold}))
        return _uncertain_action(config), config.target_layers["review_required"], "low confidence classification", issues
    return "move", target, "confident classification", issues


def _uncertain_action(config: LayerRewriteConfig) -> LayerRewriteAction:
    return "preserve" if config.uncertain_layer_action == "preserve" else "review_required"


def _move_entity(entity: DXFEntity, target_layer: str, original_layer: str, config: LayerRewriteConfig, issues: list[LayerRewriteIssue]) -> bool:
    try:
        if config.preserve_original_layer_xdata:
            entity.set_xdata(
                REWRITE_XDATA_APPID,
                [
                    (
                        1000,
                        json.dumps(
                            {"original_layer": original_layer, "target_layer": target_layer, "source_handle": _handle(entity), "stage": 9},
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                ],
            )
        entity.dxf.layer = target_layer
        return True
    except Exception as exc:
        issues.append(
            _issue(
                LayerRewriteIssueCode.ENTITY_MOVE_FAILED,
                "warning",
                f"Entity layer rewrite failed: {exc}",
                original_layer,
                entity,
                {"target_layer": target_layer},
            )
        )
        return False


def _build_mappings(
    classifications: tuple[LayerClassification, ...],
    config: LayerRewriteConfig,
    moved: Counter[str],
    unchanged: Counter[str],
    review: Counter[str],
) -> tuple[LayerRewriteMapping, ...]:
    rows: list[LayerRewriteMapping] = []
    for item in sorted(classifications, key=lambda layer: layer.original_layer_name.lower()):
        action, target, reason = _layer_level_decision(item, config)
        rows.append(
            LayerRewriteMapping(
                original_layer_name=item.original_layer_name,
                assigned_role=item.assigned_role,
                confidence=item.confidence,
                target_layer_name=target,
                action=action,
                entity_count=sum(item.entity_counts.values()),
                moved_entity_count=moved[item.original_layer_name],
                unchanged_entity_count=unchanged[item.original_layer_name],
                review_required_entity_count=review[item.original_layer_name],
                reason=reason,
            )
        )
    return tuple(rows)


def _layer_level_decision(classification: LayerClassification, config: LayerRewriteConfig) -> tuple[LayerRewriteAction, str, str]:
    target = config.target_layers.get(classification.assigned_role, config.target_layers["review_required"])
    codes = {issue.code for issue in classification.issues}
    if classification.evidence.has_proxy:
        return _uncertain_action(config), config.target_layers["review_required"], "proxy content requires review"
    if "mixed_operational_content" in codes:
        return _uncertain_action(config), config.target_layers["review_required"], "mixed content requires review"
    if classification.assigned_role in {"ambiguous", "review_required"}:
        return _uncertain_action(config), config.target_layers["review_required"], classification.review_reason or "ambiguous classification"
    if classification.confidence < config.confidence_threshold:
        return _uncertain_action(config), config.target_layers["review_required"], "low confidence classification"
    return "move", target, "confident classification"


def _write_validated_copy(
    doc: ezdxf.document.Drawing,
    source: Path,
    destination: Path,
    before_totals: EntityTotals,
    before_extents: dict[str, float] | None,
    before_sha: str,
    config: LayerRewriteConfig,
    issues: list[LayerRewriteIssue],
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        doc.saveas(temp_path)
        written = ezdxf.readfile(temp_path)
        after_totals = _entity_totals(written)
        after_extents = _modelspace_extents(written)
        if after_totals != before_totals:
            issues.append(_global_issue(LayerRewriteIssueCode.ENTITY_COUNT_MISMATCH, "fail", "Entity count changed during Stage 9 rewrite.", {"before": before_totals.model_dump(), "after": after_totals.model_dump()}))
            raise OutputWriteError("Stage 9 output validation failed: entity count mismatch")
        if not _extents_match(before_extents, after_extents, config.extents_tolerance):
            issues.append(_global_issue(LayerRewriteIssueCode.GEOMETRY_EXTENTS_MISMATCH, "fail", "Modelspace extents changed during Stage 9 rewrite.", {"before": before_extents, "after": after_extents, "tolerance": config.extents_tolerance}))
            raise OutputWriteError("Stage 9 output validation failed: geometry extents mismatch")
        if _sha256(source) != before_sha:
            issues.append(_global_issue(LayerRewriteIssueCode.OUTPUT_VALIDATION_FAILED, "fail", "Source checksum changed during Stage 9 rewrite."))
            raise OutputWriteError("Stage 9 output validation failed: source checksum changed")
        os.replace(temp_path, destination)
    except OutputWriteError:
        _cleanup_temp(temp_path)
        raise
    except Exception as exc:
        issues.append(_global_issue(LayerRewriteIssueCode.OUTPUT_WRITE_FAILED, "fail", f"Failed to write Stage 9 normalized DXF: {destination}"))
        _cleanup_temp(temp_path)
        raise OutputWriteError(f"Failed to write Stage 9 normalized DXF: {destination}") from exc


def _ensure_target_layers(doc: ezdxf.document.Drawing, config: LayerRewriteConfig) -> None:
    for name in sorted(set(config.target_layers.values()), key=str.casefold):
        style = dict(config.target_layer_styles.get(name, {}))
        if name in doc.layers:
            layer = doc.layers.get(name)
            for key, value in style.items():
                if key in {"color", "linetype", "plot"}:
                    layer.dxf.set(key, value)
            continue
        doc.layers.new(name, dxfattribs={key: value for key, value in style.items() if key in {"color", "linetype", "plot"}})


def _all_rewrite_entities(doc: ezdxf.document.Drawing) -> list[DXFEntity]:
    entities: list[DXFEntity] = []
    for layout in doc.layouts:
        entities.extend(list(layout))
    for block in doc.blocks:
        name = str(getattr(block, "name", "")).upper()
        if name.startswith("*MODEL_SPACE") or name.startswith("*PAPER_SPACE"):
            continue
        entities.extend(list(block))
    return entities


def _entity_totals(doc: ezdxf.document.Drawing) -> EntityTotals:
    modelspace_count = len(list(doc.modelspace()))
    return EntityTotals(modelspace=modelspace_count, all_layouts_and_blocks=len(_all_rewrite_entities(doc)))


def _modelspace_extents(doc: ezdxf.document.Drawing) -> dict[str, float] | None:
    try:
        extents = bbox.extents(doc.modelspace(), fast=True)
    except Exception:
        return None
    if not extents.has_data:
        return None
    return {
        "min_x": float(extents.extmin.x),
        "min_y": float(extents.extmin.y),
        "min_z": float(extents.extmin.z),
        "max_x": float(extents.extmax.x),
        "max_y": float(extents.extmax.y),
        "max_z": float(extents.extmax.z),
    }


def _extents_match(before: dict[str, float] | None, after: dict[str, float] | None, tolerance: float) -> bool:
    if before is None or after is None:
        return before is None and after is None
    return all(abs(float(before[key]) - float(after.get(key, float("inf")))) <= tolerance for key in before)


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


def _handle(entity: DXFEntity) -> str | None:
    try:
        value = entity.dxf.get("handle")
    except Exception:
        return None
    return str(value) if value is not None else None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _issue(
    code: LayerRewriteIssueCode,
    severity: IssueSeverity,
    message: str,
    layer_name: str,
    entity: DXFEntity,
    evidence: dict[str, Any] | None = None,
) -> LayerRewriteIssue:
    return LayerRewriteIssue(code=code.value, severity=severity, message=message, layer_name=layer_name, entity_handle=_handle(entity), evidence=evidence or {})


def _global_issue(code: LayerRewriteIssueCode, severity: IssueSeverity, message: str, evidence: dict[str, Any] | None = None) -> LayerRewriteIssue:
    return LayerRewriteIssue(code=code.value, severity=severity, message=message, evidence=evidence or {})


def _cleanup_temp(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass
