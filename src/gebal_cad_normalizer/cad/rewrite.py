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
from ezdxf.tools.standards import linetypes as standard_linetypes
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
    create_unused_target_layers: bool = False
    purge_emptied_source_layers: bool = True
    # Also drop layers that were already empty on input. A layer that never held
    # an entity carries no information and is pure clutter in the output.
    purge_all_empty_layers: bool = False
    # Roles for which an unsupported entity type is still routed to its target
    # layer. A vendor-confirmed safety region drawn as a 3DSOLID cannot be
    # measured, but its meaning is known and sending it to review loses the
    # only boundary in the drawing. The warning is still recorded either way.
    unsupported_type_review_exempt_roles: tuple[str, ...] = ()
    # Drop LAYER_INDEX cache objects that still name pre-rewrite layers. They
    # are a regeneratable demand-load cache, but AutoCAD recreates any layer
    # they name, which undoes the purge.
    purge_stale_layer_index: bool = False
    # Pin individual entities to a target layer by handle. Needed when one
    # entity on a layer must go somewhere other than its layer's role sends it -
    # e.g. an INSERT placing the product block that the vendor drew on the
    # dimension layer. Empty dict keeps pure layer/type routing.
    entity_handle_layer_overrides: dict[str, str] = {}
    # Route specific DXF entity types to a fixed target layer regardless of the
    # role assigned to their source layer. Empty dict keeps role-only routing.
    entity_type_layer_overrides: dict[str, str] = {}
    # Entities on layer "0" inside a block definition inherit the INSERT's layer
    # at render time. Moving them off "0" hard-codes them and breaks that idiom.
    preserve_block_internal_layer_zero: bool = False
    # Source layers whose entities are left exactly where they are. Intended for
    # special layers such as Defpoints, which is non-plotting infrastructure for
    # dimension definition points and is recreated automatically on save.
    preserve_source_layers: tuple[str, ...] = ()
    # "layer": any proxy evidence sends every entity on that layer to review.
    # "entity": only the proxy entities themselves are routed for review.
    proxy_routing_scope: Literal["layer", "entity"] = "layer"


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
    if cfg.preserve_original_layer_xdata and REWRITE_XDATA_APPID not in doc.appids:
        doc.appids.add(REWRITE_XDATA_APPID)

    moved_by_source: Counter[str] = Counter()
    unchanged_by_source: Counter[str] = Counter()
    review_by_source: Counter[str] = Counter()
    issues: list[LayerRewriteIssue] = []

    # Decide every entity's destination before creating any target layer, so
    # that only layers which actually receive geometry are added to the LAYER
    # table. Creating all eleven target layers unconditionally left real
    # drawings cluttered with empty layers that carry no information.
    populated_before = {layer for layer in (_entity_layer(entity) for entity in _all_rewrite_entities(doc))}
    decisions: list[tuple[DXFEntity, str, LayerRewriteAction, str]] = []
    for entity, in_block in _rewrite_entities_with_scope(doc):
        original_layer = _entity_layer(entity)
        layer_classification = classification_by_layer.get(original_layer)
        action, target_layer, reason, decision_issues = _mapping_decision(original_layer, layer_classification, entity, cfg, in_block)
        issues.extend(decision_issues)
        decisions.append((entity, original_layer, action, target_layer))

    used_targets = {target for _entity, _original, action, target in decisions if action != "preserve"}
    _ensure_target_layers(doc, cfg, used_targets if not cfg.create_unused_target_layers else None)

    for entity, original_layer, action, target_layer in decisions:
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

    if cfg.purge_emptied_source_layers:
        _purge_emptied_layers(cfg, doc, populated_before, issues)
    if cfg.purge_stale_layer_index:
        _purge_stale_layer_index(doc, issues)
    _repair_sub_entity_layers(doc, issues)
    _repair_current_layer(doc, issues)

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
    in_block_definition: bool = False,
) -> tuple[LayerRewriteAction, str, str, list[LayerRewriteIssue]]:
    if config.entity_handle_layer_overrides:
        pinned = config.entity_handle_layer_overrides.get(str(_handle(entity)))
        if pinned:
            return "move", pinned, "entity pinned to target layer by handle", []
    if original_layer.casefold() in {name.casefold() for name in config.preserve_source_layers}:
        return "preserve", original_layer, "source layer preserved by configuration", []
    if config.preserve_block_internal_layer_zero and in_block_definition and original_layer == "0":
        return "preserve", original_layer, "block-internal layer 0 retained for INSERT layer inheritance", []
    override_target = config.entity_type_layer_overrides.get(_entity_type(entity))
    if override_target:
        return "move", override_target, "entity type routed by explicit override", []
    if classification is None:
        target = config.target_layers["review_required"]
        return "review_required", target, "missing Stage 8 classification", [
            _issue(LayerRewriteIssueCode.AMBIGUOUS_LAYER_MAPPING, "warning", "Entity layer has no Stage 8 classification.", original_layer, entity)
        ]

    target = config.target_layers.get(classification.assigned_role, config.target_layers["review_required"])
    issues: list[LayerRewriteIssue] = []
    entity_type = _entity_type(entity)
    codes = {issue.code for issue in classification.issues}

    if entity_type == "ACAD_PROXY_ENTITY" or (classification.evidence.has_proxy and config.proxy_routing_scope == "layer"):
        issues.append(_issue(LayerRewriteIssueCode.PROXY_ENTITY_PRESERVED, "warning", "Proxy content is routed for review without geometry interpretation.", original_layer, entity))
        return _uncertain_action(config), config.target_layers["review_required"], "proxy content requires review", issues
    if classification.evidence.has_external_reference:
        issues.append(_issue(LayerRewriteIssueCode.EXTERNAL_REFERENCE_PRESERVED, "warning", "External reference evidence is preserved.", original_layer, entity))
        return "move", config.target_layers.get("external_or_proxy", "EXTERNAL"), "external reference layer preserved as external", issues
    if entity_type in _UNSUPPORTED_OR_REVIEW_TYPES:
        exempt = classification.assigned_role in config.unsupported_type_review_exempt_roles
        issues.append(
            _issue(
                LayerRewriteIssueCode.UNSUPPORTED_ENTITY_PRESERVED,
                "warning",
                "Unsupported entity kept on its role's target layer." if exempt else "Unsupported or review-required entity is routed for review.",
                original_layer,
                entity,
                {"dxf_type": entity_type},
            )
        )
        if not exempt:
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


def _repair_current_layer(doc: ezdxf.document.Drawing, issues: list[LayerRewriteIssue]) -> bool:
    """Point $CLAYER at a layer that exists.

    The header's current-layer variable survives the rewrite naming a purged
    source layer. AutoCAD recreates whatever layer it names when the file is
    opened, so a stale $CLAYER resurrects a vendor layer on its own.
    """

    try:
        current = str(doc.header.get("$CLAYER", "0"))
    except Exception:
        return False
    if current in {str(layer.dxf.name) for layer in doc.layers}:
        return False
    try:
        doc.header["$CLAYER"] = "0"
    except Exception:
        return False
    issues.append(
        _global_issue(
            LayerRewriteIssueCode.AMBIGUOUS_LAYER_MAPPING,
            "warning",
            f"Reset $CLAYER from removed layer {current!r} to '0'.",
            {"previous_current_layer": current},
        )
    )
    return True


def _purge_stale_layer_index(doc: ezdxf.document.Drawing, issues: list[LayerRewriteIssue]) -> int:
    """Remove LAYER_INDEX objects that still name layers no longer in the table.

    LAYER_INDEX is a demand-loading cache for xrefs, regenerated by AutoCAD when
    it is wanted. A stale one keeps the pre-rewrite layer names alive in the
    file, and AutoCAD recreates every layer it names on open - so the purged
    vendor layers reappear and the rewrite looks like it never happened.
    """

    existing = {str(layer.dxf.name).casefold() for layer in doc.layers}
    removed = 0
    for obj in list(doc.objects):
        if obj.dxftype() != "LAYER_INDEX":
            continue
        tags = getattr(obj, "xtags", None)
        if tags is None:
            continue
        names = [str(tag.value) for subclass in tags.subclasses for tag in subclass if tag.code == 8]
        if all(name.casefold() in existing for name in names):
            continue
        owner = doc.entitydb.get(obj.dxf.owner)
        if owner is not None and owner.dxftype() == "DICTIONARY":
            for key, value in list(owner.items()):
                if getattr(value, "dxf", None) is not None and value.dxf.handle == obj.dxf.handle:
                    owner.discard(key)
        try:
            doc.objects.delete_entity(obj)
        except Exception:
            continue
        removed += 1
    if removed:
        issues.append(
            _global_issue(
                LayerRewriteIssueCode.AMBIGUOUS_LAYER_MAPPING,
                "warning",
                f"Removed {removed} stale LAYER_INDEX cache object(s) naming purged layers.",
                {"removed_count": removed},
            )
        )
    return removed


def _repair_sub_entity_layers(doc: ezdxf.document.Drawing, issues: list[LayerRewriteIssue]) -> int:
    """Point any sub-entity at a layer that still exists.

    Catches sub-entities whose owner was preserved rather than moved, and any
    the move path could not reach. Runs after purging, so the layer table is
    final. A sub-entity referencing a purged layer would otherwise make AutoCAD
    recreate that layer on open.
    """

    existing = {str(layer.dxf.name) for layer in doc.layers}
    repaired = 0
    for entity in _all_rewrite_entities(doc):
        owner_layer = _entity_layer(entity)
        for sub in _sub_entities(entity):
            try:
                if str(sub.dxf.layer) not in existing:
                    sub.dxf.layer = owner_layer
                    repaired += 1
            except Exception:
                continue
    # The BLOCK and ENDBLK records that bracket a block definition carry their
    # own layer attribute and are not yielded by block iteration either.
    for block in doc.blocks:
        for record in (getattr(block, "block", None), getattr(block, "endblk", None)):
            if record is None:
                continue
            try:
                if str(record.dxf.layer) not in existing:
                    record.dxf.layer = "0"
                    repaired += 1
            except Exception:
                continue
    if repaired:
        issues.append(
            _global_issue(
                LayerRewriteIssueCode.AMBIGUOUS_LAYER_MAPPING,
                "warning",
                f"Repaired {repaired} sub-entity layer reference(s) left pointing at a removed layer.",
                {"repaired_count": repaired},
            )
        )
    return repaired


def _sub_entities(entity: DXFEntity) -> list[DXFEntity]:
    """Owned sub-entities that carry their own layer attribute.

    A POLYLINE owns its VERTEX list and a SEQEND; an INSERT owns its ATTRIB list
    and a SEQEND. Layout and block iteration yields only the owner, so these are
    never visited by the rewrite loop and keep their original layer. Once the
    source layer is purged from the table that becomes a dangling reference, and
    AutoCAD silently recreates the old layer on open.
    """

    subs: list[DXFEntity] = []
    for attribute in ("vertices", "attribs"):
        owned = getattr(entity, attribute, None)
        if owned:
            try:
                subs.extend(list(owned))
            except Exception:
                continue
    seqend = getattr(entity, "seqend", None)
    if seqend is not None:
        subs.append(seqend)
    return subs


def _move_sub_entities(entity: DXFEntity, target_layer: str) -> None:
    for sub in _sub_entities(entity):
        try:
            sub.dxf.layer = target_layer
        except Exception:
            continue


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
        _move_sub_entities(entity, target_layer)
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


def _ensure_target_linetypes(doc: ezdxf.document.Drawing, config: LayerRewriteConfig) -> dict[str, str]:
    """Define any linetype a target layer references but the document lacks.

    A layer whose linetype is absent from the LTYPE table is a dangling
    reference: ezdxf writes it without complaint and its auditor does not flag
    it, but AutoCAD-family readers reject the whole file as invalid. The
    default target styles use DASHED and CENTER, which real vendor drawings
    frequently do not define (they commonly ship only CONTINUOUS plus their own
    custom linetypes), so the definition is added here from ezdxf's standard
    pattern table.

    Returns the substitutions to apply to layer styles: any linetype with no
    known standard pattern cannot be defined and is downgraded to CONTINUOUS
    rather than written as a dangling reference. The mapping is returned rather
    than written back into the config because target_layer_styles may be the
    shared module-level default dict.
    """

    required = {
        str(style["linetype"])
        for style in config.target_layer_styles.values()
        if style.get("linetype")
    }
    missing = {name for name in required if name not in doc.linetypes}
    if not missing:
        return {}
    patterns = {name: (description, pattern) for name, description, pattern in standard_linetypes()}
    substitutions: dict[str, str] = {}
    for name in sorted(missing):
        definition = patterns.get(name) or patterns.get(name.upper())
        if definition is None:
            substitutions[name] = "CONTINUOUS"
            continue
        description, pattern = definition
        doc.linetypes.add(name, pattern=pattern, description=description)
    return substitutions


def _referenced_layer_names(doc: ezdxf.document.Drawing) -> set[str]:
    """Layer names referenced by something other than an entity's own layer attribute.

    A LAYER table entry can be pointed at by viewport frozen-layer lists and by
    layer-index/id-buffer objects. Removing an entry that is still referenced
    would leave exactly the kind of dangling reference that makes AutoCAD
    reject a file, so these names are never purged.
    """

    referenced: set[str] = set()
    handle_to_name = {layer.dxf.handle: layer.dxf.name for layer in doc.layers}
    for layout in doc.layouts:
        for viewport in layout.query("VIEWPORT"):
            try:
                referenced.update(str(name) for name in viewport.frozen_layers)
            except Exception:
                continue
    for obj in doc.objects:
        for attribute in ("handles", "layers"):
            values = getattr(obj, attribute, None)
            if not values:
                continue
            try:
                for value in values:
                    # These attributes hold handles. A handle that does not
                    # resolve to a LAYER table entry is not a layer reference,
                    # and adding the raw handle would protect layers by
                    # coincidence of name.
                    name = handle_to_name.get(str(value))
                    if name is not None:
                        referenced.add(name)
            except Exception:
                continue
    return referenced


def _purge_emptied_layers(
    config: LayerRewriteConfig | None,
    doc: ezdxf.document.Drawing,
    populated_before: set[str],
    issues: list[LayerRewriteIssue],
) -> list[str]:
    """Remove source layers that this rewrite emptied.

    Only layers that held entities before the rewrite and hold none after are
    removed, so layers the source deliberately shipped empty are left as-is.
    Layer "0" is never removed: DXF requires it.
    """

    still_populated = {_entity_layer(entity) for entity in _all_rewrite_entities(doc)}
    protected = _referenced_layer_names(doc)
    candidates = set(populated_before)
    if config is not None and config.purge_all_empty_layers:
        candidates.update(str(layer.dxf.name) for layer in doc.layers)
    removed: list[str] = []
    for name in sorted(candidates):
        if name == "0" or name in still_populated or name in protected:
            continue
        if name not in doc.layers:
            continue
        try:
            doc.layers.remove(name)
        except Exception:
            continue
        removed.append(name)
    return removed


def _ensure_target_layers(
    doc: ezdxf.document.Drawing,
    config: LayerRewriteConfig,
    only_layers: set[str] | None = None,
) -> None:
    linetype_substitutions = _ensure_target_linetypes(doc, config)
    for name in sorted(set(config.target_layers.values()), key=str.casefold):
        if only_layers is not None and name not in only_layers:
            continue
        style = dict(config.target_layer_styles.get(name, {}))
        if "linetype" in style:
            style["linetype"] = linetype_substitutions.get(str(style["linetype"]), style["linetype"])
        if name in doc.layers:
            layer = doc.layers.get(name)
            for key, value in style.items():
                if key in {"color", "linetype", "plot"}:
                    layer.dxf.set(key, value)
            continue
        doc.layers.new(name, dxfattribs={key: value for key, value in style.items() if key in {"color", "linetype", "plot"}})


def _rewrite_entities_with_scope(doc: ezdxf.document.Drawing) -> list[tuple[DXFEntity, bool]]:
    """Every rewritable entity paired with whether it lives in a block definition."""

    items: list[tuple[DXFEntity, bool]] = []
    for layout in doc.layouts:
        items.extend((entity, False) for entity in layout)
    for block in doc.blocks:
        name = str(getattr(block, "name", "")).upper()
        if name.startswith("*MODEL_SPACE") or name.startswith("*PAPER_SPACE"):
            continue
        items.extend((entity, True) for entity in block)
    return items


def _all_rewrite_entities(doc: ezdxf.document.Drawing) -> list[DXFEntity]:
    return [entity for entity, _in_block in _rewrite_entities_with_scope(doc)]


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
